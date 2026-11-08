"""Search runs with live progress (US-12, P-07): start, follow, resume, cancel and fetch a search by its ID.

The routes run through FastAPI's TestClient, so nothing opens a socket. Older Starlette test clients hand back a stream
only once it has ended, so the tests that need to see events as they happen read the run registry's own stream instead.
Every wait has a deadline, so a bug fails a test instead of hanging it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.contracts as C
from app.api.search_runs import (
    BUSY,
    NOT_FOUND,
    RETRY_AFTER_SECONDS,
    RunSettings,
    SearchRuns,
    default_runner,
    get_search_runs,
)
from app.contracts import examples as ex
from app.contracts.pipeline import MAX_RUN_EVENTS
from app.main import create_app
from app.pipeline import (
    CancelFlag,
    DatabaseResultStore,
    InMemoryResultStore,
    PipelineRunner,
    ProgressCallback,
    SearchStages,
    default_search_stages,
)
from app.research import ResearchStage

RUN_ID = re.compile(r"^srch_[a-f0-9]{24}$")
WAIT_S = 5.0
ALL_EVENTS = 16  # eight stages, each started and done
FINAL_ID = str(ALL_EVENTS + 1)
UNKNOWN = "srch_" + "0" * 24


def body() -> dict[str, Any]:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric()).model_dump(mode="json")


# --- fake stages --------------------------------------------------------------------------------


class Gate:
    """Holds a stage until the test opens it; never longer than WAIT_S, so a broken test cannot hang."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.opened = threading.Event()

    def hold(self) -> None:
        self.entered.set()
        self.opened.wait(WAIT_S)


class Held:
    """A real stage that waits at a gate (or for a fixed time) before it runs."""

    def __init__(self, real: Any, gate: Gate | None = None, seconds: float = 0.0) -> None:
        self.real = real
        self.name = real.name
        self.is_placeholder = getattr(real, "is_placeholder", False)
        self.gate = gate
        self.seconds = seconds

    def run(self, inp: Any, ctx: C.StageContext) -> Any:
        if self.gate is not None:
            self.gate.hold()
        if self.seconds:
            threading.Event().wait(self.seconds)
        return self.real.run(inp, ctx)

    def fallback(self, inp: Any, ctx: C.StageContext, error: Exception) -> Any:
        return self.real.fallback(inp, ctx, error)


class BrokenMatch:
    name = C.StageName.MATCH
    is_placeholder = False

    def run(self, inp: C.MatchInput, ctx: C.StageContext) -> C.MatchResult:
        raise RuntimeError("matcher crashed")

    def fallback(self, inp: C.MatchInput, ctx: C.StageContext, error: Exception) -> C.MatchResult:
        raise RuntimeError("fuzzy matcher crashed too")


def stages_with(**changes: Callable[[Any], Any]) -> Callable[[], SearchStages]:
    """A stage factory: the default stages, with each named one replaced by `change(real_stage)`."""

    def build() -> SearchStages:
        stages = default_search_stages()
        for field, change in changes.items():
            setattr(stages, field, change(getattr(stages, field)))
        return stages

    return build


# --- the app under test -------------------------------------------------------------------------


class Harness:
    def __init__(self) -> None:
        self.store = InMemoryResultStore()
        self.gates: list[Gate] = []
        self.registries: list[SearchRuns] = []

    def gate(self) -> Gate:
        gate = Gate()
        self.gates.append(gate)
        return gate

    def runs(
        self,
        stages: Callable[[], SearchStages] = default_search_stages,
        workers: int = 2,
        ping_s: float = 0.05,
        keep_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        search_time_limit_s: float = C.SEARCH_TIME_LIMIT_S,
    ) -> SearchRuns:
        def runner() -> PipelineRunner:
            return PipelineRunner(stages(), self.store, search_time_limit_s=search_time_limit_s)

        runs = SearchRuns(runner, RunSettings(workers=workers, keep_s=keep_s, ping_s=ping_s), clock=clock)
        self.registries.append(runs)
        return runs

    def app(self, runs: SearchRuns | None = None) -> FastAPI:
        app = create_app(env={})
        chosen = runs or self.runs()
        app.dependency_overrides[get_search_runs] = lambda: chosen
        return app

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


@pytest.fixture
def plain_client(harness: Harness) -> Iterator[TestClient]:
    with TestClient(harness.app()) as client:
        yield client


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


