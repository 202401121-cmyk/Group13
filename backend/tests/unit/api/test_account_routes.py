"""US-30: sign-in through the API. GET /api/me, GET /api/me/searches and GET /api/me/searches/{id}; and every other
route takes an optional user, saving a signed-in user's request and search with their id and a guest's under the
session ID alone (D-36).

No database and no network: the account store is the in-memory store, and tokens are signed locally against a fake
key set (tests/fakes/auth.py). tests/integration/test_accounts_in_postgres.py runs the same reads on a real Postgres.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api import SearchRuns, load_run_settings
from app.api.account import (
    ACCOUNT_UNAVAILABLE,
    BAD_CURSOR,
    NOT_FOUND,
    PAGE_SIZE,
    SIGN_IN_AGAIN,
    SIGN_IN_FIRST,
    SIGN_IN_UNAVAILABLE,
    get_account_store,
)
from app.api.evidence import NO_PRODUCT
from app.api.search_runs import NO_SUCH_SEARCH
from app.api.search_runs import NOT_FOUND as RUN_NOT_FOUND
from app.auth import get_token_verifier
from app.contracts import MeResponse, SavedSearchList, SearchRequest, SearchResult
from app.contracts import examples as ex
from app.db import InMemoryRequestStore, NullAccountStore, NullRequestStore, SearchRow, search_row
from app.db.store import StoredSearch
from app.main import create_app
from app.pipeline import DatabaseResultStore, PipelineRunner, default_search_stages
from tests.fakes.auth import TestSigner, bearer, verifier_for

A = uuid.UUID("a0000000-0000-4000-8000-00000000000a")
B = uuid.UUID("b0000000-0000-4000-8000-00000000000b")
SIGNER = TestSigner()


class World:
    """One app with a fake key set and the in-memory store behind every route."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.store = InMemoryRequestStore()
        self.verifier, self.keys = verifier_for(SIGNER)
        self.app = create_app(env={})
        self.app.dependency_overrides[get_token_verifier] = lambda: self.verifier
        self.app.dependency_overrides[get_account_store] = lambda: self.store
        monkeypatch.setattr("app.db.default_request_store", lambda: self.store)
        self.app.state.search_runs = SearchRuns(self.runner, load_run_settings({}))
        self.client = TestClient(self.app)

    def runner(self) -> PipelineRunner:
        return PipelineRunner(default_search_stages(), DatabaseResultStore(self.store))

    def get(self, path: str, user: uuid.UUID | None = None, **headers: str) -> Any:
        sent = {**(bearer(SIGNER.token(user)) if user else {}), **headers}
        return self.client.get(path, headers=sent)

    def save(
        self,
        owner: uuid.UUID | None,
        created_at: datetime | None = None,
        result: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        """A saved search, as the pipeline saves one, put straight into the store with its time."""
        body = result or ex.search_result().model_dump(mode="json")
        row = search_row(ex.SESSION_ID, ex.rubric().model_dump(mode="json"), body, owner)
        stored = StoredSearch(row, created_at=created_at or datetime.now(UTC))
        self.store.saved.append(stored)
        return stored.id

    def close(self) -> None:
        self.client.close()
        self.app.state.search_runs.close()


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = World(monkeypatch)
    yield built
    built.close()


def _owners(store: InMemoryRequestStore) -> list[uuid.UUID | None]:
    return [row.user_id for row in store.searches]


# --- GET /api/me --------------------------------------------------------------------------------------------------


def test_a_guest_is_not_signed_in_and_needs_no_database(world: World) -> None:
    world.app.dependency_overrides[get_account_store] = NullAccountStore
    response = world.get("/api/me")
    assert response.status_code == 200
    assert MeResponse.model_validate(response.json()) == MeResponse(signed_in=False)
    assert world.store.profiles == {}


def test_a_signed_in_user_is_named_and_their_profile_is_made_once(world: World) -> None:
    first = world.get("/api/me", A)
    assert first.status_code == 200, first.text
    assert MeResponse.model_validate(first.json()) == MeResponse(signed_in=True, user_id=A, display_name=None)
    assert world.store.profiles == {A: None}
    world.store.profiles[A] = "Asha"  # renamed through the Data API
    again = MeResponse.model_validate(world.get("/api/me", A).json())
    assert again.display_name == "Asha" and world.store.profiles == {A: "Asha"}  # nothing if it exists


@pytest.mark.parametrize(
    "token",
    [
        SIGNER.token(A, exp=int(time.time()) - 600, iat=int(time.time()) - 4200),  # expired
        SIGNER.token(A, iss="https://otherproject.supabase.test/auth/v1"),  # wrong issuer
        SIGNER.token(A, aud="anon"),  # wrong audience
        TestSigner(kid=SIGNER.kid).token(A),  # signed by another key
        "not-a-token",
    ],
    ids=["expired", "wrong-issuer", "wrong-audience", "wrong-key", "garbage"],
)
def test_a_bad_token_answers_401_on_the_user_routes(world: World, token: str) -> None:
    for path in ("/api/me", "/api/me/searches", f"/api/me/searches/{uuid.uuid4()}"):
        response = world.client.get(path, headers=bearer(token))
        assert response.status_code == 401, path
        assert response.json() == {"detail": SIGN_IN_AGAIN}
        assert response.headers["WWW-Authenticate"] == "Bearer"
    assert world.store.profiles == {}


def test_another_scheme_is_a_bad_token(world: World) -> None:
    response = world.client.get("/api/me", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 401


def test_an_unknown_key_id_fetches_the_keys_once_more_then_answers_401(world: World) -> None:
    world.get("/api/me", A)
    stranger = TestSigner(kid="nobody-published-this")
    response = world.client.get("/api/me", headers=bearer(stranger.token(A)))
    assert response.status_code == 401
    assert world.keys.calls == 1  # the cache was filled this instant: no second fetch within the gap


def test_sign_in_that_cannot_be_checked_answers_503_on_the_user_routes_and_a_guest_search(world: World) -> None:
    world.keys.fail = True  # the key set can't be fetched: no token can be checked
    response = world.get("/api/me", A)
    assert response.status_code == 503 and response.json() == {"detail": SIGN_IN_UNAVAILABLE}
    search = world.client.post("/api/search", json=_request(), headers=bearer(SIGNER.token(A)))
    assert search.status_code == 200 and _owners(world.store) == [None]


def test_sign_in_off_answers_503_to_a_token_and_a_guest_answer_without_one(world: World) -> None:
    world.app.dependency_overrides[get_token_verifier] = lambda: None
    assert world.get("/api/me", A).status_code == 503
    assert world.get("/api/me").json() == {"signed_in": False, "user_id": None, "display_name": None}


def test_a_deleted_user_must_sign_in_again(world: World) -> None:
    world.store.users = {B}
    response = world.get("/api/me", A)
    assert response.status_code == 401 and response.json() == {"detail": SIGN_IN_AGAIN}


# --- GET /api/me/searches -----------------------------------------------------------------------------------------


def test_the_search_routes_need_a_signed_in_user(world: World) -> None:
    for path in ("/api/me/searches", f"/api/me/searches/{uuid.uuid4()}"):
        response = world.get(path)
        assert response.status_code == 401 and response.json() == {"detail": SIGN_IN_FIRST}


def test_the_list_is_newest_first_twenty_a_page_with_a_cursor(world: World) -> None:
    start = datetime(2026, 11, 20, 8, 0, tzinfo=UTC)
    made = [world.save(A, start + timedelta(minutes=i)) for i in range(45)]
    world.save(B, start + timedelta(hours=5))  # someone else's, newer than all of A's
    seen: list[str] = []
    cursor: str | None = None
    sizes = []
    while True:
        response = world.get("/api/me/searches" + (f"?cursor={cursor}" if cursor else ""), A)
        assert response.status_code == 200, response.text
        page = SavedSearchList.model_validate(response.json())
        sizes.append(len(page.items))
        seen += [str(item.id) for item in page.items]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert sizes == [PAGE_SIZE, PAGE_SIZE, 5]
    assert seen == [str(i) for i in reversed(made)]  # newest first, each once, never B's


def test_a_page_that_ends_exactly_has_no_next_cursor(world: World) -> None:
    for _ in range(PAGE_SIZE):
        world.save(A)
    page = SavedSearchList.model_validate(world.get("/api/me/searches", A).json())
    assert len(page.items) == PAGE_SIZE and page.next_cursor is None


def test_a_summary_names_the_request_category_and_top_product(world: World) -> None:
    world.save(A)
    [item] = SavedSearchList.model_validate(world.get("/api/me/searches", A).json()).items
    assert item.request_text == ex.QUERY
    assert item.category == "laptop"
    assert item.top_product == "Nova Book 14"


def test_an_unscored_list_has_no_top_product(world: World) -> None:
    result = ex.search_result().model_dump(mode="json")
    for ranked in result["decision"]["ranked"]:
        ranked["score"] = None
    world.save(A, result=result)
    [item] = SavedSearchList.model_validate(world.get("/api/me/searches", A).json()).items
    assert item.top_product is None


def test_a_long_request_is_shortened_to_the_summarys_500_characters(world: World) -> None:
    long_text = "a made-up request for a quiet laptop " * 40
    long_text = long_text[:1000]
    result = ex.search_result().model_dump(mode="json")
    result["request"]["raw_query"] = long_text
    world.save(A, result=result)
    [item] = SavedSearchList.model_validate(world.get("/api/me/searches", A).json()).items
    assert len(item.request_text) == 500
    assert item.request_text == long_text[:499] + "…"


def test_a_request_of_exactly_500_characters_is_kept_whole(world: World) -> None:
    result = ex.search_result().model_dump(mode="json")
    result["request"]["raw_query"] = "x" * 500
    world.save(A, result=result)
    [item] = SavedSearchList.model_validate(world.get("/api/me/searches", A).json()).items
    assert item.request_text == "x" * 500


@pytest.mark.parametrize(
    "cursor",
    [
        "not-a-cursor",
        "eyJ0IjoxfQ",
        "e30",
        "W10",
        "eyJ0IjoiMjAyNi0xMS0yMFQwODowMDowMCIsImkiOiJ4In0",
        "eyJ0IjogIjIwMjYtMTEtMjBUMDg6MDA6MDArMDA6MDAiLCAiaSI6IDF9",  # "i" is a number
        "eyJ0IjogIjIwMjYtMTEtMjBUMDg6MDA6MDAiLCAiaSI6ICJhMDAwMDAwMC0wMDAwLTQwMDAtODAwMC0wMDAwMDAwMDAwMGEifQ",  # no zone
    ],
)
def test_a_cursor_this_route_did_not_make_answers_422(world: World, cursor: str) -> None:
    response = world.get(f"/api/me/searches?cursor={cursor}", A)
    assert response.status_code == 422 and response.json() == {"detail": BAD_CURSOR}


# --- GET /api/me/searches/{id}: user A cannot read user B's data (criterion 3) ------------------------------------


def test_user_a_cannot_read_user_bs_searches(world: World) -> None:
    bs = world.save(B)
    guests = world.save(None)
    assert SavedSearchList.model_validate(world.get("/api/me/searches", A).json()).items == []
    for search_id in (bs, guests, uuid.uuid4(), "not-a-uuid"):
        response = world.get(f"/api/me/searches/{search_id}", A)
        assert response.status_code == 404, search_id  # never 403: an id is never confirmed
        assert response.json() == {"detail": NOT_FOUND}
    own = world.get(f"/api/me/searches/{bs}", B)
    assert own.status_code == 200
    result = SearchResult.model_validate(own.json())
    assert result.saved is True and result.request.raw_query == ex.QUERY


# --- the database is off or down ----------------------------------------------------------------------------------


@pytest.mark.parametrize("down", ["off", "down"])
def test_without_a_database_the_user_routes_answer_503_and_searches_still_finish(
    world: World, monkeypatch: pytest.MonkeyPatch, down: str
) -> None:
    if down == "off":
        world.app.dependency_overrides[get_account_store] = NullAccountStore
        monkeypatch.setattr("app.db.default_request_store", NullRequestStore)
    else:
        world.store.fail = True
    for path in ("/api/me", "/api/me/searches", f"/api/me/searches/{uuid.uuid4()}"):
        response = world.get(path, A)
        assert response.status_code == 503, path
        assert response.json() == {"detail": ACCOUNT_UNAVAILABLE}
    search = world.client.post("/api/search", json=_request(), headers=bearer(SIGNER.token(A)))
    assert search.status_code == 200
    assert search.json()["saved"] is False


# --- every other route: an optional user --------------------------------------------------------------------------


def _request(user_id: str | None = None) -> dict[str, Any]:
    body = SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric()).model_dump(mode="json")
    return {**body, "user_id": user_id}


def test_a_signed_in_search_is_saved_with_the_users_id(world: World) -> None:
    response = world.client.post("/api/search", json=_request(), headers=bearer(SIGNER.token(A)))
    assert response.status_code == 200
    assert response.json()["request"]["user_id"] == str(A)
    assert _owners(world.store) == [A]
    [item] = SavedSearchList.model_validate(world.get("/api/me/searches", A).json()).items
    assert world.get(f"/api/me/searches/{item.id}", A).status_code == 200


def test_a_guest_run_is_never_linked_whatever_the_body_says(world: World) -> None:
    response = world.client.post("/api/search", json=_request(str(A)))
    assert response.status_code == 200
    assert response.json()["request"]["user_id"] is None
    assert _owners(world.store) == [None]
    assert SavedSearchList.model_validate(world.get("/api/me/searches", A).json()).items == []


def test_a_signed_in_user_cannot_save_a_search_under_someone_elses_id(world: World) -> None:
    world.client.post("/api/search", json=_request(str(B)), headers=bearer(SIGNER.token(A)))
    assert _owners(world.store) == [A]


def test_a_bad_token_on_a_search_is_a_guest_never_a_401(world: World) -> None:
    response = world.client.post("/api/search", json=_request(), headers=bearer("not-a-token"))
    assert response.status_code == 200
    assert _owners(world.store) == [None]


def _finished_run(world: World, headers: dict[str, str]) -> str:
    """Start a run with these headers and wait, reading it with the same headers, until it has ended."""
    started = world.client.post("/api/search/runs", json=_request(str(B)), headers=headers)
    assert started.status_code == 202, started.text
    search_id: str = started.json()["search_id"]
    deadline = time.monotonic() + 30
    while world.client.get(f"/api/search/runs/{search_id}", headers=headers).json()["status"] in ("queued", "running"):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    return search_id


def test_a_signed_in_search_run_is_saved_with_the_users_id_and_a_guests_without(world: World) -> None:
    _finished_run(world, bearer(SIGNER.token(A)))
    _finished_run(world, {})
    assert _owners(world.store) == [A, None]


# --- owned runs: only the owner's token reads them (review of 1202aa9) ---------------------------------------------

EVIDENCE = "evidence?product_id=prd_not_in_it&criterion_key=weight"


def _run_routes(world: World, search_id: str, headers: dict[str, str]) -> dict[str, Any]:
    """Every way to read or touch a run: its state, its events, its evidence, and cancelling it."""
    base = f"/api/search/runs/{search_id}"
    return {
        "state": world.client.get(base, headers=headers),
        "events": world.client.get(f"{base}/events", headers=headers),
        "evidence": world.client.get(f"{base}/{EVIDENCE}", headers=headers),
        "cancel": world.client.delete(base, headers=headers),
    }


def _unknown(response: Any) -> bool:
    return response.status_code == 404 and response.json()["detail"] in (RUN_NOT_FOUND, NO_SUCH_SEARCH)


def test_the_owner_reads_their_run_in_every_way(world: World) -> None:
    mine = bearer(SIGNER.token(A))
    search_id = _finished_run(world, mine)
    answers = _run_routes(world, search_id, mine)
    assert answers["state"].status_code == 200
    assert answers["events"].status_code == 200 and "event: run" in answers["events"].text
    assert answers["evidence"].status_code == 404 and answers["evidence"].json()["detail"] == NO_PRODUCT  # it got in
    assert answers["cancel"].status_code == 200  # too late to stop: the state, unchanged


@pytest.mark.parametrize(
    "headers",
    [
        {},
        bearer(SIGNER.token(B)),
        bearer("not-a-token"),
        bearer(SIGNER.token(A, exp=int(time.time()) - 600, iat=int(time.time()) - 4200)),
    ],
    ids=["no-token", "another-user", "bad-token", "expired-token"],
)
def test_anyone_else_gets_404_as_for_a_run_that_never_existed(world: World, headers: dict[str, str]) -> None:
    search_id = _finished_run(world, bearer(SIGNER.token(A)))
    for route, response in _run_routes(world, search_id, headers).items():
        assert _unknown(response), route  # 404, never 403: the ID is never confirmed
    never = _run_routes(world, "srch_" + "0" * 24, headers)
    assert {route: r.json() for route, r in _run_routes(world, search_id, headers).items()} == {
        route: r.json() for route, r in never.items()
    }  # word for word what an unknown ID gets


def test_a_guest_run_is_read_as_before_by_anyone(world: World) -> None:
    search_id = _finished_run(world, {})
    for headers in ({}, bearer(SIGNER.token(A))):
        answers = _run_routes(world, search_id, headers)
        assert answers["state"].status_code == 200
        assert answers["events"].status_code == 200
        assert answers["evidence"].json()["detail"] == NO_PRODUCT
        assert answers["cancel"].status_code == 200


def test_no_run_state_or_event_ever_shows_the_user_id(world: World) -> None:
    mine = bearer(SIGNER.token(A))
    started = world.client.post("/api/search/runs", json=_request(), headers=mine)
    search_id = started.json()["search_id"]
    states = []
    deadline = time.monotonic() + 30
    while True:
        state = world.client.get(f"/api/search/runs/{search_id}", headers=mine)
        states.append(state.text)
        if state.json()["status"] not in ("queued", "running"):
            break
        assert time.monotonic() < deadline
        time.sleep(0.02)
    events = world.client.get(f"/api/search/runs/{search_id}/events", headers=mine).text
    for text in (*states, events):
        assert str(A) not in text and A.hex not in text
    final = world.client.get(f"/api/search/runs/{search_id}", headers=mine).json()
    assert final["result"]["request"]["user_id"] is None
    assert _owners(world.store) == [A]  # saved with the id all the same


def test_a_signed_in_request_is_saved_with_the_users_id_and_a_guests_without(world: World) -> None:
    signed = world.client.post("/api/presearch/intake", json={"text": ex.QUERY}, headers=bearer(SIGNER.token(A)))
    guest = world.client.post("/api/presearch/intake", json={"text": ex.QUERY})
    bad = world.client.post("/api/presearch/intake", json={"text": ex.QUERY}, headers=bearer("not-a-token"))
    assert (signed.status_code, guest.status_code, bad.status_code) == (200, 200, 200)
    assert [row.user_id for row in world.store.rows] == [A, None, None]


def test_signing_in_later_never_links_the_guests_earlier_rows(world: World) -> None:
    """user_id is set only when a row is inserted: the guest's rows stay unlinked, the new ones are the user's."""
    guest = world.client.post("/api/presearch/intake", json={"text": ex.QUERY})
    session_id = guest.json()["session_id"]
    world.client.post("/api/search", json={**_request(), "session_id": session_id})
    world.client.post(
        "/api/presearch/intake", json={"text": ex.QUERY, "session_id": session_id}, headers=bearer(SIGNER.token(A))
    )
    world.client.post("/api/search", json={**_request(), "session_id": session_id}, headers=bearer(SIGNER.token(A)))
    assert [row.user_id for row in world.store.rows] == [None, A]
    assert _owners(world.store) == [None, A]


def test_nothing_logs_a_token_or_a_user_id(world: World, caplog: pytest.LogCaptureFixture) -> None:
    token = SIGNER.token(A)
    bad = SIGNER.token(A, iss="https://otherproject.supabase.test/auth/v1")
    with caplog.at_level(logging.DEBUG):
        world.client.get("/api/me", headers=bearer(token))
        world.client.get("/api/me", headers=bearer(bad))
        world.client.post("/api/search", json=_request(), headers=bearer(token))
        world.store.fail = True
        world.client.get("/api/me/searches", headers=bearer(token))
    for secret in (token, bad, str(A), A.hex):
        assert secret not in caplog.text


def test_the_browser_may_send_the_token() -> None:
    with TestClient(create_app(env={})) as client:
        response = client.options(
            "/api/me",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    assert response.status_code == 200
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_saved_rows_keep_their_owner_as_given() -> None:
    row: SearchRow = search_row(ex.SESSION_ID, {}, {}, A)
    assert row.user_id == A
    with pytest.raises(ValueError):
        search_row(ex.SESSION_ID, {}, {}, str(A))  # type: ignore[arg-type]
    assert json.loads(row.result) == {}


def test_an_owned_run_that_has_gone_leaves_no_trace_and_a_guests_says_expired(world: World) -> None:
    from app.api.search_runs import ExpiredRun, RunSettings, UnknownRun

    clock = {"now": 0.0}
    wall = {"now": datetime(2026, 11, 20, 8, 0, tzinfo=UTC)}
    runs = SearchRuns(
        world.runner,
        RunSettings(workers=2, keep_s=60.0, ping_s=15.0, max_kept=100),
        clock=lambda: clock["now"],
        wall_clock=lambda: wall["now"],
    )
    try:
        owner = str(A)
        plain = SearchRequest(session_id=ex.SESSION_ID, raw_query=ex.QUERY, rubric=ex.rubric())
        owned = runs.start(SearchRequest.model_validate({**plain.model_dump(), "user_id": owner}))
        guest = runs.start(plain)
        assert owned is not None and guest is not None
        deadline = time.monotonic() + 30
        while runs.get(owned, owner).status in ("queued", "running") or runs.get(guest).status in ("queued", "running"):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        clock["now"] += 61
        wall["now"] += timedelta(seconds=61)
        runs.drop_expired()
        with pytest.raises(UnknownRun) as gone:
            runs.get(owned, owner)
        assert not isinstance(gone.value, ExpiredRun)  # not even its owner learns it existed
        with pytest.raises(ExpiredRun):
            runs.get(guest)
    finally:
        runs.close()


def _held(world: World) -> Any:
    """Swap in runs whose research stage waits at a gate, so a test can act while an owned run is still going."""
    from tests.unit.api.test_search_runs_api import Gate, Held

    gate = Gate()

    def build() -> PipelineRunner:
        stages = default_search_stages()
        stages.research = Held(stages.research, gate)
        return PipelineRunner(stages, DatabaseResultStore(world.store))

    world.app.state.search_runs.close()
    world.app.state.search_runs = SearchRuns(build, load_run_settings({}))
    return gate


def test_a_stranger_cannot_cancel_a_running_owned_run(world: World) -> None:
    gate = _held(world)
    mine, theirs = bearer(SIGNER.token(A)), bearer(SIGNER.token(B))
    search_id = world.client.post("/api/search/runs", json=_request(), headers=mine).json()["search_id"]
    try:
        assert gate.entered.wait(10)
        for headers in (theirs, {}):
            assert _unknown(world.client.delete(f"/api/search/runs/{search_id}", headers=headers))
        assert world.client.get(f"/api/search/runs/{search_id}", headers=mine).json()["status"] == "running"
        assert world.client.delete(f"/api/search/runs/{search_id}", headers=mine).status_code == 202  # the owner can
    finally:
        gate.opened.set()
    deadline = time.monotonic() + 30
    while world.client.get(f"/api/search/runs/{search_id}", headers=mine).json()["status"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert world.client.get(f"/api/search/runs/{search_id}", headers=mine).json()["status"] == "cancelled"


def test_a_stranger_who_has_seen_every_event_still_gets_404_not_204(world: World) -> None:
    mine = bearer(SIGNER.token(A))
    search_id = _finished_run(world, mine)
    newest = str(len(world.client.get(f"/api/search/runs/{search_id}", headers=mine).json()["progress"]) + 1)
    path = f"/api/search/runs/{search_id}/events"
    assert world.client.get(path, headers={**mine, "Last-Event-ID": newest}).status_code == 204
    for headers in ({}, bearer(SIGNER.token(B))):
        assert _unknown(world.client.get(path, headers={**headers, "Last-Event-ID": newest}))


def test_a_result_that_cannot_lose_its_owner_ends_the_run_as_failed(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never shown with the id, and the run still ends cleanly (build rule 9)."""
    monkeypatch.setattr("app.api.search_runs._without_owner", lambda result: None)
    mine = bearer(SIGNER.token(A))
    search_id = _finished_run(world, mine)
    state = world.client.get(f"/api/search/runs/{search_id}", headers=mine)
    assert state.status_code == 200
    assert state.json()["status"] == "failed" and state.json()["result"] is None
    events = world.client.get(f"/api/search/runs/{search_id}/events", headers=mine)
    assert events.status_code == 200 and "event: run" in events.text and str(A) not in events.text
