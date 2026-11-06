"""Explain stub (US-52). US-20 and US-22 replace run() with checked LLM explanations; this becomes the fallback."""

from __future__ import annotations

from app.contracts import (
    ExplainInput,
    ExplainResult,
    Explanation,
    ExplanationSentence,
    StageContext,
    StageName,
    examples,
)


class ExplainStage:
    """Returns the example explanations for the top two products."""

    name = StageName.EXPLAIN
    is_placeholder = True

    def run(self, inp: ExplainInput, ctx: StageContext) -> ExplainResult:
        return ExplainResult(explanations=examples.explanations())

    def fallback(self, inp: ExplainInput, ctx: StageContext, error: Exception) -> ExplainResult:
        # A template sentence states only numbers the code already computed, so it needs no citation.
        return ExplainResult(
            explanations=[
                Explanation(
                    product_id=r.product_id,
                    sentences=[ExplanationSentence(text=f"Ranked #{r.rank} with a score of {r.score:.0f} out of 100.")],
                    method="template",
                )
                for r in inp.ranked
            ]
        )
