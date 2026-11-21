"""The pre-search HTTP API (US-05, US-09): request -> intent -> criteria -> interview -> rubric, and its editor.

The frontend (US-01's request box, Manu's chat window) calls these routes; docs/api/presearch.md documents them.
Each route runs the same stages as the CLI, through app/presearch.py, so time limits and fallbacks apply.
Each new request is saved once, with its session ID, through the request store (US-01); a database that is off,
down or slow never stops the route (build rule 10).
Dependencies come from small functions so tests can swap in fakes. Error messages are plain sentences: never a
stack trace, a key or prompt text.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from app import db
from app import presearch as flow
from app.auth import OptionalUser, SignedInUser
from app.contracts import (
    MAX_UNCLEAR_ATTEMPTS,
    CriteriaSet,
    IntakeResponse,
    Intent,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    LLMClient,
    PresearchSession,
    ReplyRequest,
    RubricEditRequest,
    RubricEditResponse,
)
from app.criteria import CriteriaStore
from app.intake import EDIT_REFUSED, revise_intent
from app.interview import place_reply
from app.interview.plan import load_interview_rules, plan_questions
from app.interview.remembered import dropped_leave_outs, forget, with_remembered, with_switches, without_remembered
from app.interview.rubric_builder import UnknownEdit, apply_edits, build_rubric, carry_edits, rubric_note, with_edits_of
from app.memory import MemoryService, default_memory

from .sessions import InMemorySessionStore, SessionStore, new_session_id

EXPIRED = "This session has expired. Please start a new search."
NOT_READY = "Tell us what kind of product first."
NO_CRITERIA = "We need to work out what to compare first. Please get the criteria, then start the interview."
NO_INTERVIEW = "The interview hasn't started yet. Please start it first."
FINISHED = "The interview is already finished. Go back to a question if you want to change an answer."
NOT_ANSWERED = "That question hasn't been answered yet, so there is nothing to change."
NOT_CURRENT = "That isn't the question we're on. To change an earlier answer, go back to that question first."
NOT_REMEMBERED = "That isn't one of the remembered preferences in this interview."
MAX_OWNERS = 10_000  # interviews whose signed-in starter is held in memory, the oldest dropped first
MAX_REPLY_CHARS = 500  # IntakeInput.previous_clarification_reply and InterviewTurn.reply_text limit
# A plain sentence, when the edits left every weight at 0 (US-09). The rubric route answers it in
# RubricEditResponse.note (S4-17) and a reload in PresearchSession.note (1.10.0); the header stays one more sprint
# (S5-0), on both routes.
RUBRIC_NOTE_HEADER = "X-Rubric-Note"

router = APIRouter(prefix="/api/presearch", tags=["pre-search"])
_sessions = InMemorySessionStore()


# --- dependencies (tests override these) ----------------------------------------------------------


def get_llm() -> LLMClient:
    return flow.default_llm()


def get_criteria_store() -> CriteriaStore:
    return flow.default_criteria_store()


def get_session_store() -> SessionStore:
    return _sessions


def get_request_store() -> db.RequestStore:
    return db.default_request_store()


def get_memory() -> MemoryService:
    return default_memory()


Sessions = Annotated[SessionStore, Depends(get_session_store)]
Requests = Annotated[db.RequestStore, Depends(get_request_store)]
LLM = Annotated[LLMClient, Depends(get_llm)]
Store = Annotated[CriteriaStore, Depends(get_criteria_store)]
Memory = Annotated[MemoryService, Depends(get_memory)]


class _Owners:
    """Per session, for memory (US-06, D-36), held in memory like the sessions and never shown or logged:
    - which signed-in user started its interview. A finished rubric is remembered only for the user who started the
      interview and finished it: a guest who signs in half way never has the guest's answers saved to the account;
    - the remembered preferences forgotten or removed in it, so a restart of the interview never brings them back."""

    def __init__(self, limit: int = MAX_OWNERS) -> None:
        self._limit = limit
        self._owners: OrderedDict[str, tuple[UUID | None, frozenset[UUID]]] = OrderedDict()
        self._lock = threading.Lock()

    def started(self, session_id: str, user: SignedInUser | None) -> None:
        """A (re)start: the owner is who started it now. What was forgotten stays forgotten for the same owner."""
        owner = user.id if user is not None else None
        with self._lock:
            held, forgotten = self._owners.pop(session_id, (None, frozenset()))
            self._owners[session_id] = (owner, forgotten if held == owner and owner is not None else frozenset())
            while len(self._owners) > self._limit:
                self._owners.popitem(last=False)

    def owner(self, session_id: str) -> UUID | None:
        with self._lock:
            return self._owners.get(session_id, (None, frozenset()))[0]

    def forgotten(self, session_id: str) -> frozenset[UUID]:
        with self._lock:
            return self._owners.get(session_id, (None, frozenset()))[1]

    def forget(self, session_id: str, ids: Sequence[UUID]) -> None:
        with self._lock:
            if session_id in self._owners and ids:
                owner, forgotten = self._owners[session_id]
                self._owners[session_id] = (owner, forgotten | frozenset(ids))


_owners = _Owners()


# --- request and response bodies (API models; the stage data inside them are contracts) -------------


def _trim(text: str) -> str:
    return text.strip()


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, description="Leave out for a new search")
    # Trimmed once, here, after the length check: everything below sees the same text, and blank text is simply "".
    text: Annotated[
        str,
        Field(max_length=1000, description="The request, or the reply to our question"),
        AfterValidator(_trim),
    ]


class IntentEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: dict[str, Any] = Field(description='Intent fields to change, e.g. {"use_case": "gaming"}')


class BackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=64)


class ForgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preference_id: UUID = Field(description="RememberedPreference.preference_id of the one to forget")


# --- routes ---------------------------------------------------------------------------------------


@router.post("/intake", response_model=IntakeResponse)
def intake(body: IntakeRequest, sessions: Sessions, llm: LLM, requests: Requests, user: OptionalUser) -> IntakeResponse:
    """A new request, or the reply to our clarification question (US-01, US-02).

    A new request is saved once with its session ID, and a signed-in user's with their id too (US-30); a guest's
    never is (D-36). A clarification reply is the same request, so no new row.
    Blank text is not a request: it gets our question, nothing is saved, and the next text is the request.
    """
    session = _load(sessions, body.session_id) if body.session_id else None
    session_id = session.session_id if session else new_session_id()
    if session is not None and session.intent is not None and session.intent.needs_clarification:
        request, attempts = session.request, session.unclear_attempts
        intent = flow.start_request(request, body.text[:MAX_REPLY_CHARS], attempts, llm=llm)
        request_saved = session.request_saved
    else:
        request = body.text  # a new search, or a fresh request in the same session
        attempts = session.unclear_attempts if session is not None and not session.request else 0
        intent = flow.start_request(request, None, attempts, llm=llm)
        owner = user.id if user is not None else None
        request_saved = requests.save_request(session_id, request, intent, owner) if request else None  # no raise
    if intent.needs_clarification:
        attempts = min(attempts + 1, MAX_UNCLEAR_ATTEMPTS)  # the second unclear reply shows example requests
    saved = PresearchSession(
        session_id=session_id,
        request=request,
        unclear_attempts=attempts,
        intent=intent if request else None,  # no intent yet, so the text after blank text is the request
        request_saved=request_saved,
    )
    if request or session is None or not session.request:  # stray blank text never clears a request
        sessions.put(saved)
    return IntakeResponse(session_id=saved.session_id, intent=intent, ready_for_research=intent.ready_for_research)


@router.put("/{session_id}/intent", response_model=Intent)
def edit_intent(session_id: str, body: IntentEdit, sessions: Sessions) -> Intent:
    """The user's edits to what we understood (US-02 'shown back to the user, who can edit it').

    Under the session's lock, as it reads and writes the shopper's rubric edits (US-09).
    """
    with sessions.lock(session_id):
        session = _load(sessions, session_id)
        if session.intent is None:
            raise HTTPException(409, NOT_READY)
        try:
            intent = revise_intent(session.intent, body.changes)
        except ValidationError as exc:
            errors = exc.errors(include_input=False, include_url=False)
            if errors[0]["type"] == EDIT_REFUSED:
                raise HTTPException(422, errors[0]["msg"]) from None
            raise HTTPException(422, plain_validation_message(errors, "That change doesn't work")) from None
        # The interview copies the intent, so any edit restarts it; a new use also needs new criteria. The shopper's
        # rubric edits are kept on a fresh, unstarted state, so the restarted interview keeps them (US-09). With no
        # criteria yet (a new use, waiting for POST /criteria), the edits ride on the state that already holds them.
        criteria = session.criteria if intent.use_case == session.intent.use_case else None
        held = session.criteria or (session.interview.criteria if session.interview is not None else None)
        kept = carry_edits(session.interview, intent, held) if held is not None else None
        sessions.put(session.changed(intent=intent, criteria=criteria, interview=kept, interview_step=None))
        return intent


@router.post("/{session_id}/criteria", response_model=CriteriaSet)
def criteria(
    session_id: str, sessions: Sessions, llm: LLM, store: Store, memory: Memory, user: OptionalUser
) -> CriteriaSet:
    """The criteria for the detected category and use (US-03). Starting again clears any interview.

    For a signed-in shopper, memory's model starts loading here, so it is ready when the interview starts (US-06)."""
    session = _load(sessions, session_id)
    if session.intent is None or not session.intent.ready_for_research:
        raise HTTPException(409, NOT_READY)
    if user is not None:
        memory.warm()
    result = flow.make_criteria(session.intent, llm=llm, store=store)  # outside the lock: it may wait on the LLM
    with sessions.lock(session_id):
        latest = _load(sessions, session_id)  # an edit made while the criteria were worked out is kept (US-09)
        intent = latest.intent if latest.intent is not None else session.intent
        kept = carry_edits(latest.interview, intent, result)
        sessions.put(latest.changed(criteria=result, interview=kept, interview_step=None))
    return result


