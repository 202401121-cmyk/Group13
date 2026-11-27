"""Research and extraction begun during the interview, through the API (US-34, D-94; US-12, US-14).

The pre-search routes start and stop a session's job; POST /api/search/runs takes it over. The stages are the fakes in
tests/fakes/early.py, which count every research run and every source read, so a test can tell a job taken over from
one repeated. No server, no network, no real LLM; every wait has a deadline.
"""

from __future__ import annotations

import dataclasses
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.early_search as early_module
from app import contracts as C
from app.api.presearch import get_session_store
from app.api.search_runs import BUSY, RunSettings, SearchRuns, get_search_runs
from app.api.sessions import InMemorySessionStore
from app.auth.http import optional_user
from app.auth.verify import SignedInUser
from app.early_search import EarlyJob
from app.main import create_app
from app.pipeline import CancelFlag, InMemoryResultStore, PipelineRunner, default_search_stages
from tests.fakes.early import WAIT_S, CountingResearch, Gate, SpecExtract

LAPTOP = "best laptop for coding under 70k"
PHONE = "best phone for photos under 30k"
A = SignedInUser(id=uuid.UUID("11111111-1111-4111-8111-111111111111"))
B = SignedInUser(id=uuid.UUID("22222222-2222-4222-8222-222222222222"))


class Signed:
    """Who the next request comes from: a user, or a guest (None)."""

    def __init__(self) -> None:
        self.user: SignedInUser | None = None

    def __call__(self) -> SignedInUser | None:
        return self.user


class FailsFirst(CountingResearch):
    """Research that crashes, fallback and all, on its first run only: the job's."""

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        self.fail = not self.runs
        return super().run(inp, ctx)


@dataclasses.dataclass
class World:
    client: TestClient
    runs: SearchRuns
    research: CountingResearch
    extract: SpecExtract
    signed: Signed

    def job(self, session_id: str) -> EarlyJob:
        return self.runs.early._jobs[session_id]


def build(
    research: CountingResearch | None = None, extract: SpecExtract | None = None, workers: int = 2
) -> Callable[[], Iterator[World]]:
    def make() -> Iterator[World]:
        chosen_research, chosen_extract = research or CountingResearch(), extract or SpecExtract()
        store = InMemoryResultStore()

        def runner() -> PipelineRunner:
            stages = dataclasses.replace(default_search_stages(), research=chosen_research, extract=chosen_extract)
            return PipelineRunner(stages, store)

        runs = SearchRuns(runner, RunSettings(workers=workers, ping_s=0.05, early=True))
        app = create_app(env={})
        sessions = InMemorySessionStore()
        signed = Signed()
        app.dependency_overrides[get_session_store] = lambda: sessions
        app.dependency_overrides[get_search_runs] = lambda: runs
        app.dependency_overrides[optional_user] = signed
        with TestClient(app) as client:
            yield World(client, runs, chosen_research, chosen_extract, signed)
        for gate in (chosen_research.gate, chosen_extract.gate):
            if gate is not None:
                gate.opened.set()
        runs.close()

    return make


@pytest.fixture
def world() -> Iterator[World]:
    yield from build()()


# --- helpers ---------------------------------------------------------------------------------------------------


