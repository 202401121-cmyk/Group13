"""The smart interview (US-04, D-69): ask next about the criterion the app is least sure of that also matters most.

Each turn, in plain code:
- the beliefs come from the answers so far (beliefs.py), worked out again every time;
- the fixed plan (plan.py) still says which criteria may be asked, so the topic groups, the budget thresholds and the
  10-question limit all still apply;
- among those, the largest mean x sd is asked next, ties in the fixed order, and the question says why it comes now
  (why.py): the criterion matters and the shopper's view isn't known yet;
- the interview stops when the 3 criteria with the highest means each have an sd under `settled_sd`, or after 10
  questions. Criteria the interview can't ask (skipped, settled by a topic rule, or premium under a low budget)
  never hold up the stop.

If any of that raises, the turn goes on in the fixed order: one log line, and the step says "fixed_order" with no
beliefs (criterion 5). The next turn tries the smart order again. The fixed InterviewStage is also the runner's
fallback if the whole turn fails.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from app.contracts import (
    MAX_QUESTIONS,
    AskedQuestion,
    InterviewQuestion,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    LLMClient,
    StageContext,
    WeightBelief,
)

from .beliefs import SETTLED_TOP, BeliefSettings, beliefs_for
from .plan import InterviewRules, asked_count, default_order, plan_questions
from .questions import question_for
from .rubric_builder import build_rubric
from .stage import InterviewStage, Ordering, _ask, _finished, _pending, _with
from .why import why_now

log = logging.getLogger(__name__)


def next_key(beliefs: Mapping[str, WeightBelief], candidates: Sequence[str], order: Sequence[str]) -> str | None:
    """The candidate with the largest mean x sd; a tie goes to the one first in the fixed order."""
    if not candidates:
        return None
    return min(candidates, key=lambda key: (-(beliefs[key].mean * beliefs[key].sd), order.index(key)))


def settled(
    beliefs: Mapping[str, WeightBelief], in_play: Sequence[str], order: Sequence[str], settings: BeliefSettings
) -> bool:
    """Whether the criteria with the highest means (ties in the fixed order) are each sure enough. Only the criteria
    still in play count; with none left there is nothing to ask."""
    top = sorted(in_play, key=lambda key: (-beliefs[key].mean, order.index(key)))[:SETTLED_TOP]
    return all(beliefs[key].sd < settings.settled_sd for key in top)


class AdaptiveInterviewStage(InterviewStage):
    """The interview in the order that teaches the most (US-04). Same turns, same replies, same rubric as the fixed
    order; only which question comes next, and when it stops, differ."""

    ordering: Ordering = "information_gain"

    def __init__(
        self,
        rules: InterviewRules | None = None,
        llm: LLMClient | None = None,
        settings: BeliefSettings | None = None,
    ) -> None:
        super().__init__(rules, llm)
        self.settings = settings or BeliefSettings()

    def run(self, inp: InterviewTurn, ctx: StageContext) -> InterviewStep:
        return self._with_beliefs(super().run(inp, ctx))

    def go_back(self, state: InterviewState, question_id: str) -> InterviewStep:
        return self._with_beliefs(super().go_back(state, question_id))

    def fallback(self, inp: InterviewTurn, ctx: StageContext, error: Exception) -> InterviewStep:
        """The fixed order's fallback, with no beliefs: they would no longer match the order used."""
        step = super().fallback(inp, ctx, error)
        try:
            return _stamped(step, {}, "fixed_order")
        except Exception:  # never raise from the fallback (build rule 9); the step is valid as it is
            return step

    def _next(self, state: InterviewState) -> InterviewStep:
        try:
            return self._smart_next(state)
        except Exception as exc:
            log.warning("the smart interview could not choose (%s); this turn uses the fixed order", type(exc).__name__)
            return self._fixed_next(state, "fixed_order")

    def _smart_next(self, state: InterviewState) -> InterviewStep:
        pending = _pending(state.history)
        if pending is not None:
            return _ask("question", pending.question.text, pending.question, state, self.ordering)
        plan = plan_questions(state.criteria, state.intent, state.history, self.rules)
        beliefs = beliefs_for(state, self.settings)
        order = [c.key for c in default_order(state.criteria)]
        skipped = {e.question.criterion_key for e in state.history if e.status == "skipped"}
        unaskable = skipped | set(plan.inferred) | plan.premium_skipped
        in_play = [key for key in order if key not in unaskable]
        following = None if settled(beliefs, in_play, order, self.settings) else next_key(beliefs, plan.to_ask, order)
        if following is None or asked_count(state.history) >= MAX_QUESTIONS:
            return _finished(state, build_rubric(state, plan), self.ordering)
        criterion = next(c for c in state.criteria.criteria if c.key == following)
        # The question says why it comes now (S4-5); a fixed-order question keeps the definition as its reason.
        why = why_now(criterion, beliefs[following], self.settings)
        question = InterviewQuestion.model_validate({**question_for(criterion).model_dump(), "why_asking": why})
        asked = _with(state, AskedQuestion(question=question, status="pending"))
        return _ask("question", question.text, question, asked, self.ordering)

    def _with_beliefs(self, step: InterviewStep) -> InterviewStep:
        """The step with the beliefs its answers give. A turn that fell back to the fixed order carries none."""
        if step.ordering != self.ordering:
            return _stamped(step, {}, "fixed_order")
        try:
            beliefs = beliefs_for(step.state, self.settings)
        except Exception as exc:
            log.warning("the smart interview could not work out its beliefs (%s); the fixed order", type(exc).__name__)
            return _stamped(step, {}, "fixed_order")
        return _stamped(step, beliefs, self.ordering)


def _stamped(step: InterviewStep, beliefs: Mapping[str, WeightBelief], ordering: Ordering) -> InterviewStep:
    state = {**step.state.model_dump(), "beliefs": {key: b.model_dump() for key, b in beliefs.items()}}
    return InterviewStep.model_validate({**step.model_dump(), "state": state, "ordering": ordering})
