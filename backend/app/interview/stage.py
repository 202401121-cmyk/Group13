"""Stage 3, interview (US-05, US-08, US-39): one question at a time, in fixed order, adapted to earlier answers.

Each turn takes the whole InterviewState and returns a new copy inside the InterviewStep, so nothing is kept
between turns here (the API stores the state). At most one question is "pending" at a time; the user's reply
goes to it. The order comes from plan.py. This fixed order is the baseline and the fallback of the smart interview
(US-04, adaptive.py), which asks the questions in another order through the same turns.

A reply is read in code first (answers.py): a button, a rough level, an exact weight, a whole number or "skip" never
costs an LLM call. Only other text goes to the classifier (replies.py, US-39), and every rule of the story is here:
- an answer or a correction counts; a question, a redirect or a rephrase never does, and the pending question stays;
- a question is answered (the saved definition first), then the same question is asked again;
- an answer with a question records the answer, then answers the question before the next one;
- a skip leaves the default weight; a correction changes the earlier answer, then the current question is asked again;
- off-topic gets one line back to the question; unclear gets the question once more, in other words, with buttons;
- the second unclear or off-topic reply skips the question and marks it "skipped", so it can be answered later;
- a classifier that fails makes the reply an answer at the default weight, which the shopper can change.
"""

from __future__ import annotations

import logging
import time
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
    LLMClient,
    ReplyClassification,
    ReplyType,
    Rubric,
    StageContext,
    StageName,
)

from .answers import understand_reply
from .plan import InterviewRules, default_order, load_interview_rules, plan_questions
from .questions import question_for
from .replies import answer_question, classify, criterion_of
from .rubric_builder import build_rubric, default_rubric

log = logging.getLogger(__name__)

DONE_MESSAGE = "Thanks, that's everything I need."
MAX_MESSAGE = 600  # InterviewStep.message limit
REDIRECT_MESSAGE = "That's not something I can use here, so let's get back to the question."
SKIPPED_NOTE = "Let's skip that one for now. You can answer it later."
FALLBACK_NOTE = "I couldn't read that, so I used the usual weight for {label}. You can go back and change it."
KEPT_NOTE = "I couldn't read that, so I kept your earlier answer for {label}. You can go back and change it."
CORRECTED_NOTE = "Done: I changed {label} to {weight} out of 10."

AskKind = Literal["question", "rephrase", "redirect", "answer_then_repeat"]
Ordering = Literal["information_gain", "fixed_order"]


def rephrase_message(label: str) -> str:
    """The question once more, in other words, pointing at its buttons: never a guess at what was meant (US-39)."""
    return (
        f"Let me ask another way: should {label} be low, medium or high on your list? "
        "Pick one of the buttons, or type a number from 0 to 10."
    )


