"""US-50: one test per row of the plan's worst-case table (project_docs/11_RISKS_WORST_CASE_AND_CONFLICTS.md, 15 rows).

Each test is named after its row and carries the row's own words for what the user sees. Where a fault test already
proves the row, this test runs that test (linked, never copied), and adds what the user sees when the proof doesn't say
it. docs/drills.md lists each row, its test and its state.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
import uvicorn

import app.contracts as C
from app.contracts import Provider
from app.decide import decide
from app.extract import GuardedExtractStage
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.limits import DailyTokenBudget
from app.pipeline import (
    LLM_UNAVAILABLE_NOTE,
    InMemoryResultStore,
    PipelineRunner,
    SearchStages,
    default_search_stages,
    run_stage,
)
from tests.fakes.llm import FakeAdapter
from tests.faults.interview import test_adaptive_fallback as adaptive_fallback
from tests.faults.interview.test_adaptive_fallback import (
    test_run_raises_and_the_fixed_fallback_answers as proof_interview_asks_its_fixed_questions,
)
from tests.faults.llm.test_provider_limits import (
    test_intake_falls_back_to_its_rules_when_every_private_budget_is_spent as proof_intake_takes_its_rules,
)
from tests.faults.research.test_source_faults import (
    test_one_page_failing_while_others_are_read_names_nothing as proof_one_failed_page_names_nothing,
)
from tests.faults.research.test_source_faults import test_serper_raising_never_stops_reddit as proof_search_down
from tests.faults.test_database_drills import database_at_closed_port, stuck_store  # noqa: F401 (proofs' fixtures)
from tests.faults.test_database_drills import (
    test_drill_1_the_api_intake_search_and_health_finish_when_the_database_is_down as proof_database_down,
)
from tests.faults.test_database_drills import (
    test_drill_2_each_save_gives_up_in_time_and_the_breaker_then_skips_at_once as proof_database_asleep,
)
from tests.faults.test_hostile_pages import test_each_hostile_page_and_its_twin_give_identical_results as proof_hostile
from tests.faults.test_outside_failures import (
    test_every_provider_failing_sets_llm_unavailable_and_says_so_first as proof_every_provider_down,
)
from tests.faults.test_outside_failures import (
    test_one_source_type_down_still_gives_results_with_a_note as proof_one_source_down,
)
from tests.faults.test_pipeline_fallback import (
    test_a_store_that_raises_only_marks_the_result_unsaved as proof_result_unsaved,
)
from tests.faults.test_pipeline_fallback import (
    test_failing_research_falls_back_and_the_search_finishes as proof_nothing_found,
)
from tests.faults.test_rate_limit_drill import (
    test_the_p18_drill_finishes_with_no_extraction_note as proof_one_rate_limited,
)
from tests.faults.test_result_save_faults import clock  # noqa: F401 (a fixture the proof uses)
from tests.faults.test_result_save_faults import (
    test_a_search_cut_off_at_its_150_seconds_is_still_saved_after_them as proof_cut_off_search_is_saved,
)
from tests.golden.extract.test_extraction_parsing import (
    test_anything_else_retries_once_on_each_model_then_drops_the_source as proof_broken_json,
)
from tests.unit import test_pipeline_extract as pipeline_extract
from tests.unit.api.test_evidence_route import body
from tests.unit.decide.test_filter import RULE_RAM, A, B, decide_input, known, product, rubric, unknown
from tests.unit.decide.test_filter import C as CHARLIE
from tests.unit.decide.test_filter import (
    test_when_nothing_passes_the_hint_names_the_rule_that_lets_most_through as proof_relaxation_hint,
)
from tests.unit.entities.test_model_matcher import test_matcher_force_fail_falls_back_to_fuzzy as proof_matcher
from tests.unit.explain.test_stage import (
    test_without_the_model_only_sentences_that_quote_their_evidence_stay_with_one_log_line as proof_nli,
)
from tests.unit.llm.test_client import test_a_spent_daily_budget_skips_the_provider_and_is_logged as proof_budget
from tests.unit.memory.test_service import test_the_model_failing_gives_an_empty_list_and_one_log_line as proof_memory
from tests.unit.research.test_page_readers import (
    test_both_readers_failing_skip_the_page_with_a_note as proof_readers_fail,
)
from tests.unit.research.test_youtube_stage import (
    test_youtube_failing_leaves_the_search_going_and_the_note_says_so as proof_youtube_down,
)
from tests.unit.test_pipeline_timeout import (
    test_running_out_of_search_time_skips_the_remaining_stages as proof_out_of_time,
)
from tests.worst_case.fake_stack import build_app, stub_stages


def _cases(proof: Any) -> list[tuple[Any, ...]]:
    """The cases a parametrized proof runs, in its own order, each as the arguments it takes."""
    (mark,) = [m for m in proof.pytestmark if m.name == "parametrize"]
    names = mark.args[0] if isinstance(mark.args[0], (list, tuple)) else str(mark.args[0]).split(",")
    cases = []
    for case in mark.args[1]:
        values = case.values if hasattr(case, "values") else case  # pytest.param(...) or a plain value
        cases.append(tuple(values) if len(names) > 1 or hasattr(case, "values") else (values,))
    return cases


def test_one_llm_provider_down_or_rate_limited() -> None:
    """What the user sees: "Nothing, or a slightly slower search".

    The provider rate-limited is skipped and the next answers: the search ends with no note about it."""
    proof_one_rate_limited()


def test_every_llm_provider_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """What the user sees: "A cached result if one exists; otherwise the interview still runs with fixed questions and
    research shows a clear “providers unavailable, try again” message with a retry button".

    No result cache exists yet (US-34): the note says the AI services were all unavailable (the results page shows it
    with "Try again"), intake keeps to its keyword rules, and the interview asks its fixed questions."""
    proof_every_provider_down()
    proof_intake_takes_its_rules()
    # The proof's StageContext is made when its module is imported, and a context's search time (150 s) runs from
    # then. Its own file runs early in the suite; here, near the end, it would have no time left, so it gets fresh time.
    monkeypatch.setattr(adaptive_fallback, "CTX", C.StageContext(search_id="srch_fault00004"))
    proof_interview_asks_its_fixed_questions(monkeypatch)


def test_the_daily_token_budget_used_up() -> None:
    """What the user sees: "Cached and pre-warmed results; new searches get a clear message".

    A provider whose day is spent is skipped before any call, and the next one answers; with every budget spent,
    intake keeps to its rules and a search gets the "AI services unavailable" note (no result cache yet, US-34)."""
    proof_budget()
    proof_intake_takes_its_rules()
    config = load_llm_config()
    adapters = {provider: FakeAdapter(provider) for provider in Provider}
    spent = DailyTokenBudget({Provider(p.name): 0 for p in config.providers})  # every provider's day is used up
    client = WrapperLLMClient(config, dict(adapters), InMemoryCallLog(), budget=spent, limiters={})
    pages = [pipeline_extract._page("a"), pipeline_extract._page("b")]
    result, _ = pipeline_extract._search(GuardedExtractStage(client), *pages)
    assert result.llm_unavailable is True and (result.coverage_note or "").startswith(LLM_UNAVAILABLE_NOTE)
    assert all(adapter.calls == [] for adapter in adapters.values())  # nothing was sent past a spent budget


def test_reddit_youtube_or_search_down() -> None:
    """What the user sees: "Results from the other sources, with a lower coverage figure and a note".

    The search ends with results from the other sources, and the coverage note names the source that failed: Reddit,
    web search, or YouTube in each way it can fail."""
    proof_one_source_down()
    proof_search_down()
    for case in _cases(proof_youtube_down):
        proof_youtube_down(*case)


def test_a_page_blocks_the_reader() -> None:
    """What the user sees: "Nothing".

    One page failing while others are read names nothing; a page both readers fail is skipped with a note for the
    run's log, never a stop."""
    proof_one_failed_page_names_nothing()
    proof_readers_fail()


