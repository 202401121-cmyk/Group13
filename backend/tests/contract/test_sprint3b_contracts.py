"""Sprint 3 contracts for the live verify and decide stages (S3-10b, US-17, US-18): schema 1.8.0.

- A disputed value keeps the other side: `ResolvedValue.alternatives`, each with its value or score and its claims.
- Verify is told the product category, so a site's trust can move with it (`VerifyInput.category`).
- With no evidence on any criterion nothing is ranked by score: `RankedProduct.score` is None for every product, and
  none is called the best match. Unknown is never shown as 0 (DR-09).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel, ValidationError

import app.contracts as C
from app.contracts import examples as ex

M = TypeVar("M", bound=BaseModel)

SHARED = Path(__file__).resolve().parents[3] / "shared" / "contracts"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "contracts"

SPEC = "clm_" + "a" * 16
OWNER = "clm_" + "b" * 16
OTHER = "clm_" + "c" * 16


def rebuild(model: M, **changes: object) -> M:
    """Copy a model with changes AND re-run validation (model_copy would skip it)."""
    return type(model).model_validate({**model.model_dump(), **changes})


def exported(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SHARED / "schema" / f"{name}.json").read_text(encoding="utf-8"))
    return data


def _disputed(**changes: object) -> C.ResolvedValue:
    fields: dict[str, object] = {
        "product_id": "prd_kestrel_slim_15",
        "criterion_key": "battery_life",
        "state": C.ValueState.DISPUTED,
        "value": "10 hours",
        "confidence": 0.6,
        "supporting_claim_ids": [SPEC],
        "conflicting_claim_ids": [OWNER],
        "alternatives": [{"value": 6.0, "unit": "hours", "claim_ids": [OWNER]}],
    }
    return C.ResolvedValue.model_validate({**fields, **changes})


def _ranked(product_id: str, rank: int, score: float | None, label: str | None = None) -> dict[str, object]:
    return {
        "product_id": product_id,
        "rank": rank,
        "score": score,
        "contributions": [
            {
                "criterion_key": "battery_life",
                "score": None,
                "normalized_weight": 0.0,
                "contribution": 0.0,
                "state": C.ValueState.UNKNOWN,
            }
        ],
        "coverage": 0.0,
        "confidence": "low",
        "label": label,
        "gap_to_leader": 0.0,
    }


# --- the version ---------------------------------------------------------------------------------------------------


def test_the_schema_version_is_1_8_0() -> None:
    assert C.SCHEMA_VERSION == "1.8.0"
    assert ex.search_result().schema_version == "1.8.0"
    assert (SHARED / "VERSION").read_text(encoding="utf-8").strip() == "1.8.0"


# --- the other side of a dispute -----------------------------------------------------------------------------------


def test_a_disputed_value_keeps_its_other_side() -> None:
    value = _disputed()
    [other] = value.alternatives
    assert (other.value, other.unit, other.score, other.claim_ids) == (6.0, "hours", None, [OWNER])


def test_alternatives_default_to_none_so_older_values_still_validate() -> None:
    assert _disputed(alternatives=[]).alternatives == []
    known = C.ResolvedValue(
        product_id="prd_nova_book_14",
        criterion_key="ram_gb",
        state=C.ValueState.KNOWN,
        value=16.0,
        confidence=0.9,
        supporting_claim_ids=[SPEC],
    )
    assert known.alternatives == []


@pytest.mark.parametrize("state", [C.ValueState.KNOWN, C.ValueState.UNKNOWN, C.ValueState.ESTIMATED])
def test_only_a_disputed_value_has_alternatives(state: C.ValueState) -> None:
    fields: dict[str, object] = {"state": state, "conflicting_claim_ids": [OWNER]}
    if state is C.ValueState.UNKNOWN:
        fields |= {"value": None, "supporting_claim_ids": [], "conflicting_claim_ids": [], "confidence": 0.0}
    if state is C.ValueState.ESTIMATED:
        fields |= {"value": None, "score": 6.0, "supporting_claim_ids": []}
    with pytest.raises(ValidationError, match="only a disputed value has alternatives"):
        _disputed(**fields)


def test_an_alternative_needs_a_value_or_a_score() -> None:
    with pytest.raises(ValidationError, match="needs a value or a score"):
        C.AlternativeValue(claim_ids=[OWNER])
    assert C.AlternativeValue(score=4.0, claim_ids=[OWNER]).score == 4.0
    assert C.AlternativeValue(value=False, claim_ids=[OWNER]).value is False


def test_an_alternative_cites_at_least_one_claim() -> None:
    with pytest.raises(ValidationError):
        C.AlternativeValue(value=6.0, claim_ids=[])


def test_an_alternative_cites_only_conflicting_claims() -> None:
    """So a result's citations, which cover every supporting and conflicting claim, cover the alternatives too."""
    with pytest.raises(ValidationError, match="among the conflicting claims"):
        _disputed(alternatives=[{"value": 6.0, "claim_ids": [OTHER]}])


