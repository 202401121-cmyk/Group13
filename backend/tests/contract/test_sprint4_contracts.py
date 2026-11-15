"""Sprint 4 contracts (S4-0): schema 1.9.0, only the fields Sprint 4's stories lack (D-23).

- `Contribution.basis` says how a criterion score was made: a source's opinion, a compared value (P-20) or a peer
  estimate (DR-09). None on a result from before 1.9.0.
- `RubricEditResponse.note` carries the all-zero note that S3-8 sent in a header (S4-17 fills it).
- `InterviewState.beliefs` holds a belief about each weight (US-04); empty means the fixed order.
- US-30's shapes: `MeResponse`, `SavedSearchSummary`, `SavedSearchList`. No contract ever holds an email.
- `SearchResult.llm_unavailable` is true when every LLM provider failed during the run (US-31, S4-12 sets it).
- A YouTube source whose text holds viewers' comments may carry the reddit data class (P-23); a Reddit source always
  does, and every other type stays public.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

import app.contracts as C
from app.contracts import examples as ex
from tests.contract.schema_check import schema_errors

M = TypeVar("M", bound=BaseModel)

SHARED = Path(__file__).resolve().parents[3] / "shared" / "contracts"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "contracts"
USER = UUID("6f1c2b8e-3d4a-4f5b-9c7d-2e1a0b9c8d7e")
SAVED = UUID("0b8a7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d")
T0 = datetime(2026, 11, 16, 9, 30, tzinfo=UTC)

NEW_EXAMPLES = {
    "rubric_edit_response": "RubricEditResponse",
    "interview_state": "InterviewState",
    "me_response": "MeResponse",
    "saved_search_list": "SavedSearchList",
}


def rebuild(model: M, **changes: object) -> M:
    """Copy a model with changes AND re-run validation (model_copy would skip it)."""
    return type(model).model_validate({**model.model_dump(), **changes})


def exported(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SHARED / "schema" / f"{name}.json").read_text(encoding="utf-8"))
    return data


def contribution(**changes: object) -> C.Contribution:
    base: dict[str, object] = {
        "criterion_key": "battery_life",
        "score": 7.5,
        "normalized_weight": 0.25,
        "contribution": 1.875,
        "state": C.ValueState.KNOWN,
    }
    return C.Contribution.model_validate({**base, **changes})


def summary(**changes: object) -> C.SavedSearchSummary:
    base: dict[str, object] = {
        "id": SAVED,
        "created_at": T0,
        "request_text": "Laptop for coding under 80k",
        "category": "laptop",
        "top_product": "Nova Book 14",
    }
    return C.SavedSearchSummary.model_validate({**base, **changes})


def youtube(**changes: object) -> C.Source:
    """A made-up YouTube source. Its text holds the video's description and, with comments, viewers' words."""
    base: dict[str, object] = {
        "id": "src_" + "e" * 16,
        "url": "https://www.youtube.com/watch?v=abc123",
        "type": C.SourceType.YOUTUBE,
        "tier": C.SourceTier.COMMUNITY,
        "title": "Nova Book 14 review after a month",
        "text": "The battery lasts a full day of coding. Viewers say the keyboard is good.",
        "text_hash": "f" * 64,
        "fetched_at": T0,
        "site": "youtube.com",
        "via": "api",
    }
    return C.Source.model_validate({**base, **changes})


# --- the version and old results -----------------------------------------------------------------------------------


def test_the_schema_version_is_1_9_0() -> None:
    assert C.SCHEMA_VERSION == "1.9.0"
    assert ex.search_result().schema_version == "1.9.0"
    assert (SHARED / "VERSION").read_text(encoding="utf-8").strip() == "1.9.0"


def test_a_1_8_0_result_still_validates() -> None:
    raw = json.loads((FIXTURES / "search_result_1_8_0.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == "1.8.0"
    assert "llm_unavailable" not in raw
    assert all("basis" not in c for r in raw["decision"]["ranked"] for c in r["contributions"])
    old = C.SearchResult.model_validate(raw)
    assert old.schema_version == "1.8.0"
    assert old.llm_unavailable is False
    assert old.decision is not None
    assert all(c.basis is None for r in old.decision.ranked for c in r.contributions)


# --- Contribution.basis --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("basis", ["opinion", "compared", "estimated", None])
def test_contribution_basis_takes_the_three_kinds_or_none(basis: str | None) -> None:
    assert contribution(basis=basis).basis == basis
    assert contribution().basis is None  # left out: a result from before 1.9.0


@pytest.mark.parametrize("basis", ["guess", "Opinion", "", "known"])
def test_contribution_basis_refuses_any_other_word(basis: str) -> None:
    with pytest.raises(ValidationError):
        contribution(basis=basis)


# --- RubricEditResponse.note ---------------------------------------------------------------------------------------


def test_rubric_edit_response_note_is_at_most_300_characters() -> None:
    rubric = ex.rubric()
    assert C.RubricEditResponse(rubric=rubric).note is None
    assert C.RubricEditResponse(rubric=rubric, note="x" * 300).note == "x" * 300
    with pytest.raises(ValidationError):
        C.RubricEditResponse(rubric=rubric, note="x" * 301)


# --- WeightBelief and InterviewState.beliefs -----------------------------------------------------------------------


@pytest.mark.parametrize(("mean", "sd"), [(5.0, 0.0), (11.0, 1.0), (5.0, 5.01), (-0.5, 1.0), (5.0, -1.0)])
def test_a_weight_belief_with_sd_0_or_mean_11_is_refused(mean: float, sd: float) -> None:
    with pytest.raises(ValidationError):
        C.WeightBelief(mean=mean, sd=sd)


@pytest.mark.parametrize(("mean", "sd"), [(0.0, 0.01), (10.0, 5.0), (5.0, 2.5)])
def test_a_weight_belief_inside_the_bounds_is_kept(mean: float, sd: float) -> None:
    belief = C.WeightBelief(mean=mean, sd=sd)
    assert (belief.mean, belief.sd) == (mean, sd)


def test_interview_state_beliefs_default_to_empty_and_take_slug_keys() -> None:
    state = ex.interview_state()
    plain = rebuild(state, beliefs={})
    assert plain.beliefs == {}
    assert (
        C.InterviewState.model_validate({k: v for k, v in state.model_dump().items() if k != "beliefs"}).beliefs == {}
    )
    assert state.beliefs  # the example shows the field filled
    with pytest.raises(ValidationError):
        rebuild(state, beliefs={"Battery Life": {"mean": 5.0, "sd": 1.0}})
    with pytest.raises(ValidationError):
        rebuild(state, beliefs={"battery_life": {"mean": 5.0, "sd": 0.0}})


# --- US-30 shapes --------------------------------------------------------------------------------------------------


def test_saved_search_list_with_51_items_is_refused() -> None:
    items = [summary(id=UUID(int=n + 1)) for n in range(51)]
    with pytest.raises(ValidationError):
        C.SavedSearchList(items=items)
    assert len(C.SavedSearchList(items=items[:50]).items) == 50
    assert C.SavedSearchList(items=[]).next_cursor is None


def test_me_response_signed_out_with_a_user_id_is_refused() -> None:
    with pytest.raises(ValidationError):
        C.MeResponse(signed_in=False, user_id=USER)
    with pytest.raises(ValidationError):
        C.MeResponse(signed_in=False, display_name="Asha")
    guest = C.MeResponse(signed_in=False)
    assert (guest.user_id, guest.display_name) == (None, None)


def test_me_response_signed_in_needs_a_user_id() -> None:
    with pytest.raises(ValidationError):
        C.MeResponse(signed_in=True)
    me = C.MeResponse(signed_in=True, user_id=USER, display_name="Asha")
    assert me.user_id == USER
    with pytest.raises(ValidationError):
        C.MeResponse(signed_in=True, user_id=USER, display_name="x" * 81)


def test_saved_search_summary_limits() -> None:
    assert summary(category=None, top_product=None).top_product is None
    with pytest.raises(ValidationError):
        summary(request_text="x" * 501)
    with pytest.raises(ValidationError):
        summary(request_text="")
    with pytest.raises(ValidationError):
        summary(top_product="x" * 151)
    with pytest.raises(ValidationError):
        summary(created_at=datetime(2026, 11, 16, 9, 30))  # no time zone
    with pytest.raises(ValidationError):
        summary(id="not-a-uuid")


def test_no_contract_carries_an_email() -> None:
    """No exported schema has a property whose name holds "email", at any depth (US-30, D-36)."""

    def names(node: object) -> list[str]:
        if isinstance(node, dict):
            own = list(node.get("properties", {})) if isinstance(node.get("properties"), dict) else []
            return own + [n for value in node.values() for n in names(value)]
        if isinstance(node, list):
            return [n for value in node for n in names(value)]
        return []

    schemas = sorted((SHARED / "schema").glob("*.json"))
    assert len(schemas) > 70
    found = {
        p.stem: n for p in schemas for n in names(json.loads(p.read_text(encoding="utf-8"))) if "mail" in n.lower()
    }
    assert found == {}


# --- SearchResult.llm_unavailable ----------------------------------------------------------------------------------


def test_llm_unavailable_defaults_to_false_and_can_be_set() -> None:
    result = ex.search_result()
    assert result.llm_unavailable is False
    assert rebuild(result, llm_unavailable=True).llm_unavailable is True
    with pytest.raises(ValidationError):
        rebuild(result, llm_unavailable="maybe")


# --- Source data class (P-23) --------------------------------------------------------------------------------------


@pytest.mark.parametrize("data_class", [C.DataClass.REDDIT, C.DataClass.PUBLIC])
def test_a_youtube_source_may_carry_reddit_or_public(data_class: C.DataClass) -> None:
    assert youtube(data_class=data_class).data_class is data_class
    assert youtube().data_class is C.DataClass.PUBLIC  # left out, it follows the type: YouTube is public


def test_a_reddit_source_always_carries_reddit() -> None:
    thread = ex.research().sources[0]
    assert thread.type is C.SourceType.REDDIT and thread.data_class is C.DataClass.REDDIT
    with pytest.raises(ValidationError, match="data class"):
        rebuild(thread, data_class=C.DataClass.PUBLIC)


@pytest.mark.parametrize(
    "source_type",
    [
        C.SourceType.WEB_SEARCH,
        C.SourceType.REVIEW_SITE,
        C.SourceType.MANUFACTURER,
        C.SourceType.LAB,
        C.SourceType.RETAILER,
    ],
)
def test_only_reddit_and_youtube_may_carry_reddit(source_type: C.SourceType) -> None:
    page = next(s for s in ex.research().sources if s.type is not C.SourceType.REDDIT)
    tier = C.SourceTier.RETAILER if source_type is C.SourceType.RETAILER else page.tier
    assert rebuild(page, type=source_type, tier=tier).data_class is C.DataClass.PUBLIC
    with pytest.raises(ValidationError, match="data class"):
        rebuild(page, type=source_type, tier=tier, data_class=C.DataClass.REDDIT)


# --- every new field through JSON Schema ---------------------------------------------------------------------------


def _filled() -> list[tuple[str, BaseModel]]:
    decision = ex.decision()
    return [
        ("Contribution", contribution(basis="compared")),
        ("Contribution", contribution(score=None, contribution=0.0, state=C.ValueState.UNKNOWN, basis=None)),
        ("DecideResult", decision),
        ("RubricEditResponse", C.RubricEditResponse(rubric=ex.rubric(), note="Every weight was set to 0.")),
        ("RubricEditResponse", ex.rubric_edit_response()),
        ("WeightBelief", C.WeightBelief(mean=6.5, sd=1.5)),
        ("InterviewState", ex.interview_state()),
        ("MeResponse", C.MeResponse(signed_in=True, user_id=USER, display_name="Asha")),
        ("MeResponse", C.MeResponse(signed_in=False)),
        ("SavedSearchSummary", summary()),
        ("SavedSearchList", C.SavedSearchList(items=[summary()], next_cursor="cursor_2")),
        ("SearchResult", rebuild(ex.search_result(), llm_unavailable=True)),
        ("Source", youtube(data_class=C.DataClass.REDDIT)),
    ]


@pytest.mark.parametrize(("name", "model"), _filled(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_new_field_round_trips_through_json_schema(name: str, model: BaseModel) -> None:
    data = json.loads(model.model_dump_json())
    assert schema_errors(data, exported(name)) == []
    assert type(model).model_validate(data) == model


def test_the_new_fields_are_in_the_exported_schemas() -> None:
    contribution_schema = exported("SearchResult")["$defs"]["Contribution"]
    assert {"opinion", "compared", "estimated"} <= {
        v for option in contribution_schema["properties"]["basis"]["anyOf"] for v in option.get("enum", [])
    }
    assert "note" in exported("RubricEditResponse")["properties"]
    assert "beliefs" in exported("InterviewState")["properties"]
    assert "llm_unavailable" in exported("SearchResult")["properties"]
    belief = exported("WeightBelief")["properties"]
    assert belief["sd"]["exclusiveMinimum"] == 0 and belief["sd"]["maximum"] == 5


@pytest.mark.parametrize("name", sorted(NEW_EXAMPLES))
def test_the_new_examples_match_their_exported_schemas(name: str) -> None:
    data = json.loads((SHARED / "examples" / f"{name}.json").read_text(encoding="utf-8"))
    assert schema_errors(data, exported(NEW_EXAMPLES[name])) == []
    assert data == json.loads(json.dumps(ex.ALL_EXAMPLES[name]().model_dump(mode="json")))


def test_the_decision_example_shows_each_contributions_basis() -> None:
    contributions = [c for r in ex.decision().ranked for c in r.contributions]
    assert {c.basis for c in contributions} >= {"opinion", "estimated"}
    for c in contributions:
        expected = None if c.score is None else "estimated" if c.state is C.ValueState.ESTIMATED else "opinion"
        assert c.basis == expected, c


def test_the_new_contracts_are_exported() -> None:
    for name in ("WeightBelief", "MeResponse", "SavedSearchSummary", "SavedSearchList"):
        assert (SHARED / "schema" / f"{name}.json").is_file(), name
