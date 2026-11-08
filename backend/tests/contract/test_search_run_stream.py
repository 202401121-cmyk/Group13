"""What the search-run routes send keeps to P1's contracts (US-12): ProgressEvent, SearchRun and SearchRunStarted.

These run the real stages through the real routes, the way the frontend will see them.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app.contracts as C
from app.api.search_runs import RunSettings, SearchRuns, get_search_runs
from app.contracts import examples as ex
from app.contracts.pipeline import MAX_RUN_EVENTS
from app.main import create_app
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages


@pytest.fixture
def client() -> Iterator[TestClient]:
    runs = SearchRuns(lambda: PipelineRunner(default_search_stages(), InMemoryResultStore()), RunSettings(ping_s=0.05))
    app = create_app(env={})
    app.dependency_overrides[get_search_runs] = lambda: runs
    with TestClient(app) as built:
        yield built
    runs.close()


def _finished_run(client: TestClient) -> tuple[str, str]:
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())
    started = C.SearchRunStarted.model_validate_json(
        client.post("/api/search/runs", json=request.model_dump(mode="json")).text
    )
    with client.stream("GET", f"/api/search/runs/{started.search_id}/events") as response:
        text = "".join(response.iter_text())  # the stream ends once the run has ended
    return started.search_id, text


def _events(text: str) -> list[dict[str, str]]:
    messages = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if line and not line.startswith(":"))
        if fields:
            messages.append(fields)
    return messages


def test_every_streamed_progress_event_is_a_valid_progress_event(client: TestClient) -> None:
    search_id, text = _finished_run(client)
    progress = [m for m in _events(text) if m["event"] == "progress"]
    assert progress
    for message in progress:
        event = C.ProgressEvent.model_validate_json(message["data"])
        assert event.search_id == search_id
        assert message["id"] == str(event.event_id)


def test_the_final_run_event_and_the_state_route_are_valid_search_runs(client: TestClient) -> None:
    search_id, text = _finished_run(client)
    final = _events(text)[-1]
    assert final["event"] == "run"
    streamed = C.SearchRun.model_validate_json(final["data"])
    fetched = C.SearchRun.model_validate_json(client.get(f"/api/search/runs/{search_id}").text)
    assert streamed == fetched
    assert fetched.status is C.SearchRunStatus.DONE
    assert fetched.result is not None and fetched.result.search_id == search_id
    assert final["id"] == str(len(fetched.progress) + 1)


def test_a_run_never_exceeds_max_run_events(client: TestClient) -> None:
    search_id, _ = _finished_run(client)
    run = C.SearchRun.model_validate_json(client.get(f"/api/search/runs/{search_id}").text)
    assert len(run.progress) == 16 <= MAX_RUN_EVENTS  # eight search stages, each started and finished