def state(client: TestClient, search_id: str) -> dict[str, Any]:
    response = client.get(f"/api/search/runs/{search_id}")
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def finished(client: TestClient, search_id: str) -> dict[str, Any]:
    wait_until(lambda: state(client, search_id)["finished_at"] is not None, "the run to end")
    return state(client, search_id)


def parse(text: str) -> list[dict[str, str]]:
    """Server-Sent Events as dicts: {"id", "event", "data"} for an event, {"comment"} for a keep-alive."""
    messages = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            key, _, value = line.partition(":")
            fields["comment" if key == "" else key] = value.removeprefix(" ")
        messages.append(fields)
    return messages


def read(client: TestClient, search_id: str, last_event_id: str | None = None) -> tuple[int, list[dict[str, str]]]:
    headers = {} if last_event_id is None else {"Last-Event-ID": last_event_id}
    with client.stream("GET", f"/api/search/runs/{search_id}/events", headers=headers) as response:
        text = "".join(response.iter_text())
        if response.status_code == 200:
            assert response.headers["content-type"].startswith("text/event-stream")
    return response.status_code, parse(text)


def events(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    return [m for m in messages if "comment" not in m]


def ids(messages: list[dict[str, str]]) -> list[str]:
    return [m["id"] for m in events(messages)]


def read_in_thread(
    client: TestClient, search_id: str, into: list[list[dict[str, str]]], last_event_id: str | None = None
) -> threading.Thread:
    """One client over HTTP. Wait for runs.open_streams to know it is attached: older test clients buffer the body."""
    thread = threading.Thread(target=lambda: into.append(read(client, search_id, last_event_id)[1]), daemon=True)
    thread.start()
    return thread


def follow(
    runs: SearchRuns, search_id: str, into: list[Any], on_message: Callable[[Any], None] | None = None
) -> threading.Thread:
    """Read the registry's own stream (an async generator) in a thread, keeping each message as it arrives."""

    async def read_all() -> None:
        async for message in runs.stream(search_id, 0):
            into.append(message)
            if on_message is not None:
                on_message(message)

    thread = threading.Thread(target=lambda: anyio.run(read_all), daemon=True)
    thread.start()
    return thread


# --- starting a run and reading its state --------------------------------------------------------


def test_starting_a_run_answers_202_with_its_id_before_the_first_stage_ends(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(research=lambda real: Held(real, gate)))
    with TestClient(harness.app(runs)) as client:
        response = client.post("/api/search/runs", json=body())
        assert response.status_code == 202
        search_id = response.json()["search_id"]
        assert response.json() == {"search_id": search_id}
        assert RUN_ID.fullmatch(search_id)
        assert response.headers["location"] == f"/api/search/runs/{search_id}"
        assert gate.entered.wait(WAIT_S)
        assert not gate.opened.is_set()  # the answer came while the first stage was still held
        gate.opened.set()
        assert finished(client, search_id)["status"] == "done"


def test_a_bad_run_body_gets_the_plain_422(plain_client: TestClient) -> None:
    bad = {**body(), "raw_query": ""}
    response = plain_client.post("/api/search/runs", json=bad)
    assert response.status_code == 422
    assert response.json() == plain_client.post("/api/search", json=bad).json()  # the same plain sentence
    assert list(response.json()) == ["detail"] and isinstance(response.json()["detail"], str)


def test_a_running_run_has_no_result_yet(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(extract=lambda real: Held(real, gate)))
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        now = state(client, search_id)
        assert now["status"] == "running"
        assert now["started_at"] is not None and now["finished_at"] is None
        assert now["result"] is None
        assert [(e["stage"], e["status"]) for e in now["progress"]] == [
            ("research", "started"),
            ("research", "done"),
            ("extract", "started"),
        ]
        C.SearchRun.model_validate_json(json.dumps(now))
        gate.opened.set()
        finished(client, search_id)


def test_a_finished_run_gives_its_state_and_result(harness: Harness, plain_client: TestClient) -> None:
    search_id = start(plain_client)
    done = finished(plain_client, search_id)
    run = C.SearchRun.model_validate_json(json.dumps(done))
    assert run.status is C.SearchRunStatus.DONE
    assert run.result is not None and run.result.search_id == search_id
    assert [e.event_id for e in run.progress] == list(range(1, ALL_EVENTS + 1))
    assert run.started_at is not None and run.finished_at is not None and run.finished_at >= run.started_at


def test_the_result_is_saved_at_the_end_and_a_cancelled_run_saves_nothing(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(extract=lambda real: Held(real, gate)))
    with TestClient(harness.app(runs)) as client:
        cancelled = start(client)
        assert gate.entered.wait(WAIT_S)
        assert client.delete(f"/api/search/runs/{cancelled}").status_code == 202
        gate.opened.set()
        assert finished(client, cancelled)["status"] == "cancelled"
        kept = start(client)
        assert finished(client, kept)["result"]["saved"] is True
    assert [r.search_id for r in harness.store.results] == [kept]


# --- the event stream ---------------------------------------------------------------------------


def test_the_stream_sends_each_event_with_its_id_then_the_final_run_event_and_ends(plain_client: TestClient) -> None:
    search_id = start(plain_client)
    finished(plain_client, search_id)
    status, messages = read(plain_client, search_id)
    assert status == 200
    sent = events(messages)
    assert ids(messages) == [str(n) for n in range(1, ALL_EVENTS + 2)]
    for message in sent[:-1]:
        assert message["event"] == "progress"
        event = C.ProgressEvent.model_validate_json(message["data"])
        assert str(event.event_id) == message["id"]
        assert event.search_id == search_id
    final = sent[-1]
    assert final["event"] == "run"
    run = C.SearchRun.model_validate_json(final["data"])
    assert run.status is C.SearchRunStatus.DONE
    assert run.model_dump(mode="json") == state(plain_client, search_id)  # the same body as the state route


def test_two_clients_on_one_run_see_the_same_events(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(match=lambda real: Held(real, gate)))
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        over_http: list[list[dict[str, str]]] = []
        readers = [read_in_thread(client, search_id, over_http) for _ in range(2)]
        wait_until(lambda: runs.open_streams == 2, "both clients to be following the run")
        gate.opened.set()  # the rest of the run happens while both are attached
        for reader in readers:
            reader.join(WAIT_S)
            assert not reader.is_alive()
        assert runs.open_streams == 0
    assert len(over_http) == 2
    assert events(over_http[0]) == events(over_http[1])
    assert ids(over_http[0]) == [str(n) for n in range(1, ALL_EVENTS + 2)]


def test_a_late_client_gets_every_event_so_far_then_the_live_ones(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(match=lambda real: Held(real, gate)))
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)  # research and extract are done and match has started: five events so far
        received: list[tuple[str | None, bool]] = []
        backlog = threading.Event()

        def note(message: Any) -> None:
            if message.comment is None:
                received.append((message.id, gate.opened.is_set()))
                if message.id == "5":
                    backlog.set()

        follower = follow(runs, search_id, [], note)
        assert backlog.wait(WAIT_S)
        over_http: list[list[dict[str, str]]] = []
        reader = read_in_thread(client, search_id, over_http)
        wait_until(lambda: runs.open_streams == 2, "the late client over HTTP to attach")
        gate.opened.set()
        for thread in (follower, reader):
            thread.join(WAIT_S)
            assert not thread.is_alive()
    assert [i for i, _ in received] == [str(n) for n in range(1, ALL_EVENTS + 2)]
    assert all(not after for _, after in received[:5])  # the five events so far came at once
    assert all(after for _, after in received[5:])  # the rest arrived live, after the stage was let go
    assert ids(over_http[0]) == [str(n) for n in range(1, ALL_EVENTS + 2)]


