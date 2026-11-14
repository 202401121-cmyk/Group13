"""The sources behind one value (US-21, S3-11): GET /api/search/runs/{search_id}/evidence.

A run keeps, in memory, the claims its result's values cite, and the route answers them with their sources as an
EvidenceResponse. The searches run through the stub stages (tests/conftest.py), whose result has known, disputed and
unknown values, and cites claims from Reddit and from web pages. Every wait has a deadline, so a bug fails a test
instead of hanging it.
"""

from __future__ import annotations

import copy
import hashlib
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.contracts as C
from app.api.evidence import (
    NO_PRODUCT,
    NO_VALUE,
    SOURCES_GONE,
    ClaimRecorder,
    EvidenceUnavailable,
    evidence_for,
)
from app.api.search_runs import (
    NO_RESULTS,
    NO_SUCH_SEARCH,
    NOT_FINISHED,
    NOT_FOUND,
    RUN_EXPIRED,
    RecentIds,
    RunSettings,
    SearchRuns,
    get_search_runs,
)
from app.contracts import examples as ex
from app.main import create_app
from app.pipeline import InMemoryResultStore, PipelineRunner, SearchStages, default_search_stages
from app.research.retention import without_reddit_text

WAIT_S = 5.0
UNKNOWN_RUN = "srch_" + "0" * 24
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def body() -> dict[str, Any]:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric()).model_dump(mode="json")


class Clock:
    """The registry's clock, which a test can move forward."""

    def __init__(self) -> None:
        self.jumped_s = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.jumped_s


class Gate:
    """Holds a stage until the test opens it; never longer than WAIT_S, so a broken test cannot hang."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.opened = threading.Event()


class HeldResearch:
    """The stub research stage, held at a gate."""

    def __init__(self, real: Any, gate: Gate) -> None:
        self.real = real
        self.name = real.name
        self.is_placeholder = getattr(real, "is_placeholder", False)
        self.gate = gate

    def run(self, inp: Any, ctx: C.StageContext) -> Any:
        self.gate.entered.set()
        self.gate.opened.wait(WAIT_S)
        return self.real.run(inp, ctx)

    def fallback(self, inp: Any, ctx: C.StageContext, error: Exception) -> Any:
        return self.real.fallback(inp, ctx, error)


def _extra_claim_id(source_id: str) -> str:
    return "clm_" + hashlib.sha256(f"{source_id}|colour".encode()).hexdigest()[:16]


class WithUncitedClaim:
    """The stub extract, plus one claim per source about a criterion the search never asks about, so none is cited."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.name = real.name
        self.is_placeholder = getattr(real, "is_placeholder", False)

    def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> C.ExtractionResult:
        out: C.ExtractionResult = self.real.run(inp, ctx)
        extra = C.Claim(
            id=_extra_claim_id(inp.source.id),
            source_id=inp.source.id,
            source_url=inp.source.url,
            product_mention="Nova Book 14",
            criterion_key="colour_options",
            score=6.0,
            quote="Nova Book 14 comes in three colours.",
            claim_type=C.ClaimType.EXPERIENCE,
            polarity="neutral",
            retrieved_at=T0,
            extracted_at=T0,
        )
        return C.ExtractionResult(source_id=out.source_id, mentions=out.mentions, claims=[*out.claims, extra])

    def fallback(self, inp: C.ExtractInput, ctx: C.StageContext, error: Exception) -> C.ExtractionResult:
        result: C.ExtractionResult = self.real.fallback(inp, ctx, error)
        return result


def stages_with(**changes: Callable[[Any], Any]) -> Callable[[], SearchStages]:
    def build() -> SearchStages:
        stages = default_search_stages()
        for name, change in changes.items():
            setattr(stages, name, change(getattr(stages, name)))
        return stages

    return build


