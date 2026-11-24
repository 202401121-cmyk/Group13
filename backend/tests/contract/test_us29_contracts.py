"""US-29 (S5-11): schema 1.11.0. A citation keeps what its claim said and when, so a saved result reopens with the
same evidence; every new field is optional, so a result saved before 1.11.0 still reads (D-23)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import app.contracts as C
from app.contracts import examples as ex

SHARED = Path(__file__).resolve().parents[3] / "shared" / "contracts"
NEW = {"value", "unit", "score", "polarity", "claim_type", "retrieved_at"}


def test_the_schema_version_is_1_11_0() -> None:
    assert C.SCHEMA_VERSION == "1.11.0"
    assert ex.search_result().schema_version == "1.11.0"
    assert (SHARED / "VERSION").read_text(encoding="utf-8").strip() == "1.11.0"


def test_the_example_results_citations_keep_what_each_claim_said_and_when() -> None:
    claims = {c.id: c for c in ex.claims()}
    for citation in ex.search_result().citations:
        claim = claims[citation.claim_id]
        assert (citation.value, citation.unit, citation.score) == (claim.value, claim.unit, claim.score)
        assert (citation.polarity, citation.claim_type, citation.retrieved_at) == (
            claim.polarity,
            claim.claim_type,
            claim.retrieved_at,
        )


def test_a_citation_from_before_1_11_0_still_reads() -> None:
    old = C.Citation.model_validate({"claim_id": "clm_" + "a" * 24, "source_id": "src_" + "b" * 16, "quote": None})
    assert all(getattr(old, field) is None for field in NEW)


def test_a_citation_keeps_a_value_or_a_score_never_both() -> None:
    base = {"claim_id": "clm_" + "a" * 24, "source_id": "src_" + "b" * 16, "polarity": "neutral"}
    assert C.Citation.model_validate({**base, "value": 1.4, "unit": "kg"}).value == 1.4
    assert C.Citation.model_validate({**base, "score": 8.0}).score == 8.0
    with pytest.raises(ValidationError):
        C.Citation.model_validate({**base, "value": 1.4, "score": 8.0})
    with pytest.raises(ValidationError):
        C.Citation.model_validate({**base, "score": 11.0})
    when = C.Citation.model_validate({**base, "score": 8.0, "retrieved_at": datetime(2026, 11, 25, tzinfo=UTC)})
    assert when.retrieved_at is not None


def test_the_new_fields_are_exported_and_optional_on_input() -> None:
    """The export lists what a result always holds (every field, a default included); what may be left out on input
    is the input schema's to say: a result saved before 1.11.0 omits the new fields."""
    schema = json.loads((SHARED / "schema" / "SearchResult.json").read_text(encoding="utf-8"))
    assert set(schema["$defs"]["Citation"]["properties"]) >= NEW
    accepted = C.Citation.model_json_schema(mode="validation")
    assert not NEW & set(accepted.get("required", []))
