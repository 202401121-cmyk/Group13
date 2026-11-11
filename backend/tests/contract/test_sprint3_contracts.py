"""Sprint 3 contracts (S3-0): DeepSeek, Firecrawl, hard rule ids and switches, rubric edits, term explanations,
evidence and citations.

They are agreed and merged before the stories that use them (D-23), so every rule here is one a story relies on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel, ValidationError

import app.contracts as C
from app.contracts import examples as ex
from tests.contract.schema_check import schema_errors

M = TypeVar("M", bound=BaseModel)

SHARED = Path(__file__).resolve().parents[3] / "shared" / "contracts"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "contracts"


def rebuild(model: M, **changes: object) -> M:
    """Copy a model with changes AND re-run validation (model_copy would skip it)."""
    return type(model).model_validate({**model.model_dump(), **changes})


def exported(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SHARED / "schema" / f"{name}.json").read_text(encoding="utf-8"))
    return data


def _rule(**changes: object) -> C.HardRule:
    fields: dict[str, object] = {
        "attribute": "ram_gb",
        "operator": ">=",
        "value": 16,
        "unit": "GB",
        "source_text": "at least 16 GB RAM",
    }
    return C.HardRule.model_validate({**fields, **changes})


def _state(**changes: object) -> C.InterviewState:
    fields: dict[str, object] = {"session_id": "sess_demo0001", "intent": ex.intent(), "criteria": ex.criteria()}
    return C.InterviewState.model_validate({**fields, **changes})


def _question(**changes: object) -> C.InterviewQuestion:
    fields: dict[str, object] = {
        "id": "q_cpu_performance",
        "criterion_key": "cpu_performance",
        "text": "How much does processor speed matter to you?",
    }
    return C.InterviewQuestion.model_validate({**fields, **changes})


def _sources() -> dict[str, C.Source]:
    return {s.id: s for s in ex.sources()}


def _cited_ids(result: C.SearchResult) -> set[str]:
    return {
        claim_id
        for summary in result.products
        for value in summary.values
        for claim_id in (*value.supporting_claim_ids, *value.conflicting_claim_ids)
    }


# --- DeepSeek (D-63) and Firecrawl (D-62) ------------------------------------------------------------------------


def test_deepseek_is_a_provider() -> None:
    assert C.Provider("deepseek") is C.Provider.DEEPSEEK
    assert "deepseek" in exported("LLMCallRecord")["$defs"]["Provider"]["enum"]


def test_a_source_can_be_read_through_firecrawl() -> None:
    page = next(s for s in ex.sources() if s.type is C.SourceType.REVIEW_SITE)
    assert rebuild(page, via="firecrawl").via == "firecrawl"
    with pytest.raises(ValidationError):
        rebuild(page, via="scraper")


# --- hard rule ids and switches (US-09) ----------------------------------------------------------------------------


def test_the_same_hard_rule_gets_the_same_id_after_a_rebuild() -> None:
    first = _rule()
    assert _rule(source_text="16 gigs of RAM at least", unit="gb").id == first.id  # how it was said doesn't count
    assert _rule(value=16.0).id == first.id  # 16 and 16.0 are the same number
    assert C.HardRule.model_validate_json(first.model_dump_json()).id == first.id
    rubric = ex.rubric()
    again = C.Rubric.model_validate(json.loads(rubric.model_dump_json()))
    assert [r.id for r in again.hard_rules] == [r.id for r in rubric.hard_rules] == [first.id]
    brands = _rule(
        attribute="brand", operator="in", value=["Nova", "Kestrel"], unit=None, source_text="Nova or Kestrel"
    )
    assert _rule(attribute="brand", operator="in", value=["kestrel", " NOVA "], unit=None, source_text="x").id == (
        brands.id
    )


def test_a_hard_rule_id_is_pinned() -> None:
    """Saved results and rule switches keep ids, so the way an id is made must never drift."""
    assert _rule().id == C.hard_rule_id("ram_gb", ">=", 16.0) == "ram_gb_min_1c18a881e7"
    assert ex.intent().hard_rules[0].id == "ram_gb_min_1c18a881e7"


def test_different_rules_get_different_ids() -> None:
    ids = {
        _rule().id,
        _rule(value=32).id,
        _rule(operator="<=").id,
        _rule(attribute="ssd_gb").id,
        _rule(value="16").id,  # text is not the number
        _rule(attribute="has_backlight", operator="==", value=True, unit=None).id,
        _rule(attribute="has_backlight", operator="==", value=False, unit=None).id,
    }
    assert len(ids) == 7


def test_minus_zero_and_zero_give_the_same_id() -> None:
    """The browser writes -0 back as 0, so a rule sent out and back must keep its id."""
    minus = _rule(attribute="warranty_years", value=-0.0, unit=None)
    assert minus.id == _rule(attribute="warranty_years", value=0, unit=None).id
    assert C.HardRule.model_validate({**minus.model_dump(), "value": 0}).id == minus.id


def test_every_operator_gives_a_valid_id() -> None:
    long_attribute = "a" * 39 + "_b" * 12  # 63 characters; the id keeps the first 40, without a trailing underscore
    for operator in ("<=", ">=", "==", "!=", "in", "not_in", "contains"):
        value: object = ["x"] if operator in ("in", "not_in") else "x"
        rule = _rule(attribute=long_attribute, operator=operator, value=value, unit=None)
        assert len(rule.id) <= 64 and "__" not in rule.id


def test_a_hard_rule_id_that_does_not_match_its_rule_is_refused() -> None:
    rule = _rule()
    assert rebuild(rule).id == rule.id  # its own id is kept
    with pytest.raises(ValidationError, match="made from its attribute, operator and value"):
        rebuild(rule, value=32)  # a changed value with the old id: leave the id out to make a new one
    with pytest.raises(ValidationError, match="made from its attribute, operator and value"):
        _rule(id="ram_gb_min_0123456789")
    assert C.HardRule.model_validate({**rule.model_dump(exclude={"id"}), "value": 32}).id == _rule(value=32).id


def test_a_hard_rule_is_active_until_switched_to_a_preference() -> None:
    rule = _rule()
    assert rule.active is True
    preference = rebuild(rule, active=False)
    assert preference.active is False and preference.id == rule.id  # switching keeps the id
    with pytest.raises(ValidationError):
        rebuild(rule, active="sometimes")


def test_a_1_6_0_hard_rule_without_an_id_gets_one() -> None:
    old = {"attribute": "ram_gb", "operator": ">=", "value": 16.0, "unit": "GB", "source_text": "at least 16 GB RAM"}
    rule = C.HardRule.model_validate(old)
    assert rule.id == _rule().id and rule.active is True


def test_an_invalid_rule_is_refused_with_a_validation_error_not_a_crash() -> None:
    with pytest.raises(ValidationError):
        C.HardRule.model_validate({"operator": ">=", "value": 16, "source_text": "x"})
    with pytest.raises(ValidationError):
        C.HardRule.model_validate({"attribute": "ram_gb", "operator": "about", "value": 16, "source_text": "x"})
    with pytest.raises(ValidationError):
        C.HardRule.model_validate({"attribute": "ram_gb", "operator": ">=", "value": {"a": 1}, "source_text": "x"})


# --- the shopper's rubric edits on the interview state (US-09) -------------------------------------------------


def test_rubric_edits_on_the_interview_state_start_empty() -> None:
    state = _state()
    assert state.manual_rule_switches == {}
    assert state.manual_exclusions_added == [] and state.manual_exclusions_removed == []
    assert state.manual_weights == {}
    old = state.model_dump(exclude={"manual_rule_switches", "manual_exclusions_added", "manual_exclusions_removed"})
    assert C.InterviewState.model_validate(old) == state  # a 1.6.0 state still loads


def test_the_rubric_edits_are_kept_on_the_state() -> None:
    rule_id = ex.intent().hard_rules[0].id
    state = _state(
        manual_rule_switches={rule_id: False},
        manual_exclusions_added=[C.Exclusion(kind="brand", value="Orion")],
        manual_exclusions_removed=[C.Exclusion(kind="brand", value="Zenith")],
        manual_weights={"battery_life": 9.0},
    )
    again = C.InterviewState.model_validate_json(state.model_dump_json())
    assert again.manual_rule_switches == {rule_id: False}
    assert [e.value for e in again.manual_exclusions_added] == ["Orion"]
    assert [e.value for e in again.manual_exclusions_removed] == ["Zenith"]
    assert again.manual_weights == {"battery_life": 9.0}


def test_the_added_exclusions_must_fit_the_rubric_beside_the_requests() -> None:
    """Rubric.exclusions holds at most 30, so a rebuild can always apply the shopper's additions."""
    taken = [C.Exclusion(kind="brand", value=f"Brand {i}") for i in range(25)]
    intent = rebuild(ex.intent(), exclusions=taken)
    five = [C.Exclusion(kind="model", value=f"Model {i}") for i in range(5)]
    assert len(_state(intent=intent, manual_exclusions_added=five).manual_exclusions_added) == 5
    with pytest.raises(ValidationError, match="must fit the rubric's 30"):
        _state(intent=intent, manual_exclusions_added=[*five, C.Exclusion(kind="model", value="Model 5")])
    many = [C.Exclusion(kind="seller", value=f"Seller {i}") for i in range(31)]
    for field in ("manual_exclusions_added", "manual_exclusions_removed"):
        with pytest.raises(ValidationError):
            _state(**{field: many})


