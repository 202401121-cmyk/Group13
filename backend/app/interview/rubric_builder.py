"""The rubric at the end of the interview, Sprint 1 version (US-05). US-09 refines it in Sprint 3.

Every criterion gets a weight, so nothing is left out of the score:
- answered: the user's weight, origin "answered", uncertainty 0.2;
- settled by a low answer on the same topic: that weight, origin "answered", uncertainty 0.6 (US-04 may ask it);
- skipped: the default weight, origin "skipped", uncertainty 0.5;
- never asked: the default weight, origin "default", uncertainty 0.5.
Hard rules, exclusions, budget, region and use come straight from the intent: they filter, they never weigh.
"""

from __future__ import annotations

from app.contracts import InterviewState, Rubric, WeightedCriterion, normalize

from .plan import Plan, answered_weights

ANSWERED_UNCERTAINTY = 0.2
INFERRED_UNCERTAINTY = 0.6
DEFAULT_UNCERTAINTY = 0.5


def build_rubric(state: InterviewState, plan: Plan | None = None) -> Rubric:
    """The rubric from the answers so far. `plan` supplies the weights settled by topic rules, if any."""
    answers = answered_weights(state.history)
    inferred = plan.inferred if plan else {}
    skipped = {e.question.criterion_key for e in state.history if e.status == "skipped"}

    rows: list[tuple[str, float, str, float]] = []
    for c in state.criteria.criteria:
        if c.key in answers:
            rows.append((c.key, answers[c.key], "answered", ANSWERED_UNCERTAINTY))
        elif c.key in inferred:
            rows.append((c.key, inferred[c.key], "answered", INFERRED_UNCERTAINTY))
        elif c.key in skipped:
            rows.append((c.key, c.default_weight, "skipped", DEFAULT_UNCERTAINTY))
        else:
            rows.append((c.key, c.default_weight, "default", DEFAULT_UNCERTAINTY))
    return _rubric(state, rows)


def default_rubric(state: InterviewState) -> Rubric:
    """Every criterion at its default weight, ignoring any answers. The last resort when nothing else works."""
    rows = [(c.key, c.default_weight, "default", DEFAULT_UNCERTAINTY) for c in state.criteria.criteria]
    return _rubric(state, rows)


def _rubric(state: InterviewState, rows: list[tuple[str, float, str, float]]) -> Rubric:
    shares = normalize({key: weight for key, weight, _, _ in rows})
    intent = state.intent
    return Rubric.model_validate(
        {
            "category": state.criteria.category,
            "use_case": intent.use_case,
            "region": intent.region,
            "budget": intent.budget,
            "hard_rules": intent.hard_rules,
            "exclusions": intent.exclusions,
            "weights": [
                WeightedCriterion.model_validate(
                    {"key": key, "weight": weight, "normalized": shares[key], "origin": origin, "uncertainty": unsure}
                )
                for key, weight, origin, unsure in rows
            ],
        }
    )
