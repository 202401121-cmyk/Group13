"""Sprint 5 contracts (S5-0): schema 1.10.0, only the fields Sprint 5's stories lack (D-23, P-40, P-42).

- Memory (US-06, US-11): `PreferenceItem`, `PreferenceList`, `PreferenceEdit` and `PreferenceExport`, with no
  embedding, user id or email in any of them. `RememberedPreference` names its item (`preference_id`) and `kind`, so the
  interview can change or forget it in one click.
- The cache (US-34): `SearchResult.cached`, and `cached_at` only when it is.
- Re-rank (US-23, P-42): a ranked product's contributions and the rubric already give its score, so scoring needs
  nothing new. `DecideResult.checked_values` holds what the filter read for each product: its price and each hard
  rule's attribute, with their state.
- The session (S4-17): `PresearchSession.note` carries the all-zero note on a reload, and `InterviewState.manual_always`
  keeps the shopper's "Always" on the request's own leave-outs apart from the added ones, so one counts once.
- `RankedProduct.rule_notes`: how a RAM or storage rule passed through one version of the product (S4-24).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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
ITEM = UUID("3c4d5e6f-7a8b-4c9d-8e0f-1a2b3c4d5e6f")
T0 = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)

NEW_EXAMPLES = {
    "preference_list": "PreferenceList",
    "preference_export": "PreferenceExport",
    "preference_edit": "PreferenceEdit",
}


def rebuild(model: M, **changes: object) -> M:
    """Copy a model with changes AND re-run validation (model_copy would skip it)."""
    return type(model).model_validate({**model.model_dump(), **changes})


def exported(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SHARED / "schema" / f"{name}.json").read_text(encoding="utf-8"))
    return data


def item(**changes: object) -> C.PreferenceItem:
    """A remembered weight, changed as given."""
    base: dict[str, object] = {
        "id": ITEM,
        "kind": "weight",
        "category": "laptop",
        "criterion_key": "battery_life",
        "text": "Battery life matters a lot",
        "weight": 8.0,
        "rule": None,
        "exclusion": None,
        "always": False,
        "created_at": T0,
        "updated_at": T0,
    }
    return C.PreferenceItem.model_validate({**base, **changes})


RULE = C.HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least 16 GB RAM")
XIAOMI = C.Exclusion(kind="brand", value="Xiaomi", always=True)


# --- the version and an old result ---------------------------------------------------------------------------------


def test_the_schema_version_is_1_10_0() -> None:
    assert C.SCHEMA_VERSION == "1.10.0"
    assert ex.search_result().schema_version == "1.10.0"
    assert (SHARED / "VERSION").read_text(encoding="utf-8").strip() == "1.10.0"


def test_a_1_9_0_result_still_validates_and_is_not_cached() -> None:
    raw = json.loads((FIXTURES / "search_result_1_9_0.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == "1.9.0"
    assert "cached" not in raw and "cached_at" not in raw
    assert "checked_values" not in raw["decision"]
    assert all("rule_notes" not in r for r in raw["decision"]["ranked"])
    old = C.SearchResult.model_validate(raw)
    assert old.schema_version == "1.9.0"
    assert (old.cached, old.cached_at) == (False, None)
    assert old.decision is not None
    assert old.decision.checked_values == []
    assert all(r.rule_notes == [] for r in old.decision.ranked)


# --- the cache (US-34) -----------------------------------------------------------------------------------------------


def test_a_cached_result_says_when_it_was_made() -> None:
    cached = rebuild(ex.search_result(), cached=True, cached_at=T0)
    assert (cached.cached, cached.cached_at) == (True, T0)


def test_cached_at_without_cached_is_refused() -> None:
    with pytest.raises(ValidationError, match="cached_at"):
        rebuild(ex.search_result(), cached=False, cached_at=T0)


def test_a_cached_result_needs_its_time() -> None:
    with pytest.raises(ValidationError, match="cached_at"):
        rebuild(ex.search_result(), cached=True, cached_at=None)


def test_cached_at_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError):
        rebuild(ex.search_result(), cached=True, cached_at=datetime(2026, 11, 21, 9, 0))


# --- memory (US-06, US-11) -------------------------------------------------------------------------------------------


def test_a_remembered_weight_a_must_have_and_a_leave_out_each_validate() -> None:
    assert item().weight == 8.0
    rule = item(kind="hard_rule", criterion_key=None, weight=None, rule=RULE, text="At least 16 GB RAM")
    assert rule.rule == RULE
    left_out = item(
        kind="exclusion",
        category=None,
        criterion_key=None,
        weight=None,
        exclusion=XIAOMI,
        always=True,
        text="No Xiaomi",
    )
    assert left_out.category is None and left_out.exclusion == XIAOMI


@pytest.mark.parametrize("weight", [-0.5, 10.5, 11])
def test_an_item_refuses_a_weight_outside_0_to_10(weight: float) -> None:
    with pytest.raises(ValidationError):
        item(weight=weight)


@pytest.mark.parametrize("weight", [0, 10])
def test_an_item_takes_the_ends_of_the_scale(weight: float) -> None:
    assert item(weight=weight).weight == weight


def test_an_item_refuses_text_over_200_characters() -> None:
    assert len(item(text="a" * 200).text) == 200
    with pytest.raises(ValidationError):
        item(text="a" * 201)


def test_an_item_needs_some_text() -> None:
    with pytest.raises(ValidationError):
        item(text="   ")


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"weight": None}, id="a-weight-without-its-weight"),
        pytest.param({"criterion_key": None}, id="a-weight-without-its-criterion"),
        pytest.param({"rule": RULE}, id="a-weight-with-a-rule"),
        pytest.param({"kind": "hard_rule", "weight": None}, id="a-must-have-without-its-rule"),
        pytest.param({"kind": "hard_rule", "rule": RULE}, id="a-must-have-with-a-weight"),
        pytest.param({"kind": "exclusion", "weight": None, "criterion_key": None}, id="a-leave-out-without-it"),
        pytest.param(
            {"kind": "exclusion", "weight": None, "criterion_key": None, "exclusion": XIAOMI, "rule": RULE},
            id="a-leave-out-with-a-rule",
        ),
    ],
)
def test_an_item_carries_exactly_what_its_kind_needs(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        item(**changes)


def test_only_a_brand_left_out_with_always_may_have_no_category() -> None:
    with pytest.raises(ValidationError, match="category"):
        item(category=None)
    model = C.Exclusion(kind="model", value="Nova Book 14", always=True)
    with pytest.raises(ValidationError, match="category"):
        item(kind="exclusion", category=None, criterion_key=None, weight=None, exclusion=model, always=True)
    not_always = C.Exclusion(kind="brand", value="Xiaomi", always=False)
    with pytest.raises(ValidationError, match="category"):
        item(kind="exclusion", category=None, criterion_key=None, weight=None, exclusion=not_always, always=False)


def test_a_brand_left_out_with_always_holds_everywhere_so_has_no_category() -> None:
    with pytest.raises(ValidationError, match="category"):
        item(kind="exclusion", category="laptop", criterion_key=None, weight=None, exclusion=XIAOMI, always=True)


def test_a_leave_outs_always_is_the_items_own() -> None:
    with pytest.raises(ValidationError, match="always"):
        item(kind="exclusion", criterion_key=None, weight=None, exclusion=XIAOMI, always=False)


def test_an_item_is_never_changed_before_it_was_made() -> None:
    with pytest.raises(ValidationError, match="updated_at"):
        item(updated_at=T0 - timedelta(seconds=1))


def test_the_list_is_newest_first() -> None:
    later = T0 + timedelta(days=1)
    older, newer = item(created_at=T0, updated_at=T0), item(id=UUID(int=7), created_at=later, updated_at=later)
    assert C.PreferenceList(items=[newer, older]).items == [newer, older]
    with pytest.raises(ValidationError, match="newest first"):
        C.PreferenceList(items=[older, newer])


def test_an_edit_changes_something() -> None:
    assert C.PreferenceEdit(weight=6).weight == 6
    assert C.PreferenceEdit(active=False).active is False
    with pytest.raises(ValidationError, match="changes nothing"):
        C.PreferenceEdit()


@pytest.mark.parametrize("weight", ["7", True, -1, 10.5])
def test_an_edit_takes_a_real_0_to_10_weight_only(weight: object) -> None:
    with pytest.raises(ValidationError):
        C.PreferenceEdit.model_validate({"weight": weight})


def test_an_edit_refuses_text_over_200_characters() -> None:
    with pytest.raises(ValidationError):
        C.PreferenceEdit(text="a" * 201)


def test_the_export_holds_every_item_and_when() -> None:
    export = ex.preference_export()
    assert export.items == ex.preference_list().items
    assert export.exported_at.tzinfo is not None


def _property_names(schema: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                names |= set(value)
            names |= _property_names(value)
    elif isinstance(schema, list):
        for value in schema:
            names |= _property_names(value)
    return names


@pytest.mark.parametrize("name", ["PreferenceItem", "PreferenceList", "PreferenceEdit", "PreferenceExport"])
def test_no_memory_shape_holds_an_embedding_a_user_id_or_an_email(name: str) -> None:
    names = _property_names(exported(name))
    assert names, name
    assert not {n for n in names if any(word in n for word in ("embedding", "user", "email"))}


def test_a_remembered_preference_names_its_item_and_kind() -> None:
    remembered = C.RememberedPreference(
        preference_id=ITEM, kind="weight", text="Battery life matters a lot", criterion_key="battery_life",
        weight=8.0, similarity=0.92, age_days=12,
    )  # fmt: skip
    assert (remembered.preference_id, remembered.kind) == (ITEM, "weight")
    data = remembered.model_dump()
    for missing in ("preference_id", "kind"):
        with pytest.raises(ValidationError):
            C.RememberedPreference.model_validate({k: v for k, v in data.items() if k != missing})
    with pytest.raises(ValidationError):
        C.RememberedPreference.model_validate({**data, "kind": "budget"})


# --- the session (S4-17) ---------------------------------------------------------------------------------------------


def test_the_session_note_is_none_by_default_and_at_most_300_characters() -> None:
    session = C.PresearchSession(session_id=ex.SESSION_ID, request=ex.QUERY)
    assert session.note is None
    assert session.changed(note="a" * 300).note == "a" * 300
    with pytest.raises(ValidationError):
        session.changed(note="a" * 301)


def _state(**changes: object) -> C.InterviewState:
    return rebuild(ex.interview_state(), **changes)


ZENITH = {"kind": "brand", "value": "Zenith"}  # the example request's own leave-out


def test_always_on_the_requests_own_leave_out_is_kept_apart() -> None:
    state = _state(manual_always=[{**ZENITH, "always": True}])
    assert [(e.value, e.always) for e in state.manual_always] == [("Zenith", True)]
    assert state.manual_exclusions_added == []


def test_an_always_choice_names_one_of_the_requests_leave_outs() -> None:
    with pytest.raises(ValidationError, match="leave-out the request"):
        _state(manual_always=[{"kind": "brand", "value": "Kestrel", "always": True}])


def test_an_always_choice_on_a_removed_leave_out_is_refused() -> None:
    with pytest.raises(ValidationError, match="removed"):
        _state(manual_always=[{**ZENITH, "always": True}], manual_exclusions_removed=[ZENITH])


def test_each_leave_out_has_one_always_choice() -> None:
    with pytest.raises(ValidationError, match="once"):
        _state(manual_always=[{**ZENITH, "always": True}, {"kind": "brand", "value": "ZENITH", "always": True}])


def _thirty() -> C.Intent:
    leave_outs = [{"kind": "brand", "value": f"Brand {n}"} for n in range(30)]
    return rebuild(ex.intent(), exclusions=leave_outs)


def test_a_leave_out_marked_always_counts_once_at_the_cap_of_30() -> None:
    state = _state(intent=_thirty(), manual_always=[{"kind": "brand", "value": "brand 7", "always": True}])
    assert len(state.intent.exclusions) == 30 and len(state.manual_always) == 1


def test_an_added_leave_out_past_the_30_is_still_refused() -> None:
    with pytest.raises(ValidationError, match="30"):
        _state(intent=_thirty(), manual_exclusions_added=[{"kind": "brand", "value": "Brand 30"}])


def test_a_removed_leave_out_makes_room_for_an_added_one() -> None:
    state = _state(
        intent=_thirty(),
        manual_exclusions_removed=[{"kind": "brand", "value": "Brand 3"}],
        manual_exclusions_added=[{"kind": "brand", "value": "Brand 30"}],
    )
    assert len(state.manual_exclusions_added) == 1


# --- the ranked product's rule notes (S4-24) -------------------------------------------------------------------------


def _ranked(**changes: object) -> C.RankedProduct:
    decision = ex.decision()
    return rebuild(decision.ranked[0], **changes)


def test_rule_notes_are_empty_by_default() -> None:
    assert (
        C.RankedProduct.model_validate(
            {k: v for k, v in ex.decision().ranked[0].model_dump().items() if k != "rule_notes"}
        ).rule_notes
        == []
    )


def test_rule_notes_hold_at_most_5_short_strings() -> None:
    note = 'It meets "at least 16 GB RAM" in its 16 GB version'
    assert _ranked(rule_notes=[note] * 5).rule_notes == [note] * 5
    with pytest.raises(ValidationError):
        _ranked(rule_notes=[note] * 6)
    with pytest.raises(ValidationError):
        _ranked(rule_notes=["a" * 401])
    with pytest.raises(ValidationError):
        _ranked(rule_notes=[""])


# --- what the filter read (US-23, P-42) ------------------------------------------------------------------------------


def checked(**changes: object) -> C.CheckedValue:
    base: dict[str, object] = {
        "product_id": "prd_nova_book_14",
        "attribute": "price_inr",
        "state": C.ValueState.KNOWN,
        "values": [64990.0],
        "unit": "INR",
        "any_version": False,
    }
    return C.CheckedValue.model_validate({**base, **changes})


def test_a_checked_value_holds_what_the_filter_compared() -> None:
    assert checked().values == [64990.0]
    versions = checked(attribute="ram_gb", values=[8.0, 16.0], unit="gb", any_version=True)
    assert versions.any_version and versions.values == [8.0, 16.0]
    sides = checked(state=C.ValueState.DISPUTED, values=[68990.0, 74990.0])
    assert sides.values == [68990.0, 74990.0]


def test_a_value_the_filter_could_not_read_has_no_values_unless_names_state_versions() -> None:
    assert checked(state=C.ValueState.UNKNOWN, values=[], unit=None).values == []
    named = checked(
        attribute="ram_gb", state=C.ValueState.UNKNOWN, values=[8.0], unit="gb", any_version=True, from_names=1
    )
    assert (named.values, named.from_names) == ([8.0], 1)
    with pytest.raises(ValidationError, match="no values"):
        checked(state=C.ValueState.UNKNOWN, values=[64990.0])
    with pytest.raises(ValidationError, match="no values"):
        checked(state=C.ValueState.ESTIMATED, values=[64990.0])
    with pytest.raises(ValidationError, match="no values"):  # a version from the claims needs a value the filter read
        checked(
            attribute="ram_gb", state=C.ValueState.UNKNOWN, values=[6.0, 8.0], unit="gb", any_version=True, from_names=1
        )


def test_from_names_counts_versions_only() -> None:
    both = checked(attribute="ram_gb", values=[6.0, 8.0], unit="gb", any_version=True, from_names=1)
    assert len(both.values) - both.from_names == 1  # one version from the claims
    with pytest.raises(ValidationError, match="from_names"):
        checked(attribute="ram_gb", values=[6.0], unit="gb", any_version=True, from_names=2)
    with pytest.raises(ValidationError, match="names"):
        checked(attribute="ram_gb", values=[6.0, 8.0], unit="gb", any_version=False, from_names=1)


def test_a_unit_goes_with_a_number() -> None:
    assert checked(attribute="has_ois", values=[True], unit=None).values == [True]
    with pytest.raises(ValidationError, match="unit"):
        checked(attribute="has_ois", values=[True], unit="gb")


def test_a_product_has_one_checked_value_per_attribute() -> None:
    decision = ex.decision()
    twice = [*decision.checked_values, decision.checked_values[0]]
    with pytest.raises(ValidationError, match="once"):
        rebuild(decision, checked_values=[v.model_dump() for v in twice])


def test_the_example_decision_reads_every_decided_products_price_and_ram() -> None:
    decision = ex.decision()
    decided = {r.product_id for r in decision.ranked} | {f.product_id for f in decision.filtered}
    pairs = {(v.product_id, v.attribute) for v in decision.checked_values}
    assert pairs == {(pid, key) for pid in decided for key in ("price_inr", "ram_gb")}


# --- exported files ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "model"), sorted(NEW_EXAMPLES.items()))
def test_each_new_example_is_exported_and_fits_its_schema(name: str, model: str) -> None:
    data = json.loads((SHARED / "examples" / f"{name}.json").read_text(encoding="utf-8"))
    assert schema_errors(data, exported(model)) == []
    assert getattr(C, model).model_validate(data) == getattr(ex, name)()


@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("search_result", "SearchResult"),
        ("decision", "DecideResult"),
        ("interview_state", "InterviewState"),
    ],
)
def test_the_changed_examples_fit_their_schemas(name: str, model: str) -> None:
    data = json.loads((SHARED / "examples" / f"{name}.json").read_text(encoding="utf-8"))
    assert schema_errors(data, exported(model)) == []


def test_every_new_field_is_in_the_exported_schemas() -> None:
    assert {"cached", "cached_at"} <= set(exported("SearchResult")["properties"])
    assert "note" in exported("PresearchSession")["properties"]
    assert "manual_always" in exported("InterviewState")["properties"]
    assert "checked_values" in exported("DecideResult")["properties"]
    assert "rule_notes" in exported("RankedProduct")["properties"]
    remembered = exported("InterviewState")["$defs"]["RememberedPreference"]["properties"]
    assert {"preference_id", "kind"} <= set(remembered)