def test_a_rule_switch_must_name_one_of_the_intents_hard_rules() -> None:
    with pytest.raises(ValidationError, match="names no hard rule"):
        _state(manual_rule_switches={"weight_kg_max_0123456789": False})


def test_an_exclusion_cannot_be_both_added_and_removed() -> None:
    with pytest.raises(ValidationError, match="both added and removed"):
        _state(
            manual_exclusions_added=[C.Exclusion(kind="brand", value="Orion")],
            manual_exclusions_removed=[C.Exclusion(kind="brand", value="orion ")],
        )
    other_kind = _state(
        manual_exclusions_added=[C.Exclusion(kind="brand", value="Orion")],
        manual_exclusions_removed=[C.Exclusion(kind="model", value="Orion")],
    )
    assert len(other_kind.manual_exclusions_added) == 1


# --- the rubric editor's route bodies (US-09) ---------------------------------------------------------------------

EDITS: dict[str, dict[str, object]] = {
    "switch_rule": {"rule_id": "ram_gb_min_0123456789", "active": False},
    "add_exclusion": {"exclusion": {"kind": "brand", "value": "Orion"}},
    "remove_exclusion": {"exclusion": {"kind": "brand", "value": "Zenith"}},
    "set_weight": {"criterion_key": "battery_life", "weight": 9.0},
}