def test_last_event_id_resumes_after_that_event(plain_client: TestClient) -> None:
    search_id = start(plain_client)
    finished(plain_client, search_id)
    status, messages = read(plain_client, search_id, last_event_id="4")
    assert status == 200
    assert ids(messages) == [str(n) for n in range(5, ALL_EVENTS + 2)]


def test_last_event_id_resumes_a_run_that_is_still_going(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(match=lambda real: Held(real, gate)))
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        over_http: list[list[dict[str, str]]] = []
        reader = read_in_thread(client, search_id, over_http, last_event_id="3")
        wait_until(lambda: runs.open_streams == 1, "the client to reconnect")
        gate.opened.set()
        reader.join(WAIT_S)
        assert not reader.is_alive()
    assert ids(over_http[0]) == [str(n) for n in range(4, ALL_EVENTS + 2)]


def test_a_reconnect_after_the_last_progress_event_gets_only_the_final_one(plain_client: TestClient) -> None:
    search_id = start(plain_client)
    finished(plain_client, search_id)
    status, messages = read(plain_client, search_id, last_event_id=str(ALL_EVENTS))
    assert status == 200
    assert [(m["id"], m["event"]) for m in events(messages)] == [(FINAL_ID, "run")]


@pytest.mark.parametrize("last_event_id", ["abc", "-1", "4.5", "99", " "])
def test_a_last_event_id_that_is_not_a_known_number_replays_from_the_start(
    plain_client: TestClient, last_event_id: str
) -> None:
    search_id = start(plain_client)
    finished(plain_client, search_id)
    status, messages = read(plain_client, search_id, last_event_id=last_event_id)
    assert status == 200
    assert ids(messages) == [str(n) for n in range(1, ALL_EVENTS + 2)]


