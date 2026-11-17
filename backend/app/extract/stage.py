"""The extract stage (US-14, US-28). ExtractStage is the stub (US-52), and stays as the real stage's fallback.

GuardedExtractStage reads one source through the wrapper with every US-28 defence in place: the text only inside the
data block, the answer only as schema JSON, and each claim checked by code.

The runner calls it once per source, in parallel, so it keeps a small count for each search: the Reddit tokens booked
so far, the sources sent, and the ones it skipped or could not read. These are numbers only, and the runner collects
them as a coverage note at the end. Claims are never kept here: Reddit text and what's drawn from it stay with the run
(D-53).
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.contracts import (
    Claim,
    DataClass,
    Difficulty,
    ExtractInput,
    ExtractionResult,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    Source,
    StageContext,
    StageName,
    examples,
)
from app.decide.features import known_feature
from app.llm.fitting import estimate_tokens
from app.llm.output import build_system_prompt
from app.llm.pool import ATTEMPT_SHARE, CALL_MARGIN_S, time_limit_s
from app.llm.usage import TokenMeter
from app.llm.window import send_window

from .parsing import RawExtraction, to_extraction_result
from .prompts import (
    BASE_CLAIMS,
    EXTRA_CLAIMS,
    SYSTEM_PROMPT,
    PromptCriteria,
    SpecAsk,
    allowed_criteria,
    user_message,
)
from .settings import ExtractSettings, load_extract_settings

log = logging.getLogger(__name__)

MAX_TRACKED_SEARCHES = 64  # a search that never collects its note must not grow this stage's memory for ever
SEARCH_SNIPPET = "search_snippet"  # Source.via for a search result whose page was not fetched
# The longest a Reddit thread waits for running calls to free the budget: it still leaves a full attempt's time.
BUDGET_WAIT_S = time_limit_s(LLMTask.EXTRACT_CLAIMS) * (1 - ATTEMPT_SHARE) - CALL_MARGIN_S

_FULL_SYSTEM_TOKENS = estimate_tokens(build_system_prompt(SYSTEM_PROMPT, RawExtraction))


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ExtractStage:
    """Returns the example claims that belong to this one source, plus the example mentions."""

    name = StageName.EXTRACT
    is_placeholder = True

    def run(self, inp: ExtractInput, ctx: StageContext) -> ExtractionResult:
        return ExtractionResult(
            source_id=inp.source.id,
            claims=[c for c in examples.claims() if c.source_id == inp.source.id],
            mentions=examples.mentions(),
        )

    def fallback(self, inp: ExtractInput, ctx: StageContext, error: Exception) -> ExtractionResult:
        # Nothing read from this source is better than a guess (build rule 5).
        return ExtractionResult(source_id=inp.source.id)


@dataclass
class _Counts:
    """One search so far. Numbers only, so it holds nothing from a source."""

    reddit_booked: int = 0
    reddit_running: int = 0  # the part of reddit_booked held by calls still running
    sent: int = 0
    reddit_sent: int = 0
    over_reddit_budget: int = 0
    over_reddit_share: int = 0  # Reddit threads past their share of the source cap, to leave room for pages
    over_source_cap: int = 0
    unreadable: int = 0
    no_criteria: int = 0  # sent with no criteria: it can name products, but every claim it gives is dropped
    not_reached: int = 0  # never sent: the search had no time left to send it and still finish (US-46)

    def snapshot(self) -> dict[str, int]:
        """The counts the live briefs' funnel reads (US-14): sources sent and not sent, by reason."""
        return {
            "sent": self.sent,
            "over_reddit_budget": self.over_reddit_budget,
            "over_reddit_share": self.over_reddit_share,
            "over_source_cap": self.over_source_cap,
            "unreadable": self.unreadable,
            "no_criteria": self.no_criteria,
            "not_reached": self.not_reached,
        }


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _note(counts: _Counts) -> str | None:
    parts: list[str] = []
    if counts.over_reddit_budget:
        threads = _plural(counts.over_reddit_budget, "Reddit thread was", "Reddit threads were")
        parts.append(f"{threads} not read, to stay within this search's reading limit.")
    if counts.over_source_cap:
        sources = _plural(counts.over_source_cap, "source was", "sources were")
        parts.append(f"{sources} not read, to keep the search from running too long.")
    if counts.over_reddit_share:  # only under a small cap: under the usual one Reddit never reaches its share
        threads = _plural(counts.over_reddit_share, "Reddit thread was", "Reddit threads were")
        parts.append(f"{threads} not read, to leave room for web pages.")
    if counts.not_reached:
        sources = _plural(counts.not_reached, "source was", "sources were")
        parts.append(f"{sources} not read, because the search ran out of time to send them.")
    if counts.unreadable:
        sources = _plural(counts.unreadable, "source", "sources")
        parts.append(
            f"{sources} could not be read, because the AI services were busy or their answers could not be used."
        )
    return " ".join(parts) or None