@pytest.mark.parametrize("kind", sorted(EDITS))
def test_each_rubric_edit_carries_exactly_its_own_fields(kind: str) -> None:
    edit = C.RubricEdit.model_validate({"kind": kind, **EDITS[kind]})
    assert edit.kind == kind
    for other, fields in EDITS.items():
        if other == kind or fields.keys() == EDITS[kind].keys():
            continue
        with pytest.raises(ValidationError, match="carries"):
            C.RubricEdit.model_validate({"kind": kind, **EDITS[kind], **fields})
    some_missing = dict(list(EDITS[kind].items())[:-1])
    with pytest.raises(ValidationError, match="carries"):
        C.RubricEdit.model_validate({"kind": kind, **some_missing})


def test_a_weight_edit_is_a_real_0_to_10_number() -> None:
    for weight in (11, -1, True, "9"):
        with pytest.raises(ValidationError):
            C.RubricEdit.model_validate({"kind": "set_weight", "criterion_key": "battery_life", "weight": weight})


def test_a_rubric_edit_request_takes_one_edit_or_a_list() -> None:
    one = C.RubricEditRequest.model_validate({"edits": [{"kind": "set_weight", **EDITS["set_weight"]}]})
    assert len(one.edits) == 1
    every = ex.rubric_edit_request()
    assert {e.kind for e in every.edits} == set(EDITS)
    with pytest.raises(ValidationError):
        C.RubricEditRequest.model_validate({"edits": []})
    with pytest.raises(ValidationError):
        C.RubricEditRequest.model_validate({"edits": [{"kind": "set_weight", **EDITS["set_weight"]}] * 21})


def test_the_rubric_edit_response_is_the_rebuilt_rubric() -> None:
    response = C.RubricEditResponse(rubric=ex.rubric())
    assert C.RubricEditResponse.model_validate_json(response.model_dump_json()) == response


def test_request_bodies_may_leave_their_defaults_out() -> None:
    """Like ReplyRequest: what the frontend sends may leave out a field that has a default."""
    edit = exported("RubricEditRequest")["$defs"]["RubricEdit"]
    assert set(edit["required"]) == {"kind"}
    assert set(exported("ExclusionParseRequest")["required"]) == {"text"}
    assert C.RubricEdit.model_validate({"kind": "switch_rule", "rule_id": "ram_gb_min_0123456789", "active": True})


