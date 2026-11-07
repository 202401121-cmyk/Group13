"""Stage 3, interview (US-05, US-08): one question at a time, in fixed order, adapted to earlier answers.

Each turn takes the whole InterviewState and returns a new copy inside the InterviewStep, so nothing is kept
between turns here (the API stores the state). At most one question is "pending" at a time; the user's reply
goes to it. There are no LLM calls: replies are understood by answers.py (US-39 extends it), and the order comes
from plan.py (US-04 replaces it).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from app.contracts import (
    MAX_QUESTIONS,
    MAX_UNCLEAR_PER_QUESTION,
    AskedQuestion,
    InterviewQuestion,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    ReplyClassification,
    ReplyType,
    Rubric,
    StageContext,
    StageName,
)

from .answers import understand_reply
from .plan import InterviewRules, default_order, load_interview_rules, plan_questions
from .questions import question_for
from .rubric_builder import build_rubric, default_rubric

log = logging.getLogger(__name__)

DONE_MESSAGE = "Thanks, that's everything I need."
REPHRASE_MESSAGE = "Please pick one of the buttons, or type a number from 0 to 10."

AskKind = Literal["question", "rephrase"]


class InterviewStage:
    """Asks the planned questions in order, understands button and number replies, and builds the rubric."""

    name = StageName.INTERVIEW
    is_placeholder = False

    def __init__(self, rules: InterviewRules | None = None) -> None:
        self._rules = rules

    @property
    def rules(self) -> InterviewRules:
        return self._rules or load_interview_rules()

    def run(self, inp: InterviewTurn, ctx: StageContext) -> InterviewStep:
        state = inp.state
        pending = _pending(state.history)
        if not _replied(inp) or pending is None:
            return self._next(state)  # first turn, or a reload: (re)ask the current question

        text = inp.reply_text or ""
        reply = understand_reply(text, pending.question, inp.level, inp.exact_weight)
        if reply.reply_type is ReplyType.UNCLEAR:
            unclear = pending.unclear_count + 1
            if unclear < MAX_UNCLEAR_PER_QUESTION:
                again = _rebuild(pending, unclear_count=unclear)
                return _ask("rephrase", REPHRASE_MESSAGE, again.question, _with(state, again))
            # Two unclear replies: move on rather than ask a third time (US-39 sets the same limit).
            return self._next(_with(state, _give_up(pending, text, reply)))

        if reply.reply_type is ReplyType.SKIP:
            done = _rebuild(pending, status="skipped", reply_text=inp.reply_text, classification=reply, unclear_count=0)
        else:
            if pending.classification is not None:
                # The question was asked again after the user went back: this answer corrects the old one.
                reply = ReplyClassification(
                    reply_type=ReplyType.CORRECTION,
                    weight=reply.weight,
                    corrects_question_id=pending.question.id,
                    classified_by="fallback",
                )
            done = _rebuild(
                pending, status="answered", reply_text=inp.reply_text, classification=reply, unclear_count=0
            )
        return self._next(_with(state, done))

    def go_back(self, state: InterviewState, question_id: str) -> InterviewStep:
        """Ask an earlier question again. The next reply corrects it, and the plan is worked out again after it.

        Raises ValueError if that question has not been answered or skipped yet.
        """
        target = next((e for e in state.history if e.question.id == question_id), None)
        if target is None or target.status not in ("answered", "skipped"):
            raise ValueError(f"question {question_id!r} has not been answered yet")
        history: list[AskedQuestion] = []
        for entry in state.history:
            if entry is target:
                # Keep the old reply and classification: they mark this question as being corrected.
                history.append(_rebuild(entry, status="pending", unclear_count=0))
            elif entry.status == "pending":
                if entry.classification is not None:
                    history.append(_restore(entry))  # it was itself being corrected: keep its earlier answer
                # else: asked but never answered; drop it, the plan will ask it again later
            else:
                history.append(entry)
        return _ask("question", target.question.text, target.question, _state(state, history))

    def fallback(self, inp: InterviewTurn, ctx: StageContext, error: Exception) -> InterviewStep:
        """The next unasked question in default order, with no topic or budget rules; never raises."""
        try:
            state = inp.state
            pending = _pending(state.history)
            if pending is not None and not _replied(inp):
                return _ask("question", pending.question.text, pending.question, state)
            history = [e if e is not pending else _abandon(e) for e in state.history]
            asked = {e.question.criterion_key for e in history}
            following = next((c for c in default_order(state.criteria) if c.key not in asked), None)
            if following is None or len(history) >= MAX_QUESTIONS:
                settled = _state(state, history)
                return _finished(settled, build_rubric(settled))
            question = question_for(following)
            history.append(AskedQuestion(question=question, status="pending"))
            return _ask("question", question.text, question, _state(state, history))
        except Exception:  # the fallback itself must never crash the search (build rule 9)
            log.exception("interview fallback failed; finishing with default weights")
            return _finished(inp.state, default_rubric(inp.state))

    def _next(self, state: InterviewState) -> InterviewStep:
        pending = _pending(state.history)
        if pending is not None:
            return _ask("question", pending.question.text, pending.question, state)
        plan = plan_questions(state.criteria, state.intent, state.history, self.rules)
        following = next((c for c in state.criteria.criteria if c.key == plan.next_key), None)
        if following is None or len(state.history) >= MAX_QUESTIONS:
            return _finished(state, build_rubric(state, plan))
        question = question_for(following)
        return _ask(
            "question", question.text, question, _with(state, AskedQuestion(question=question, status="pending"))
        )


# --- small helpers: every change makes a new, validated copy (contracts are frozen) ----------------


def _replied(turn: InterviewTurn) -> bool:
    return turn.reply_text is not None or turn.level is not None or turn.exact_weight is not None


def _pending(history: Sequence[AskedQuestion]) -> AskedQuestion | None:
    return next((e for e in history if e.status == "pending"), None)


def _rebuild(entry: AskedQuestion, **changes: object) -> AskedQuestion:
    return AskedQuestion.model_validate({**entry.model_dump(), **changes})


def _restore(entry: AskedQuestion) -> AskedQuestion:
    """Put a question being corrected back to its earlier answer (or skip)."""
    earlier = entry.classification
    status = "answered" if earlier is not None and earlier.weight is not None else "skipped"
    return _rebuild(entry, status=status, unclear_count=0)


def _give_up(entry: AskedQuestion, reply_text: str, unclear: ReplyClassification) -> AskedQuestion:
    """After two unclear replies: keep an earlier answer if this was a correction, else mark it skipped."""
    if entry.classification is not None:
        return _rebuild(_restore(entry), unclear_count=MAX_UNCLEAR_PER_QUESTION)
    return _rebuild(
        entry, status="skipped", reply_text=reply_text, classification=unclear, unclear_count=MAX_UNCLEAR_PER_QUESTION
    )


def _abandon(entry: AskedQuestion) -> AskedQuestion:
    """The fallback could not use the reply to this question: keep an earlier answer, else skip it."""
    return _restore(entry) if entry.classification is not None else _rebuild(entry, status="skipped")


def _with(state: InterviewState, entry: AskedQuestion) -> InterviewState:
    """The state with `entry` replacing the entry for the same question, or added at the end."""
    history = [entry if e.question.id == entry.question.id else e for e in state.history]
    if not any(e.question.id == entry.question.id for e in state.history):
        history.append(entry)
    return _state(state, history)


def _state(state: InterviewState, history: list[AskedQuestion]) -> InterviewState:
    return InterviewState.model_validate({**state.model_dump(), "history": [e.model_dump() for e in history]})


def _ask(kind: AskKind, message: str, question: InterviewQuestion, state: InterviewState) -> InterviewStep:
    return InterviewStep(
        kind=kind,
        message=message,
        question=question,
        state=state,
        questions_asked=len(state.history),
        ordering="fixed_order",
    )


def _finished(state: InterviewState, rubric: Rubric) -> InterviewStep:
    return InterviewStep(
        kind="finished",
        message=DONE_MESSAGE,
        rubric=rubric,
        state=state,
        questions_asked=len(state.history),
        ordering="fixed_order",
    )
