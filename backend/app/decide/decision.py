"""One decision (US-17, US-18): filter in code, then rank what is shown. The LLM never filters or ranks (DR-01)."""

from __future__ import annotations

from app.contracts import DecideInput, DecideResult

from .rules import filter_products, index_values
from .scoring import rank


def decide(inp: DecideInput) -> DecideResult:
    """The same input always gives the same result. The plain weighted score uses no randomness, so no seed."""
    values = index_values(inp.values, {p.id for p in inp.products})
    rubric = inp.rubric
    outcome = filter_products(inp.products, rubric.budget, rubric.hard_rules, rubric.exclusions, values)
    return DecideResult(
        ranked=rank(outcome.shown, rubric, values),
        filtered=outcome.filtered,
        relaxation_hint=outcome.relaxation_hint,
        method="weighted",
    )
