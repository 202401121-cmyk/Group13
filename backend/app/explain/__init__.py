"""Stage explain (US-20): why each product fits, from code's numbers. The LLM writes the words; a template backs it."""

from .stage import ExplainStage, TemplateExplainStage

__all__ = ["ExplainStage", "TemplateExplainStage"]