class InterviewStage:
    """Asks the planned questions in order, understands every kind of reply, and builds the rubric."""

    name = StageName.INTERVIEW
    is_placeholder = False
    ordering: Ordering = "fixed_order"  # what each step says chose its question (US-04's class says information_gain)

    def __init__(self, rules: InterviewRules | None = None, llm: LLMClient | None = None) -> None:
        self._rules = rules
        self._llm = llm

    @property
    def rules(self) -> InterviewRules:
        return self._rules or load_interview_rules()

    def _client(self) -> LLMClient:
        """The given client, else the shared one: asked for only when a reply needs it, so a turn of buttons, numbers
        and skips (the interview simulator's) never builds or calls one."""
        if self._llm is not None:
            return self._llm
        from app.presearch import default_llm  # here, because app.presearch imports this stage

        return default_llm()

    def run(self, inp: InterviewTurn, ctx: StageContext) -> InterviewStep:
        started = time.monotonic()  # the turn's 5 seconds start here; the LLM calls share them
        state = inp.state
        pending = _pending(state.history)
        if not _replied(inp) or pending is None:
            return self._next(state)  # first turn, or a reload: (re)ask the current question

        text = inp.reply_text or ""
        reply = understand_reply(text, pending.question, inp.level, inp.exact_weight)
        fell_back = False
        if reply.reply_type is ReplyType.UNCLEAR and text.strip():  # words code can't place: ask the classifier
            reply = classify(self._client(), text, state, pending, ctx.search_id, started)
            fell_back = reply.classified_by == "fallback"
        kind = reply.reply_type
        if kind is ReplyType.QUESTION:
            return self._answer_then_repeat(state, pending, reply, ctx, started)
        if kind in (ReplyType.UNCLEAR, ReplyType.OFF_TOPIC):
            return self._not_an_answer(state, pending, text, reply)
        if kind is ReplyType.CORRECTION and reply.corrects_question_id != pending.question.id:
            return self._correct_earlier(state, pending, text, reply)
        if kind is ReplyType.SKIP:
            done = _rebuild(pending, status="skipped", reply_text=inp.reply_text, classification=reply, unclear_count=0)
            return self._next(_with(state, done))

        label = criterion_of(state, pending).label
        note: str | None = None
        if fell_back and _had_answer(pending):
            # Correcting an earlier answer, and the reply couldn't be read: keep the answer it had, never a guess. (A
            # question skipped earlier had none: its reply counts as an answer at the default weight, below.)
            done, note = _rebuild(_restore(pending), unclear_count=0), KEPT_NOTE.format(label=label)
        else:
            if fell_back:
                note = FALLBACK_NOTE.format(label=label)
            recorded = reply
            if pending.classification is not None:
                # The question was asked again after the user went back: this answer corrects the old one.
                recorded = ReplyClassification(
                    reply_type=ReplyType.CORRECTION,
                    weight=reply.weight,
                    corrects_question_id=pending.question.id,
                    classified_by=reply.classified_by,
                )
            done = _rebuild(
                pending, status="answered", reply_text=inp.reply_text, classification=recorded, unclear_count=0
            )
        step = self._next(_with(state, done))
        if kind is ReplyType.ANSWER_WITH_QUESTION and reply.user_question:
            answered = answer_question(
                self._client(),
                reply.user_question,
                criterion_of(state, pending),
                _category(state),
                ctx.search_id,
                started,
            )
            step = _opened(step, answered.text)
        return _opened(step, note) if note else step

    def _answer_then_repeat(
        self,
        state: InterviewState,
        pending: AskedQuestion,
        reply: ReplyClassification,
        ctx: StageContext,
        started: float,
    ) -> InterviewStep:
        """Answer the shopper's question, then ask the same question again. Nothing is recorded or counted."""
        answered = answer_question(
            self._client(),
            reply.user_question or "",
            criterion_of(state, pending),
            _category(state),
            ctx.search_id,
            started,
        )
        message = _joined(answered.text, pending.question.text)
        return _ask("answer_then_repeat", message, pending.question, state, self.ordering, answered.source)

    def _not_an_answer(
        self, state: InterviewState, pending: AskedQuestion, text: str, reply: ReplyClassification
    ) -> InterviewStep:
        """Unclear or off-topic: once more (a rephrase or a redirect); the second time, skip it and mark it so."""
        unclear = pending.unclear_count + 1
        if unclear < MAX_UNCLEAR_PER_QUESTION:
            again = _rebuild(pending, unclear_count=unclear)
            if reply.reply_type is ReplyType.OFF_TOPIC:
                message = _joined(REDIRECT_MESSAGE, pending.question.text)
                return _ask("redirect", message, again.question, _with(state, again), self.ordering)
            label = criterion_of(state, pending).label
            return _ask("rephrase", rephrase_message(label), again.question, _with(state, again), self.ordering)
        # The second one: move on rather than ask a third time. A question being corrected keeps its earlier answer.
        note = KEPT_NOTE.format(label=criterion_of(state, pending).label) if _had_answer(pending) else SKIPPED_NOTE
        return _opened(self._next(_with(state, _give_up(pending, text, reply))), note)

    def _correct_earlier(
        self, state: InterviewState, pending: AskedQuestion, text: str, reply: ReplyClassification
    ) -> InterviewStep:
        """A correction of an earlier question: change that answer, then ask the current question again."""
        earlier = next(e for e in state.history if e.question.id == reply.corrects_question_id)
        changed = _rebuild(earlier, status="answered", reply_text=text, classification=reply, unclear_count=0)
        weight = f"{reply.weight:g}" if reply.weight is not None else "?"
        note = CORRECTED_NOTE.format(label=criterion_of(state, earlier).label, weight=weight)
        message = _joined(note, pending.question.text)
        return _ask("question", message, pending.question, _with(state, changed), self.ordering)

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
        return _ask("question", target.question.text, target.question, _state(state, history), self.ordering)

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
        return self._fixed_next(state, self.ordering)

    def _fixed_next(self, state: InterviewState, ordering: Ordering = "fixed_order") -> InterviewStep:
        """The current question again, or the next one in the fixed order, or the finished rubric."""
        pending = _pending(state.history)
        if pending is not None:
            return _ask("question", pending.question.text, pending.question, state, ordering)
        plan = plan_questions(state.criteria, state.intent, state.history, self.rules)
        following = next((c for c in state.criteria.criteria if c.key == plan.next_key), None)
        if following is None or len(state.history) >= MAX_QUESTIONS:
            return _finished(state, build_rubric(state, plan), ordering)
        question = question_for(following)
        asked = _with(state, AskedQuestion(question=question, status="pending"))
        return _ask("question", question.text, question, asked, ordering)


# --- small helpers: every change makes a new, validated copy (contracts are frozen) ----------------


def _replied(turn: InterviewTurn) -> bool:
    return turn.reply_text is not None or turn.level is not None or turn.exact_weight is not None


def _pending(history: Sequence[AskedQuestion]) -> AskedQuestion | None:
    return next((e for e in history if e.status == "pending"), None)


def _rebuild(entry: AskedQuestion, **changes: object) -> AskedQuestion:
    return AskedQuestion.model_validate({**entry.model_dump(), **changes})


def _had_answer(entry: AskedQuestion) -> bool:
    """Being corrected, and its earlier reply was an answer with a weight (not a skip)."""
    return entry.classification is not None and entry.classification.weight is not None


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


def _ask(
    kind: AskKind,
    message: str,
    question: InterviewQuestion,
    state: InterviewState,
    ordering: Ordering = "fixed_order",
    answer_source: Literal["definition", "llm"] | None = None,
) -> InterviewStep:
    return InterviewStep(
        kind=kind,
        message=message,
        question=question,
        answer_source=answer_source,
        state=state,
        questions_asked=len(state.history),
        ordering=ordering,
    )


def _joined(first: str, then: str) -> str:
    """Two parts of one chat message, or the first alone when both don't fit: the frontend then shows the question as
    its heading, as it does whenever the message doesn't hold the question."""
    both = f"{first} {then}"
    return both if len(both) <= MAX_MESSAGE else first[:MAX_MESSAGE]


def _opened(step: InterviewStep, first: str) -> InterviewStep:
    """The step with a line before its own message."""
    return InterviewStep.model_validate({**step.model_dump(), "message": _joined(first, step.message)})


def _category(state: InterviewState) -> str:
    return state.intent.category_label or state.criteria.category


def _finished(state: InterviewState, rubric: Rubric, ordering: Ordering = "fixed_order") -> InterviewStep:
    return InterviewStep(
        kind="finished",
        message=DONE_MESSAGE,
        rubric=rubric,
        state=state,
        questions_asked=len(state.history),
        ordering=ordering,
    )
