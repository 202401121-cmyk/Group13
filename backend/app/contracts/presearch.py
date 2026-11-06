"""The pre-search API's own bodies (US-05): what the frontend sends and gets back around the stage data."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from .common import Contract


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