def test_an_alternatives_unit_is_short() -> None:
    with pytest.raises(ValidationError):
        C.AlternativeValue(value=6.0, unit="x" * 17, claim_ids=[OWNER])


# --- verify is told the category -----------------------------------------------------------------------------------


def test_verify_input_takes_an_optional_category() -> None:
    fields: dict[str, object] = {
        "match": ex.match(),
        "claims": ex.claims(),
        "sources": ex.sources(),
        "criterion_keys": ["battery_life"],
    }
    assert C.VerifyInput.model_validate(fields).category is None
    assert C.VerifyInput.model_validate({**fields, "category": "laptop"}).category == "laptop"
    with pytest.raises(ValidationError):
        C.VerifyInput.model_validate({**fields, "category": "Not A Slug"})


# --- nothing ranked by score ---------------------------------------------------------------------------------------


def test_a_none_score_is_allowed_when_no_product_has_one() -> None:
    result = C.DecideResult.model_validate(
        {
            "ranked": [_ranked("prd_aurora_air_13", 1, None), _ranked("prd_nova_book_14", 2, None)],
            "method": "weighted",
        }
    )
    assert [r.score for r in result.ranked] == [None, None]
    assert [r.label for r in result.ranked] == [None, None]


def test_a_none_score_must_be_every_products() -> None:
    with pytest.raises(ValidationError, match="every ranked product has no score, or none does"):
        C.DecideResult.model_validate(
            {
                "ranked": [_ranked("prd_nova_book_14", 1, 70.0), _ranked("prd_vela_pro_14", 2, None)],
                "method": "weighted",
            }
        )


def test_a_product_with_no_score_has_no_label() -> None:
    with pytest.raises(ValidationError, match="with no score, no product is labelled"):
        C.DecideResult.model_validate(
            {"ranked": [_ranked("prd_nova_book_14", 1, None, "best_match")], "method": "weighted"}
        )


@pytest.mark.parametrize("score", [-0.1, 100.1])
def test_a_present_score_stays_between_0_and_100(score: float) -> None:
    with pytest.raises(ValidationError):
        C.RankedProduct.model_validate(_ranked("prd_nova_book_14", 1, score))


def test_scored_products_still_follow_their_score() -> None:
    with pytest.raises(ValidationError, match="ranks follow the score"):
        C.DecideResult.model_validate(
            {
                "ranked": [_ranked("prd_nova_book_14", 1, 60.0), _ranked("prd_vela_pro_14", 2, 70.0)],
                "method": "weighted",
            }
        )


# --- old results and the exported files ----------------------------------------------------------------------------


def test_a_1_7_0_result_still_validates() -> None:
    raw = json.loads((FIXTURES / "search_result_1_7_0.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == "1.7.0"
    assert all("alternatives" not in v for p in raw["products"] for v in p["values"])
    old = C.SearchResult.model_validate(raw)
    assert old.schema_version == "1.7.0"
    assert all(v.alternatives == [] for p in old.products for v in p.values)
    assert old.decision is not None and all(r.score is not None for r in old.decision.ranked)


def test_the_new_fields_are_exported() -> None:
    assert (SHARED / "schema" / "AlternativeValue.json").is_file()
    resolved = exported("SearchResult")["$defs"]["ResolvedValue"]
    assert "alternatives" in resolved["properties"]
    assert "AlternativeValue" in exported("SearchResult")["$defs"]
    assert "category" in exported("VerifyInput")["properties"]
    score = exported("SearchResult")["$defs"]["RankedProduct"]["properties"]["score"]
    assert {"type": "null"} in score["anyOf"]
