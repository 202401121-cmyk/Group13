"""Stage explain (US-20, US-22, D-91): why each product fits, in code's words, with sentences from public evidence.

- The personal part is code's (templates.py, from facts.py): each product's rank and score, its top 3 criteria by
  contribution and why each matters to this shopper, one trade-off, and what could not be verified, by name. No LLM sees
  the shopper's priorities, weights or words: the private explanation call is gone (D-91).
- Evidence sentences: each of the top 3 by rank with public quotes gets one public-route call (WRITE_EVIDENCE:
  Mistral, then Gemini, then Groq as the last resort, D-76) given only its name and those quotes (evidence.py). The
  calls run side by side. A product whose only evidence is community words gets code's COMMUNITY_ONLY sentence instead:
  those words are never sent anywhere.
- Code checks each sentence (parsing.py), then the citation check reads it against its quotes (check.py): with NLI=on
  and the model ready, the local model; otherwise only sentences that quote a cited quote word for word (DECIDED 5). A
  failed sentence is dropped.
- A paragraph always has code's sentences, so an explanation always appears (paragraph.py). With no evidence sentence
  kept (no public quote, every sentence failing, or no public provider answering) it is the template.

Time: the whole stage inside its 15 s and the search's time left, less MARGIN_S. The calls leave the check its own
`time_limit_s` when it can run. The model loads in its own thread when the stage is built, overlapping the search.
Any other error goes to the runner, whose fallback is the template (TemplateExplainStage), which never needs a model.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Literal

from app.contracts import (
    STAGE_TIME_LIMITS_S,
    Claim,
    DataClass,
    Difficulty,
    ExplainInput,
    ExplainResult,
    Explanation,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    StageContext,
    StageName,
)
from app.llm.usage import TokenMeter

from .check import checked_by_model, checked_by_quotes
from .evidence import EvidenceRequest, community_only, community_quotes, evidence_request
from .facts import ProductFacts, facts_for
from .nli import NliHolder, NliUnavailable, PairScorer, shared_nli
from .nli_settings import NliSettings, load_nli_settings
from .paragraph import paragraph
from .parsing import Draft, RawEvidence, drafts_from
from .prompts import SYSTEM_PROMPT, evidence_message
from .templates import template

log = logging.getLogger(__name__)

MAX_WRITTEN = 3  # evidence sentences for the top 3 by rank (US-20); the rest get the template
OUTPUT_TOKENS = 1024  # at most 4 short sentences; Groq's reasoning models think within the same limit
MARGIN_S = 1.0  # kept free of the stage's limit, so the paragraphs are always handed over in time
STAGE_LIMIT_S = float(STAGE_TIME_LIMITS_S[StageName.EXPLAIN])

Method = Literal["llm_checked", "llm_unchecked"]


class TemplateExplainStage:
    """The baseline: every shown product's explanation in code's words, from code's numbers. Never needs a model."""

    name = StageName.EXPLAIN
    is_placeholder = False  # code's own numbers, never a sample

    def run(self, inp: ExplainInput, ctx: StageContext) -> ExplainResult:
        return ExplainResult(explanations=[template(facts_for(r, inp.claims), inp.user_priorities) for r in inp.ranked])

    def fallback(self, inp: ExplainInput, ctx: StageContext, error: Exception) -> ExplainResult:
        return self.run(inp, ctx)


@dataclass(frozen=True)
class _Asked:
    facts: ProductFacts
    request: EvidenceRequest
    community: tuple[str, ...]  # its community quotes: never sent, and never allowed back in a sentence


