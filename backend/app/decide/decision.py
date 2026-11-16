"""One decision (US-17 to US-19): filter in code, then rank what is shown. The LLM never filters or ranks (DR-01).

The ranking comes from 1,000 seeded simulations (US-19, simulation.py, method "monte_carlo"). The plain weighted
ranking (scoring.py) is its starting point and its fallback: it stands when nothing has a score to rank on (S3-10b's
"not ranked"), and, with one log line, when the simulations fail for any reason, running past TIME_LIMIT_S from the
start of the decision included.
"""

from __future__ import annotations

import logging
import time

from app.contracts import DecideInput, DecideResult

from .directions import directions_for
from .rules import filter_products, index_values
from .scoring import rank_with_notes
from .simulation import TIME_LIMIT_S, simulate

log = logging.getLogger(__name__)


def decide_with_notes(inp: DecideInput, *, weighted_only: bool = False) -> tuple[DecideResult, list[str]]:
    """decide(), and the criteria scored by comparing the shown products' stated values (US-18), for the decide note.
    DecideResult has no notes field, so the stage passes them on through its coverage note. `weighted_only` gives the
    plain weighted ranking, with no simulations: for a measure of the weights themselves (evals/interview_sim.py)."""
    deadline = time.monotonic() + TIME_LIMIT_S
    values = index_values(inp.values, {p.id for p in inp.products})
    rubric = inp.rubric
    outcome = filter_products(inp.products, rubric.budget, rubric.hard_rules, rubric.exclusions, values)
    directions = directions_for([w.key for w in rubric.weights], rubric.category)
    ranked, compared = rank_with_notes(outcome.shown, rubric, values, directions)
    weighted = DecideResult(
        ranked=ranked,
        filtered=outcome.filtered,
        relaxation_hint=outcome.relaxation_hint,
        method="weighted",
    )
    if weighted_only or not any(r.score is not None for r in ranked):
        return weighted, compared  # asked for, or nothing to rank on: no simulation can say more
    try:
        simulated = DecideResult(
            ranked=simulate(outcome.shown, rubric, values, directions, inp.seed, deadline=deadline),
            filtered=outcome.filtered,
            relaxation_hint=outcome.relaxation_hint,
            method="monte_carlo",
        )
    except Exception as exc:  # the plain weighted result stands (build rule 9). The type only, never its text
        log.warning("decide: the simulations failed (%s); the plain weighted ranking stands", type(exc).__name__)
        return weighted, compared
    return simulated, compared


def decide(inp: DecideInput, *, weighted_only: bool = False) -> DecideResult:
    """The same input and seed always give the same result (DecideInput.seed draws the simulations)."""
    return decide_with_notes(inp, weighted_only=weighted_only)[0]