def test_the_llm_returns_broken_json() -> None:
    """What the user sees: "Nothing".

    Each broken reply is retried once on each model and then the source is dropped; the search goes on."""
    for case in _cases(proof_broken_json):
        proof_broken_json(*case)


def test_the_matcher_fails_to_load(caplog: pytest.LogCaptureFixture) -> None:
    """What the user sees: "Nothing visible; logged".

    Matching falls back to fuzzy names, the same result, with one warning in the log."""
    proof_matcher(caplog)


def test_the_nli_checker_fails(caplog: pytest.LogCaptureFixture) -> None:
    """What the user sees: "Explanations use only sentences that quote evidence directly".

    The model failing, not tuned, past its time or not pinned: only the sentences that quote their evidence stay,
    with one log line."""
    for make, reason in _cases(proof_nli):
        caplog.clear()
        proof_nli(make, reason, caplog)


def test_embeddings_fail(caplog: pytest.LogCaptureFixture) -> None:
    """What the user sees: "No pre-filled answers this time".

    Memory gives no remembered answer, with one log line; the interview goes on unchanged."""
    proof_memory(caplog)


@pytest.mark.usefixtures("database_at_closed_port")
def test_the_database_down_or_asleep(request: pytest.FixtureRequest) -> None:
    """What the user sees: "The search still works; a banner says results will not be saved".

    Intake, the search and health all finish with the database at a closed port: the result says it isn't saved
    (the results page then shows "This result isn't saved"), and health says the database is down. Asleep (a database
    that takes the call and never answers): each save gives up in time, and the breaker then skips it at once."""
    proof_database_down()
    proof_result_unsaved()  # saved: false, the flag the banner reads
    proof_database_asleep(request.getfixturevalue("stuck_store"))