def test_a_reconnect_after_the_final_event_gets_204(plain_client: TestClient) -> None:
    search_id = start(plain_client)
    finished(plain_client, search_id)
    response = plain_client.get(f"/api/search/runs/{search_id}/events", headers={"Last-Event-ID": FINAL_ID})
    assert response.status_code == 204  # tells a browser's EventSource to stop reconnecting
    assert response.content == b""


def test_the_stream_sends_a_keep_alive_while_a_long_stage_runs(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(research=lambda real: Held(real, gate)), ping_s=0.02)
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        pinged = threading.Event()
        live: list[Any] = []

        def note(message: Any) -> None:
            if message.comment == "ping":
                pinged.set()

        follower = follow(runs, search_id, live, note)
        assert pinged.wait(WAIT_S)  # a ping came while research was still held
        gate.opened.set()
        follower.join(WAIT_S)
    first_ping = next(i for i, m in enumerate(live) if m.comment == "ping")
    research_done = next(i for i, m in enumerate(live) if m.id == "2")
    assert first_ping < research_done


def test_the_keep_alive_reaches_the_client_over_http(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(research=lambda real: Held(real, gate)), ping_s=0.02)
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        got: list[list[dict[str, str]]] = []
        reader = read_in_thread(client, search_id, got)
        wait_until(lambda: runs.open_streams == 1, "the client to attach")
        time.sleep(0.2)  # ten keep-alive intervals while research is held
        gate.opened.set()
        reader.join(WAIT_S)
    messages = got[0]
    assert {"comment": "ping"} in messages
    assert messages.index({"comment": "ping"}) < next(i for i, m in enumerate(messages) if m.get("id") == "2")


# --- unknown IDs --------------------------------------------------------------------------------


@pytest.mark.parametrize("search_id", [UNKNOWN, "nope", "srch_123"])
@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/api/search/runs/{}"), ("GET", "/api/search/runs/{}/events"), ("DELETE", "/api/search/runs/{}")],
)
def test_an_unknown_id_is_404_on_every_route(plain_client: TestClient, method: str, path: str, search_id: str) -> None:
    response = plain_client.request(method, path.format(search_id))
    assert response.status_code == 404
    assert response.json() == {"detail": NOT_FOUND}


# --- cancel -------------------------------------------------------------------------------------


def test_cancel_during_a_stage_ends_cancelled_with_a_final_event_and_saves_nothing(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(extract=lambda real: Held(real, gate)))
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        response = client.delete(f"/api/search/runs/{search_id}")
        assert response.status_code == 202
        assert response.json()["status"] == "running"  # cancel is cooperative: it stops before the next stage
        gate.opened.set()
        ended = finished(client, search_id)
        assert ended["status"] == "cancelled"
        assert ended["result"] is None
        assert [(e["stage"], e["status"]) for e in ended["progress"]] == [
            ("research", "started"),
            ("research", "done"),
            ("extract", "started"),
            ("extract", "done"),
        ]
        _, messages = read(client, search_id)
        final = events(messages)[-1]
        assert (final["id"], final["event"]) == ("5", "run")
        assert C.SearchRun.model_validate_json(final["data"]).status is C.SearchRunStatus.CANCELLED
        again = client.delete(f"/api/search/runs/{search_id}")
        assert (again.status_code, again.json()) == (200, ended)
    assert harness.store.results == []


def test_cancel_after_the_run_finished_is_a_no_op_with_the_final_state(plain_client: TestClient) -> None:
    search_id = start(plain_client)
    done = finished(plain_client, search_id)
    response = plain_client.delete(f"/api/search/runs/{search_id}")
    assert response.status_code == 200
    assert response.json() == done
    assert state(plain_client, search_id) == done