def wait_until(check: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + WAIT_S
    while not check():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def stopped_soon(flag: CancelFlag) -> bool:
    deadline = time.monotonic() + WAIT_S
    while not flag.requested and time.monotonic() < deadline:
        time.sleep(0.01)
    return flag.requested


def post(client: TestClient, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(f"/api/presearch/{path}", json=body)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


def ready(client: TestClient, text: str = LAPTOP) -> str:
    """Intake, criteria and the first question: everything before the shopper's first answer."""
    session_id: str = post(client, "intake", {"text": text})["session_id"]
    post(client, f"{session_id}/criteria")
    post(client, f"{session_id}/interview/start")
    return session_id


def answer(client: TestClient, session_id: str) -> dict[str, Any]:
    return post(client, f"{session_id}/interview/reply", {"reply_text": "Medium"})


def finish(client: TestClient, session_id: str) -> C.Rubric:
    for _ in range(30):
        step = C.InterviewStep.model_validate(answer(client, session_id))
        if step.kind == "finished":
            assert step.rubric is not None
            return step.rubric
    raise AssertionError("the interview never finished")


def confirm(client: TestClient, session_id: str, rubric: C.Rubric, status: int = 202) -> str:
    body = C.SearchRequest(session_id=session_id, raw_query=LAPTOP, rubric=rubric).model_dump(mode="json")
    response = client.post("/api/search/runs", json=body)
    assert response.status_code == status, response.text
    search_id: str = response.json().get("search_id", "")
    return search_id


def ended(world: World, search_id: str) -> dict[str, Any]:
    def state() -> dict[str, Any]:
        found: dict[str, Any] = world.client.get(f"/api/search/runs/{search_id}").json()
        return found

    wait_until(lambda: state()["finished_at"] is not None, "the run to end")
    return state()


def job_done(world: World, session_id: str) -> EarlyJob:
    wait_until(lambda: world.runs.early.running(session_id), "the job to start")
    job = world.job(session_id)
    assert job.done.wait(WAIT_S), "the job never ended"
    return job


def free(runs: SearchRuns, count: int) -> bool:
    taken = 0
    try:
        while taken < count and runs._free.acquire(blocking=False):
            taken += 1
        return taken == count
    finally:
        for _ in range(taken):
            runs._free.release()


def stream(world: World, search_id: str) -> list[tuple[str, str]]:
    """(stage, status) for each progress event, read over HTTP once the run has ended."""
    with world.client.stream("GET", f"/api/search/runs/{search_id}/events") as response:
        text = "".join(response.iter_text())
    seen = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line and not line.startswith(":"))
        if fields.get("event") == "progress":
            event = C.ProgressEvent.model_validate_json(fields["data"])
            seen.append((event.stage.value, event.status))
    return seen


# --- when a job starts ----------------------------------------------------------------------------------------


def test_a_session_that_bounces_before_its_first_answer_spends_nothing(world: World) -> None:
    session_id = ready(world.client)
    post(world.client, "intake", {"text": LAPTOP})  # and another visit that only typed a request
    assert not world.runs.early.running(session_id)
    assert world.research.runs == [] and world.extract.reads == []
    assert free(world.runs, 2)


def test_the_first_answer_starts_one_job_and_later_answers_start_none(world: World) -> None:
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)
    answer(world.client, session_id)
    answer(world.client, session_id)
    assert world.job(session_id) is job
    assert len(world.research.runs) == 1
    assert len(world.extract.read_by(job.search.ctx.search_id)) == 3
    assert free(world.runs, 2)  # the job gave its worker back when it ended


def test_a_new_request_mid_interview_cancels_the_job_and_drops_what_it_found(world: World) -> None:
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)
    post(world.client, "intake", {"text": PHONE, "session_id": session_id})
    assert job.stop.requested and not world.runs.early.running(session_id)


@pytest.mark.parametrize(
    "changes",
    [
        {"use_case": "gaming"},
        {"region": "US"},
        {"budget": {"min": None, "max": {"amount": 90000, "currency": "INR"}}},
    ],
)
def test_a_change_of_use_budget_or_region_cancels_the_job(world: World, changes: dict[str, Any]) -> None:
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)
    response = world.client.put(f"/api/presearch/{session_id}/intent", json={"changes": changes})
    assert response.status_code == 200, response.text
    assert job.stop.requested and not world.runs.early.running(session_id)


def test_an_edit_that_keeps_the_ground_keeps_the_job(world: World) -> None:
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)
    response = world.client.put(f"/api/presearch/{session_id}/intent", json={"changes": {"category": "laptop"}})
    assert response.status_code == 200, response.text
    assert world.job(session_id) is job and not job.stop.requested


def test_new_criteria_cancel_the_job(world: World) -> None:
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)
    post(world.client, f"{session_id}/criteria")
    assert job.stop.requested and not world.runs.early.running(session_id)


def test_another_owner_starting_the_interview_drops_the_job(world: World) -> None:
    world.signed.user = A
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)
    post(world.client, f"{session_id}/interview/start")  # the same owner again: the job stays
    assert world.job(session_id) is job
    world.signed.user = None  # signed out (D-80)
    post(world.client, f"{session_id}/interview/start")
    assert job.stop.requested and not world.runs.early.running(session_id)


def test_a_full_pool_starts_no_job_and_the_confirm_gets_the_normal_search() -> None:
    for world in build(workers=1)():
        assert world.runs._free.acquire(blocking=False)  # another shopper's search holds the only worker
        session_id = ready(world.client)
        rubric = finish(world.client, session_id)  # every answer meets a full pool
        assert not world.runs.early.running(session_id) and world.research.runs == []
        world.runs._free.release()  # that search ends just before this shopper confirms
        state = ended(world, confirm(world.client, session_id, rubric))
        assert state["status"] == "done"
        assert len(world.research.runs) == 1  # the search's own: no job ever ran


# --- the confirm ----------------------------------------------------------------------------------------------