@router.post("/{session_id}/interview/start", response_model=InterviewStep)
def start_interview(
    session_id: str, sessions: Sessions, memory: Memory, user: OptionalUser, background: BackgroundTasks
) -> InterviewStep:
    """The first question (US-05). Calling it again restarts the interview.

    A signed-in shopper's interview starts with what is remembered (US-06): up to 5 past preferences, each marked
    "remembered" in InterviewState.remembered. A guest's never does (D-36). Memory that fails or is slow leaves the
    interview exactly as it is without it."""
    with sessions.lock(session_id):
        session = _load(sessions, session_id)
        if session.intent is None or session.criteria is None:
            raise HTTPException(409, NO_CRITERIA)
        try:
            state = InterviewState(session_id=session.session_id, intent=session.intent, criteria=session.criteria)
        except ValidationError:
            raise HTTPException(409, NO_CRITERIA) from None  # a safety net: intent and criteria must share a category
        # The shopper's rubric edits survive a restart (US-09). What memory added last time is taken out first and
        # recalled afresh below, so it keeps its "remembered" mark, and a switch on a remembered rule is kept.
        earlier = without_remembered(session.interview) if session.interview is not None else None
        state = with_edits_of(earlier, state)
        _owners.started(session.session_id, user)
        if user is not None:
            forgotten = _owners.forgotten(session.session_id)
            recalled = [i for i in memory.recall(user.id, state) if i.preference.preference_id not in forgotten]
            state = with_remembered(state, recalled)
            if session.interview is not None:
                state = with_switches(state, session.interview.manual_rule_switches)
        step = _save_step(sessions, session, flow.interview_turn(InterviewTurn(state=state)))
        _remember_if_final(background, memory, user, step)
        return step


