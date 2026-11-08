"""A failing stage's error text never leaves the server (US-12 review).

An HTTP error's message can hold the request URL, and some APIs take the key in the query string; other errors can
hold what the shopper typed. Neither may reach the log, StageReport.fallback_reason, the coverage note, the progress
events, the run's state or stream, the saved result, POST /api/search's answer or the CLI's output: each says a fixed
reason instead (app.pipeline.plain_reason). The key and the URL are built at run time, and are made up.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import app.contracts as C
from app.api.search_runs import RunSettings, SearchRuns, get_search_runs
from app.cli import main as cli_main
from app.contracts import examples as ex
from app.main import create_app
from app.pipeline import (
    SERVICE_REFUSED,
    SOMETHING_WENT_WRONG,
    InMemoryResultStore,
    PipelineRunner,
    SearchStages,
    default_search_stages,
    new_search_id,
)
from app.research import ResearchStage

FAKE_KEY = "not-a-real-key-" + "7f3a91c2"
PHRASE = "a laptop for my aunt Zorabel's bakery"  # what a shopper might type; the words appear nowhere else


def _url() -> str:
    return str(httpx.URL("https://api.example.test/v1/search", params={"api_key": FAKE_KEY, "q": PHRASE}))


def _http_error() -> Exception:
    request = httpx.Request("GET", _url())
    response = httpx.Response(401, request=request)
    return httpx.HTTPStatusError(
        f"Client error '401 Unauthorized' for url '{_url()}'", request=request, response=response
    )


def _plain_error() -> Exception:
    return RuntimeError(f"could not read the reply to {PHRASE!r} (sent with key {FAKE_KEY})")


class _FailingResearch(ResearchStage):
    def __init__(self, error: Exception, fallback_error: Exception | None = None) -> None:
        super().__init__()
        self.error = error
        self.fallback_error = fallback_error

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        raise self.error

    def fallback(self, inp: C.ResearchInput, ctx: C.StageContext, error: Exception) -> C.ResearchResult:
        if self.fallback_error is not None:
            raise self.fallback_error
        return super().fallback(inp, ctx, error)


def _stages_failing_with(error: Exception, fallback_error: Exception | None = None) -> Callable[[], SearchStages]:
    def build() -> SearchStages:
        stages = default_search_stages()
        stages.research = _FailingResearch(error, fallback_error)
        return stages

    return build


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())


def _search_with(research: Any, caplog: pytest.LogCaptureFixture) -> C.SearchResult:
    stages = default_search_stages()
    stages.research = research
    with caplog.at_level(logging.DEBUG):
        return PipelineRunner(stages, InMemoryResultStore()).run(_request())


def _assert_private(text: str) -> None:
    assert FAKE_KEY not in text
    assert "api_key" not in text
    assert "api.example.test" not in text
    for word in ("Zorabel", "bakery"):  # the phrase, plain or URL-encoded
        assert word not in text


# --- the log ------------------------------------------------------------------------------------


@pytest.mark.parametrize("error", [_http_error(), _plain_error()], ids=["http_error", "plain_error"])
def test_a_failing_stage_logs_its_name_and_error_type_only(error: Exception, caplog: pytest.LogCaptureFixture) -> None:
    assert FAKE_KEY in str(error)  # the message really does carry the key
    result = _search_with(_FailingResearch(error), caplog)
    assert result.stage_reports[0].status is C.StageStatus.FALLBACK  # the search still carried on
    _assert_private(caplog.text)
    assert f"stage research: {type(error).__name__}; using its fallback" in caplog.text


def test_a_failing_fallback_logs_its_type_only(caplog: pytest.LogCaptureFixture) -> None:
    result = _search_with(_FailingResearch(_plain_error(), fallback_error=_http_error()), caplog)
    assert result.partial  # research failed outright, so the search stopped there
    _assert_private(caplog.text)
    assert "stage research: fallback failed too: HTTPStatusError" in caplog.text


# --- every other way out: the result, the events, the saved row, the routes and the CLI --------------


@pytest.mark.parametrize("error", [_http_error(), _plain_error()], ids=["http_error", "plain_error"])
def test_the_result_the_events_and_the_saved_row_say_a_fixed_reason(
    error: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    events: list[C.ProgressEvent] = []
    store = InMemoryResultStore()
    with caplog.at_level(logging.DEBUG):
        result = PipelineRunner(_stages_failing_with(error)(), store).run(
            _request(), search_id=new_search_id(), progress=events.append
        )
    reason = SERVICE_REFUSED if isinstance(error, httpx.HTTPStatusError) else SOMETHING_WENT_WRONG
    assert result.stage_reports[0].fallback_reason == reason
    assert result.coverage_note is not None
    assert f"Research used a simpler fallback: {reason}." in result.coverage_note
    assert [e.status for e in events[:2]] == ["started", "fallback"]
    for text in (caplog.text, result.model_dump_json(), store.results[0].model_dump_json()):
        _assert_private(text)
    for event in events:
        _assert_private(event.model_dump_json())


def test_a_failed_fallback_says_fixed_reasons_too() -> None:
    result = PipelineRunner(_stages_failing_with(_plain_error(), _http_error())(), InMemoryResultStore()).run(
        _request()
    )
    assert result.stage_reports[0].fallback_reason == f"{SOMETHING_WENT_WRONG}; the fallback failed too"
    _assert_private(result.model_dump_json())


def test_the_run_state_the_stream_and_post_search_never_carry_the_error_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    build = _stages_failing_with(_http_error())
    store = InMemoryResultStore()
    runs = SearchRuns(lambda: PipelineRunner(build(), store), RunSettings(ping_s=0.05))
    monkeypatch.setattr("app.main.default_search_stages", build)  # POST /api/search meets the same failing stage
    app = create_app(env={})
    app.dependency_overrides[get_search_runs] = lambda: runs
    body = _request().model_dump(mode="json")
    try:
        with caplog.at_level(logging.DEBUG), TestClient(app) as client:
            search_id = client.post("/api/search/runs", json=body).json()["search_id"]
            with client.stream("GET", f"/api/search/runs/{search_id}/events") as response:
                streamed = "".join(response.iter_text())  # ends once the run has ended
                assert response.status_code == 200
            state = client.get(f"/api/search/runs/{search_id}").text
            answered = client.post("/api/search", json=body).text
    finally:
        runs.close()
    assert f"That step had a problem: {SERVICE_REFUSED}." in streamed  # the progress event names the fixed reason
    assert "event: run" in streamed  # and the final state came through the stream too
    assert SERVICE_REFUSED in state
    assert SERVICE_REFUSED in answered
    for text in (streamed, state, answered, store.results[0].model_dump_json(), caplog.text):
        _assert_private(text)


def test_the_cli_never_prints_the_error_text(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("app.cli.default_search_stages", _stages_failing_with(_http_error()))
    with caplog.at_level(logging.DEBUG):
        assert cli_main(["best laptop for coding under 70k"]) == 0
    out = capsys.readouterr()
    assert f"Research used a simpler fallback: {SERVICE_REFUSED}." in out.out
    _assert_private(out.out + out.err + caplog.text)


# --- the pre-search stages, which run through the same run_stage -----------------------------------


class _FailingIntake:
    """Intake whose run and fallback both fail with the key and the phrase in their messages."""

    name = C.StageName.INTAKE
    is_placeholder = False

    def __init__(self, llm: Any) -> None:
        self.llm = llm

    def run(self, inp: C.IntakeInput, ctx: C.StageContext) -> C.Intent:
        raise _http_error()

    def fallback(self, inp: C.IntakeInput, ctx: C.StageContext, error: Exception) -> C.Intent:
        raise _plain_error()


def test_a_failing_pre_search_stage_never_carries_the_error_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.presearch import start_request

    monkeypatch.setattr("app.presearch.IntakeStage", _FailingIntake)
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as raised:
        start_request("best laptop for coding under 70k")
    assert str(raised.value) == f"intake failed and so did its fallback: {SERVICE_REFUSED}; the fallback failed too"
    assert raised.value.__cause__ is None and raised.value.__context__ is None  # no error text chained behind it
    _assert_private(str(raised.value) + caplog.text)

    app = create_app(env={})
    with TestClient(app, raise_server_exceptions=False) as client:
        answer = client.post("/api/presearch/intake", json={"text": "best laptop for coding under 70k"})
    assert answer.status_code == 500
    _assert_private(answer.text)
