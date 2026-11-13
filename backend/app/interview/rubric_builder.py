"""The rubric (US-05, US-09, DR-08): two lists, must-haves and preferences, with the shopper's own edits applied last.

Every criterion gets a weight, so nothing is left out of the score:
- answered: the user's weight, origin "answered", uncertainty 0.2;
- settled by a low answer on the same topic: that weight, origin "answered", uncertainty 0.6 (US-04 may ask it);
- skipped: the default weight, origin "skipped", uncertainty 0.5;
- never asked: the category's default weight, origin "default", uncertainty 0.5;
- set by the shopper in the rubric editor: that weight, origin "manual", uncertainty 0. It wins over the interview.
The shares add up to exactly 1: the largest takes the rounding remainder. If the shopper's edits leave every weight at
0, every criterion goes back to its default weight and rubric_note() says so; the edits stay stored, so raising one
weight counts again. Answers of 0 alone keep equal shares, as before.

Hard rules, exclusions, budget, region and use come from the intent: they filter, they never weigh (DR-08). Each hard
rule keeps the shopper's own words (source_text), and identical rules share one id, so the rubric keeps one rule per id.
A rule the shopper switched to a preference stays in the list with active false: only active rules filter (S3-10).
Exclusions are the intent's, plus the ones the shopper added, less the ones they removed.

A rebuild (a new answer, a step back, a changed intent) applies the stored edits again, so they survive it.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.contracts import (
    CriteriaSet,
    Exclusion,
    HardRule,
    Intent,
    InterviewState,
    Rubric,
    RubricEdit,
    WeightedCriterion,
)

from .plan import Plan, answered_weights

ANSWERED_UNCERTAINTY = 0.2
INFERRED_UNCERTAINTY = 0.6
DEFAULT_UNCERTAINTY = 0.5
MANUAL_UNCERTAINTY = 0.0  # the shopper said exactly this
ALL_ZERO_NOTE = "Every weight was set to 0, so the usual weights are used until you raise one."

_Row = tuple[str, float, str, float]  # key, weight, origin, uncertainty


class UnknownEdit(ValueError):
    """An edit names a hard rule or a criterion this rubric doesn't have. The message names it."""


def build_rubric(state: InterviewState, plan: Plan | None = None) -> Rubric:
    """The rubric from the answers so far, with the shopper's edits applied last. `plan` supplies the weights settled
    by topic rules, if any."""
    return _rubric(state, _interview_rows(state, plan))


def default_rubric(state: InterviewState) -> Rubric:
    """Every criterion at its default weight, ignoring any answers, but still with the shopper's edits. The last
    resort when nothing else works."""
    return _rubric(state, _default_rows(state))


def rubric_note(state: InterviewState, plan: Plan | None = None) -> str | None:
    """A plain sentence when the shopper's edits leave every weight at 0, so the defaults were used. Else None."""
    return ALL_ZERO_NOTE if _all_zero(_with_manual(state, _interview_rows(state, plan))) else None


def _interview_rows(state: InterviewState, plan: Plan | None) -> list[_Row]:
    answers = answered_weights(state.history)
    inferred = plan.inferred if plan else {}
    skipped = {e.question.criterion_key for e in state.history if e.status == "skipped"}
    rows: list[_Row] = []
    for c in state.criteria.criteria:
        if c.key in answers:
            rows.append((c.key, answers[c.key], "answered", ANSWERED_UNCERTAINTY))
        elif c.key in inferred:
            rows.append((c.key, inferred[c.key], "answered", INFERRED_UNCERTAINTY))
        elif c.key in skipped:
            rows.append((c.key, c.default_weight, "skipped", DEFAULT_UNCERTAINTY))
        else:
            rows.append((c.key, c.default_weight, "default", DEFAULT_UNCERTAINTY))
    return rows


def _default_rows(state: InterviewState) -> list[_Row]:
    return [(c.key, c.default_weight, "default", DEFAULT_UNCERTAINTY) for c in state.criteria.criteria]


def _with_manual(state: InterviewState, rows: Sequence[_Row]) -> list[_Row]:
    manual = state.manual_weights
    return [
        (key, manual[key], "manual", MANUAL_UNCERTAINTY) if key in manual else (key, weight, origin, unsure)
        for key, weight, origin, unsure in rows
    ]


def _all_zero(rows: Sequence[_Row]) -> bool:
    """Every weight is 0 because of the shopper's edits. Answers of 0 alone keep equal shares, as in Sprint 1."""
    return all(weight == 0 for _, weight, _, _ in rows) and any(origin == "manual" for _, _, origin, _ in rows)


def exact_shares(weights: dict[str, float]) -> dict[str, float]:
    """Shares that add up to exactly 1: the largest share takes whatever rounding left over."""
    total = sum(weights.values())
    count = len(weights)
    shares = {key: (weight / total if total else 1 / count) for key, weight in weights.items()}  # all 0: equal shares
    largest = max(shares, key=lambda key: (shares[key], key))
    shares[largest] = 1.0 - sum(share for key, share in shares.items() if key != largest)
    return shares


