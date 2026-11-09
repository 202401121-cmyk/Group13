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
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.contracts import (
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
from app.llm.fitting import estimate_tokens
from app.llm.output import build_system_prompt

from .parsing import RawExtraction, to_extraction_result
from .prompts import SYSTEM_PROMPT, allowed_criteria, user_message
from .settings import ExtractSettings, load_extract_settings

log = logging.getLogger(__name__)

MAX_TRACKED_SEARCHES = 64  # a search that never collects its note must not grow this stage's memory for ever

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
    sent: int = 0
    over_reddit_budget: int = 0
    over_source_cap: int = 0
    unreadable: int = 0


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
        self._counts: OrderedDict[str, _Counts] = OrderedDict()

    def run(self, inp: ExtractInput, ctx: StageContext) -> ExtractionResult:
        source = inp.source
        criteria = allowed_criteria(inp, source.data_class)
        user = user_message(source, criteria)
        if not self._may_send(ctx.search_id, source, self._booking(user, source.data_class)):
            return ExtractionResult(source_id=source.id)
        try:
            raw = self.llm.complete(
                LLMTask.EXTRACT_CLAIMS,
                SYSTEM_PROMPT,
                user,
                RawExtraction,
                data_class=source.data_class,
                difficulty=Difficulty.LONG,
                search_id=ctx.search_id,
                max_output_tokens=self.settings.output_tokens(source.data_class),
            )
        except LLMUnavailable:
            log.info("extract: a %s source was dropped: no valid answer", source.type.value)
            with self._lock:
                self._for(ctx.search_id).unreadable += 1
            return ExtractionResult(source_id=source.id)
        return to_extraction_result(raw, source, criteria.keys, self._now(), self.settings.domain_tiers)

    def fallback(self, inp: ExtractInput, ctx: StageContext, error: Exception) -> ExtractionResult:
        return self.stub.fallback(inp, ctx, error)

    def coverage_note(self, search_id: str) -> str | None:
        """What this search skipped or could not read, in plain words. Asked once, at the end: its counts then go."""
        with self._lock:
            counts = self._counts.pop(search_id, None)
        return _note(counts) if counts is not None else None

    def booked_tokens(self, inp: ExtractInput) -> int:
        """What the pool will book for this source: the same sum as the wrapper's own, for a source that fits."""
        data_class = inp.source.data_class
        return self._booking(user_message(inp.source, allowed_criteria(inp, data_class)), data_class)

    def _booking(self, user: str, data_class: DataClass) -> int:
        return _FULL_SYSTEM_TOKENS + estimate_tokens(user) + self.settings.output_tokens(data_class)

    def _may_send(self, search_id: str, source: Source, booking: int) -> bool:
        """Book this source if it fits the search's limits. Under one lock, as sources run in parallel.

        A retry isn't counted, because the wrapper does it out of sight. Bookings run well above real use (5,129 booked
        for 3,216 used when measured), so real use stays inside the budget even so.
        """
        reddit = source.data_class is DataClass.REDDIT
        with self._lock:
            counts = self._for(search_id)
            if reddit and counts.reddit_booked + booking > self.settings.reddit_tokens_per_search:
                counts.over_reddit_budget += 1
                return False
            if counts.sent >= self.settings.max_sources_per_search:
                counts.over_source_cap += 1
                return False
            counts.sent += 1
            if reddit:
                counts.reddit_booked += booking
            return True

    def _for(self, search_id: str) -> _Counts:
        """This search's counts. The caller holds the lock."""
        counts = self._counts.setdefault(search_id, _Counts())
        self._counts.move_to_end(search_id)
        while len(self._counts) > MAX_TRACKED_SEARCHES:
            self._counts.popitem(last=False)
        return counts
