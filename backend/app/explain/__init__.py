"""Stage explain (US-20, US-22): why each product fits, from code's numbers. The LLM writes the words, a local NLI
model checks each sentence against the quotes it cites, and a template backs both."""

from .checked import CheckedExplainStage
from .stage import ExplainStage, TemplateExplainStage

__all__ = ["CheckedExplainStage", "ExplainStage", "TemplateExplainStage"]
