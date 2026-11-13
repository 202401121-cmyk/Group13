"""The decide stage (US-17, US-18, DR-01, DR-08 to DR-10): plain code only, the LLM never filters or ranks.

DecideStage is the Sprint 1 stub, kept as it was (replace, never rewrite: D-20). WeightedDecideStage replaces it: it
filters on the budget, the active hard rules and the exclusions, then ranks what is shown on the shopper's weights. Its
fallback hands over to the stub, so a failure gives the example ranking under the sample note. US-19's simulations
come later, with the weighted score as their fallback.

Its notes (criteria left out, or nothing ranked) reach the coverage note through `coverage_note(search_id)`, which the
pipeline reads after the stage, as it reads extract's (S3-10b).
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict

from app.contracts import DecideInput, DecideResult, StageContext, StageName, examples

from .decision import decide
from .notes import decide_note

log = logging.getLogger(__name__)

MAX_NOTES_KEPT = 1024  # the pipeline reads each note right after decide: this bound only guards against a leak


class DecideStage:
    """Returns the example ranking: three ranked, one filtered out, one can't-tell."""

    name = StageName.DECIDE
    is_placeholder = True

    def run(self, inp: DecideInput, ctx: StageContext) -> DecideResult:
        return examples.decision()

    def fallback(self, inp: DecideInput, ctx: StageContext, error: Exception) -> DecideResult:
        return examples.decision()


class WeightedDecideStage:
    """Filters, then ranks by the weighted score (method "weighted"). The same input always gives the same result."""

    name = StageName.DECIDE
    is_placeholder = False

    def __init__(self) -> None:
        self.baseline = DecideStage()  # what the fallback hands over to, so the sample label follows it (D-20)
        self._lock = threading.Lock()
        self._notes: OrderedDict[str, str] = OrderedDict()

    def run(self, inp: DecideInput, ctx: StageContext) -> DecideResult:
        result = decide(inp)
        note = decide_note(result)
        if note is not None:  # criterion names and fixed words only, never what the shopper typed
            log.info("decide: %s", note)
            self._keep(ctx.search_id, note)
        return result

    def coverage_note(self, search_id: str) -> str | None:
        """This search's note for the coverage note, or None. Read once: it is removed as it is read."""
        with self._lock:
            return self._notes.pop(search_id, None)

    def _keep(self, search_id: str, note: str) -> None:
        with self._lock:
            self._notes[search_id] = note
            self._notes.move_to_end(search_id)
            while len(self._notes) > MAX_NOTES_KEPT:
                self._notes.popitem(last=False)

    def fallback(self, inp: DecideInput, ctx: StageContext, error: Exception) -> DecideResult:
        log.warning("decide fell back to the example ranking (%s)", type(error).__name__)
        return self.baseline.fallback(inp, ctx, error)
