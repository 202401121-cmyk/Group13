"""Research stub (US-52). US-12, US-13 and US-38 replace run() with real searches; this becomes the fallback."""

from __future__ import annotations

from app.contracts import ResearchInput, ResearchResult, StageContext, StageName, examples


class ResearchStage:
    """Returns the three example sources.

    `fail=True` makes run() raise, so fault tests can prove the pipeline survives a research failure.
    """

    name = StageName.RESEARCH
    is_placeholder = True

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def run(self, inp: ResearchInput, ctx: StageContext) -> ResearchResult:
        if self.fail:
            raise RuntimeError("simulated research failure")
        return examples.research()

    def fallback(self, inp: ResearchInput, ctx: StageContext, error: Exception) -> ResearchResult:
        # One query and no sources: the search goes on, and the coverage note says research fell back.
        query = " ".join(part for part in ("best", inp.rubric.category, inp.rubric.use_case) if part)
        return ResearchResult(queries=[query], sources=[])