def test_exclusion_text_is_at_most_200_characters() -> None:
    assert C.ExclusionParseRequest(text="no Zenith, nothing refurbished").text
    assert len(C.ExclusionParseRequest(text="x" * 200).text) == 200
    for text in ("x" * 201, "", "   "):
        with pytest.raises(ValidationError):
            C.ExclusionParseRequest(text=text)


def test_exclusion_parse_lists_the_words_not_understood() -> None:
    response = ex.exclusion_parse_response()
    assert [(e.kind, e.value) for e in response.exclusions] == [("brand", "Zenith")]
    assert response.not_understood == ["nothing too shiny"]
    empty = C.ExclusionParseResponse()
    assert empty.exclusions == [] and empty.not_understood == []
    for words in ([""], ["x" * 201], ["words"] * 21):
        with pytest.raises(ValidationError):
            C.ExclusionParseResponse(not_understood=words)
    assert len(C.ExclusionParseResponse(not_understood=["x" * 200] * 20).not_understood) == 20
    brands = [C.Exclusion(kind="brand", value=f"Brand {i}") for i in range(31)]
    assert len(C.ExclusionParseResponse(exclusions=brands[:30]).exclusions) == 30
    with pytest.raises(ValidationError):
        C.ExclusionParseResponse(exclusions=brands)


# --- term explanations in a question -------------------------------------------------------------------------------


def _term(**changes: object) -> dict[str, object]:
    return {
        "term": "thermal throttling",
        "criterion_key": "cpu_performance",
        "explanation": "When a laptop gets hot, it slows its processor down to cool off.",
        **changes,
    }


def test_a_question_has_no_terms_by_default_and_at_most_five() -> None:
    assert _question().terms == []
    five = _question(terms=[_term(term=f"term {i}") for i in range(5)])
    assert len(five.terms) == 5
    with pytest.raises(ValidationError):
        _question(terms=[_term(term=f"term {i}") for i in range(6)])
    old = _question().model_dump(exclude={"terms"})
    assert C.InterviewQuestion.model_validate(old).terms == []  # a 1.6.0 question still loads


def test_an_explanation_over_40_words_is_refused() -> None:
    assert C.TermExplanation.model_validate(_term(explanation=" ".join(["word"] * 40)))
    with pytest.raises(ValidationError, match="40 words"):
        C.TermExplanation.model_validate(_term(explanation=" ".join(["word"] * 41)))
    with pytest.raises(ValidationError):
        C.TermExplanation.model_validate(_term(explanation=""))


def test_forty_long_words_still_fit_and_only_a_runaway_text_is_refused_by_size() -> None:
    """The word count is the rule; 1,000 characters only guards the size, so 40 long words are kept."""
    long_words = " ".join(["approximately"] * 40)  # 559 characters
    assert C.TermExplanation.model_validate(_term(explanation=long_words))
    with pytest.raises(ValidationError):
        C.TermExplanation.model_validate(_term(explanation="x" * 1001))


def test_a_term_is_at_most_40_characters() -> None:
    assert C.TermExplanation.model_validate(_term(term="x" * 40))
    for term in ("x" * 41, ""):
        with pytest.raises(ValidationError):
            C.TermExplanation.model_validate(_term(term=term))
    with pytest.raises(ValidationError):
        C.TermExplanation.model_validate(_term(criterion_key="CPU Performance"))


# --- evidence for one value (US-21) --------------------------------------------------------------------------------


def _withheld(response: C.EvidenceResponse) -> dict[str, Any]:
    """The same response with the Reddit quote withheld, as a saved result gives it (D-53)."""
    sources = _sources()
    data = response.model_dump()
    for claim in data["claims"]:
        if sources[claim["source"]["id"]].type is C.SourceType.REDDIT:
            claim["quote"] = None
    return data


def test_an_evidence_response_for_an_unknown_value_has_no_claims() -> None:
    unknown = ex.evidence_unknown()
    assert unknown.state is C.ValueState.UNKNOWN
    assert unknown.claims == [] and unknown.winning_claim_ids == []
    some = ex.evidence_disputed().claims[:1]
    with pytest.raises(ValidationError, match="no claims to show"):
        rebuild(unknown, claims=some)


