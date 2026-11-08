"""Why a stage fell back, in fixed plain words chosen by the error's kind, never its text (US-12)."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.contracts import LLMUnavailable, Provider
from app.llm.errors import (
    AuthFailed,
    InvalidOutput,
    ModelForbidden,
    ModelNotFound,
    ProviderTimeout,
    RateLimited,
    ServerError,
)
from app.pipeline import (
    REPLY_UNREADABLE,
    SERVICE_BUSY,
    SERVICE_REFUSED,
    SERVICE_UNREACHABLE,
    SOMETHING_WENT_WRONG,
    TOOK_TOO_LONG,
    as_clause,
    plain_reason,
)

SECRET = "not-a-real-key-" + "c41d"  # made up, and built at run time


def _url() -> str:
    return str(httpx.URL("https://api.example.test/v1/search", params={"key": SECRET}))


def _http(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", _url())
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status} for url {_url()}", request=request, response=response)


class _Reply(BaseModel):
    score: int


def _invalid_reply() -> ValidationError:
    try:
        _Reply.model_validate({"score": f"not a number {SECRET}"})
    except ValidationError as exc:
        return exc
    raise AssertionError("the reply was valid")


def _bad_json() -> json.JSONDecodeError:
    try:
        json.loads("{not json " + SECRET)
    except json.JSONDecodeError as exc:
        return exc
    raise AssertionError("the JSON was valid")


def _raised_from(cause: BaseException) -> Exception:
    """A stage's own error, raised `from` the error that caused it."""
    try:
        try:
            raise cause
        except BaseException as inner:
            raise RuntimeError(f"the search for {SECRET} failed") from inner
    except RuntimeError as outer:
        return outer


def _raised_while_handling(earlier: BaseException) -> Exception:
    """A bug in an error handler: a new error that merely happened while another was being handled."""
    try:
        try:
            raise earlier
        except BaseException:
            raise TypeError(f"handler bug with {SECRET}")  # noqa: B904 - deliberately not `from`
    except TypeError as new:
        return new


class _UnreadableError(Exception):
    """An error whose response cannot even be looked at."""

    @property
    def response(self) -> object:
        raise RuntimeError("cannot read the response")


CASES = [
    ("timeout", TimeoutError(f"slow {SECRET}"), TOOK_TOO_LONG),
    ("httpx timeout", httpx.ReadTimeout(f"read timed out {_url()}"), TOOK_TOO_LONG),
    ("provider timeout", ProviderTimeout(Provider.GROQ, "no reply"), TOOK_TOO_LONG),
    ("401", _http(401), SERVICE_REFUSED),
    ("403", _http(403), SERVICE_REFUSED),
    ("provider 401", AuthFailed(Provider.GROQ, "HTTP 401"), SERVICE_REFUSED),
    ("provider 403", ModelForbidden(Provider.GROQ, "HTTP 403"), SERVICE_REFUSED),
    ("429", _http(429), SERVICE_BUSY),
    ("provider 429", RateLimited(Provider.GROQ, "HTTP 429"), SERVICE_BUSY),
    ("500", _http(500), SERVICE_UNREACHABLE),
    ("503", _http(503), SERVICE_UNREACHABLE),
    ("provider 5xx", ServerError(Provider.GROQ, "HTTP 502"), SERVICE_UNREACHABLE),
    ("connect error", httpx.ConnectError(f"cannot connect to {_url()}"), SERVICE_UNREACHABLE),
    ("connection refused", ConnectionRefusedError(f"refused {SECRET}"), SERVICE_UNREACHABLE),
    ("invalid reply", _invalid_reply(), REPLY_UNREADABLE),
    ("bad json", _bad_json(), REPLY_UNREADABLE),
    ("other 4xx", _http(404), SOMETHING_WENT_WRONG),
    ("provider 404", ModelNotFound(Provider.GROQ, "HTTP 404"), SOMETHING_WENT_WRONG),
    ("anything else", RuntimeError(f"crashed on {SECRET}"), SOMETHING_WENT_WRONG),
    ("unreadable model reply", InvalidOutput(f"the reply has no complete JSON object: {SECRET}"), REPLY_UNREADABLE),
    ("raised from a 429", _raised_from(_http(429)), SERVICE_BUSY),
    ("raised from nothing known", _raised_from(KeyError(SECRET)), SOMETHING_WENT_WRONG),
    ("a handler bug after a timeout", _raised_while_handling(httpx.ReadTimeout("slow")), SOMETHING_WENT_WRONG),
    # The LLM wrapper raises this after every provider failed, with no cause attached (app/llm/client.py).
    ("every provider failed", LLMUnavailable("every provider failed"), SOMETHING_WENT_WRONG),
    ("an error that cannot be read", _UnreadableError(SECRET), SOMETHING_WENT_WRONG),
]


@pytest.mark.parametrize(("error", "reason"), [(e, r) for _, e, r in CASES], ids=[name for name, _, _ in CASES])
def test_each_kind_of_error_gets_its_fixed_reason(error: BaseException, reason: str) -> None:
    assert plain_reason(error) == reason
    assert SECRET not in plain_reason(error)


def test_there_are_six_reasons_and_each_is_short_and_plain() -> None:
    reasons = {
        TOOK_TOO_LONG,
        SERVICE_REFUSED,
        SERVICE_BUSY,
        SERVICE_UNREACHABLE,
        REPLY_UNREADABLE,
        SOMETHING_WENT_WRONG,
    }
    assert {plain_reason(e) for _, e, _ in CASES} == reasons
    assert all(r == r.lower() and not r.endswith(".") and len(r.split()) <= 6 for r in reasons)


def test_an_error_that_causes_itself_does_not_loop() -> None:
    error = RuntimeError("loops")
    error.__cause__ = error
    assert plain_reason(error) == SOMETHING_WENT_WRONG


@pytest.mark.parametrize(
    ("reason", "clause"),
    [
        (TOOK_TOO_LONG, "it took too long"),
        (SERVICE_BUSY, SERVICE_BUSY),
        (f"{SOMETHING_WENT_WRONG}; {TOOK_TOO_LONG}", f"{SOMETHING_WENT_WRONG}; it took too long"),
        (None, SOMETHING_WENT_WRONG),
    ],
)
def test_a_reason_reads_as_a_clause_in_a_sentence(reason: str | None, clause: str) -> None:
    assert as_clause(reason) == clause