# --- stages that fail, and the time limit --------------------------------------------------------


def test_a_failing_stage_streams_a_fallback_event_and_the_run_finishes(harness: Harness) -> None:
    runs = harness.runs(stages_with(research=lambda real: ResearchStage(fail=True)))
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        finished(client, search_id)
        _, messages = read(client, search_id)
    sent = events(messages)
    research = [C.ProgressEvent.model_validate_json(m["data"]) for m in sent[:2]]
    assert [(e.stage, e.status) for e in research] == [
        (C.StageName.RESEARCH, "started"),
        (C.StageName.RESEARCH, "fallback"),
    ]
    assert C.SearchRun.model_validate_json(sent[-1]["data"]).status is C.SearchRunStatus.DONE


def test_a_stage_whose_fallback_fails_ends_the_run_partial(harness: Harness) -> None:
    runs = harness.runs(stages_with(match=lambda real: BrokenMatch()))
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        ended = finished(client, search_id)
    assert ended["status"] == "partial"
    assert ended["result"]["partial"] is True
    assert (ended["progress"][-1]["stage"], ended["progress"][-1]["status"]) == ("match", "failed")


class _JumpingClock:
    def __init__(self) -> None:
        self.jumped_s = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.jumped_s


def test_a_run_hitting_the_search_limit_ends_partial(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _JumpingClock()
    monkeypatch.setattr("app.pipeline.monotonic", clock)
    monkeypatch.setattr("app.contracts.pipeline.monotonic", clock)

    class TakesAllTheTime(Held):
        def run(self, inp: Any, ctx: C.StageContext) -> Any:
            clock.jumped_s = C.SEARCH_TIME_LIMIT_S + 1  # 150 s pass, in no time
            return self.real.run(inp, ctx)

    runs = harness.runs(stages_with(research=TakesAllTheTime))
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        ended = finished(client, search_id)
    assert ended["status"] == "partial"
    assert ended["result"]["partial"] is True
    assert {e["stage"] for e in ended["progress"]} == {"research"}


def test_a_run_whose_runner_crashes_ends_failed_with_a_final_event(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    def crashes() -> SearchStages:
        raise RuntimeError("could not build the stages: secret-looking text")

    runs = harness.runs(crashes)
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        ended = finished(client, search_id)
        _, messages = read(client, search_id)
    assert ended["status"] == "failed"
    assert ended["result"] is None
    assert events(messages)[-1]["event"] == "run"
    assert "RuntimeError" in caplog.text
    assert "secret-looking text" not in caplog.text


# --- the pool -----------------------------------------------------------------------------------


def test_a_full_pool_answers_503_with_retry_after_and_a_plain_message(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(research=lambda real: Held(real, gate)), workers=1)
    with TestClient(harness.app(runs)) as client:
        first = start(client)
        assert gate.entered.wait(WAIT_S)
        busy = client.post("/api/search/runs", json=body())
        assert busy.status_code == 503
        assert busy.json() == {"detail": BUSY}
        assert busy.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
        assert state(client, first)["status"] == "running"  # the refused run did not disturb the one running
        gate.opened.set()
        finished(client, first)


def test_a_freed_worker_takes_the_next_run(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(research=lambda real: Held(real, gate)), workers=1)
    with TestClient(harness.app(runs)) as client:
        first = start(client)
        assert gate.entered.wait(WAIT_S)
        assert client.post("/api/search/runs", json=body()).status_code == 503
        gate.opened.set()
        finished(client, first)
        second = start(client)  # a run that has ended has given its worker back
        assert finished(client, second)["status"] == "done"


# --- keep-time ----------------------------------------------------------------------------------


def test_a_finished_run_is_dropped_after_the_keep_time(harness: Harness) -> None:
    clock = _JumpingClock()
    runs = harness.runs(keep_s=60.0, clock=clock)
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        finished(client, search_id)
        clock.jumped_s = 59.0
        assert client.get(f"/api/search/runs/{search_id}").status_code == 200
        clock.jumped_s = 61.0
        assert client.get(f"/api/search/runs/{search_id}").status_code == 404
    assert [r.search_id for r in harness.store.results] == [search_id]  # it was still saved at the end


def test_a_running_run_is_never_dropped(harness: Harness) -> None:
    gate = harness.gate()
    clock = _JumpingClock()
    runs = harness.runs(stages_with(research=lambda real: Held(real, gate)), keep_s=60.0, clock=clock)
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        clock.jumped_s = 3600.0
        assert state(client, search_id)["status"] == "running"
        gate.opened.set()
        finished(client, search_id)


# --- POST /api/search is unchanged ----------------------------------------------------------------


def test_post_api_search_is_unchanged(plain_client: TestClient) -> None:
    response = plain_client.post("/api/search", json=body())
    assert response.status_code == 200
    result = C.SearchResult.model_validate_json(response.text)
    assert RUN_ID.fullmatch(result.search_id)
    assert [r.status for r in result.stage_reports] == [C.StageStatus.OK] * 8


def test_the_app_builds_its_own_registry_from_the_environment() -> None:
    app = create_app(env={"SEARCH_RUN_WORKERS": "3", "SEARCH_RUN_PING_SECONDS": "5"})
    runs = app.state.search_runs
    assert isinstance(runs, SearchRuns)
    assert (runs.settings.workers, runs.settings.ping_s) == (3, 5.0)
    with TestClient(app):
        pass  # the app's shutdown closes its registry
    assert runs.closed


# --- shutting down, an early cancel and the event limit ------------------------------------------


def test_a_closed_registry_refuses_new_runs(harness: Harness) -> None:
    runs = harness.runs()
    runs.close()
    with TestClient(harness.app(runs)) as client:
        refused = client.post("/api/search/runs", json=body())
    assert refused.status_code == 503
    assert refused.json() == {"detail": BUSY}


def test_shutting_down_ends_open_streams_and_stops_the_runs(harness: Harness) -> None:
    gate = harness.gate()
    runs = harness.runs(stages_with(extract=lambda real: Held(real, gate)))
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        got: list[Any] = []
        follower = follow(runs, search_id, got)
        wait_until(lambda: sum(m.comment is None for m in got) >= 3, "the follower to catch up")
        runs.close()
        follower.join(WAIT_S)
        assert not follower.is_alive()  # the stream ended, so it cannot hold the server open
        gate.opened.set()
        wait_until(lambda: runs.get(search_id).status is C.SearchRunStatus.CANCELLED, "the run to stop")
    assert harness.store.results == []


def test_a_run_cancelled_before_its_worker_starts_ends_without_starting(harness: Harness) -> None:
    runs = harness.runs()
    with runs._lock:  # the registry's lock is re-entrant: holding it keeps the worker from starting the run
        search_id = runs.start(C.SearchRequest.model_validate(body()))
        assert search_id is not None
        assert runs.cancel(search_id)[1] is True
    wait_until(lambda: runs.get(search_id).finished_at is not None, "the run to end")
    run = runs.get(search_id)
    assert run.status is C.SearchRunStatus.CANCELLED
    assert run.started_at is None
    assert run.progress == []
    assert run.result is None
    assert harness.store.results == []


class Chatty(PipelineRunner):
    """Sends more progress events than a run may hold, then runs the search as usual."""

    def run(
        self,
        request: C.SearchRequest,
        *,
        search_id: str | None = None,
        progress: ProgressCallback | None = None,
        cancel: CancelFlag | None = None,
    ) -> C.SearchResult:
        assert search_id is not None and progress is not None
        for n in range(1, MAX_RUN_EVENTS + 6):
            progress(
                C.ProgressEvent(
                    event_id=n,
                    search_id=search_id,
                    stage=C.StageName.RESEARCH,
                    status="started",
                    at=datetime.now(UTC),
                    message="Still looking.",
                )
            )
        return super().run(request, search_id=search_id, cancel=cancel)


def test_a_run_keeps_at_most_max_run_events(harness: Harness) -> None:
    runs = SearchRuns(lambda: Chatty(default_search_stages(), harness.store), RunSettings(ping_s=0.05))
    harness.registries.append(runs)
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        ended = finished(client, search_id)
        _, messages = read(client, search_id)
    assert ended["status"] == "done"
    assert len(ended["progress"]) == MAX_RUN_EVENTS  # what P1's SearchRun may hold; the state still validates
    assert ids(messages)[-1] == str(MAX_RUN_EVENTS + 1)  # the final event still comes, with the next ID


def test_the_default_runner_is_the_one_post_api_search_uses() -> None:
    runner = default_runner()
    assert isinstance(runner.store, DatabaseResultStore)  # saves at the end, as POST /api/search does (FX-4)
    kinds = [type(stage) for stage in vars(runner.stages).values()]
    assert kinds == [type(stage) for stage in vars(default_search_stages()).values()]


class SavesSlowly(InMemoryResultStore):
    """A store whose save waits at a gate, so a cancel can arrive while the result is being saved."""

    def __init__(self, gate: Gate) -> None:
        super().__init__()
        self.gate = gate

    def save(self, result: C.SearchResult) -> bool:
        self.gate.hold()
        return super().save(result)


def test_a_cancel_once_the_result_is_being_saved_is_too_late_and_the_run_ends_done(harness: Harness) -> None:
    gate = harness.gate()
    store = SavesSlowly(gate)
    runs = SearchRuns(lambda: PipelineRunner(default_search_stages(), store), RunSettings(ping_s=0.05))
    harness.registries.append(runs)
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)  # every stage is done and the result is being saved
        late = client.delete(f"/api/search/runs/{search_id}")
        assert late.status_code == 200  # too late to cancel, and the answer says so: not 202
        assert late.json()["status"] == "running"
        gate.opened.set()
        ended = finished(client, search_id)
        _, messages = read(client, search_id)
    assert ended["status"] == "done"
    assert ended["result"]["saved"] is True
    assert [r.search_id for r in store.results] == [search_id]
    assert C.SearchRun.model_validate_json(events(messages)[-1]["data"]).status is C.SearchRunStatus.DONE


def test_only_the_newest_finished_runs_are_kept(harness: Harness) -> None:
    settings = RunSettings(ping_s=0.05, max_kept=2)
    runs = SearchRuns(lambda: PipelineRunner(default_search_stages(), harness.store), settings)
    harness.registries.append(runs)
    with TestClient(harness.app(runs)) as client:
        made = []
        for _ in range(3):
            made.append(start(client))
            finished(client, made[-1])
        assert client.get(f"/api/search/runs/{made[0]}").status_code == 404  # the oldest went first
        assert [client.get(f"/api/search/runs/{s}").status_code for s in made[1:]] == [200, 200]
    assert [r.search_id for r in harness.store.results] == made  # every one was still saved


def test_open_streams_hold_no_worker_thread(harness: Harness) -> None:
    """Following a run must not use up the server's shared worker threads, or every other request would wait."""
    gate = harness.gate()
    runs = harness.runs(stages_with(research=lambda real: Held(real, gate)), ping_s=5.0)
    with TestClient(harness.app(runs)) as client:
        assert client.portal is not None

        async def two_worker_threads_only() -> None:
            anyio.to_thread.current_default_thread_limiter().total_tokens = 2

        client.portal.call(two_worker_threads_only)
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        got: list[list[dict[str, str]]] = []
        readers = [read_in_thread(client, search_id, got) for _ in range(3)]  # more streams than worker threads
        wait_until(lambda: runs.open_streams == 3, "three clients to be following the run")
        asked = time.monotonic()
        assert client.get(f"/api/search/runs/{search_id}").status_code == 200
        assert client.get("/api/health").status_code == 200
        assert time.monotonic() - asked < 2.0  # a stream holding a thread would keep it for a 5-second slice
        gate.opened.set()
        for reader in readers:
            reader.join(WAIT_S)
            assert not reader.is_alive()
    assert all(ids(messages) == [str(n) for n in range(1, ALL_EVENTS + 2)] for messages in got)


def test_the_limit_never_drops_a_running_run_or_one_being_read(harness: Harness) -> None:
    held = harness.gate()
    settings = RunSettings(workers=3, ping_s=0.05, max_kept=1)

    def stages() -> SearchStages:
        return (
            stages_with(research=lambda real: Held(real, held))()
            if not held.entered.is_set()
            else default_search_stages()
        )

    runs = SearchRuns(lambda: PipelineRunner(stages(), harness.store), settings)
    harness.registries.append(runs)
    with TestClient(harness.app(runs)) as client:
        running = start(client)
        assert held.entered.wait(WAIT_S)
        followed = start(client)
        finished(client, followed)
        live: list[Any] = []
        reader_gate = threading.Event()

        def wait_inside(message: Any) -> None:
            if message.comment is None and message.id == "1":
                reader_gate.wait(WAIT_S)  # keep the stream open on the run while newer runs end

        follower = follow(runs, followed, live, wait_inside)
        wait_until(lambda: runs.open_streams == 1, "the stream to open")
        newer = []
        for _ in range(2):  # one at a time: each ends before the next starts
            newer.append(start(client))
            finished(client, newer[-1])
        assert client.get(f"/api/search/runs/{running}").json()["status"] == "running"  # never dropped while running
        assert client.get(f"/api/search/runs/{followed}").status_code == 200  # kept while a stream reads it
        assert client.get(f"/api/search/runs/{newer[0]}").status_code == 404  # the limit (1) still applies otherwise
        reader_gate.set()
        follower.join(WAIT_S)
        held.opened.set()
        finished(client, running)
    assert [m.id for m in live if m.comment is None][-1] == FINAL_ID


def test_a_cancelled_run_whose_runner_then_crashes_ends_cancelled(harness: Harness) -> None:
    gate = harness.gate()

    def crashes_after_the_gate() -> PipelineRunner:
        gate.hold()
        raise RuntimeError("could not build the runner")

    runs = SearchRuns(crashes_after_the_gate, RunSettings(ping_s=0.05))
    harness.registries.append(runs)
    with TestClient(harness.app(runs)) as client:
        search_id = start(client)
        assert gate.entered.wait(WAIT_S)
        assert client.delete(f"/api/search/runs/{search_id}").status_code == 202
        gate.opened.set()
        ended = finished(client, search_id)
    assert ended["status"] == "cancelled"  # what the 202 promised, even though the run then went wrong
    assert ended["result"] is None


def test_the_kept_runs_limit_holds_as_runs_end(harness: Harness) -> None:
    """Not only at the next request: the oldest finished run goes as soon as a newer one ends."""
    runs = SearchRuns(lambda: PipelineRunner(default_search_stages(), harness.store), RunSettings(max_kept=1))
    harness.registries.append(runs)
    request = C.SearchRequest.model_validate(body())
    first = runs.start(request)
    assert first is not None
    wait_until(lambda: runs._runs[first].ended_at is not None, "the first run to end")
    second = runs.start(request)
    assert second is not None
    wait_until(lambda: first not in runs._runs, "the first run to be dropped when the second ends")
    assert list(runs._runs) == [second]  # no request came in between


def test_a_run_that_passed_the_stream_check_is_kept_for_its_first_read(harness: Harness) -> None:
    """A run dropped between the check and the first read used to give a 200 with no events, not a 404."""
    clock = _JumpingClock()
    settings = RunSettings(ping_s=0.05, max_kept=1)
    runs = SearchRuns(lambda: PipelineRunner(default_search_stages(), harness.store), settings, clock=clock)
    harness.registries.append(runs)
    request = C.SearchRequest.model_validate(body())
    first = runs.start(request)
    assert first is not None
    wait_until(lambda: runs._runs[first].ended_at is not None, "the first run to end")
    assert runs.resume_from(first, None) == 0  # the stream's start check passed
    second = runs.start(request)  # a newer run ends: without the reservation the limit (1) would drop the first
    assert second is not None
    wait_until(lambda: runs._runs[second].ended_at is not None, "the second run to end")
    got: list[Any] = []
    follow(runs, first, got).join(WAIT_S)
    assert [m.id for m in got if m.comment is None][-1] == FINAL_ID  # its events, not an empty answer
    clock.jumped_s = 11.0  # past the grace, with no stream reading it: the oldest goes
    with pytest.raises(LookupError):
        runs.get(first)


def test_checking_a_stream_again_and_again_never_keeps_a_run_past_its_keep_time(harness: Harness) -> None:
    clock = _JumpingClock()
    runs = SearchRuns(
        lambda: PipelineRunner(default_search_stages(), harness.store), RunSettings(keep_s=60.0), clock=clock
    )
    harness.registries.append(runs)
    search_id = runs.start(C.SearchRequest.model_validate(body()))
    assert search_id is not None
    wait_until(lambda: runs._runs[search_id].ended_at is not None, "the run to end")
    for jumped in (20.0, 40.0, 55.0):  # a client that reconnects without the last ID keeps passing the check
        clock.jumped_s = jumped
        assert runs.resume_from(search_id, None) == 0
    clock.jumped_s = 61.0
    with pytest.raises(LookupError):
        runs.get(search_id)  # gone at its keep-time, reservation or not