def test_an_estimated_value_has_no_winning_claims_but_may_show_the_ones_that_conflicted() -> None:
    """As ResolvedValue: a peer average has no claims of its own, yet may cite the ones it set aside."""
    unknown = ex.evidence_unknown()
    some = ex.evidence_disputed().claims[:1]
    assert rebuild(unknown, state=C.ValueState.ESTIMATED).claims == []
    assert rebuild(unknown, state=C.ValueState.ESTIMATED, claims=some).winning_claim_ids == []
    with pytest.raises(ValidationError, match="no winning claims"):
        rebuild(unknown, state=C.ValueState.ESTIMATED, claims=some, winning_claim_ids=[some[0].claim_id])


def test_a_disputed_evidence_response_lists_both_sides() -> None:
    disputed = ex.evidence_disputed()
    assert disputed.state is C.ValueState.DISPUTED
    winners = set(disputed.winning_claim_ids)
    assert winners and any(c.claim_id not in winners for c in disputed.claims)
    assert {c.source.type for c in disputed.claims} == {C.SourceType.MANUFACTURER, C.SourceType.REDDIT}
    one_side = [c for c in disputed.claims if c.claim_id in winners]
    with pytest.raises(ValidationError, match="both sides"):
        rebuild(disputed, claims=one_side)
    with pytest.raises(ValidationError, match="winning"):
        rebuild(disputed, winning_claim_ids=[])


def test_a_known_value_names_its_winning_claims() -> None:
    disputed = ex.evidence_disputed()
    known = rebuild(disputed, state=C.ValueState.KNOWN)  # a known value may still list a claim that lost
    assert known.winning_claim_ids
    with pytest.raises(ValidationError, match="winning"):
        rebuild(disputed, state=C.ValueState.KNOWN, winning_claim_ids=[])


def test_winning_claims_must_be_among_the_claims() -> None:
    disputed = ex.evidence_disputed()
    with pytest.raises(ValidationError, match="among the claims"):
        rebuild(disputed, winning_claim_ids=["clm_" + "0" * 16])
    with pytest.raises(ValidationError, match="once"):
        rebuild(disputed, claims=[*disputed.claims, disputed.claims[0]])


def test_a_withheld_quote_needs_a_reason_and_a_reason_needs_one() -> None:
    disputed = ex.evidence_disputed()
    assert disputed.quote_withheld_reason is None and all(c.quote for c in disputed.claims)
    data = _withheld(disputed)
    with pytest.raises(ValidationError, match="quote_withheld_reason"):
        C.EvidenceResponse.model_validate(data)
    saved = C.EvidenceResponse.model_validate({**data, "quote_withheld_reason": "Reddit quotes are kept for 24 hours."})
    assert [c.quote is None for c in saved.claims].count(True) == 1
    with pytest.raises(ValidationError, match="quote_withheld_reason"):
        rebuild(disputed, quote_withheld_reason="Reddit quotes are kept for 24 hours.")


def test_claim_evidence_has_exactly_one_of_a_value_and_a_score() -> None:
    evidence = ex.evidence_disputed().claims[0]
    assert (evidence.value is None) != (evidence.score is None)
    with pytest.raises(ValidationError, match="exactly one of a value and a score"):
        rebuild(evidence, value=10.0, score=8.0)
    with pytest.raises(ValidationError, match="exactly one of a value and a score"):
        rebuild(evidence, value=None, score=None)


def test_the_evidence_source_is_named_by_its_site_or_community() -> None:
    names = {c.source.type: c.source.name for c in ex.evidence_disputed().claims}
    assert names == {C.SourceType.MANUFACTURER: "kestrel.example.com", C.SourceType.REDDIT: "r/developersIndia"}
    source = ex.evidence_disputed().claims[0].source
    assert rebuild(source, name="x" * 120).name
    for name in ("", "x" * 121):
        with pytest.raises(ValidationError):
            rebuild(source, name=name)


def test_quotes_keep_the_claim_limits_and_a_response_holds_at_most_200_claims() -> None:
    evidence = ex.evidence_disputed().claims[0]
    citation = ex.search_result().citations[0]
    for model in (evidence, citation):
        assert rebuild(model, quote="x" * 600).quote
        for quote in ("xx", "x" * 601):
            with pytest.raises(ValidationError):
                rebuild(model, quote=quote)
    claims = [rebuild(evidence, claim_id=f"clm_{i:016x}") for i in range(201)]
    known = rebuild(ex.evidence_disputed(), state=C.ValueState.KNOWN)
    winner = [claims[0].claim_id]
    assert len(rebuild(known, claims=claims[:200], winning_claim_ids=winner).claims) == 200
    with pytest.raises(ValidationError):
        rebuild(known, claims=claims, winning_claim_ids=winner)


