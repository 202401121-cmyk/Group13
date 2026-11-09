"""Stage extract: real extraction (US-14), with the US-52 stub as its fallback."""

from .stage import ExtractStage, GuardedExtractStage


def default_extract_stage() -> GuardedExtractStage:
    """Real extraction, on the client intake and criteria use, so they all share one pool and its shopper reserve."""
    from app.presearch import default_llm  # here, because app.presearch imports the pipeline, which imports this

    return GuardedExtractStage(default_llm())


__all__ = ["ExtractStage", "GuardedExtractStage", "default_extract_stage"]