def test_the_confirm_takes_the_job_over_and_the_stream_shows_its_stages_done(world: World) -> None:
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)
    rubric = finish(world.client, session_id)
    search_id = confirm(world.client, session_id, rubric)
    state = ended(world, search_id)
    assert state["status"] == "done"
    assert len(world.research.runs) == 1  # never run again
    assert len(world.extract.read_by(job.search.ctx.search_id)) == 3
    assert not world.runs.early.running(session_id)
    seen = stream(world, search_id)
    assert seen[:4] == [("research", "started"), ("research", "done"), ("extract", "started"), ("extract", "done")]
    assert len(seen) == 16  # every stage, started and done, as for any search
    assert free(world.runs, 2)


def test_a_confirm_before_the_job_has_anything_runs_the_normal_search() -> None:
    gate = Gate()
    for world in build(research=CountingResearch(gate=gate))():
        session_id = ready(world.client)
        answer(world.client, session_id)
        assert gate.entered.wait(WAIT_S)
        job = world.job(session_id)
        rubric = finish(world.client, session_id)
        search_id = confirm(world.client, session_id, rubric)
        assert job.stop.requested and not world.runs.early.running(session_id)
        gate.opened.set()
        assert ended(world, search_id)["status"] == "done"
        assert len(world.research.runs) == 2  # the job's, which was dropped, and the search's own
        assert world.extract.read_by(job.search.ctx.search_id) == []  # the job never read a page


@pytest.mark.parametrize(("owner", "confirmer"), [(A, None), (A, B), (None, A)])
def test_another_users_or_a_guests_confirm_never_gets_the_job(
    world: World, owner: SignedInUser | None, confirmer: SignedInUser | None
) -> None:
    world.signed.user = owner
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)
    rubric = finish(world.client, session_id)
    world.signed.user = confirmer
    search_id = confirm(world.client, session_id, rubric)
    assert ended(world, search_id)["status"] == "done"
    assert job.stop.requested
    assert len(world.research.runs) == 2  # the search ran its own research


def test_a_job_still_reading_at_confirm_is_joined_not_repeated() -> None:
    gate = Gate()
    for world in build(extract=SpecExtract(gate=gate))():
        session_id = ready(world.client)
        answer(world.client, session_id)
        assert gate.entered.wait(WAIT_S)
        job = world.job(session_id)
        rubric = finish(world.client, session_id)
        search_id = confirm(world.client, session_id, rubric)
        assert job.slot == "handed" and not job.stop.requested
        assert free(world.runs, 1) and not free(world.runs, 2)  # one worker for both: the job's
        gate.opened.set()
        assert ended(world, search_id)["status"] == "done"
        assert len(world.research.runs) == 1
        assert len(world.extract.read_by(job.search.ctx.search_id)) == 3  # each page read once, by the job
        assert free(world.runs, 2)


def test_the_jobs_failure_leaves_the_normal_search_intact() -> None:
    for world in build(research=FailsFirst())():
        session_id = ready(world.client)
        answer(world.client, session_id)
        job = job_done(world, session_id)
        assert not job.researched_event.is_set()
        rubric = finish(world.client, session_id)
        assert ended(world, confirm(world.client, session_id, rubric))["status"] == "done"
        assert len(world.research.runs) == 2


def test_a_confirm_with_no_free_worker_is_busy_and_keeps_the_job_for_the_next_try(world: World) -> None:
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)  # it ended, so its worker went back
    rubric = finish(world.client, session_id)
    assert world.runs._free.acquire(blocking=False) and world.runs._free.acquire(blocking=False)
    try:
        response = world.client.post(
            "/api/search/runs",
            json=C.SearchRequest(session_id=session_id, raw_query=LAPTOP, rubric=rubric).model_dump(mode="json"),
        )
        assert (response.status_code, response.json()) == (503, {"detail": BUSY})
        assert world.job(session_id) is job and not job.stop.requested
    finally:
        world.runs._free.release()
        world.runs._free.release()
    assert ended(world, confirm(world.client, session_id, rubric))["status"] == "done"
    assert len(world.research.runs) == 1  # the retry took the job over: nothing read twice


def test_a_busy_confirm_with_a_job_that_cannot_be_taken_over_drops_it(world: World) -> None:
    world.signed.user = A
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)
    rubric = finish(world.client, session_id)
    world.signed.user = None  # a guest's confirm never gets A's job
    assert world.runs._free.acquire(blocking=False) and world.runs._free.acquire(blocking=False)
    try:
        assert confirm(world.client, session_id, rubric, status=503) == ""
        assert job.stop.requested and not world.runs.early.running(session_id)
    finally:
        world.runs._free.release()
        world.runs._free.release()


