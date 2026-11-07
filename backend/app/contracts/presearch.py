"""The pre-search API's own bodies (US-05): what the frontend sends and gets back around the stage data."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .common import Contract, SessionId, StrictScore10
from .criteria import CriteriaSet
from .intake import Intent
from .interview import InterviewState, InterviewStep, RoughLevel, check_one_answer_form

MAX_UNCLEAR_ATTEMPTS = 2  # IntakeInput.unclear_attempts limit

RequestSaved = Literal["saved", "off", "failed"]  # app.db's SaveOutcome; contracts never import app.db


class ReplyRequest(Contract):
    """The body of POST /api/presearch/{session_id}/interview/reply.

    question_id says which question the reply is for, so a repeated reply cannot answer the next question. It is
    optional so that a client that does not send it keeps working; docs/api/presearch.md has the rules.
    """

    # This is a request, so a default really can be left out. And it is not stripped: a reply of only spaces must
    # reach the interview, which asks again, rather than be refused here as empty.
    model_config = ConfigDict(json_schema_serialization_defaults_required=False, str_strip_whitespace=False)

    reply_text: str = Field(min_length=1, max_length=500)
    question_id: str | None = Field(
        default=None, min_length=1, max_length=64, description="InterviewQuestion.id of the question being answered"
    )
    level: RoughLevel | None = Field(default=None, description="A rough answer, or use exact_weight (US-08)")
    exact_weight: StrictScore10 | None = Field(default=None, description="An exact 0 to 10 answer, or use level")

    @model_validator(mode="after")
    def _check(self) -> ReplyRequest:
        check_one_answer_form(self.level, self.exact_weight)
        return self


class IntakeResponse(Contract):
    """What POST /api/presearch/intake answers."""

    session_id: SessionId
    intent: Intent
    ready_for_research: bool


class PresearchSession(Contract):
    """Everything the pre-search API knows about one shopper. GET /api/presearch/{session_id} returns it whole."""

    session_id: SessionId
    request: str = Field(max_length=1000, description="The request as first typed, trimmed. Empty until there is one")
    unclear_attempts: int = Field(default=0, ge=0, le=MAX_UNCLEAR_ATTEMPTS)
    intent: Intent | None = None
    criteria: CriteriaSet | None = None
    interview: InterviewState | None = None
    interview_step: InterviewStep | None = Field(default=None, description="The latest step, for a page reload")
    request_saved: RequestSaved | None = Field(
        default=None, description="Whether the request reached the database: saved, off (no database) or failed"
    )

    def changed(self, **changes: object) -> PresearchSession:
        """A validated copy with some fields changed (the model is frozen)."""
        return PresearchSession.model_validate({**self.model_dump(), **changes})
