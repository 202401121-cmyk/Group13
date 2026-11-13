"""The verify stage (US-16, DR-03, DR-09): claims in, one Known, Disputed or Unknown value per product and key out.

VerifyStage is the Sprint 1 stub, kept as it was (replace, never rewrite: D-20). TrustVerifyStage replaces it: it weighs
every claim by its source's trust (backend/config/trust.yaml), learns which sites to trust from how often they agree,
and marks contested facts Disputed with both sides. Its fallback is the tier-order baseline, which is real, so a
fallback is never labelled a sample; if that fails too, every value is Unknown and the search goes on.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Iterable

from app.contracts import StageContext, StageName, VerifyInput, VerifyResult, examples

from .resolve import FILTER_KEYS, all_unknown, verify
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
        """The search is done with verify (run or fallback): its filter keys go."""
        with self._lock:
            self._filter_keys.pop(search_id, None)

    def run(self, inp: VerifyInput, ctx: StageContext) -> VerifyResult:
        return verify(inp, self.table, "learned", self.filter_keys(ctx.search_id))

    def fallback(self, inp: VerifyInput, ctx: StageContext, error: Exception) -> VerifyResult:
        """Tier order, and the result says so (trust_method "tier_order"). The error's type only, never its text."""
        log.warning("verify fell back to tier order (%s)", type(error).__name__)
        try:
            return verify(inp, self.table, "tier_order", self.filter_keys(ctx.search_id))
        except Exception as exc:
            log.error("verify's tier order failed too (%s); every value is Unknown", type(exc).__name__)
            return all_unknown(inp)