class GuardedExtractStage:
    name = StageName.EXTRACT
    is_placeholder = False
    # The fallback gives nothing, not sample data, so this is not `baseline`: one bad source must not make the runner
    # label real results as a sample.
    stub = ExtractStage()

    def __init__(
        self,
        llm: LLMClient,
        settings: ExtractSettings | None = None,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.llm = llm
        self.settings = settings if settings is not None else load_extract_settings()
        self._now = now
        self._lock = threading.Lock()
        self._settled = threading.Condition(self._lock)  # wakes a thread waiting for the Reddit budget
        self._counts: OrderedDict[str, _Counts] = OrderedDict()
        self._asks: OrderedDict[str, SpecAsk] = OrderedDict()  # keys only: no words of the shopper's
        self._windows: OrderedDict[str, float] = OrderedDict()  # when each search's send window ends (US-46)

    def run(self, inp: ExtractInput, ctx: StageContext) -> ExtractionResult:
        started = time.monotonic()
        source = inp.source
        if source.via == SEARCH_SNIPPET:  # it stays a source and a link, but a few words are too little to quote from
            return ExtractionResult(source_id=source.id)
        criteria = allowed_criteria(inp, source.data_class, self._ask_for(ctx.search_id), self.settings)
        user = user_message(source, criteria)
        held = self._booking(user, source.data_class)
        if not self._may_send(ctx.search_id, source, held):
            return ExtractionResult(source_id=source.id)
        if not criteria.keys:
            with self._lock:
                self._for(ctx.search_id).no_criteria += 1
        meter = TokenMeter(already_spent_s=time.monotonic() - started)  # the runner's limit started with this run
        failed = False
        try:
            window = self._window_for(ctx.search_id) if source.data_class is DataClass.PUBLIC else None
            with meter.counting(), send_window(window):
                raw = self.llm.complete(
                    LLMTask.EXTRACT_CLAIMS,
                    criteria.system,  # SYSTEM_PROMPT, or its price-first variant for a public page (S4-1)
                    user,
                    RawExtraction,
                    data_class=source.data_class,
                    difficulty=Difficulty.LONG,
                    search_id=ctx.search_id,
                    max_output_tokens=self.settings.output_tokens(source.data_class),
                )
        except LLMUnavailable:
            failed = True
            log.info("extract: a %s source was dropped: no valid answer", source.type.value)
            with self._lock:
                self._for(ctx.search_id).unreadable += 1
            return ExtractionResult(source_id=source.id)
        finally:
            self._book_real_use(ctx.search_id, source, held, meter, failed)
        result = to_extraction_result(
            raw,
            source,
            criteria.keys,
            self._now(),
            self.settings.domain_tiers,
            inp.category_label,
            # Only a call asked for keys first can be trimmed, and only past 15 claims, or past 10 that aren't on those
            # keys: the 5 above the base are theirs. An answer that arrived whole within that keeps every claim.
            room=BASE_CLAIMS + EXTRA_CLAIMS if criteria.first else None,
            first=criteria.first,
            others=BASE_CLAIMS,
        )
        return _under_own_keys(result, criteria)

    def fallback(self, inp: ExtractInput, ctx: StageContext, error: Exception) -> ExtractionResult:
        return self.stub.fallback(inp, ctx, error)

    def ask_for_specs(
        self, search_id: str, attributes: Sequence[str], has_budget: bool, features: Sequence[str] = ()
    ) -> None:
        """Before a search's sources are read: its hard rules' keys, whether it has a budget (US-14) and the features
        it rules out (S3-9). Public calls ask for the known spec keys among them, for price_inr with a budget, and for
        each known feature as "has_<feature>", so verify and decide get values.

        Only features config/exclusions.yaml lists are kept, by their own names there: whatever a caller passes, this
        stage's memory never holds the shopper's words."""
        known = (known_feature(feature) for feature in features)
        kept = tuple(dict.fromkeys(name for name in known if name is not None))
        with self._lock:
            self._asks[search_id] = SpecAsk(tuple(attributes), has_budget, kept)
            self._asks.move_to_end(search_id)
            while len(self._asks) > MAX_TRACKED_SEARCHES:
                self._asks.popitem(last=False)

    def coverage_note(self, search_id: str) -> str | None:
        """What this search skipped or could not read, in plain words. Asked once, at the end: its counts then go."""
        with self._lock:
            self._asks.pop(search_id, None)
            self._windows.pop(search_id, None)
            counts = self._counts.pop(search_id, None)
        return _note(counts) if counts is not None else None

    def open_window(self, search_id: str, until: float) -> None:
        """This search sends new sources until `until` (time.monotonic): a public call may wait inside it (US-46)."""
        with self._lock:
            self._windows[search_id] = until
            while len(self._windows) > MAX_TRACKED_SEARCHES:
                self._windows.popitem(last=False)

    def count_not_reached(self, search_id: str, count: int) -> None:
        """Sources this search never sent, for its coverage note and the funnel."""
        if count > 0:
            with self._lock:
                self._for(search_id).not_reached += count

    def room_for(self, search_id: str, inp: ExtractInput, within_s: float) -> bool | None:
        """Wait, up to `within_s`, until a public provider has room for this page (US-46). True at once for a client
        that can't say: None, so it is sent at once, as before. Only looks: the call books its own slot."""
        wait = getattr(self.llm, "wait_for_public_room", None)
        if wait is None:
            return None
        data_class = inp.source.data_class
        criteria = allowed_criteria(inp, data_class, self._ask_for(search_id), self.settings)
        sent_in = _FULL_SYSTEM_TOKENS + estimate_tokens(user_message(inp.source, criteria))
        return bool(wait(LLMTask.EXTRACT_CLAIMS, sent_in, self.settings.output_tokens(data_class), within_s))

    def counts_for(self, search_id: str) -> dict[str, int] | None:
        """A copy of this search's counts so far, or None. Unlike coverage_note, reading them clears nothing."""
        with self._lock:
            counts = self._counts.get(search_id)
            return None if counts is None else counts.snapshot()

    def booked_tokens(self, inp: ExtractInput) -> int:
        """What the pool will book for this source: the same sum as the wrapper's own, for a source that fits."""
        data_class = inp.source.data_class
        criteria = allowed_criteria(inp, data_class, settings=self.settings)
        return self._booking(user_message(inp.source, criteria), data_class)

    def _window_for(self, search_id: str) -> float | None:
        with self._lock:
            return self._windows.get(search_id)

    def _ask_for(self, search_id: str) -> SpecAsk | None:
        with self._lock:
            return self._asks.get(search_id)

    def _booking(self, user: str, data_class: DataClass) -> int:
        return _FULL_SYSTEM_TOKENS + estimate_tokens(user) + self.settings.output_tokens(data_class)

    def _may_send(self, search_id: str, source: Source, booking: int) -> bool:
        """Book this source if it fits the search's limits. Under one lock, as sources run in parallel.

        A Reddit call's whole booking counts while it runs, then its real use. So a thread with no room waits while the
        running calls could still free enough, as it would otherwise be skipped for tokens about to come back.
        """
        reddit = source.data_class is DataClass.REDDIT
        give_up_at = time.monotonic() + BUDGET_WAIT_S
        with self._settled:
            counts = self._for(search_id)
            if reddit and self._over_share(counts):
                counts.over_reddit_share += 1  # at once, never after a wait: the share can't free up
                return False
            while reddit and counts.reddit_booked + booking > self.settings.reddit_tokens_per_search:
                left = give_up_at - time.monotonic()
                if left <= 0 or not self._running_calls_may_free(counts, booking):
                    counts.over_reddit_budget += 1
                    return False
                self._settled.wait(left)
            if reddit and self._over_share(counts):  # the wait let go of the lock: other threads may have taken it
                counts.over_reddit_share += 1
                return False
            if counts.sent >= self.settings.max_sources_per_search:
                counts.over_source_cap += 1
                return False
            counts.sent += 1
            if reddit:
                counts.reddit_sent += 1
                counts.reddit_booked += booking
                counts.reddit_running += booking
            return True

    def _reddit_room(self) -> int:
        """How many of the source cap Reddit threads may take: their share, rounded up, and at least one."""
        share = self.settings.reddit_share_of_sources * self.settings.max_sources_per_search
        return max(1, math.ceil(share - 1e-9))

    def _over_share(self, counts: _Counts) -> bool:
        """Has Reddit taken its share while the cap still has room? (A full cap is counted as the cap.) The caller
        holds the lock."""
        room_left = counts.sent < self.settings.max_sources_per_search
        return room_left and counts.reddit_sent >= self._reddit_room()

    def _running_calls_may_free(self, counts: _Counts, booking: int) -> bool:
        """Would this booking fit if the calls running now used nothing? The caller holds the lock."""
        unused = counts.reddit_booked - counts.reddit_running + booking <= self.settings.reddit_tokens_per_search
        return self.settings.book_real_use and unused

    def _book_real_use(self, search_id: str, source: Source, held: int, meter: TokenMeter, failed: bool) -> None:
        """A Reddit call is over: swap what it held for what it used. A client that reports nothing keeps the hold."""
        if source.data_class is not DataClass.REDDIT:
            return
        used = meter.tokens if meter.attempts else (0 if failed else held)
        with self._settled:
            counts = self._for(search_id)
            counts.reddit_running = max(0, counts.reddit_running - held)
            if self.settings.book_real_use:
                counts.reddit_booked = max(0, counts.reddit_booked - held + used)
            self._settled.notify_all()

    def _for(self, search_id: str) -> _Counts:
        """This search's counts. The caller holds the lock."""
        counts = self._counts.setdefault(search_id, _Counts())
        self._counts.move_to_end(search_id)
        while len(self._counts) > MAX_TRACKED_SEARCHES:
            self._counts.popitem(last=False)
        return counts


def _under_own_keys(result: ExtractionResult, criteria: PromptCriteria) -> ExtractionResult:
    """A claim made under a fixed key sent in place of a generated one goes back under the search's own key (US-14)."""
    if not criteria.renames:
        return result
    claims = [
        Claim.model_validate({**c.model_dump(), "criterion_key": criteria.claim_key(c.criterion_key)})
        if c.criterion_key is not None and c.criterion_key in criteria.renames
        else c
        for c in result.claims
    ]
    return ExtractionResult.model_validate({**result.model_dump(), "claims": [c.model_dump() for c in claims]})