def test_no_product_passes_the_hard_rules() -> None:
    """What the user sees: "The “can’t tell” group, plus the one rule whose relaxation would help most".

    With nothing passing, the hint names the one change that lets the most products through, and a product whose
    price can't be read is held in the "can't tell" group."""
    proof_relaxation_hint()
    rules = rubric(budget_max=70000, rules=[RULE_RAM])
    items = [(A, "Alpha One", 80000), (B, "Bravo Two", None), (CHARLIE, "Charlie 3", 90000)]
    values = []
    for pid, _, price in items:
        values += [known(pid, "battery_life", score=7.0), known(pid, "ram_gb", value=16)]
        values.append(known(pid, "price_inr", value=price) if price is not None else unknown(pid, "price_inr"))
    decision = decide(decide_input(rules, [product(pid, name) for pid, name, _ in items], values))
    assert decision.ranked == [] and decision.relaxation_hint
    assert {f.product_id: f.group for f in decision.filtered}[B] == "cant_tell"


def test_nothing_found_at_all() -> None:
    """What the user sees: "An honest “not enough evidence” with the criteria and search links; no invented
    products".

    Research failing still ends the search: no source, no invented product, and a note saying why."""
    proof_nothing_found()
    stages = default_search_stages()  # the real stages: the stubs make up example products, labelled a sample
    stages.research = _Failing(stages.research)
    result = PipelineRunner(stages, InMemoryResultStore()).run(_request())
    assert result.sources == [] and result.products == []  # nothing found, and nothing made up
    assert result.decision is None or result.decision.ranked == []
    assert result.coverage_note


def test_the_search_passes_150_seconds(request: pytest.FixtureRequest) -> None:
    """What the user sees: "The best result so far, marked partial".

    Past the search's time the remaining stages are skipped and the result says it is partial; it is still saved."""
    proof_out_of_time()
    proof_cut_off_search_is_saved(request.getfixturevalue("clock"))


class _SlowResearch:
    """Research's stub, slow enough that the run is still going when the browser lets go of its progress."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def run(self, inp: Any, ctx: Any) -> Any:
        time.sleep(1.5)
        return self.inner.run(inp, ctx)


class _Failing:
    """A stage whose run raises: its fallback answers."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def run(self, inp: Any, ctx: Any) -> Any:
        raise RuntimeError("made-up research failure")