def _rubric(state: InterviewState, rows: list[_Row]) -> Rubric:
    edited = _with_manual(state, rows)
    if _all_zero(edited):  # nothing would weigh: the category's defaults, and rubric_note() says so
        edited = _default_rows(state)
    shares = exact_shares({key: weight for key, weight, _, _ in edited})
    intent = state.intent
    return Rubric.model_validate(
        {
            "category": state.criteria.category,
            "use_case": intent.use_case,
            "region": intent.region,
            "budget": intent.budget,
            "hard_rules": [r.model_dump() for r in _hard_rules(state)],
            "exclusions": [e.model_dump() for e in _exclusions(state)],
            "weights": [
                WeightedCriterion.model_validate(
                    {"key": key, "weight": weight, "normalized": shares[key], "origin": origin, "uncertainty": unsure}
                )
                for key, weight, origin, unsure in edited
            ],
        }
    )


def _hard_rules(state: InterviewState) -> list[HardRule]:
    """One rule per id (the first the shopper said), each switched as the shopper chose. The words are kept."""
    seen: set[str] = set()
    rules: list[HardRule] = []
    for rule in state.intent.hard_rules:
        if rule.id in seen:
            continue
        seen.add(rule.id)
        active = state.manual_rule_switches.get(rule.id, rule.active)
        rules.append(
            rule if active == rule.active else HardRule.model_validate({**rule.model_dump(), "active": active})
        )
    return rules


def _key(exclusion: Exclusion) -> tuple[str, str]:
    return exclusion.kind, exclusion.value.casefold()


def _exclusions(state: InterviewState) -> list[Exclusion]:
    """The request's own exclusions as they were, less the removed ones, then each added one not already there."""
    removed = {_key(e) for e in state.manual_exclusions_removed}
    kept = [e for e in state.intent.exclusions if _key(e) not in removed]
    present = {_key(e) for e in kept}
    for exclusion in state.manual_exclusions_added:
        if _key(exclusion) not in present and _key(exclusion) not in removed:
            kept.append(exclusion)
            present.add(_key(exclusion))
    return kept


# --- the shopper's edits ---------------------------------------------------------------------------------------------


def apply_edits(state: InterviewState, edits: Sequence[RubricEdit]) -> InterviewState:
    """The edits, in order, stored on the state. Raises UnknownEdit for a rule id or criterion key it doesn't have."""
    rule_ids = {r.id for r in state.intent.hard_rules}
    keys = {c.key for c in state.criteria.criteria}
    request = {_key(e) for e in state.intent.exclusions}
    weights = dict(state.manual_weights)
    switches = dict(state.manual_rule_switches)
    added = list(state.manual_exclusions_added)
    removed = list(state.manual_exclusions_removed)
    for edit in edits:
        if edit.kind == "switch_rule" and edit.rule_id is not None and edit.active is not None:
            if edit.rule_id not in rule_ids:
                raise UnknownEdit(f"No hard rule has the id {edit.rule_id}.")
            switches[edit.rule_id] = edit.active
        elif edit.kind == "set_weight" and edit.criterion_key is not None and edit.weight is not None:
            if edit.criterion_key not in keys:
                raise UnknownEdit(f"No criterion is called {edit.criterion_key}.")
            weights[edit.criterion_key] = float(edit.weight)
        elif edit.kind == "add_exclusion" and edit.exclusion is not None:
            key = _key(edit.exclusion)
            removed = [e for e in removed if _key(e) != key]
            if key not in request and all(_key(e) != key for e in added):
                added.append(edit.exclusion)
        elif edit.kind == "remove_exclusion" and edit.exclusion is not None:
            key = _key(edit.exclusion)
            added = [e for e in added if _key(e) != key]
            if key in request and all(_key(e) != key for e in removed):
                removed.append(edit.exclusion)
    return InterviewState.model_validate(
        {
            **state.model_dump(),
            "manual_weights": weights,
            "manual_rule_switches": switches,
            "manual_exclusions_added": [e.model_dump() for e in added],
            "manual_exclusions_removed": [e.model_dump() for e in removed],
        }
    )


def has_edits(state: InterviewState | None) -> bool:
    return state is not None and bool(
        state.manual_weights
        or state.manual_rule_switches
        or state.manual_exclusions_added
        or state.manual_exclusions_removed
    )


def carry_edits(old: InterviewState | None, intent: Intent, criteria: CriteriaSet) -> InterviewState | None:
    """A fresh, unstarted state for a changed intent or new criteria, keeping the shopper's edits (US-09).

    None when there are no edits to keep, or when the category changed: the edits belong to the other category. A rule
    switch is kept only if the intent still has that rule; a weight for a criterion no longer listed is kept but unused.
    """
    if old is None or not has_edits(old) or intent.category != criteria.category:
        return None
    if old.criteria.category != criteria.category:
        return None
    rule_ids = {r.id for r in intent.hard_rules}
    try:
        return InterviewState.model_validate(
            {
                "session_id": old.session_id,
                "intent": intent.model_dump(),
                "criteria": criteria.model_dump(),
                "manual_weights": old.manual_weights,
                "manual_rule_switches": {k: v for k, v in old.manual_rule_switches.items() if k in rule_ids},
                "manual_exclusions_added": [e.model_dump() for e in old.manual_exclusions_added],
                "manual_exclusions_removed": [e.model_dump() for e in old.manual_exclusions_removed],
            }
        )
    except ValueError:  # the intent can't start an interview (say, no category yet): nothing to keep the edits on
        return None


def with_edits_of(old: InterviewState | None, fresh: InterviewState) -> InterviewState:
    """A new interview's state, carrying over the edits a previous state held."""
    if old is None or not has_edits(old):
        return fresh
    carried = carry_edits(old, fresh.intent, fresh.criteria)
    if carried is None:
        return fresh
    return InterviewState.model_validate({**carried.model_dump(), "history": [h.model_dump() for h in fresh.history]})
