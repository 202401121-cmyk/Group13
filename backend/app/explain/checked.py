"""Stage explain with the citation check (US-22, S5-4): ExplainStage's paragraphs, each sentence read against its
quotes by a local NLI model before a shopper sees it.

ExplainStage writes as before (US-20). Then every written paragraph is checked (check.py): a sentence its quotes don't
support, or that names a number neither its quotes nor the scores hold, is swapped for the template's sentence for the
same point, or removed, and a paragraph left with no written sentence becomes the full template. Template paragraphs
are code's own words and stay as they are.

Off by default (P-48): unless NLI=on, the stage gives ExplainStage's paragraphs unchanged and never loads the model.

Time (DECIDED 3): the check gets at most `time_limit_s` (5 s) for every product together, and never runs past
explain's own 15 s limit or the time the search has left. Each product's pairs go to the model in one batch. Model
missing, wrong sha256, failing, not tuned, or out of time: only sentences that quote a cited
quote word for word are kept, the paragraph is llm_unchecked, and one log line names the reason (DECIDED 4). A product
checked before the time ran out keeps its check.

The model loads in its own thread when the stage is built, overlapping the search (nli.py). Any other error goes to the
runner, whose fallback is ExplainStage's: the template, which never needs the model.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from app.contracts import STAGE_TIME_LIMITS_S, ExplainInput, ExplainResult, LLMClient, StageContext, StageName

from .check import check_explanation, quoted_only
from .facts import facts_for
from .nli import NliHolder, NliUnavailable, PairScorer, shared_nli
from .nli_settings import NliSettings, load_nli_settings
from .stage import ExplainStage

log = logging.getLogger(__name__)

MARGIN_S = 1.0  # kept free of explain's limit, so the check can never cost the paragraphs already written


class CheckedExplainStage:
    """ExplainStage's paragraphs, each sentence checked against the quotes it cites."""

    name = StageName.EXPLAIN
    is_placeholder = False

    def __init__(
        self,
        llm: LLMClient | None = None,
        *,
        settings: NliSettings | None = None,
        nli: NliHolder | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings if settings is not None else load_nli_settings()
        self.inner = ExplainStage(llm)
        self.baseline = self.inner.baseline
        self._clock = clock
        self._nli = nli
        if self._nli is None and self.settings.enabled and self.settings.pinned:
            self._nli = shared_nli(self.settings)
        if self._nli is not None and self.settings.enabled:
            self._nli.start()  # the load overlaps the search: research, extract, match, verify, decide

    def run(self, inp: ExplainInput, ctx: StageContext) -> ExplainResult:
        if not self.settings.enabled:  # NLI is off (the default, P-48): explain exactly as before US-22
            return self.inner.run(inp, ctx)
        started = self._clock()
        search_left = ctx.seconds_left  # the runner stops waiting at the search's 150 s too, so the check must as well
        written = self.inner.run(inp, ctx)
        if not any(e.method == "llm_unchecked" for e in written.explanations):
            return written  # templates only: code's own words, nothing to check
        facts = {f.product_id: f for f in (facts_for(r, inp.claims) for r in inp.ranked)}
        claims = {c.id: c for c in inp.claims}
        deadline = min(
            started + STAGE_TIME_LIMITS_S[StageName.EXPLAIN] - MARGIN_S,
            started + search_left - MARGIN_S,
            self._clock() + self.settings.time_limit_s,
        )
        scorer, reason = self._scorer(deadline)
        out = []
        for explanation in written.explanations:
            if explanation.method != "llm_unchecked":
                out.append(explanation)
                continue
            product = facts[explanation.product_id]
            if scorer is not None and self.settings.threshold is not None:
                try:
                    out.append(
                        check_explanation(
                            explanation, product, claims, scorer, self.settings.threshold, deadline - self._clock()
                        )
                    )
                    continue
                except NliUnavailable as exc:  # this product and the rest fall back; those checked keep it
                    scorer, reason = None, exc.reason
            out.append(quoted_only(explanation, product, claims, self.settings.quoted_words))
        if reason is not None:
            log.info("explain: the citation check fell back to sentences that quote their evidence: %s", reason)
        return ExplainResult(explanations=out)

    def fallback(self, inp: ExplainInput, ctx: StageContext, error: Exception) -> ExplainResult:
        return self.inner.fallback(inp, ctx, error)

    def _scorer(self, deadline: float) -> tuple[PairScorer | None, str | None]:
        if self._nli is None or self.settings.threshold is None:
            return None, "no revision, sha256 and threshold are set in config/nli.yaml"
        try:
            return self._nli.get(deadline - self._clock()), None
        except NliUnavailable as exc:
            return None, exc.reason
