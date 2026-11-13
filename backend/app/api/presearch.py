"""The pre-search HTTP API (US-05, US-09): request -> intent -> criteria -> interview -> rubric, and its editor.

The frontend (US-01's request box, Manu's chat window) calls these routes; docs/api/presearch.md documents them.
Each route runs the same stages as the CLI, through app/presearch.py, so time limits and fallbacks apply.
Each new request is saved once, with its session ID, through the request store (US-01); a database that is off,
down or slow never stops the route (build rule 10).
Dependencies come from small functions so tests can swap in fakes. Error messages are plain sentences: never a
stack trace, a key or prompt text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError

from app import db
from app import presearch as flow
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
from app.interview.rubric_builder import UnknownEdit, apply_edits, build_rubric, carry_edits, rubric_note, with_edits_of

from .sessions import InMemorySessionStore, SessionStore, new_session_id

EXPIRED = "This session has expired. Please start a new search."
NOT_READY = "Tell us what kind of product first."
NO_CRITERIA = "We need to work out what to compare first. Please get the criteria, then start the interview."
NO_INTERVIEW = "The interview hasn't started yet. Please start it first."
FINISHED = "The interview is already finished. Go back to a question if you want to change an answer."
NOT_ANSWERED = "That question hasn't been answered yet, so there is nothing to change."
NOT_CURRENT = "That isn't the question we're on. To change an earlier answer, go back to that question first."
MAX_REPLY_CHARS = 500  # IntakeInput.previous_clarification_reply and InterviewTurn.reply_text limit
RUBRIC_NOTE_HEADER = "X-Rubric-Note"  # a plain sentence, when the edits left every weight at 0 (US-09)

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


Sessions = Annotated[SessionStore, Depends(get_session_store)]
Requests = Annotated[db.RequestStore, Depends(get_request_store)]
LLM = Annotated[LLMClient, Depends(get_llm)]
Store = Annotated[CriteriaStore, Depends(get_criteria_store)]


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


# --- routes ---------------------------------------------------------------------------------------


@router.post("/intake", response_model=IntakeResponse)
def intake(body: IntakeRequest, sessions: Sessions, llm: LLM, requests: Requests) -> IntakeResponse:
    """A new request, or the reply to our clarification question (US-01, US-02).

    A new request is saved once with its session ID; a clarification reply is the same request, so no new row.
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
        request_saved = requests.save_request(session_id, request, intent) if request else None  # never raises
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
def criteria(session_id: str, sessions: Sessions, llm: LLM, store: Store) -> CriteriaSet:
    """The criteria for the detected category and use (US-03). Starting again clears any interview."""
    session = _load(sessions, session_id)
    if session.intent is None or not session.intent.ready_for_research:
        raise HTTPException(409, NOT_READY)
    result = flow.make_criteria(session.intent, llm=llm, store=store)  # outside the lock: it may wait on the LLM
    with sessions.lock(session_id):
        latest = _load(sessions, session_id)  # an edit made while the criteria were worked out is kept (US-09)
        intent = latest.intent if latest.intent is not None else session.intent
        kept = carry_edits(latest.interview, intent, result)
        sessions.put(latest.changed(criteria=result, interview=kept, interview_step=None))
    return result


@router.post("/{session_id}/interview/start", response_model=InterviewStep)
def start_interview(session_id: str, sessions: Sessions) -> InterviewStep:
    """The first question (US-05). Calling it again restarts the interview."""
    with sessions.lock(session_id):
        session = _load(sessions, session_id)
        if session.intent is None or session.criteria is None:
            raise HTTPException(409, NO_CRITERIA)
        try:
            state = InterviewState(session_id=session.session_id, intent=session.intent, criteria=session.criteria)
        except ValidationError:
            raise HTTPException(409, NO_CRITERIA) from None  # a safety net: intent and criteria must share a category
        state = with_edits_of(session.interview, state)  # the shopper's rubric edits survive a restart (US-09)
        return _save_step(sessions, session, flow.interview_turn(InterviewTurn(state=state)))


@router.post("/{session_id}/interview/reply", response_model=InterviewStep)
def reply(session_id: str, body: ReplyRequest, sessions: Sessions) -> InterviewStep:
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
        return _save_step(sessions, session, step)


@router.post("/{session_id}/interview/back", response_model=InterviewStep)
def go_back(session_id: str, body: BackRequest, sessions: Sessions) -> InterviewStep:
    """Ask an earlier question again; the next reply changes that answer and the later questions update."""
    with sessions.lock(session_id):
        session = _load(sessions, session_id)
        if session.interview is None:
            raise HTTPException(409, NO_INTERVIEW)
        try:
            step = flow.interview_back(session.interview, body.question_id)
        except ValueError:
            raise HTTPException(409, NOT_ANSWERED) from None
        return _save_step(sessions, session, step)


@router.post("/{session_id}/rubric", response_model=RubricEditResponse)
def edit_rubric(session_id: str, body: RubricEditRequest, sessions: Sessions, response: Response) -> RubricEditResponse:
    """The shopper's rubric edits (US-09): switch a must-have to a preference or back, add or remove an exclusion, or
    set a weight from 0 to 10. They are stored on the session and applied last, so any later rebuild keeps them.

    Answers the rebuilt rubric. An unknown rule id or criterion key answers 422 and names it, and nothing is stored.
    When the edits leave every weight at 0, the defaults are used and the X-Rubric-Note header says so.
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
        plan = plan_questions(state.criteria, state.intent, state.history, load_interview_rules())
        rubric = build_rubric(state, plan)
        step = session.interview_step
        if step is not None:
            changed = {"state": state.model_dump()}
            if step.rubric is not None:
                changed["rubric"] = rubric.model_dump()
            step = step.model_validate({**step.model_dump(), **changed})
        sessions.put(session.changed(interview=state, interview_step=step))
        note = rubric_note(state, plan)
        if note is not None:
            response.headers[RUBRIC_NOTE_HEADER] = note
        return RubricEditResponse(rubric=rubric)


@router.get("/{session_id}", response_model=PresearchSession)
def get_session(session_id: str, sessions: Sessions) -> PresearchSession:
    """Everything about the session, so a page reload can carry on where it left off."""
    return _load(sessions, session_id)


# --- helpers ----------------------------------------------------------------------------------------


def _load(sessions: SessionStore, session_id: str) -> PresearchSession:
    session = sessions.get(session_id)
    if session is None:
        raise HTTPException(404, EXPIRED)
    return session


def _save_step(sessions: SessionStore, session: PresearchSession, step: InterviewStep) -> InterviewStep:
    sessions.put(session.changed(interview=step.state, interview_step=step))
    return step


def plain_validation_message(errors: Sequence[Mapping[str, Any]], lead: str) -> str:
    """One plain sentence naming the field. Never the input value, which may be the user's own words."""
    if not errors:
        return f"{lead}."
    first = errors[0]
    field = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = str(first.get("msg", "it is not a valid value")).removeprefix("Value error, ")
    return f"{lead}: {field + ' - ' if field else ''}{message}."
