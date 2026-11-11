"""The pre-search API's own bodies (US-05): what the frontend sends and gets back around the stage data."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from .common import Contract, SessionId, Slug, StrictScore10
from .criteria import CriteriaSet
from .intake import Exclusion, Intent
from .interview import InterviewState, InterviewStep, RoughLevel, check_one_answer_form
from .rubric import Rubric

MAX_UNCLEAR_ATTEMPTS = 2  # IntakeInput.unclear_attempts limit
MAX_RUBRIC_EDITS = 20  # edits in one RubricEditRequest
MAX_EXCLUSION_TEXT = 200  # characters in one ExclusionParseRequest

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


RubricEditKind = Literal["switch_rule", "add_exclusion", "remove_exclusion", "set_weight"]

# The fields each kind of edit carries; every other edit field stays empty.
_EDIT_FIELDS: dict[str, frozenset[str]] = {
    "switch_rule": frozenset({"rule_id", "active"}),
    "add_exclusion": frozenset({"exclusion"}),
    "remove_exclusion": frozenset({"exclusion"}),
    "set_weight": frozenset({"criterion_key", "weight"}),
}
_ALL_EDIT_FIELDS: frozenset[str] = frozenset().union(*_EDIT_FIELDS.values())


class RubricEdit(Contract):
    """One change the shopper makes to the rubric (US-09). It carries exactly the fields of its kind."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=False)  # part of a request body

    kind: RubricEditKind
    rule_id: Slug | None = Field(default=None, description="switch_rule: the HardRule.id to switch")
    active: bool | None = Field(
        default=None, description="switch_rule: true keeps it a must-have; false makes it a preference"
    )
    exclusion: Exclusion | None = Field(default=None, description="add_exclusion or remove_exclusion: which one")
    criterion_key: Slug | None = Field(default=None, description="set_weight: the criterion")
    weight: StrictScore10 | None = Field(default=None, description="set_weight: its new 0 to 10 weight")

    @model_validator(mode="after")
    def _check(self) -> RubricEdit:
        present = {name for name in _ALL_EDIT_FIELDS if getattr(self, name) is not None}
        if present != _EDIT_FIELDS[self.kind]:
            needed = ", ".join(sorted(_EDIT_FIELDS[self.kind]))
            raise ValueError(f"a {self.kind} edit carries {needed} and nothing else")
        return self


class RubricEditRequest(Contract):
    """The body of the rubric editor's route (US-09). One edit is a list of one; edits apply in order."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=False)

    edits: list[RubricEdit] = Field(min_length=1, max_length=MAX_RUBRIC_EDITS)


class RubricEditResponse(Contract):
    """What the rubric editor's route answers: the rubric rebuilt with the shopper's edits applied last (US-09)."""

    rubric: Rubric


class ExclusionParseRequest(Contract):
    """The body of the route that reads exclusions from the shopper's own words, such as "no Zenith" (US-09)."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=False)

    text: str = Field(min_length=1, max_length=MAX_EXCLUSION_TEXT, description="What the shopper typed")


class ExclusionParseResponse(Contract):
    """The exclusions read from the text, and the words that could not be read as one (US-09)."""

    exclusions: list[Exclusion] = Field(default_factory=list, max_length=30)
    not_understood: list[Annotated[str, Field(min_length=1, max_length=MAX_EXCLUSION_TEXT)]] = Field(
        default_factory=list, max_length=20, description="Parts of the text that were not understood, as typed"
    )


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
