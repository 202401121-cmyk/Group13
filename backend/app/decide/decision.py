"""One decision (US-17, US-18): filter in code, then rank what is shown. The LLM never filters or ranks (DR-01)."""

from __future__ import annotations

from app.contracts import DecideInput, DecideResult

from .directions import directions_for
from .rules import filter_products, index_values
from .scoring import rank_with_notes


def decide_with_notes(inp: DecideInput) -> tuple[DecideResult, list[str]]:
    """decide(), and the criteria scored by comparing the shown products' stated values (US-18), for the decide note.
    DecideResult has no notes field, so the stage passes them on through its coverage note."""
    values = index_values(inp.values, {p.id for p in inp.products})
    rubric = inp.rubric
    outcome = filter_products(inp.products, rubric.budget, rubric.hard_rules, rubric.exclusions, values)
    directions = directions_for([w.key for w in rubric.weights], rubric.category)
    ranked, compared = rank_with_notes(outcome.shown, rubric, values, directions)
    result = DecideResult(
        ranked=ranked,
        filtered=outcome.filtered,
        relaxation_hint=outcome.relaxation_hint,
        method="weighted",
    )
    return result, compared


def decide(inp: DecideInput) -> DecideResult:
    """The same input always gives the same result. The plain weighted score uses no randomness, so no seed."""
    return decide_with_notes(inp)[0]