def _request() -> C.SearchRequest:
    from app.contracts import examples as ex

    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=ex.rubric())


def _slow_stages() -> SearchStages:
    stages = stub_stages()
    stages.research = _SlowResearch(stages.research)
    return stages


@contextmanager
def _serving(app: Any) -> Iterator[str]:
    """The app on a real server on this machine, so a client can truly go away mid-stream (the test client can't)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)


def test_the_browser_disconnects() -> None:
    """What the user sees: "The search keeps running; reopening it shows the result".

    The progress stream is dropped after its first event while the search is still running: the run goes on to the
    end, and asking for it again gives the finished result."""
    with _serving(build_app(stages=_slow_stages)) as base, httpx.Client(base_url=base, timeout=15) as client:
        search_id = client.post("/api/search/runs", json=body()).json()["search_id"]
        with client.stream("GET", f"/api/search/runs/{search_id}/events") as events:
            first = next(line for line in events.iter_lines() if line)
            still = client.get(f"/api/search/runs/{search_id}").json()
        assert first and still["finished_at"] is None  # the browser went away while the search ran
        deadline = time.monotonic() + 30
        while not client.get(f"/api/search/runs/{search_id}").json()["finished_at"] and time.monotonic() < deadline:
            time.sleep(0.1)
        reopened = C.SearchRun.model_validate_json(client.get(f"/api/search/runs/{search_id}").text)
        assert reopened.status is C.SearchRunStatus.DONE
        assert reopened.result is not None and reopened.result.products


def test_a_page_hides_instructions_for_the_model() -> None:
    """What the user sees: "Nothing; the ranking is unchanged".

    Each of the hostile pages gives the same claims, decision and ranking as its clean twin."""
    for case in _cases(proof_hostile):
        proof_hostile(*case)


def test_the_interview_proof_gets_its_own_time_however_late_it_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The interview proof's context is made when its module is imported. Late in a long suite (minutes on the Windows
    runner) that context's 150 s are spent, and its stage would time out at once: the row's test gives it fresh time."""
    aged = C.StageContext(search_id="srch_fault00004", started_at=time.monotonic() - 200)
    monkeypatch.setattr(adaptive_fallback, "CTX", aged)
    time_given: list[float] = []

    def timed_run_stage(stage: Any, inp: Any, ctx: C.StageContext, limit_s: float) -> Any:
        time_given.append(min(limit_s, ctx.seconds_left))
        return run_stage(stage, inp, ctx, limit_s)

    monkeypatch.setattr(adaptive_fallback, "run_stage", timed_run_stage)
    test_every_llm_provider_down(monkeypatch)
    assert time_given == [adaptive_fallback.LIMIT]  # the stage's whole limit, not the 0 s an aged context leaves


ROWS = (
    "test_one_llm_provider_down_or_rate_limited",
    "test_every_llm_provider_down",
    "test_the_daily_token_budget_used_up",
    "test_reddit_youtube_or_search_down",
    "test_a_page_blocks_the_reader",
    "test_the_llm_returns_broken_json",
    "test_the_matcher_fails_to_load",
    "test_the_nli_checker_fails",
    "test_embeddings_fail",
    "test_the_database_down_or_asleep",
    "test_no_product_passes_the_hard_rules",
    "test_nothing_found_at_all",
    "test_the_search_passes_150_seconds",
    "test_the_browser_disconnects",
    "test_a_page_hides_instructions_for_the_model",
)


def test_every_row_of_the_table_has_its_test_with_what_the_user_sees() -> None:
    tests = {name: obj for name, obj in globals().items() if name.startswith("test_") and name != __name__}
    assert len(ROWS) == 15 and all(name in tests for name in ROWS)
    assert all('What the user sees: "' in (tests[name].__doc__ or "") for name in ROWS)
