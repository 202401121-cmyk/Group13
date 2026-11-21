"""Remembered preferences in the interview (US-06, DECIDED 4).

- A remembered weight enters as an answer: a history entry marked "remembered", with the weight as its answer. The
  belief moves as for an answer of that level (beliefs.py), the planner doesn't ask it (plan.py), the rubric marks it
  "remembered" (rubric_builder.py), and it never counts as a question asked. One click changes it: going back to it asks
  it again, and the reply is an answer like any other; going away again leaves it remembered. The rubric editor can also
  set its weight.
- A remembered must-have joins the request's rules, so the shopper switches it like their own (US-09); a remembered
  leave-out joins the added leave-outs, so the shopper removes it like their own.
- Forgetting one takes it out of this interview: a weight is then asked, a rule or leave-out leaves the rubric.
  (Deleting it from memory is the memory screen's, US-11.)
Nothing remembered leaves the state exactly as it was. Anything that would break the state's rules is left out, never
guessed at.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Protocol

from app.contracts import (
    AskedQuestion,
    Criterion,
    Exclusion,
    HardRule,
    InterviewQuestion,
    InterviewState,
    RememberedPreference,
    ReplyClassification,
    ReplyType,
)
from app.memory.text import exclusion_text, rule_text

from .questions import question_for

log = logging.getLogger(__name__)

REMEMBERED_WHY = "You told us this in an earlier search. Change it if it is different now."
MAX_REMEMBERED = 5  # InterviewState.remembered limit
MAX_RULES = 20  # Intent.hard_rules limit


class Recalled(Protocol):
    """One remembered preference, as memory recalls it (app.memory.RememberedItem)."""

    @property
    def preference(self) -> RememberedPreference: ...

    @property
    def rule(self) -> HardRule | None: ...

    @property
    def exclusion(self) -> Exclusion | None: ...


def remembered_entry(criterion: Criterion, weight: float) -> AskedQuestion:
    """The history entry for a remembered weight: its question, marked remembered, with the weight as the answer."""
    question = InterviewQuestion.model_validate({**question_for(criterion).model_dump(), "why_asking": REMEMBERED_WHY})
    return AskedQuestion(
        question=question,
        status="remembered",
        classification=ReplyClassification(reply_type=ReplyType.ANSWER, weight=weight, classified_by="fallback"),
    )


def is_remembered_answer(entry: AskedQuestion) -> bool:
    """A remembered weight, or one being changed whose change hasn't been given yet: back to "remembered" if left."""
    return (
        entry.question.why_asking == REMEMBERED_WHY
        and entry.reply_text is None
        and entry.classification is not None
        and entry.classification.reply_type is ReplyType.ANSWER
        and entry.classification.weight is not None
    )


def with_remembered(state: InterviewState, items: Sequence[Recalled]) -> InterviewState:
    """The state with the remembered preferences added, the best first, at most 5. Never raises: one that would break
    the state's rules is left out alone, with a log line, and the others are still added."""
    for item in items:
        if len(state.remembered) >= MAX_REMEMBERED:
            break
        try:
            state = _with_one(state, item)
        except ValueError as exc:  # this one would break the state's rules (say, its 30 leave-outs): left out alone
            log.warning("memory: a remembered %s was not used (%s)", item.preference.kind, type(exc).__name__)
    return state


def _key(exclusion: Exclusion) -> tuple[str, str]:
    return exclusion.kind, exclusion.value.casefold()


def leave_out_words(exclusion: Exclusion) -> str:
    return exclusion_text(exclusion.value, exclusion.kind)


