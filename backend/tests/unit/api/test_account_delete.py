"""US-11: deleting the account (DELETE /api/me, P-43, D-32). The user is deleted through Supabase's Auth admin API with
the secret key, which stays in the backend; the foreign keys then remove every request, search, profile and preference.
The route answers 204 only after a read-back finds none of the user's rows. It asks for the word "delete" first; with
no secret key it answers 503 and everything else works; and the key never reaches a log line or a response.

No network: the admin API is a fake, or the real client over an httpx MockTransport."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.account import (
    CONFIRM_FIRST,
    DELETE_FAILED,
    DELETE_NOT_FINISHED,
    DELETE_UNAVAILABLE,
    SIGN_IN_TO_DELETE,
    get_account_store,
    get_auth_admin,
    get_memory_controls,
)
from app.auth import get_token_verifier
from app.auth.settings import load_admin_settings
from app.db import AccountsUnavailable, InMemoryRequestStore
from app.db.account_admin import AdminUnavailable, SupabaseAuthAdmin
from app.main import create_app
from app.memory import InMemoryPreferenceStore, MemoryService, MemorySettings
from tests.fakes.auth import TestSigner, bearer, verifier_for
from tests.unit.memory.fakes import WordEmbedder, ready_holder

A = uuid.UUID("a2000000-0000-4000-8000-00000000000a")
SIGNER = TestSigner()
SENTINEL = "sb_secret_SENTINEL-never-to-be-logged-0123456789"
PROJECT = "https://projectref.supabase.co"


class FakeAdmin:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.deleted: list[uuid.UUID] = []

    def delete_user(self, user_id: uuid.UUID) -> None:
        if self.fail:
            raise AdminUnavailable("Supabase Auth answered 500")
        self.deleted.append(user_id)


class Accounts(InMemoryRequestStore):
    """The read-back as the test sets it: `reads` answers each read in turn (a count of left rows, or None for the
    database being down); the last answer repeats."""

    def __init__(self, *reads: int | None) -> None:
        super().__init__()
        self.reads = list(reads) or [0]
        self.asked = 0

    def account_rows(self, user_id: uuid.UUID) -> dict[str, int]:
        left = self.reads[min(self.asked, len(self.reads) - 1)]
        self.asked += 1
        if left is None:
            raise AccountsUnavailable("the database is down")
        return {"requests": left, "searches": 0, "profiles": 0, "preferences": 0}


def make_client(admin: Any, accounts: Accounts | None = None) -> TestClient:
    verifier, _ = verifier_for(SIGNER)
    app = create_app(env={})
    app.dependency_overrides[get_token_verifier] = lambda: verifier
    app.dependency_overrides[get_account_store] = lambda: accounts or Accounts()
    app.dependency_overrides[get_auth_admin] = lambda: admin
    memory = MemoryService(MemorySettings(), ready_holder(WordEmbedder()), InMemoryPreferenceStore())
    app.dependency_overrides[get_memory_controls] = lambda: memory
    return TestClient(app)


def delete_me(client: TestClient, body: Any = None, user: uuid.UUID | None = A) -> httpx.Response:
    headers = bearer(SIGNER.token(user)) if user else {}
    return client.request("DELETE", "/api/me", headers=headers, **({"json": body} if body is not None else {}))


@pytest.mark.parametrize("body", [None, {}, {"confirm": ""}, {"confirm": "yes"}, {"confirm": "delete my stuff"}])
def test_without_the_word_delete_nothing_is_deleted(body: Any) -> None:
    admin = FakeAdmin()
    response = delete_me(make_client(admin), body)
    assert response.status_code == 422 and response.json()["detail"] == CONFIRM_FIRST
    assert admin.deleted == []


@pytest.mark.parametrize("word", ["delete", "Delete", " DELETE "])
def test_the_word_delete_deletes_the_user_and_answers_204_after_the_read_back(word: str) -> None:
    admin = FakeAdmin()
    response = delete_me(make_client(admin), {"confirm": word})
    assert response.status_code == 204 and response.content == b""
    assert admin.deleted == [A]


def test_a_guest_cant_delete_anything() -> None:
    admin = FakeAdmin()
    response = delete_me(make_client(admin), {"confirm": "delete"}, user=None)
    assert response.status_code == 401 and response.json()["detail"] == SIGN_IN_TO_DELETE
    assert admin.deleted == []


def test_with_no_secret_key_the_delete_is_503_and_everything_else_works() -> None:
    client = make_client(None)
    response = delete_me(client, {"confirm": "delete"})
    assert response.status_code == 503 and response.json()["detail"] == DELETE_UNAVAILABLE
    listed = client.get("/api/me/preferences", headers=bearer(SIGNER.token(A)))
    assert listed.status_code == 200


def test_the_admin_api_failing_is_503_and_nothing_is_deleted() -> None:
    accounts = Accounts(2)
    response = delete_me(make_client(FakeAdmin(fail=True), accounts), {"confirm": "delete"})
    assert response.status_code == 503 and response.json()["detail"] == DELETE_FAILED
    assert accounts.asked == 1  # only the check before the delete


def test_rows_left_after_the_delete_are_never_answered_204() -> None:
    """A row the foreign keys didn't remove means the user wasn't really deleted: 503, never 204."""
    admin = FakeAdmin()
    response = delete_me(make_client(admin, Accounts(0, 2)), {"confirm": "delete"})
    assert response.status_code == 503 and response.json()["detail"] == DELETE_NOT_FINISHED
    assert admin.deleted == [A]


def test_the_database_down_before_the_delete_deletes_nothing() -> None:
    admin = FakeAdmin()
    response = delete_me(make_client(admin, Accounts(None)), {"confirm": "delete"})
    assert response.status_code == 503 and response.json()["detail"] == DELETE_FAILED
    assert admin.deleted == []  # the result couldn't be checked, so Supabase is never asked


def test_the_database_down_for_the_read_back_is_503_and_asking_again_finishes_it() -> None:
    admin = FakeAdmin()
    client = make_client(admin, Accounts(0, None, 0))
    first = delete_me(client, {"confirm": "delete"})
    assert first.status_code == 503 and first.json()["detail"] == DELETE_NOT_FINISHED
    assert delete_me(client, {"confirm": "delete"}).status_code == 204  # the user is gone; the read-back now answers


# --- the real admin client, over a fake network -----------------------------------------------------------------------


def admin_over(handler: Any) -> SupabaseAuthAdmin:
    settings = load_admin_settings({"SUPABASE_URL": PROJECT, "SUPABASE_SECRET_KEY": SENTINEL})
    assert settings is not None
    return SupabaseAuthAdmin(settings, transport=httpx.MockTransport(handler))


def test_the_admin_call_sends_the_key_on_apikey_only_and_asks_for_a_hard_delete() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    admin_over(handler).delete_user(A)
    [request] = seen
    assert (request.method, str(request.url)) == ("DELETE", f"{PROJECT}/auth/v1/admin/users/{A}")
    assert request.headers["apikey"] == SENTINEL and "authorization" not in request.headers
    assert json.loads(request.content) == {"should_soft_delete": False}


def test_a_user_already_gone_counts_as_deleted() -> None:
    admin_over(lambda request: httpx.Response(404, json={"msg": "User not found"})).delete_user(A)


@pytest.mark.parametrize("status", [401, 403, 500, 502])
def test_any_other_answer_is_admin_unavailable(status: int) -> None:
    with pytest.raises(AdminUnavailable, match=str(status)):
        admin_over(lambda request: httpx.Response(status, json={"msg": "no"})).delete_user(A)


def test_a_key_that_cant_go_in_a_header_is_admin_unavailable_and_never_shown() -> None:
    settings = load_admin_settings({"SUPABASE_URL": PROJECT, "SUPABASE_SECRET_KEY": "sb_secret_“quoted”"})
    assert settings is not None
    admin = SupabaseAuthAdmin(settings, transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    with pytest.raises(AdminUnavailable) as raised:
        admin.delete_user(A)
    assert "quoted" not in raised.value.reason


def test_the_network_failing_is_admin_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AdminUnavailable, match="ConnectError"):
        admin_over(handler).delete_user(A)


@pytest.fixture
def every_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    caplog.set_level(logging.DEBUG)
    yield caplog


@pytest.mark.parametrize("status", [200, 500])
def test_the_secret_key_never_appears_in_a_log_or_a_response(every_log: pytest.LogCaptureFixture, status: int) -> None:
    admin = admin_over(lambda request: httpx.Response(status, json={"msg": SENTINEL}))
    response = delete_me(make_client(admin), {"confirm": "delete"})
    assert response.status_code in (204, 503)
    assert SENTINEL not in response.text
    assert all(SENTINEL not in r.getMessage() and SENTINEL not in str(r.args) for r in every_log.records)
    assert SENTINEL not in every_log.text
    settings = load_admin_settings({"SUPABASE_URL": PROJECT, "SUPABASE_SECRET_KEY": SENTINEL})
    assert SENTINEL not in repr(settings) and SENTINEL not in str(settings) and SENTINEL not in repr(admin)


# --- settings ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "on"),
    [
        ({"SUPABASE_URL": PROJECT, "SUPABASE_SECRET_KEY": SENTINEL}, True),
        ({"SUPABASE_URL": PROJECT}, False),
        ({"SUPABASE_URL": PROJECT, "SUPABASE_SECRET_KEY": "  "}, False),
        ({"SUPABASE_SECRET_KEY": SENTINEL}, False),
        ({"SUPABASE_URL": "http://insecure.example", "SUPABASE_SECRET_KEY": SENTINEL}, False),
        ({"SUPABASE_URL": PROJECT, "SUPABASE_SECRET_KEY": "sb_publishable_browserkey"}, False),
    ],
)
def test_the_admin_settings_need_the_project_and_a_secret_key(env: dict[str, str], on: bool) -> None:
    assert (load_admin_settings(env) is not None) is on