class ExplainStage:
    """Code's paragraph for every product, with checked evidence sentences from public quotes for the top 3."""

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
        self._llm = llm
        self.baseline = TemplateExplainStage()
        self.settings = settings if settings is not None else load_nli_settings()
        self._clock = clock
        self._nli = nli
        if self._nli is None and self.settings.enabled and self.settings.pinned:
            self._nli = shared_nli(self.settings)
        if self._nli is not None and self.settings.enabled:
            self._nli.start()  # the load overlaps the search: research, extract, match, verify, decide

    def run(self, inp: ExplainInput, ctx: StageContext) -> ExplainResult:
        started = self._clock()
        end = started + min(STAGE_LIMIT_S, ctx.seconds_left) - MARGIN_S
        facts = [facts_for(r, inp.claims) for r in inp.ranked]
        asked, community = _asked(facts, inp.claims)
        reserve = self.settings.time_limit_s if self._may_check() else 0.0
        written = self._write(asked, ctx, end - reserve)
        kept, methods = self._check(asked, written, end)
        return ExplainResult(
            explanations=[
                _paragraph(f, inp.user_priorities, kept, methods, community_only=f.product_id in community)
                for f in facts
            ]
        )

    def fallback(self, inp: ExplainInput, ctx: StageContext, error: Exception) -> ExplainResult:
        return self.baseline.run(inp, ctx)

    def _client(self) -> LLMClient:
        if self._llm is not None:
            return self._llm
        from app.presearch import default_llm  # here, because app.presearch imports the pipeline, which imports this

        return default_llm()

    def _may_check(self) -> bool:
        return self.settings.enabled and self._nli is not None and self.settings.threshold is not None

    # --- the evidence calls --------------------------------------------------------------------------------------

    def _write(self, asked: Sequence[_Asked], ctx: StageContext, until: float) -> dict[str, list[Draft]]:
        """Each asked product's drafts: its call's sentences that pass code's rules. [] when its call failed."""
        if not asked:
            return {}
        if until - self._clock() <= 0:
            log.info("explain: no time was left for the evidence calls; every product gets code's sentences only")
            return {}
        llm = self._client()
        pool = ThreadPoolExecutor(max_workers=len(asked), thread_name_prefix="explain")
        futures = {a.facts.product_id: pool.submit(self._one, llm, a, ctx, until) for a in asked}
        pool.shutdown(wait=False)
        written: dict[str, list[Draft]] = {}
        for product_id, future in futures.items():
            try:
                written[product_id] = future.result(timeout=max(0.0, until - self._clock()))
            except FutureTimeout:
                log.info("explain: %s gets code's sentences only: its evidence call ran out of time", product_id)
                written[product_id] = []
        return written

    def _one(self, llm: LLMClient, asked: _Asked, ctx: StageContext, until: float) -> list[Draft]:
        product_id = asked.facts.product_id
        # The wrapper's deadline is the stage's limit less what is "spent": so it ends by `until` (build rule 9).
        spent = STAGE_LIMIT_S - (until - self._clock())
        try:
            with TokenMeter(already_spent_s=spent).counting():
                answer = llm.complete(
                    LLMTask.WRITE_EVIDENCE,
                    SYSTEM_PROMPT,
                    evidence_message(asked.request),
                    RawEvidence,
                    data_class=DataClass.PUBLIC,
                    difficulty=Difficulty.MEDIUM,
                    search_id=ctx.search_id,
                    max_output_tokens=OUTPUT_TOKENS,
                )
        except LLMUnavailable:
            log.info("explain: %s gets code's sentences only: no public provider answered", product_id)
            return []
        drafts, dropped = drafts_from(answer, asked.request, asked.facts, asked.community, self.settings.quoted_words)
        for reason, count in sorted(Counter(dropped).items()):
            log.info("explain: %s: %d evidence sentence(s) dropped: %s", product_id, count, reason)
        return drafts

    # --- the citation check --------------------------------------------------------------------------------------

    def _check(
        self, asked: Sequence[_Asked], written: dict[str, list[Draft]], end: float
    ) -> tuple[dict[str, list[Draft]], dict[str, Method]]:
        todo = [(a, written[a.facts.product_id]) for a in asked if written.get(a.facts.product_id)]
        if not todo:
            return {}, {}
        until = min(end, self._clock() + self.settings.time_limit_s)
        scorer, reason = self._scorer(until) if self.settings.enabled else (None, None)
        kept: dict[str, list[Draft]] = {}
        methods: dict[str, Method] = {}
        for item, drafts in todo:
            product_id = item.facts.product_id
            verdicts: list[bool] | None = None
            if scorer is not None and self.settings.threshold is not None:
                try:
                    verdicts = checked_by_model(
                        drafts, item.facts, scorer, self.settings.threshold, until - self._clock()
                    )
                    methods[product_id] = "llm_checked"
                except NliUnavailable as exc:  # this product and the rest fall back; those checked keep it
                    scorer, reason = None, exc.reason
            if verdicts is None:
                verdicts = checked_by_quotes(drafts, self.settings.quoted_words)
                methods[product_id] = "llm_unchecked"
            kept[product_id] = [d for d, ok in zip(drafts, verdicts, strict=True) if ok]
            log.info("explain: %s: %d of %d evidence sentences kept", product_id, len(kept[product_id]), len(drafts))
        if reason is not None:
            log.info("explain: the citation check fell back to sentences that quote their evidence: %s", reason)
        return kept, methods

    def _scorer(self, until: float) -> tuple[PairScorer | None, str | None]:
        if self._nli is None or self.settings.threshold is None:
            return None, "no revision, sha256 and threshold are set in config/nli.yaml"
        try:
            return self._nli.get(until - self._clock()), None
        except NliUnavailable as exc:
            return None, exc.reason


def _asked(facts: Sequence[ProductFacts], claims: Sequence[Claim]) -> tuple[list[_Asked], set[str]]:
    """The top products to ask for evidence sentences, and those whose only evidence is community words."""
    asked: list[_Asked] = []
    community: set[str] = set()
    for f in facts[:MAX_WRITTEN]:
        if f.score is None or not f.top:
            continue  # nothing ranked, nothing to say evidence about: the template
        request = evidence_request(f, claims)
        if request is not None:
            asked.append(_Asked(f, request, tuple(community_quotes(f, claims))))
        elif community_only(f, claims):
            community.add(f.product_id)
    return asked, community


def _paragraph(
    facts: ProductFacts,
    priorities: Collection[str],
    kept: dict[str, list[Draft]],
    methods: dict[str, Method],
    *,
    community_only: bool,
) -> Explanation:
    product_id = facts.product_id
    return paragraph(
        facts,
        priorities,
        kept.get(product_id, []),
        methods.get(product_id, "llm_unchecked"),
        community_only=community_only,
    )
