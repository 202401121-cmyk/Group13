"""Contract tests for the pre-search API's own bodies (US-01, US-05). Every rule here has a test."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.contracts as C

SCHEMAS = Path(__file__).resolve().parents[3] / "shared" / "contracts" / "schema"


def exported(name: str) -> dict[str, object]:
    schema: dict[str, object] = json.loads((SCHEMAS / f"{name}.json").read_text(encoding="utf-8"))
    return schema


# --- ReplyRequest ------------------------------------------------------------------------------------


def test_a_reply_needs_only_its_text() -> None:
    assert C.ReplyRequest(reply_text="High").question_id is None
    assert C.ReplyRequest(reply_text="High", question_id="q_battery_life").question_id == "q_battery_life"


def test_the_exported_schema_says_question_id_is_optional() -> None:
    schema = exported("ReplyRequest")
    assert schema["required"] == ["reply_text"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"reply_text": ""},
        {"reply_text": "x" * 501},
        {"question_id": "q_x"},
        {"reply_text": "High", "question_id": ""},
        {"reply_text": "High", "question_id": "q" * 65},
        {"reply_text": "High", "extra": True},
    ],
)
def test_a_bad_reply_is_refused(body: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        C.ReplyRequest.model_validate(body)


def test_a_reply_of_only_spaces_passes_so_the_interview_can_ask_again() -> None:
    assert C.ReplyRequest(reply_text="   ").reply_text == "   "


def test_a_reply_is_frozen() -> None:
    with pytest.raises(ValidationError):
        C.ReplyRequest(reply_text="High").reply_text = "Low"  # type: ignore[misc]
