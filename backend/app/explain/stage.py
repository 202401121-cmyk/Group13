"""Stage explain (US-20): why each product fits, from code's numbers, in plain words.

Code picks the facts (facts.py): each product's top 3 criteria by contribution, one trade-off and what it couldn't
verify. The LLM only writes the words, for the top 3 products, in one private call (the shopper's priorities are in
it, D-51). Each paragraph is checked (parsing.py); one that names a number code didn't give, cites the wrong claims or
breaks a rule becomes the template, and one too hard to read is asked for once more, then becomes the template. The
template (templates.py) says the same facts in fixed words: it is what products 4 to 10, products nothing could be
ranked on and products with no claim to cite get. With no LLM provider answering, every product gets it, as intake and
criteria use their rules (US-20, criterion 5). Any other error goes to the runner, whose fallback is the template too
(TemplateExplainStage).

The method is llm_unchecked: US-22 adds the check that each sentence is supported by its claims (Sprint 5).
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from collections.abc import Mapping, Sequence

from app.contracts import (
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

from .facts import ProductFacts, facts_for, label
from .parsing import RawExplanations, RawParagraph, Rejected, to_contract
from .prompts import REWRITE_PROMPT, SYSTEM_PROMPT, number_refs, rewrite_message, write_message
from .templates import template

log = logging.getLogger(__name__)

MAX_WRITTEN = 3  # the LLM writes for the top 3 by rank (US-20, DECIDED 1); the rest get the template
OUTPUT_TOKENS = 2048  # 3 paragraphs are about 600 tokens; Groq's reasoning models think within the same limit


class TemplateExplainStage:
    """The baseline: every shown product's explanation in fixed words, from code's numbers. Never needs the LLM."""

    name = StageName.EXPLAIN
    is_placeholder = False  # code's own numbers, never a sample

    def run(self, inp: ExplainInput, ctx: StageContext) -> ExplainResult:
        return ExplainResult(explanations=[template(facts_for(r, inp.claims)) for r in inp.ranked])

    def fallback(self, inp: ExplainInput, ctx: StageContext, error: Exception) -> ExplainResult:
        return self.run(inp, ctx)


class ExplainStage:
    """The LLM writes the words for the top 3; code checks every paragraph, and the template covers the rest."""

    name = StageName.EXPLAIN
    is_placeholder = False

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm
        self.baseline = TemplateExplainStage()

    def run(self, inp: ExplainInput, ctx: StageContext) -> ExplainResult:
        facts = [facts_for(r, inp.claims) for r in inp.ranked]
        asked = [f for f in facts[:MAX_WRITTEN] if f.score is not None and f.claim_ids()]
        written: dict[str, Explanation] = {}
        if asked:  # nothing to ask about: products not ranked, or with no claim to cite, keep the template
            try:
                written = self._write(inp, asked, ctx)
            except LLMUnavailable:  # as intake and criteria do: the template, from the same numbers (US-20)
                log.info("explain: no LLM provider answered; every product gets the template")
        return ExplainResult(explanations=[written.get(f.product_id) or template(f) for f in facts])

    def fallback(self, inp: ExplainInput, ctx: StageContext, error: Exception) -> ExplainResult:
        return self.baseline.run(inp, ctx)

    def _client(self) -> LLMClient:
        if self._llm is not None:
            return self._llm
        from app.presearch import default_llm  # here, because app.presearch imports the pipeline, which imports this

        return default_llm()

    def _write(self, inp: ExplainInput, asked: list[ProductFacts], ctx: StageContext) -> dict[str, Explanation]:
        started = time.monotonic()  # the runner's time limit started with this run
        llm = self._client()
        priorities = [label(key) for key in inp.user_priorities]
        refs = number_refs(asked)
        with TokenMeter(already_spent_s=time.monotonic() - started).counting():
            answer = llm.complete(
                LLMTask.WRITE_EXPLANATION,
                SYSTEM_PROMPT,
                write_message(priorities, asked, refs),
                RawExplanations,
                data_class=DataClass.PRIVATE,
                difficulty=Difficulty.MEDIUM,
                search_id=ctx.search_id,
                max_output_tokens=OUTPUT_TOKENS,
            )
        written, too_hard = _checked(answer, asked, refs)
        if not too_hard:
            return written
        hard_facts = [f for f, _ in too_hard]
        try:
            # The wrapper's deadline is the stage's limit less what the first call used, so the rewrite can never run
            # past the runner's limit and cost the paragraphs already kept (build rule 9).
            with TokenMeter(already_spent_s=time.monotonic() - started).counting():
                again = llm.complete(
                    LLMTask.WRITE_EXPLANATION,
                    REWRITE_PROMPT,
                    rewrite_message(priorities, hard_facts, refs, [p.model_dump(mode="json") for _, p in too_hard]),
                    RawExplanations,
                    data_class=DataClass.PRIVATE,
                    difficulty=Difficulty.MEDIUM,
                    search_id=ctx.search_id,
                    max_output_tokens=OUTPUT_TOKENS,
                )
        except LLMUnavailable:
            for f in hard_facts:
                log.info("explain: %s gets the template: it was too hard to read, and the rewrite failed", f.product_id)
            return written
        rewritten, still_hard = _checked(again, hard_facts, refs)
        for f, _ in still_hard:
            log.info("explain: %s gets the template: the rewrite was still too hard to read", f.product_id)
        return {**written, **rewritten}


def _checked(
    answer: RawExplanations, asked: Sequence[ProductFacts], refs: Mapping[str, str]
) -> tuple[dict[str, Explanation], list[tuple[ProductFacts, RawParagraph]]]:
    """The paragraphs that passed, and those that passed everything but readability. Every other one is logged."""
    counts = Counter(p.product for p in answer.paragraphs)
    by_product = {p.product: p for p in answer.paragraphs}
    written: dict[str, Explanation] = {}
    too_hard: list[tuple[ProductFacts, RawParagraph]] = []
    for facts in asked:
        found = counts[facts.product_id]
        if found != 1:  # none, or two for one product: never guess which
            log.info("explain: %s gets the template: %d paragraphs came back for it", facts.product_id, found)
            continue
        paragraph = by_product[facts.product_id]
        try:
            written[facts.product_id] = to_contract(paragraph, facts, refs)
        except Rejected as rejected:
            if rejected.too_hard:
                too_hard.append((facts, paragraph))
            else:
                log.info("explain: %s gets the template: %s", facts.product_id, rejected.reason)
    return written, too_hard