# --- citations in a result (US-21, D-53) ---------------------------------------------------------------------------


def test_the_example_result_cites_every_claim_its_values_cite() -> None:
    result = ex.search_result()
    assert {c.claim_id for c in result.citations} == _cited_ids(result)
    claims = {c.id: c for c in ex.claims()}
    for citation in result.citations:
        claim = claims[citation.claim_id]
        assert (citation.source_id, citation.quote) == (claim.source_id, claim.quote)


def test_a_citation_must_point_to_a_claim_that_a_value_cites() -> None:
    result = ex.search_result()
    stray = {"claim_id": "clm_" + "0" * 16, "source_id": result.sources[0].id, "quote": None}
    with pytest.raises(ValidationError, match="that no value cites"):
        rebuild(result, citations=[*result.model_dump()["citations"], stray])


def test_a_citation_must_name_a_source_of_the_result() -> None:
    result = ex.search_result()
    citations = result.model_dump()["citations"]
    citations[0]["source_id"] = "src_" + "0" * 16
    with pytest.raises(ValidationError, match="not in this result's sources"):
        rebuild(result, citations=citations)


def test_each_claim_is_cited_once() -> None:
    result = ex.search_result()
    citations = result.model_dump()["citations"]
    with pytest.raises(ValidationError, match="once"):
        rebuild(result, citations=[*citations, citations[0]])


def test_a_citation_quote_is_optional() -> None:
    result = ex.search_result()
    citations = [{**c, "quote": None} for c in result.model_dump()["citations"]]
    assert all(c.quote is None for c in rebuild(result, citations=citations).citations)


def test_citations_are_all_or_nothing() -> None:
    """Empty is a result from before 1.7.0, or from a pipeline that doesn't fill them yet. Otherwise all are there."""
    result = ex.search_result()
    assert rebuild(result, citations=[]).citations == []
    citations = result.model_dump()["citations"]
    with pytest.raises(ValidationError, match="leave out claims a value cites"):
        rebuild(result, citations=citations[1:])


# --- the version, old results and the exported files ------------------------------------------------------------


def test_the_schema_version_is_1_7_0() -> None:
    assert C.SCHEMA_VERSION == "1.7.0"
    assert ex.search_result().schema_version == "1.7.0"
    assert (SHARED / "VERSION").read_text(encoding="utf-8").strip() == "1.7.0"


def test_a_1_6_0_result_still_validates() -> None:
    raw = json.loads((FIXTURES / "search_result_1_6_0.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == "1.6.0" and "citations" not in raw
    assert all("id" not in rule for rule in raw["request"]["rubric"]["hard_rules"])
    old = C.SearchResult.model_validate(raw)
    assert old.schema_version == "1.6.0"
    assert old.citations == []
    assert [r.id for r in old.request.rubric.hard_rules] == [_rule().id]
    assert all(r.active for r in old.request.rubric.hard_rules)


NEW_EXAMPLES = {
    "evidence_disputed": "EvidenceResponse",
    "evidence_unknown": "EvidenceResponse",
    "rubric_edit_request": "RubricEditRequest",
    "exclusion_parse_response": "ExclusionParseResponse",
    "search_result": "SearchResult",
    "intent": "Intent",
}


@pytest.mark.parametrize("name", sorted(NEW_EXAMPLES))
def test_the_new_examples_match_their_exported_schemas(name: str) -> None:
    data = json.loads((SHARED / "examples" / f"{name}.json").read_text(encoding="utf-8"))
    assert schema_errors(data, exported(NEW_EXAMPLES[name])) == []
    assert data == json.loads(json.dumps(ex.ALL_EXAMPLES[name]().model_dump(mode="json")))


def test_every_new_contract_is_exported() -> None:
    for name in (
        "Citation",
        "ClaimEvidence",
        "EvidenceResponse",
        "EvidenceSource",
        "ExclusionParseRequest",
        "ExclusionParseResponse",
        "RubricEdit",
        "RubricEditRequest",
        "RubricEditResponse",
        "TermExplanation",
    ):
        assert (SHARED / "schema" / f"{name}.json").is_file(), name
    hard_rule = exported("Intent")["$defs"]["HardRule"]
    assert {"id", "active"} <= set(hard_rule["required"])
    assert "citations" in exported("SearchResult")["required"]
    assert "terms" in exported("InterviewStep")["$defs"]["InterviewQuestion"]["required"]
