"""The research stage (US-12, US-27). ResearchStage is the US-52 stub and baseline; LiveResearchStage is real."""

from .stage import LiveResearchStage, ResearchStage


def default_research_stage() -> LiveResearchStage:
    return LiveResearchStage()


__all__ = ["LiveResearchStage", "ResearchStage", "default_research_stage"]