def test_a_dropped_job_still_researching_gives_its_worker_to_the_normal_search() -> None:
    """One worker, as on staging: the shopper confirms while the job is still researching. The job is dropped, and
    its worker passes to the normal search, so the shopper never gets a 503 for their own job."""
    gate = Gate()
    for world in build(research=CountingResearch(gate=gate), workers=1)():
        session_id = ready(world.client)
        answer(world.client, session_id)
        assert gate.entered.wait(WAIT_S)
        job = world.job(session_id)
        rubric = finish(world.client, session_id)
        search_id = confirm(world.client, session_id, rubric)
        assert job.stop.requested and job.slot == "handed"
        gate.opened.set()
        assert ended(world, search_id)["status"] == "done"
        assert job.done.wait(WAIT_S)
        assert free(world.runs, 1)  # the search gave the one worker back, once


def test_the_reply_that_finishes_the_interview_starts_no_job(world: World) -> None:
    session_id = ready(world.client)
    for _ in range(30):
        step = C.InterviewStep.model_validate(answer(world.client, session_id))
        if step.kind == "finished":
            break
        assert world.runs.early.running(session_id)
        world.runs.early.cancel(session_id)  # so the next answer would start a new one, if any did
    assert step.kind == "finished"
    assert not world.runs.early.running(session_id)


def test_signing_in_starts_a_new_session_whose_confirm_never_gets_the_old_job(world: World) -> None:
    """D-80: the frontend gives a new session at sign-in. The guest session's job is never taken over by the new
    one, and goes with its own keep time."""
    old_session = ready(world.client)
    answer(world.client, old_session)
    old_job = job_done(world, old_session)
    world.signed.user = A
    new_session = ready(world.client)
    answer(world.client, new_session)
    job_done(world, new_session)
    rubric = finish(world.client, new_session)
    assert ended(world, confirm(world.client, new_session, rubric))["status"] == "done"
    assert world.job(old_session) is old_job and not old_job.stop.requested  # nobody took it
    assert len(world.research.runs) == 2  # each session's own job; the new search took over its own
    world.runs.early.keep_s = 0.0
    world.runs.drop_expired()
    assert old_job.stop.requested and not world.runs.early.running(old_session)


def test_a_cancel_while_the_search_waits_for_its_job_stops_both_at_once() -> None:
    gate = Gate()
    for world in build(extract=SpecExtract(gate=gate))():
        session_id = ready(world.client)
        answer(world.client, session_id)
        assert gate.entered.wait(WAIT_S)
        job = world.job(session_id)
        rubric = finish(world.client, session_id)
        search_id = confirm(world.client, session_id, rubric)
        assert world.client.delete(f"/api/search/runs/{search_id}").status_code == 202
        assert stopped_soon(job.stop)
        assert ended(world, search_id)["status"] == "cancelled"
        gate.opened.set()
        assert job.done.wait(WAIT_S)
        assert free(world.runs, 2)


def test_a_registry_closing_as_the_job_is_taken_over_stops_the_job_and_frees_its_worker(world: World) -> None:
    gate = world.extract.gate = Gate()
    session_id = ready(world.client)
    answer(world.client, session_id)
    assert gate.entered.wait(WAIT_S)
    job = world.job(session_id)
    rubric = finish(world.client, session_id)
    world.runs.closed = True  # closing, between the claim and the run's start
    request = C.SearchRequest(session_id=session_id, raw_query=LAPTOP, rubric=rubric)
    assert world.runs.start(request) is None
    assert job.stop.requested
    gate.opened.set()
    assert job.done.wait(WAIT_S)
    assert free(world.runs, 2)


# --- housekeeping and the switch -------------------------------------------------------------------------------


def test_housekeeping_and_shutdown_reach_the_early_jobs(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id = ready(world.client)
    answer(world.client, session_id)
    job = job_done(world, session_id)
    world.runs.drop_expired()
    assert world.runs.early.running(session_id)  # still young
    monkeypatch.setattr(world.runs.early, "keep_s", 0.0)
    world.runs.drop_expired()
    assert job.stop.requested and not world.runs.early.running(session_id)
    world.runs.close()
    assert world.runs.early.closed


def test_the_app_turns_early_jobs_on_unless_early_research_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(early_module, "DEFAULT_ON", True)
    on, off = create_app(env={}), create_app(env={"EARLY_RESEARCH": "off"})
    assert on.state.search_runs.settings.early is True and on.state.search_runs.early.enabled is True
    assert off.state.search_runs.settings.early is False and off.state.search_runs.early.enabled is False


def test_a_reply_when_early_jobs_are_off_starts_nothing() -> None:
    for world in build()():
        world.runs.early.enabled = False
        session_id = ready(world.client)
        answer(world.client, session_id)
        assert not world.runs.early.running(session_id) and world.research.runs == []
        threading.Event().wait(0.05)
        assert world.research.runs == []