@router.post("/{session_id}/interview/reply", response_model=InterviewStep)
def reply(
    session_id: str,
    body: ReplyRequest,
    sessions: Sessions,
    memory: Memory,
    user: OptionalUser,
    background: BackgroundTasks,
) -> InterviewStep:
    """The user's reply to the current question: the next question, a rephrase, or the finished rubric.

    With question_id a repeated reply is safe (docs/api/presearch.md). The check and the change happen under one
    lock, so two identical replies at the same moment apply once.
    """
    with sessions.lock(session_id):
        session = _load(sessions, session_id)
        if session.interview is None or session.interview_step is None:
            raise HTTPException(409, NO_INTERVIEW)
        if body.question_id is not None:
            place = place_reply(session.interview, body.question_id, body.reply_text, body.level, body.exact_weight)
            if place == "conflict":
                raise HTTPException(409, NOT_CURRENT)
            if place == "repeat":
                return session.interview_step  # before the finished check: the last reply can be retried too
        if session.interview_step.kind == "finished":
            raise HTTPException(409, FINISHED)
        step = flow.interview_turn(
            InterviewTurn(
                state=session.interview, reply_text=body.reply_text, level=body.level, exact_weight=body.exact_weight
            )
        )
        _remember_if_final(background, memory, user, step)
        return _save_step(sessions, session, step)


