"""US-29: GET /api/me/searches/{id}/evidence. A saved result reopens with the same evidence as its run gave, from the
saved row alone, with no Reddit words (D-53); only for its owner. The example result and its made-up claims; the
in-memory account store; tokens signed locally. tests/integration/test_saved_evidence_in_postgres.py does the same
on a real Postgres with the recorded runs.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import pytest

from app.api.account import ACCOUNT_UNAVAILABLE, NOT_FOUND, get_account_store
from app.api.evidence import (
    NO_PRODUCT,
    NO_VALUE,
    QUOTE_WITHHELD,
    SAVED_BEFORE,
    EvidenceUnavailable,
    evidence_for,
    saved_evidence_for,
)
from app.contracts import Citation, EvidenceResponse, SearchResult, ValueState
from app.contracts import examples as ex
from app.db import NullAccountStore
from app.research.retention import REDDIT_TEXT_NOT_KEPT, without_reddit_text
from tests.unit.api.test_account_routes import A, B, World

RESULT = ex.search_result()
CLAIMS = {c.id: c for c in ex.claims()}
SAVED_ROW = without_reddit_text(RESULT.model_dump(mode="json"))  # as DatabaseResultStore.save writes it
REDDIT = {s.id for s in RESULT.sources if s.data_class.value == "reddit"}


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = World(monkeypatch)
    yield built
    built.close()


def _path(search_id: Any, product_id: str, criterion_key: str) -> str:
    return f"/api/me/searches/{search_id}/evidence?product_id={product_id}&criterion_key={criterion_key}"


def _values() -> list[tuple[str, str, ValueState]]:
    return [(p.product.id, v.criterion_key, v.state) for p in RESULT.products for v in p.values]


def _as_saved(answer: EvidenceResponse) -> dict[str, Any]:
    """The run's answer as a saved row can give it: a Reddit claim's quote withheld, its text value the marker."""
    body = answer.model_dump(mode="json")
    for claim in body["claims"]:
        if claim["source"]["id"] in REDDIT:
            claim["quote"], claim["unit"] = None, None
            if isinstance(claim["value"], str):
                claim["value"] = REDDIT_TEXT_NOT_KEPT
    withheld = any(claim["quote"] is None for claim in body["claims"])
    body["quote_withheld_reason"] = QUOTE_WITHHELD if withheld else None
    return body


def test_every_value_reopens_with_the_same_evidence_its_run_gave(world: World) -> None:
    saved = world.save(A, result=SAVED_ROW)
    checked = 0
    for product_id, key, _ in _values():
        try:
            run = evidence_for(RESULT, CLAIMS, product_id, key)
        except EvidenceUnavailable as unavailable:
            assert world.get(_path(saved, product_id, key), A).status_code == unavailable.status_code
            continue
        reply = world.get(_path(saved, product_id, key), A)
        assert reply.status_code == 200, (product_id, key)
        assert reply.json() == _as_saved(run)
        checked += 1
    assert checked > 0


def test_a_disputed_value_reopens_with_both_sides(world: World) -> None:
    saved = world.save(A, result=SAVED_ROW)
    product_id, key, _ = next(v for v in _values() if v[2] is ValueState.DISPUTED)
    body = EvidenceResponse.model_validate(world.get(_path(saved, product_id, key), A).json())
    others = [c for c in body.claims if c.claim_id not in body.winning_claim_ids]
    assert body.state is ValueState.DISPUTED and body.winning_claim_ids and others


def test_an_estimated_value_names_no_claim_of_its_own_and_says_estimated(world: World) -> None:
    saved = world.save(A, result=SAVED_ROW)
    product_id, key, _ = next(v for v in _values() if v[2] is ValueState.ESTIMATED)
    body = EvidenceResponse.model_validate(world.get(_path(saved, product_id, key), A).json())
    assert body.state is ValueState.ESTIMATED
    assert body.winning_claim_ids == []


def test_a_reddit_claim_reopens_with_its_source_and_time_and_no_words(world: World) -> None:
    saved = world.save(A, result=SAVED_ROW)
    for product_id, key, _ in _values():
        reply = world.get(_path(saved, product_id, key), A)
        if reply.status_code != 200:
            continue
        body = EvidenceResponse.model_validate(reply.json())
        for claim in body.claims:
            if claim.source.id in REDDIT:
                assert claim.quote is None and body.quote_withheld_reason == QUOTE_WITHHELD
                assert claim.retrieved_at and claim.source.fetched_at and str(claim.source.url)
                assert not isinstance(claim.value, str) or claim.value == REDDIT_TEXT_NOT_KEPT
                return
    pytest.fail("the example result cites no Reddit claim")


def test_another_users_saved_search_a_made_up_id_and_a_guest_get_nothing(world: World) -> None:
    saved = world.save(A, result=SAVED_ROW)
    product_id, key, _ = _values()[0]
    for asked in (world.get(_path(saved, product_id, key), B), world.get(_path("not-an-id", product_id, key), A)):
        assert (asked.status_code, asked.json()["detail"]) == (404, NOT_FOUND)
    guest = world.save(None, result=SAVED_ROW)
    assert world.get(_path(guest, product_id, key), A).status_code == 404  # a guest's row is never anyone's
    assert world.get(_path(saved, product_id, key)).status_code == 401  # signed out


def test_a_result_saved_before_this_change_reopens_with_what_it_has_and_says_the_rest_is_unavailable(
    world: World,
) -> None:
    old = dict(SAVED_ROW)
    old["citations"] = [{k: c[k] for k in ("claim_id", "source_id", "quote")} for c in SAVED_ROW["citations"]]
    saved = world.save(A, result=old)
    reopened = world.get(f"/api/me/searches/{saved}", A)
    assert reopened.status_code == 200
    assert [c["quote"] for c in reopened.json()["citations"]] == [c["quote"] for c in old["citations"]]
    product_id, key, _ = next(v for v in _values() if v[2] is ValueState.KNOWN)
    answer = world.get(_path(saved, product_id, key), A)
    assert (answer.status_code, answer.json()["detail"]) == (404, SAVED_BEFORE)


def test_a_result_saved_before_citations_existed_says_so_too(world: World) -> None:
    """A row from before 1.7.0 has no citations at all: its values' evidence says it was saved before, not that the
    sources are gone."""
    saved = world.save(A, result={**SAVED_ROW, "citations": []})
    product_id, key, _ = next(v for v in _values() if v[2] is ValueState.KNOWN)
    answer = world.get(_path(saved, product_id, key), A)
    assert (answer.status_code, answer.json()["detail"]) == (404, SAVED_BEFORE)


def test_every_ranking_decision_names_the_claims_behind_it_through_the_value_it_read() -> None:
    """A contribution, a checked value and a held product's rule each name a product and a key; the saved row's value
    for them names its claims, and each of those is cited with its source, quote (or none, for Reddit) and time."""
    saved = SearchResult.model_validate(SAVED_ROW)
    assert saved.decision is not None
    values = {(p.product.id, v.criterion_key): v for p in saved.products for v in p.values}
    cited = {c.claim_id: c for c in saved.citations}
    sources = {s.id for s in saved.sources}
    decisions = [
        (r.product_id, c.criterion_key, c.state)
        for r in saved.decision.ranked
        for c in r.contributions
        if c.state in (ValueState.KNOWN, ValueState.DISPUTED)
    ]
    decisions += [
        (v.product_id, v.attribute, v.state)
        for v in saved.decision.checked_values
        if v.state in (ValueState.KNOWN, ValueState.DISPUTED)
    ]
    decisions += [(f.product_id, f.rule_attribute, None) for f in saved.decision.filtered if f.rule_attribute]
    traced = 0
    for product_id, key, _ in decisions:
        value = values.get((product_id, key))
        if value is None or value.state not in (ValueState.KNOWN, ValueState.DISPUTED):
            continue  # a held product's rule on a value it doesn't have: "can't tell", with no claim to name
        claim_ids = [*value.supporting_claim_ids, *value.conflicting_claim_ids]
        assert claim_ids, (product_id, key)
        for claim_id in claim_ids:
            citation = cited[claim_id]
            assert citation.source_id in sources and citation.retrieved_at is not None
        traced += 1
    assert traced > 0


def test_a_runs_citations_keep_what_each_claim_said_and_when() -> None:
    """The pipeline fills each citation from its claim (US-29): the made-up live-shaped run, with every claim kept."""
    from app.api.evidence import ClaimRecorder
    from app.contracts import SearchRequest
    from app.pipeline import InMemoryResultStore, PipelineRunner
    from tests.unit.test_pipeline_live_decide import live_stages

    stages = live_stages()
    stages.extract = recorder = ClaimRecorder(stages.extract)
    request = SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=ex.rubric())
    result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    claims = recorder.claims()
    assert result.citations
    for citation in result.citations:
        claim = claims[citation.claim_id]
        assert (citation.value, citation.unit, citation.score, citation.polarity, citation.claim_type) == (
            claim.value,
            claim.unit,
            claim.score,
            claim.polarity,
            claim.claim_type,
        )
        assert citation.retrieved_at == claim.retrieved_at


def test_a_saved_row_that_no_longer_reads_as_a_result_answers_404_and_logs_no_row_text(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    saved = world.save(A, result={"made_up_field": "Secret row words 7Q2"})
    product_id, key, _ = _values()[0]
    with caplog.at_level(logging.WARNING, logger="app.api.account"):
        answer = world.get(_path(saved, product_id, key), A)
    assert (answer.status_code, answer.json()["detail"]) == (404, NOT_FOUND)
    assert "could not be read back as a result" in caplog.text
    assert "Secret row words" not in caplog.text  # the type only, never the row's text


def test_an_unknown_product_or_criterion_answers_404_naming_which(world: World) -> None:
    saved = world.save(A, result=SAVED_ROW)
    product_id, key, _ = _values()[0]
    no_product = world.get(_path(saved, "prd_made_up_none", key), A)
    assert (no_product.status_code, no_product.json()["detail"]) == (404, NO_PRODUCT)
    no_value = world.get(_path(saved, product_id, "made_up_criterion"), A)
    assert (no_value.status_code, no_value.json()["detail"]) == (404, NO_VALUE)


def _two_supporting() -> tuple[SearchResult, str, str, str, str]:
    """The saved example, with the disputed value resting on two claims: its own, and another cited claim. Built
    with model_copy, past validation: a row missing a citation or a source can't be read as a result otherwise, and
    these tests are for what the code does if one ever does."""
    saved = SearchResult.model_validate(SAVED_ROW)
    product_id, key, _ = next(v for v in _values() if v[2] is ValueState.DISPUTED)
    summary = next(p for p in saved.products if p.product.id == product_id)
    value = next(v for v in summary.values if v.criterion_key == key)
    taken = {*value.supporting_claim_ids, *value.conflicting_claim_ids}
    extra = next(c.claim_id for c in saved.citations if c.claim_id not in taken)
    wider = value.model_copy(update={"supporting_claim_ids": [*value.supporting_claim_ids, extra]})
    values = [wider if v is value else v for v in summary.values]
    products = [s.model_copy(update={"values": values}) if s is summary else s for s in saved.products]
    return saved.model_copy(update={"products": products}), product_id, key, value.supporting_claim_ids[0], extra


def test_a_cited_claim_whose_citation_or_source_is_missing_from_the_row_is_left_out() -> None:
    result, product_id, key, kept, extra = _two_supporting()
    whole = saved_evidence_for(result, product_id, key)
    assert {kept, extra} <= set(whole.winning_claim_ids)
    no_citation = result.model_copy(update={"citations": [c for c in result.citations if c.claim_id != extra]})
    assert saved_evidence_for(no_citation, product_id, key).winning_claim_ids == [kept]
    moved = [
        c.model_copy(update={"source_id": "src_" + "f" * 16}) if c.claim_id == extra else c for c in result.citations
    ]
    no_source = result.model_copy(update={"citations": moved})
    assert saved_evidence_for(no_source, product_id, key).winning_claim_ids == [kept]


def test_a_saved_citation_the_evidence_contract_refuses_is_left_out_with_one_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result, product_id, key, kept, extra = _two_supporting()
    citation = next(c for c in result.citations if c.claim_id == extra)
    broken = Citation.model_construct(  # past validation: a value longer than any claim's may be (200 characters)
        **{**dict(citation), "value": "x" * 300, "score": None}
    )
    citations = [broken if c.claim_id == extra else c for c in result.citations]
    with caplog.at_level(logging.WARNING, logger="app.api.evidence"):
        answer = saved_evidence_for(result.model_copy(update={"citations": citations}), product_id, key)
    assert answer.winning_claim_ids == [kept]
    assert [r.getMessage() for r in caplog.records] == ["a saved claim was left out of the evidence: ValidationError"]


def test_the_database_down_answers_503(world: World) -> None:
    world.app.dependency_overrides[get_account_store] = NullAccountStore
    product_id, key, _ = _values()[0]
    answer = world.get(_path("a0000000-0000-4000-8000-000000000001", product_id, key), A)
    assert (answer.status_code, answer.json()["detail"]) == (503, ACCOUNT_UNAVAILABLE)


def test_the_saved_row_is_a_valid_result_with_every_citation_read() -> None:
    reopened = SearchResult.model_validate(SAVED_ROW)
    assert reopened.citations and all(c.retrieved_at and c.polarity and c.claim_type for c in reopened.citations)