class Harness:
    def __init__(self) -> None:
        self.store = InMemoryResultStore()
        self.registries: list[SearchRuns] = []
        self.gates: list[Gate] = []

    def runs(
        self,
        stages: Callable[[], SearchStages] = default_search_stages,
        keep_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        factory: Callable[[], PipelineRunner] | None = None,
    ) -> SearchRuns:
        def runner() -> PipelineRunner:
            return PipelineRunner(stages(), self.store)

        runs = SearchRuns(factory or runner, RunSettings(workers=2, keep_s=keep_s, ping_s=0.05), clock=clock)
        self.registries.append(runs)
        return runs

    def client(self, runs: SearchRuns) -> TestClient:
        app = create_app(env={})
        app.dependency_overrides[get_search_runs] = lambda: runs
        return TestClient(app)

    def close(self) -> None:
        for gate in self.gates:
            gate.opened.set()
        for runs in self.registries:
            runs.close()


@pytest.fixture
def harness() -> Iterator[Harness]:
    built = Harness()
    yield built
    built.close()


# --- helpers ------------------------------------------------------------------------------------


def wait_until(check: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + WAIT_S
    while not check():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def start(client: TestClient) -> str:
    response = client.post("/api/search/runs", json=body())
    assert response.status_code == 202, response.text
    search_id: str = response.json()["search_id"]
    return search_id


def finished(client: TestClient, search_id: str) -> C.SearchRun:
    def ended() -> bool:
        return bool(client.get(f"/api/search/runs/{search_id}").json()["finished_at"])

    wait_until(ended, "the run to end")
    return C.SearchRun.model_validate_json(client.get(f"/api/search/runs/{search_id}").text)


def result_of(client: TestClient, search_id: str) -> C.SearchResult:
    run = finished(client, search_id)
    assert run.result is not None
    return run.result


def evidence(client: TestClient, search_id: str, product_id: str, criterion_key: str) -> Any:
    query = {"product_id": product_id, "criterion_key": criterion_key}
    return client.get(f"/api/search/runs/{search_id}/evidence", params=query)


def values(result: C.SearchResult, state: C.ValueState | None = None) -> list[C.ResolvedValue]:
    return [v for p in result.products for v in p.values if state is None or v.state is state]


def shown_ids(value: C.ResolvedValue) -> list[str]:
    """What the route shows for a value: supporting, then conflicting; an estimate's conflicting; none if unknown."""
    if value.state is C.ValueState.UNKNOWN:
        return []
    if value.state is C.ValueState.ESTIMATED:
        return list(value.conflicting_claim_ids)
    return list(dict.fromkeys([*value.supporting_claim_ids, *value.conflicting_claim_ids]))


def cited_ids(result: C.SearchResult) -> set[str]:
    return {
        claim_id
        for value in values(result)
        for claim_id in (
            *value.supporting_claim_ids,
            *value.conflicting_claim_ids,
            *(i for alternative in value.alternatives for i in alternative.claim_ids),
        )
    }


EXAMPLE_CLAIMS = {c.id: c for c in ex.claims()}


@pytest.fixture
def done(harness: Harness) -> Iterator[tuple[TestClient, str, C.SearchResult]]:
    """One finished search through the stub stages, and the client that ran it."""
    runs = harness.runs()
    with harness.client(runs) as client:
        search_id = start(client)
        yield client, search_id, result_of(client, search_id)


# --- the contract test --------------------------------------------------------------------------


def test_every_cited_claim_in_the_example_result_is_served(done: tuple[TestClient, str, C.SearchResult]) -> None:
    client, search_id, result = done
    assert result.citations, "the stub stages' result cites its claims"
    served: set[str] = set()
    for summary in result.products:
        for value in summary.values:
            if not (value.supporting_claim_ids or value.conflicting_claim_ids):
                continue
            response = evidence(client, search_id, summary.product.id, value.criterion_key)
            assert response.status_code == 200, response.text
            answer = C.EvidenceResponse.model_validate_json(response.text)
            assert (answer.search_id, answer.product_id, answer.criterion_key) == (
                search_id,
                summary.product.id,
                value.criterion_key,
            )
            assert answer.state is value.state
            by_id = {c.claim_id: c for c in answer.claims}
            for claim_id in shown_ids(value):
                assert claim_id in by_id, f"{claim_id} is cited by {value.criterion_key} but not served"
                assert by_id[claim_id].quote == EXAMPLE_CLAIMS[claim_id].quote  # the exact words
            served |= set(by_id)
    citations = {c.claim_id: c for c in result.citations}
    assert served == set(citations)  # every claim the result cites is served by some value
    assert all(EXAMPLE_CLAIMS[i].quote == c.quote for i, c in citations.items())


# --- one value at a time ------------------------------------------------------------------------


def test_a_known_value_with_one_source(done: tuple[TestClient, str, C.SearchResult]) -> None:
    client, search_id, result = done
    sources = {s.id: s for s in result.sources}
    product_id, value = next(
        (p.product.id, v)
        for p in result.products
        for v in p.values
        if v.state is C.ValueState.KNOWN
        and len(v.supporting_claim_ids) == 1
        and not v.conflicting_claim_ids
        and sources[EXAMPLE_CLAIMS[v.supporting_claim_ids[0]].source_id].site is not None
    )
    claim = EXAMPLE_CLAIMS[value.supporting_claim_ids[0]]
    source = sources[claim.source_id]

    response = evidence(client, search_id, product_id, value.criterion_key)

    assert response.status_code == 200, response.text
    answer = C.EvidenceResponse.model_validate_json(response.text)
    assert answer.state is C.ValueState.KNOWN
    assert answer.winning_claim_ids == [claim.id]
    assert answer.quote_withheld_reason is None
    [shown] = answer.claims
    assert shown.claim_id == claim.id
    assert (shown.quote, shown.value, shown.score, shown.unit) == (claim.quote, claim.value, claim.score, claim.unit)
    assert (shown.polarity, shown.claim_type, shown.retrieved_at) == (
        claim.polarity,
        claim.claim_type,
        claim.retrieved_at,
    )
    assert shown.source == C.EvidenceSource(
        id=source.id,
        url=source.url,
        name=source.site or "",
        type=source.type,
        tier=source.tier,
        fetched_at=source.fetched_at,
    )


def test_a_disputed_value_shows_both_sides(done: tuple[TestClient, str, C.SearchResult]) -> None:
    client, search_id, result = done
    disputed = [(p.product.id, v) for p in result.products for v in p.values if v.state is C.ValueState.DISPUTED]
    assert disputed, "the stub stages' result has a disputed value"
    product_id, value = disputed[0]

    response = evidence(client, search_id, product_id, value.criterion_key)

    assert response.status_code == 200, response.text
    answer = C.EvidenceResponse.model_validate_json(response.text)
    assert answer.state is C.ValueState.DISPUTED
    assert answer.winning_claim_ids == value.supporting_claim_ids
    # Supporting first, then the other side.
    assert [c.claim_id for c in answer.claims] == [*value.supporting_claim_ids, *value.conflicting_claim_ids]
    sources = {s.id: s for s in result.sources}
    for shown in answer.claims:
        source = sources[EXAMPLE_CLAIMS[shown.claim_id].source_id]
        assert shown.source.name == (source.site or source.community)
        assert shown.quote == EXAMPLE_CLAIMS[shown.claim_id].quote


def test_an_unknown_value_has_no_claims(done: tuple[TestClient, str, C.SearchResult]) -> None:
    client, search_id, result = done
    product_id, value = next(
        (p.product.id, v) for p in result.products for v in p.values if v.state is C.ValueState.UNKNOWN
    )

    response = evidence(client, search_id, product_id, value.criterion_key)

    assert response.status_code == 200, response.text
    answer = C.EvidenceResponse.model_validate_json(response.text)
    assert answer.state is C.ValueState.UNKNOWN
    assert (answer.claims, answer.winning_claim_ids, answer.quote_withheld_reason) == ([], [], None)


def test_an_estimated_value_shows_only_the_claims_that_conflicted(done: tuple[TestClient, str, C.SearchResult]) -> None:
    _, _, result = done
    product_id, disputed = next(
        (p.product.id, v) for p in result.products for v in p.values if v.state is C.ValueState.DISPUTED
    )
    estimated = C.ResolvedValue(
        product_id=product_id,
        criterion_key=disputed.criterion_key,
        state=C.ValueState.ESTIMATED,
        score=5.0,
        confidence=0.3,
        conflicting_claim_ids=[*disputed.supporting_claim_ids, *disputed.conflicting_claim_ids],
    )
    changed = _with_value(result, estimated)
    claims = {i: EXAMPLE_CLAIMS[i] for i in estimated.conflicting_claim_ids}

    answer = evidence_for(changed, claims, product_id, disputed.criterion_key)

    assert answer.state is C.ValueState.ESTIMATED
    assert answer.winning_claim_ids == []
    assert [c.claim_id for c in answer.claims] == estimated.conflicting_claim_ids


def test_a_value_whose_claims_were_not_kept_answers_404_never_500(
    done: tuple[TestClient, str, C.SearchResult],
) -> None:
    _, _, result = done
    product_id, disputed = next(
        (p.product.id, v) for p in result.products for v in p.values if v.state is C.ValueState.DISPUTED
    )
    kept = {i: EXAMPLE_CLAIMS[i] for i in cited_ids(result)}
    without_the_other_side = {i: c for i, c in kept.items() if i not in disputed.conflicting_claim_ids}
    without_the_winners = {i: c for i, c in kept.items() if i not in disputed.supporting_claim_ids}
    with_no_sources = C.SearchResult.model_validate({**result.model_dump(), "sources": [], "citations": []})

    for claims, of in (
        (without_the_other_side, result),  # a disputed value with only one side would be invalid
        (without_the_winners, result),  # so would one with no winning claim
        (kept, with_no_sources),  # a claim whose source is not the result's is left out
    ):
        with pytest.raises(EvidenceUnavailable) as raised:
            evidence_for(of, claims, product_id, disputed.criterion_key)
        assert (raised.value.status_code, raised.value.detail) == (404, SOURCES_GONE)


# --- Reddit quotes ------------------------------------------------------------------------------


def test_a_reddit_claims_quote_is_served_from_memory(done: tuple[TestClient, str, C.SearchResult]) -> None:
    client, search_id, result = done
    reddit = {s.id for s in result.sources if s.type is C.SourceType.REDDIT}
    product_id, value, claim = next(
        (p.product.id, v, EXAMPLE_CLAIMS[i])
        for p in result.products
        for v in p.values
        for i in v.supporting_claim_ids
        if v.state is C.ValueState.KNOWN and EXAMPLE_CLAIMS[i].source_id in reddit
    )

    response = evidence(client, search_id, product_id, value.criterion_key)

    assert response.status_code == 200, response.text
    answer = C.EvidenceResponse.model_validate_json(response.text)
    shown = next(c for c in answer.claims if c.claim_id == claim.id)
    assert shown.quote == claim.quote  # from the run's memory
    assert shown.source.type is C.SourceType.REDDIT
    assert answer.quote_withheld_reason is None
    saved = C.SearchResult.model_validate(without_reddit_text(result.model_dump(mode="json")))
    assert next(c for c in saved.citations if c.claim_id == claim.id).quote is None  # the saved copy has none


# --- errors -------------------------------------------------------------------------------------


def test_an_expired_run_answers_404_run_expired(harness: Harness) -> None:
    clock = Clock()
    runs = harness.runs(keep_s=60.0, clock=clock)
    with harness.client(runs) as client:
        search_id = start(client)
        result = result_of(client, search_id)
        product_id, value = next((p.product.id, v) for p in result.products for v in p.values)
        clock.jumped_s = 59.0
        assert evidence(client, search_id, product_id, value.criterion_key).status_code == 200
        clock.jumped_s = 61.0
        response = evidence(client, search_id, product_id, value.criterion_key)
        assert (response.status_code, response.json()) == (404, {"detail": RUN_EXPIRED})
        never = evidence(client, UNKNOWN_RUN, product_id, value.criterion_key)
        assert (never.status_code, never.json()) == (404, {"detail": NO_SUCH_SEARCH})
        state = client.get(f"/api/search/runs/{search_id}")  # the other routes answer as before
        assert (state.status_code, state.json()) == (404, {"detail": NOT_FOUND})


def test_unknown_search_product_and_criterion_answer_404_naming_which(
    done: tuple[TestClient, str, C.SearchResult],
) -> None:
    client, search_id, result = done
    product_id, value = next((p.product.id, v) for p in result.products for v in p.values)

    for asked, detail in (
        ((UNKNOWN_RUN, product_id, value.criterion_key), NO_SUCH_SEARCH),
        (("nope", product_id, value.criterion_key), NO_SUCH_SEARCH),
        ((search_id, "prd_not_in_this_search", value.criterion_key), NO_PRODUCT),
        ((search_id, product_id, "not_a_criterion"), NO_VALUE),
    ):
        response = evidence(client, *asked)
        assert (response.status_code, response.json()) == (404, {"detail": detail}), asked
    assert len({NO_SUCH_SEARCH, NO_PRODUCT, NO_VALUE, RUN_EXPIRED}) == 4


def test_missing_parameters_answer_422(done: tuple[TestClient, str, C.SearchResult]) -> None:
    client, search_id, result = done
    product_id, value = next((p.product.id, v) for p in result.products for v in p.values)
    path = f"/api/search/runs/{search_id}/evidence"

    for params, missing in (
        ({"criterion_key": value.criterion_key}, "product_id"),
        ({"product_id": product_id}, "criterion_key"),
        ({}, "product_id"),
        ({"product_id": "", "criterion_key": value.criterion_key}, "product_id"),  # empty counts as missing
        ({"product_id": product_id, "criterion_key": ""}, "criterion_key"),
    ):
        response = client.get(path, params=params)
        assert response.status_code == 422
        assert list(response.json()) == ["detail"]
        detail = response.json()["detail"]
        assert isinstance(detail, str) and missing in detail  # one plain sentence naming the field
        assert all(sent not in detail for sent in params.values() if sent)  # never what was sent


def test_a_run_still_going_answers_409(harness: Harness) -> None:
    gate = Gate()
    harness.gates.append(gate)
    runs = harness.runs(stages_with(research=lambda real: HeldResearch(real, gate)))
    with harness.client(runs) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        response = evidence(client, search_id, "prd_nova_book_14", "cpu_performance")
        assert (response.status_code, response.json()) == (409, {"detail": NOT_FINISHED})
        gate.opened.set()
        result = result_of(client, search_id)
        product_id, value = next((p.product.id, v) for p in result.products for v in p.values)
        assert evidence(client, search_id, product_id, value.criterion_key).status_code == 200


def test_a_cancelled_run_has_no_results_to_show_sources_for(harness: Harness) -> None:
    gate = Gate()
    harness.gates.append(gate)
    runs = harness.runs(stages_with(research=lambda real: HeldResearch(real, gate)))
    with harness.client(runs) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        assert client.delete(f"/api/search/runs/{search_id}").status_code == 202
        gate.opened.set()
        assert finished(client, search_id).status is C.SearchRunStatus.CANCELLED
        response = evidence(client, search_id, "prd_nova_book_14", "cpu_performance")
        assert (response.status_code, response.json()) == (404, {"detail": NO_RESULTS})


# --- what the run keeps -------------------------------------------------------------------------


def test_claims_are_kept_only_for_cited_values_and_dropped_with_the_run(harness: Harness) -> None:
    clock = Clock()
    runs = harness.runs(stages_with(extract=WithUncitedClaim), keep_s=60.0, clock=clock)
    with harness.client(runs) as client:
        search_id = start(client)
        result = result_of(client, search_id)
        run = runs._runs[search_id]
        cited = cited_ids(result)
        assert cited and set(run.claims) == cited  # every cited claim, and nothing else
        uncited = {_extra_claim_id(s.id) for s in result.sources}
        assert not uncited & set(run.claims)
        assert all(run.claims[i] == EXAMPLE_CLAIMS[i] for i in cited)

        clock.jumped_s = 61.0
        assert client.get(f"/api/search/runs/{search_id}").status_code == 404
    assert search_id not in runs._runs
    assert run.claims == {}  # dropped with the run


def test_a_shared_runner_is_never_changed(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    shared = PipelineRunner(default_search_stages(), harness.store)
    stages, extract = shared.stages, shared.stages.extract
    wrapped: list[Any] = []

    class Spy(ClaimRecorder):
        def __init__(self, inner: Any) -> None:
            wrapped.append(inner)
            super().__init__(inner)

    monkeypatch.setattr("app.api.search_runs.ClaimRecorder", Spy)
    runs = harness.runs(factory=lambda: shared)
    with harness.client(runs) as client:
        for _ in range(2):
            search_id = start(client)
            result = result_of(client, search_id)
            product_id, value = next(
                (p.product.id, v) for p in result.products for v in p.values if v.state is C.ValueState.KNOWN
            )
            assert evidence(client, search_id, product_id, value.criterion_key).status_code == 200
    assert wrapped == [extract, extract]  # each run wrapped the stage itself, never an earlier run's wrapper
    assert shared.stages is stages and shared.stages.extract is extract


def test_the_run_state_never_exposes_claims(done: tuple[TestClient, str, C.SearchResult]) -> None:
    client, search_id, _ = done
    state = client.get(f"/api/search/runs/{search_id}").json()
    assert set(state) == set(C.SearchRun.model_fields)
    assert "claims" not in state and "claims" not in state["result"]
    with client.stream("GET", f"/api/search/runs/{search_id}/events") as response:
        streamed = "".join(response.iter_text())
    assert '"claims"' not in streamed


def test_a_claim_recorder_passes_everything_else_to_its_stage() -> None:
    class Stage:
        name = C.StageName.EXTRACT
        is_placeholder = True
        baseline = "the stage it replaced"

        def coverage_note(self, search_id: str) -> str:
            return f"note for {search_id}"

    recorder = ClaimRecorder(Stage())  # type: ignore[arg-type]
    assert recorder.name is C.StageName.EXTRACT and recorder.is_placeholder
    assert recorder.coverage_note("srch_abcdef") == "note for srch_abcdef"
    assert recorder.baseline == "the stage it replaced"
    assert getattr(recorder, "ask_for_specs", None) is None
    assert copy.copy(recorder).inner is recorder.inner  # a copy is built without looking for `inner` on itself


def test_a_claim_recorder_keeps_the_claims_of_every_source_read_at_once_and_of_a_fallback() -> None:
    sources = ex.research().sources
    by_source = {s.id: [c for c in ex.claims() if c.source_id == s.id] for s in sources}
    failing = sources[0].id

    class Stage:
        name = C.StageName.EXTRACT

        def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> C.ExtractionResult:
            if inp.source.id == failing:
                raise RuntimeError("this source could not be read")
            return C.ExtractionResult(source_id=inp.source.id, claims=by_source[inp.source.id])

        def fallback(self, inp: C.ExtractInput, ctx: C.StageContext, error: Exception) -> C.ExtractionResult:
            return C.ExtractionResult(source_id=inp.source.id, claims=by_source[inp.source.id])

    recorder = ClaimRecorder(Stage())  # type: ignore[arg-type]
    ctx = C.StageContext(search_id="srch_abcdef", time_limit_s=10.0)
    inputs = [C.ExtractInput(source=s, criterion_keys=["battery_life"], category_label="laptop") for s in sources]

    def read(inp: C.ExtractInput) -> None:
        try:
            recorder.run(inp, ctx)
        except RuntimeError as error:
            recorder.fallback(inp, ctx, error)

    threads = [threading.Thread(target=read, args=(inp,)) for inp in inputs for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_S)
    assert recorder.claims() == {c.id: c for c in ex.claims() if c.source_id in by_source}
    assert by_source[failing], "the failing source's claims came from its fallback"


def test_a_runner_that_cant_be_copied_still_finishes_without_sources(harness: Harness) -> None:
    from types import SimpleNamespace

    def uncopyable() -> PipelineRunner:
        runner = PipelineRunner(default_search_stages(), harness.store)
        runner.stages = SimpleNamespace(**vars(runner.stages))  # type: ignore[assignment]  # not a dataclass
        return runner

    runs = harness.runs(factory=uncopyable)
    with harness.client(runs) as client:
        search_id = start(client)
        assert finished(client, search_id).status is C.SearchRunStatus.DONE  # build rule 9
        result = result_of(client, search_id)
        product_id, value = next(
            (p.product.id, v) for p in result.products for v in p.values if v.state is C.ValueState.KNOWN
        )
        response = evidence(client, search_id, product_id, value.criterion_key)
        assert (response.status_code, response.json()) == (404, {"detail": SOURCES_GONE})


def test_a_value_with_too_many_claims_shows_both_sides_within_the_limit(
    done: tuple[TestClient, str, C.SearchResult],
) -> None:
    _, _, result = done
    product_id, disputed = next(
        (p.product.id, v) for p in result.products for v in p.values if v.state is C.ValueState.DISPUTED
    )
    template = EXAMPLE_CLAIMS[disputed.supporting_claim_ids[0]]

    def many(side: str) -> dict[str, C.Claim]:
        made = {}
        for n in range(150):
            claim_id = "clm_" + hashlib.sha256(f"{side}{n}".encode()).hexdigest()[:16]
            made[claim_id] = C.Claim.model_validate({**template.model_dump(), "id": claim_id})
        return made

    supporting, conflicting = many("for"), many("against")
    value = C.ResolvedValue.model_validate(
        {
            **disputed.model_dump(),
            "supporting_claim_ids": list(supporting),
            "conflicting_claim_ids": list(conflicting),
            "alternatives": [],
        }
    )

    answer = evidence_for(_with_value(result, value), {**supporting, **conflicting}, product_id, value.criterion_key)

    assert len(answer.claims) == 200
    assert answer.winning_claim_ids == list(supporting)[:100]
    assert [c.claim_id for c in answer.claims[100:]] == list(conflicting)[:100]


def test_only_the_newest_forgotten_ids_are_remembered() -> None:
    recent = RecentIds(limit=2)
    for search_id in ("srch_aaaaaa", "srch_bbbbbb", "srch_cccccc"):
        recent.add(search_id)
    assert "srch_aaaaaa" not in recent
    assert "srch_bbbbbb" in recent and "srch_cccccc" in recent


# --- helpers that change a result -----------------------------------------------------------------


def _with_value(result: C.SearchResult, value: C.ResolvedValue) -> C.SearchResult:
    """The result with one value replaced, and its citations left out (they are all or none, so none here)."""
    data = result.model_dump()
    for summary in data["products"]:
        if summary["product"]["id"] == value.product_id:
            summary["values"] = [
                value.model_dump() if v["criterion_key"] == value.criterion_key else v for v in summary["values"]
            ]
    return C.SearchResult.model_validate({**data, "citations": []})