@router.post("/{session_id}/interview/back", response_model=InterviewStep)
def go_back(session_id: str, body: BackRequest, sessions: Sessions) -> InterviewStep:
    """Ask an earlier question again; the next reply changes that answer and the later questions update. A remembered
    answer (US-06) can be asked again the same way: one click to change it."""
    with sessions.lock(session_id):
        session = _load(sessions, session_id)
        if session.interview is None:
            raise HTTPException(409, NO_INTERVIEW)
        try:
            step = flow.interview_back(session.interview, body.question_id)
        except ValueError:
            raise HTTPException(409, NOT_ANSWERED) from None
        return _save_step(sessions, session, step)


@router.post("/{session_id}/interview/forget", response_model=InterviewStep)
def forget_remembered(
    session_id: str,
    body: ForgetRequest,
    sessions: Sessions,
    memory: Memory,
    user: OptionalUser,
    background: BackgroundTasks,
) -> InterviewStep:
    """Forget one remembered preference in this interview (US-06): a weight is then asked like any other, a must-have
    or a leave-out leaves the rubric. The memory itself is kept; the memory screen deletes it (US-11).

    Answers the interview's next step: the current question, a new one, or the finished rubric. 404 if this interview
    holds no remembered preference with that id."""
    with sessions.lock(session_id):
        session = _load(sessions, session_id)
        if session.interview is None or session.interview_step is None:
            raise HTTPException(409, NO_INTERVIEW)
        try:
            state = forget(session.interview, body.preference_id)
        except KeyError:
            raise HTTPException(404, NOT_REMEMBERED) from None
        _owners.forget(session.session_id, [body.preference_id])  # a restart never brings it back
        step = _save_step(sessions, session, flow.interview_turn(InterviewTurn(state=state)))
        _remember_if_final(background, memory, user, step)
        return step


