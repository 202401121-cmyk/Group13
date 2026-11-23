"""Stage explain (US-20, US-22, D-91): why each product fits. Code writes the personal part from its own numbers; a
public LLM call writes evidence sentences from public quotes only, each checked against the quotes it cites, and the
template backs both."""

from .stage import ExplainStage, TemplateExplainStage

__all__ = ["ExplainStage", "TemplateExplainStage"]