def _with_one(state: InterviewState, item: Recalled) -> InterviewState:
    """The state with one more remembered preference, or the same state when it is already there in any form: a weight
    the interview has, a rule with the same id or the same words, a leave-out with the same words."""
    found = item.preference
    if found.preference_id in {r.preference_id for r in state.remembered}:
        return state
    changes: dict[str, object] = {}
    if found.kind == "weight":
        criterion = next((c for c in state.criteria.criteria if c.key == found.criterion_key), None)
        taken = {e.question.criterion_key for e in state.history} | set(state.manual_weights)
        if criterion is None or criterion.key in taken or found.weight is None:
            return state
        changes["history"] = [
            *(e.model_dump() for e in state.history),
            remembered_entry(criterion, found.weight).model_dump(),
        ]
    elif found.kind == "hard_rule":
        rules = state.intent.hard_rules
        if item.rule is None or len(rules) >= MAX_RULES:
            return state
        if item.rule.id in {r.id for r in rules} or found.text in {rule_text(r.source_text) for r in rules}:
            return state  # the shopper's own rule already says it: never twice, so forgetting can't take theirs
        changes["intent"] = {
            **state.intent.model_dump(),
            "hard_rules": [*(r.model_dump() for r in rules), item.rule.model_dump()],
        }
    else:
        if item.exclusion is None:
            return state
        everyone = [*state.intent.exclusions, *state.manual_exclusions_added, *state.manual_exclusions_removed]
        if _key(item.exclusion) in {_key(e) for e in everyone} or found.text in {leave_out_words(e) for e in everyone}:
            return state  # there already, or removed by the shopper in this interview: never brought back
        added = Exclusion(kind=item.exclusion.kind, value=item.exclusion.value, always=True)
        changes["manual_exclusions_added"] = [
            *(e.model_dump() for e in state.manual_exclusions_added),
            added.model_dump(),
        ]
    changes["remembered"] = [*(r.model_dump() for r in state.remembered), found.model_dump()]
    return InterviewState.model_validate({**state.model_dump(), **changes})


def forget(state: InterviewState, preference_id: uuid.UUID) -> InterviewState:
    """The state without this remembered preference. Raises KeyError if this interview doesn't hold it."""
    found = next((r for r in state.remembered if r.preference_id == preference_id), None)
    if found is None:
        raise KeyError(str(preference_id))
    history = list(state.history)
    rules = list(state.intent.hard_rules)
    switches = dict(state.manual_rule_switches)
    added = list(state.manual_exclusions_added)
    if found.kind == "weight":
        history = [
            e
            for e in history
            if not (
                e.question.criterion_key == found.criterion_key
                and e.status in ("remembered", "pending")
                and is_remembered_answer(e)
            )
        ]
    elif found.kind == "hard_rule":
        # a remembered rule's words are never those of one of the shopper's own (see _with_one)
        gone = {r.id for r in rules if rule_text(r.source_text) == found.text}
        rules = [r for r in rules if r.id not in gone]
        switches = {key: on for key, on in switches.items() if key not in gone}
    else:
        added = [e for e in added if not (e.always and leave_out_words(e) == found.text)]
    return InterviewState.model_validate(
        {
            **state.model_dump(),
            "intent": {**state.intent.model_dump(), "hard_rules": [r.model_dump() for r in rules]},
            "history": [e.model_dump() for e in history],
            "manual_rule_switches": switches,
            "manual_exclusions_added": [e.model_dump() for e in added],
            "remembered": [r.model_dump() for r in state.remembered if r.preference_id != preference_id],
        }
    )


def without_remembered(state: InterviewState) -> InterviewState:
    """The state with everything memory added taken out again: before a restart recalls afresh, so a remembered
    leave-out carried with the shopper's edits keeps its mark instead of passing as theirs."""
    for found in list(state.remembered):
        state = forget(state, found.preference_id)
    return state


def with_switches(state: InterviewState, switches: Mapping[str, bool]) -> InterviewState:
    """The shopper's earlier switch on each rule this state has again (a remembered must-have they switched off stays
    off after a restart). Never raises: a state that can't take them is returned as it was."""
    ids = {r.id for r in state.intent.hard_rules}
    kept = {key: on for key, on in switches.items() if key in ids and key not in state.manual_rule_switches}
    if not kept:
        return state
    try:
        return InterviewState.model_validate(
            {**state.model_dump(), "manual_rule_switches": {**state.manual_rule_switches, **kept}}
        )
    except ValueError:
        return state


def dropped_leave_outs(state: InterviewState) -> tuple[InterviewState, list[uuid.UUID]]:
    """After the shopper's rubric edits: each remembered leave-out they removed is no longer remembered here. Its id is
    returned, so the API keeps it out of a restart too."""
    present = {leave_out_words(e) for e in [*state.intent.exclusions, *state.manual_exclusions_added]}
    removed = {leave_out_words(e) for e in state.manual_exclusions_removed}
    gone = [r for r in state.remembered if r.kind == "exclusion" and (r.text not in present or r.text in removed)]
    if not gone:
        return state, []
    ids = {r.preference_id for r in gone}
    kept = [r.model_dump() for r in state.remembered if r.preference_id not in ids]
    return InterviewState.model_validate({**state.model_dump(), "remembered": kept}), [r.preference_id for r in gone]