@router.post("/{session_id}/rubric", response_model=RubricEditResponse)
def edit_rubric(
    session_id: str,
    body: RubricEditRequest,
    sessions: Sessions,
    response: Response,
    memory: Memory,
    user: OptionalUser,
    background: BackgroundTasks,
) -> RubricEditResponse:
    """The shopper's rubric edits (US-09): switch a must-have to a preference or back, add or remove an exclusion, or
    set a weight from 0 to 10. They are stored on the session and applied last, so any later rebuild keeps them.

    Answers the rebuilt rubric. An unknown rule id or criterion key answers 422 and names it, and nothing is stored.
    When the edits leave every weight at 0, the defaults are used and `note` says so, as does the X-Rubric-Note header
    (kept one more sprint, S4-17, S5-0). A reload carries it in PresearchSession.note.
    """
    with sessions.lock(session_id):
        session = _load(sessions, session_id)
        if session.interview is None:
            raise HTTPException(409, NO_INTERVIEW)
        try:
            state = apply_edits(session.interview, body.edits)
        except UnknownEdit as exc:
            raise HTTPException(422, str(exc)) from None
        except ValidationError as exc:
            errors = exc.errors(include_input=False, include_url=False)
            raise HTTPException(422, plain_validation_message(errors, "That change doesn't work")) from None
        state, removed = dropped_leave_outs(state)  # a remembered leave-out the shopper removed: not remembered now
        _owners.forget(session.session_id, removed)
        plan = plan_questions(state.criteria, state.intent, state.history, load_interview_rules())
        rubric = build_rubric(state, plan)
        step = session.interview_step
        if step is not None:
            changed = {"state": state.model_dump()}
            if step.rubric is not None:
                changed["rubric"] = rubric.model_dump()
            step = step.model_validate({**step.model_dump(), **changed})
        sessions.put(session.changed(interview=state, interview_step=step))
        if step is not None:
            _remember_if_final(background, memory, user, step)  # a finished rubric changed: remembered again (US-06)
        note = rubric_note(state, plan)
        if note is not None:
            response.headers[RUBRIC_NOTE_HEADER] = note
        return RubricEditResponse(rubric=rubric, note=note)


@router.get("/{session_id}", response_model=PresearchSession)
def get_session(session_id: str, sessions: Sessions, response: Response) -> PresearchSession:
    """Everything about the session, so a page reload can carry on where it left off.

    While the shopper's edits leave every weight at 0, `note` carries the rubric route's note, so a reload shows it too
    (1.10.0). It is worked out from the edits each time, never stored, so it can't go stale. The X-Rubric-Note header
    carries it as well, for one more sprint (S4-17, S5-0).
    """
    session = _load(sessions, session_id)
    note = None
    if session.interview is not None:
        plan = plan_questions(
            session.interview.criteria, session.interview.intent, session.interview.history, load_interview_rules()
        )
        note = rubric_note(session.interview, plan)
        if note is not None:
            response.headers[RUBRIC_NOTE_HEADER] = note
    return session if session.note == note else session.changed(note=note)


# --- helpers ----------------------------------------------------------------------------------------


def _load(sessions: SessionStore, session_id: str) -> PresearchSession:
    session = sessions.get(session_id)
    if session is None:
        raise HTTPException(404, EXPIRED)
    return session


def _save_step(sessions: SessionStore, session: PresearchSession, step: InterviewStep) -> InterviewStep:
    sessions.put(session.changed(interview=step.state, interview_step=step))
    return step


def _remember_if_final(
    background: BackgroundTasks, memory: MemoryService, user: SignedInUser | None, step: InterviewStep
) -> None:
    """When the rubric is final, save its lasting preferences after the reply has gone (US-06): only for the signed-in
    user who started this interview, never a guest's (D-36)."""
    if step.kind != "finished" or step.rubric is None or user is None:
        return
    if _owners.owner(step.state.session_id) != user.id:
        return
    background.add_task(memory.remember, user.id, step.state, step.rubric)


def plain_validation_message(errors: Sequence[Mapping[str, Any]], lead: str) -> str:
    """One plain sentence naming the field. Never the input value, which may be the user's own words."""
    if not errors:
        return f"{lead}."
    first = errors[0]
    field = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = str(first.get("msg", "it is not a valid value")).removeprefix("Value error, ")
    return f"{lead}: {field + ' - ' if field else ''}{message}."
