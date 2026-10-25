"""The pre-search HTTP API (US-05): request -> intent -> criteria -> interview -> rubric.

The frontend (US-01's request box, Manu's chat window) calls these routes; docs/api/presearch.md documents them.
Each route runs the same stages as the CLI, through app/presearch.py, so time limits and fallbacks apply.
Dependencies come from small functions so tests can swap in fakes. Error messages are plain sentences: never a
stack trace, a key or prompt text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import presearch as flow
from app.contracts import CriteriaSet, Intent, InterviewState, InterviewStep, InterviewTurn, LLMClient
from app.criteria import CriteriaStore
from app.intake import revise_intent

from .sessions import MAX_UNCLEAR_ATTEMPTS, InMemorySessionStore, PresearchSession, SessionStore, new_session_id

EXPIRED = "This session has expired. Please start a new search."
NOT_READY = "Tell us what kind of product first."
NO_CRITERIA = "We need to work out what to compare first. Please get the criteria, then start the interview."
NO_INTERVIEW = "The interview hasn't started yet. Please start it first."
FINISHED = "The interview is already finished. Go back to a question if you want to change an answer."
NOT_ANSWERED = "That question hasn't been answered yet, so there is nothing to change."
MAX_REPLY_CHARS = 500  # IntakeInput.previous_clarification_reply and InterviewTurn.reply_text limit

router = APIRouter(prefix="/api/presearch", tags=["pre-search"])
_sessions = InMemorySessionStore()


# --- dependencies (tests override these) ----------------------------------------------------------


def get_llm() -> LLMClient:
    return flow.default_llm()


def get_criteria_store() -> CriteriaStore:
    return flow.default_criteria_store()


def get_session_store() -> SessionStore:
    return _sessions


Sessions = Annotated[SessionStore, Depends(get_session_store)]
LLM = Annotated[LLMClient, Depends(get_llm)]
Store = Annotated[CriteriaStore, Depends(get_criteria_store)]


# --- request and response bodies (API models; the stage data inside them are contracts) -------------


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(default=None, description="Leave out for a new search")
    text: str = Field(min_length=1, max_length=1000, description="The request, or the reply to our question")


class IntakeResponse(BaseModel):
    session_id: str
    intent: Intent
    ready_for_research: bool


class IntentEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: dict[str, Any] = Field(description='Intent fields to change, e.g. {"use_case": "gaming"}')


class ReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reply_text: str = Field(min_length=1, max_length=MAX_REPLY_CHARS)


class BackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1, max_length=64)


# --- routes ---------------------------------------------------------------------------------------


@router.post("/intake", response_model=IntakeResponse)
def intake(body: IntakeRequest, sessions: Sessions, llm: LLM) -> IntakeResponse:
    """A new request, or the reply to our clarification question (US-01, US-02)."""
    session = _load(sessions, body.session_id) if body.session_id else None
    if session is not None and session.intent is not None and session.intent.needs_clarification:
        request, attempts = session.request, session.unclear_attempts
        intent = flow.start_request(request, body.text[:MAX_REPLY_CHARS], attempts, llm=llm)
    else:
        request, attempts = body.text, 0  # a new search, or a fresh request in the same session
        intent = flow.start_request(request, llm=llm)
    if intent.needs_clarification:
        attempts = min(attempts + 1, MAX_UNCLEAR_ATTEMPTS)  # the second unclear reply shows example requests
    saved = PresearchSession(
        session_id=session.session_id if session else new_session_id(),
        request=request,
        unclear_attempts=attempts,
        intent=intent,
    )
    sessions.put(saved)
    return IntakeResponse(session_id=saved.session_id, intent=intent, ready_for_research=intent.ready_for_research)


@router.put("/{session_id}/intent", response_model=Intent)
def edit_intent(session_id: str, body: IntentEdit, sessions: Sessions) -> Intent:
    """The user's edits to what we understood (US-02 'shown back to the user, who can edit it')."""
    session = _load(sessions, session_id)
    if session.intent is None:
        raise HTTPException(409, NOT_READY)
    try:
        intent = revise_intent(session.intent, body.changes)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        raise HTTPException(422, plain_validation_message(errors, "That change doesn't work")) from None
    # The interview copies the intent, so any edit restarts it; a new category or use also needs new criteria.
    new_product = (intent.category, intent.use_case) != (session.intent.category, session.intent.use_case)
    reset: dict[str, object] = {"interview": None, "interview_step": None}
    if new_product:
        reset["criteria"] = None
    sessions.put(session.changed(intent=intent, **reset))
    return intent


@router.post("/{session_id}/criteria", response_model=CriteriaSet)
def criteria(session_id: str, sessions: Sessions, llm: LLM, store: Store) -> CriteriaSet:
    """The criteria for the detected category and use (US-03). Starting again clears any interview."""
    session = _load(sessions, session_id)
    if session.intent is None or not session.intent.ready_for_research:
        raise HTTPException(409, NOT_READY)
    result = flow.make_criteria(session.intent, llm=llm, store=store)
    sessions.put(session.changed(criteria=result, interview=None, interview_step=None))
    return result


@router.post("/{session_id}/interview/start", response_model=InterviewStep)
def start_interview(session_id: str, sessions: Sessions) -> InterviewStep:
    """The first question (US-05). Calling it again restarts the interview."""
    session = _load(sessions, session_id)
    if session.intent is None or session.criteria is None:
        raise HTTPException(409, NO_CRITERIA)
    try:
        state = InterviewState(session_id=session.session_id, intent=session.intent, criteria=session.criteria)
    except ValidationError:
        raise HTTPException(409, NO_CRITERIA) from None  # e.g. the intent changed category after the criteria
    return _save_step(sessions, session, flow.interview_turn(InterviewTurn(state=state)))


@router.post("/{session_id}/interview/reply", response_model=InterviewStep)
def reply(session_id: str, body: ReplyRequest, sessions: Sessions) -> InterviewStep:
    """The user's reply to the current question: the next question, a rephrase, or the finished rubric."""
    session = _load(sessions, session_id)
    if session.interview is None:
        raise HTTPException(409, NO_INTERVIEW)
    if session.interview_step is not None and session.interview_step.kind == "finished":
        raise HTTPException(409, FINISHED)
    step = flow.interview_turn(InterviewTurn(state=session.interview, reply_text=body.reply_text))
    return _save_step(sessions, session, step)


@router.post("/{session_id}/interview/back", response_model=InterviewStep)
def go_back(session_id: str, body: BackRequest, sessions: Sessions) -> InterviewStep:
    """Ask an earlier question again; the next reply changes that answer and the later questions update."""
    session = _load(sessions, session_id)
    if session.interview is None:
        raise HTTPException(409, NO_INTERVIEW)
    try:
        step = flow.interview_back(session.interview, body.question_id)
    except ValueError:
        raise HTTPException(409, NOT_ANSWERED) from None
    return _save_step(sessions, session, step)


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
