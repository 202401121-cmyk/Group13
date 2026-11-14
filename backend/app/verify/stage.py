"""The verify stage (US-16, DR-03, DR-09): claims in, one Known, Disputed or Unknown value per product and key out.

VerifyStage is the Sprint 1 stub, kept as it was (replace, never rewrite: D-20). TrustVerifyStage replaces it: it weighs
every claim by its source's trust (backend/config/trust.yaml), learns which sites to trust from how often they agree,
and marks contested facts Disputed with both sides. Its fallback is the tier-order baseline, which is real, so a
fallback is never labelled a sample; if that fails too, every value is Unknown and the search goes on.

Before any of that, a product no source names is dropped (US-40, app/verify/sourced.py). The stage logs each drop once
per search, however many of run, fallback and the last resort find it, and counts the distinct drops for the verify
report's notes (report_notes), until the pipeline tells it to forget the search.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Iterable

from app.contracts import StageContext, StageName, VerifyInput, VerifyResult, examples

from .resolve import FILTER_KEYS, all_unknown, verify
from .sourced import UNSOURCED_REASON, names_of
from .trust import TrustTable, default_trust_table

log = logging.getLogger(__name__)

# Filter keys stay until the pipeline forgets them, once verify is done. This bound only guards against a leak.
MAX_SEARCHES_KEPT = 1024


class VerifyStage:
    """Returns the example resolved values, including a Disputed, an Estimated and an Unknown one."""

    name = StageName.VERIFY
    is_placeholder = True

    def run(self, inp: VerifyInput, ctx: StageContext) -> VerifyResult:
        return examples.verify()

    def fallback(self, inp: VerifyInput, ctx: StageContext, error: Exception) -> VerifyResult:
        return VerifyResult.model_validate({**examples.verify().model_dump(), "trust_method": "tier_order"})


class TrustVerifyStage:
    """Learned source trust (trust_method "learned"), with tier order as the fallback (US-16)."""

    name = StageName.VERIFY
    is_placeholder = False

    def __init__(self, table: TrustTable | None = None) -> None:
        self.table = table if table is not None else default_trust_table()
        self._lock = threading.Lock()
        self._filter_keys: OrderedDict[str, frozenset[str]] = OrderedDict()
        self._dropped: OrderedDict[str, set[str]] = OrderedDict()  # product ids logged as dropped: no source text

    def use_filter_keys(self, search_id: str, keys: Iterable[str]) -> None:
        """The keys decide filters on for this search: the price and the hard rules' attributes (S3-10b).

        VerifyInput has no rubric, so the pipeline says which of its keys are filters, as it tells extract which specs
        to ask for. Keys only. A search that set none has only the price. Kept until forget(search_id).
        """
        with self._lock:
            self._filter_keys[search_id] = frozenset(keys) | FILTER_KEYS
            self._filter_keys.move_to_end(search_id)
            while len(self._filter_keys) > MAX_SEARCHES_KEPT:
                self._filter_keys.popitem(last=False)

    def filter_keys(self, search_id: str) -> frozenset[str]:
        """This search's filter keys: the price alone if it set none."""
        with self._lock:
            return self._filter_keys.get(search_id, FILTER_KEYS)

    def forget(self, search_id: str) -> None:
        """The search is done with verify (run or fallback): its filter keys and its drop count go."""
        with self._lock:
            self._filter_keys.pop(search_id, None)
            self._dropped.pop(search_id, None)

    def report_notes(self, search_id: str) -> list[str]:
        """For the verify report: how many products this search dropped because no source names them (US-40)."""
        with self._lock:
            count = len(self._dropped.get(search_id, ()))
        if not count:
            return []
        return [f"dropped {count} {'product' if count == 1 else 'products'} no source names"]

    def _counted(self, search_id: str, inp: VerifyInput, result: VerifyResult) -> VerifyResult:
        """Note this search's drops, and log each one the first time this search drops it (INFO): its id, its names
        and the reason. Never a source's text."""
        with self._lock:
            logged = self._dropped.setdefault(search_id, set())
            new = [product_id for product_id in result.dropped_unsourced if product_id not in logged]
            logged.update(new)
            self._dropped.move_to_end(search_id)
            while len(self._dropped) > MAX_SEARCHES_KEPT:
                self._dropped.popitem(last=False)
        if new:
            names = names_of(inp)
            for product_id in new:
                log.info("verify: dropped %s %s: %s", product_id, names.get(product_id, []), UNSOURCED_REASON)
        return result

    def run(self, inp: VerifyInput, ctx: StageContext) -> VerifyResult:
        return self._counted(ctx.search_id, inp, verify(inp, self.table, "learned", self.filter_keys(ctx.search_id)))

    def fallback(self, inp: VerifyInput, ctx: StageContext, error: Exception) -> VerifyResult:
        """Tier order, and the result says so (trust_method "tier_order"). The error's type only, never its text."""
        log.warning("verify fell back to tier order (%s)", type(error).__name__)
        try:
            result = verify(inp, self.table, "tier_order", self.filter_keys(ctx.search_id))
        except Exception as exc:
            log.error("verify's tier order failed too (%s); every value is Unknown", type(exc).__name__)
            result = all_unknown(inp)
        return self._counted(ctx.search_id, inp, result)
