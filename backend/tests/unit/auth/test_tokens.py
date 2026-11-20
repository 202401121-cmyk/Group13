"""US-30: the backend checks a Supabase access token itself (P-24), against the project's JWT signing keys (JWKS).

Signature, expiry, issuer and audience are all checked, and the backend holds no secret the browser has. Keys are
cached and fetched again once when a token names a key id the cache doesn't have. Every token here is signed locally
by tests/fakes/auth.py; no real account, key or project is used.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid

import jwt
import pytest

from app.auth import (
    AuthUnavailable,
    InvalidToken,
    KeySet,
    TokenVerifier,
    auth_settings_from,
    load_auth_settings,
)
from app.auth.keys import KEYS_KEEP_S, KEYS_STALE_S, UNKNOWN_KEY_GAP_S
from tests.fakes.auth import ISSUER, SETTINGS, Clock, FakeKeyFetch, TestSigner, jwks, verifier_for

USER = uuid.UUID("0b8f4c1e-6a2d-4e3f-9b7c-5d1a2e3f4a5b")


@pytest.fixture(scope="module")
def signer() -> TestSigner:
    return TestSigner()


# --- a good token -------------------------------------------------------------------------------------------------


def test_a_good_token_names_its_user(signer: TestSigner) -> None:
    verifier, fetch = verifier_for(signer)
    assert verifier.verify(signer.token(USER)).id == USER
    assert fetch.calls == 1


def test_an_rs256_key_works_too() -> None:
    rsa_signer = TestSigner(kid="rsa-key", kty="RSA")
    verifier, _ = verifier_for(rsa_signer)
    assert verifier.verify(rsa_signer.token(USER)).id == USER


def test_an_audience_list_holding_authenticated_is_accepted(signer: TestSigner) -> None:
    verifier, _ = verifier_for(signer)
    assert verifier.verify(signer.token(USER, aud=["authenticated", "other"])).id == USER


# --- refused tokens -----------------------------------------------------------------------------------------------


def _refused(verifier: TokenVerifier, token: str) -> str:
    with pytest.raises(InvalidToken) as caught:
        verifier.verify(token)
    return str(caught.value)


def test_an_expired_token_is_refused(signer: TestSigner) -> None:
    verifier, _ = verifier_for(signer)
    past = int(time.time()) - 600
    assert "expired" in _refused(verifier, signer.token(USER, exp=past, iat=past - 3600))


def test_a_token_from_another_issuer_is_refused(signer: TestSigner) -> None:
    verifier, _ = verifier_for(signer)
    _refused(verifier, signer.token(USER, iss="https://otherproject.supabase.test/auth/v1"))


def test_a_token_for_another_audience_is_refused(signer: TestSigner) -> None:
    verifier, _ = verifier_for(signer)
    _refused(verifier, signer.token(USER, aud="anon"))


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "iss", "aud"])
def test_a_token_missing_a_required_claim_is_refused(signer: TestSigner, missing: str) -> None:
    verifier, _ = verifier_for(signer)
    _refused(verifier, signer.token(USER, **{missing: None}))


def test_a_token_signed_by_another_key_with_the_same_id_is_refused(signer: TestSigner) -> None:
    impostor = TestSigner(kid=signer.kid)
    verifier, _ = verifier_for(signer)
    _refused(verifier, impostor.token(USER))


@pytest.mark.parametrize(
    "claims",
    [
        {"role": "anon"},
        {"role": "service_role"},
        {"is_anonymous": True},  # an anonymous Supabase user is not a Google account (D-36)
        {"sub": "not-a-uuid"},
    ],
)
def test_a_token_that_is_not_a_signed_in_shopper_is_refused(signer: TestSigner, claims: dict[str, object]) -> None:
    verifier, _ = verifier_for(signer)
    _refused(verifier, signer.token(USER, **claims))


def _unsigned(header: dict[str, str], payload: dict[str, object]) -> str:
    def part(data: dict[str, object] | dict[str, str]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part(header)}.{part(payload)}."


def test_alg_none_is_refused(signer: TestSigner) -> None:
    verifier, _ = verifier_for(signer)
    now = int(time.time())
    payload = {"iss": ISSUER, "aud": "authenticated", "exp": now + 60, "iat": now, "sub": str(USER)}
    _refused(
        verifier, _unsigned({"alg": "none", "kid": signer.kid, "typ": "JWT"}, {**payload, "role": "authenticated"})
    )


def test_a_shared_secret_token_is_refused(signer: TestSigner) -> None:
    """HS256 would need a secret the backend never holds; a token signed with the public key as a secret fails too."""
    verifier, _ = verifier_for(signer)
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": "authenticated",
        "exp": now + 60,
        "iat": now,
        "sub": str(USER),
        "role": "authenticated",
    }
    token = jwt.encode(payload, "a-shared-secret-of-thirty-two-bytes!", algorithm="HS256", headers={"kid": signer.kid})
    _refused(verifier, token)


@pytest.mark.parametrize("token", ["", "abc", "a.b.c", "x" * 9000])
def test_garbage_is_refused(signer: TestSigner, token: str) -> None:
    verifier, _ = verifier_for(signer)
    _refused(verifier, token)


def test_a_token_without_a_key_id_is_refused(signer: TestSigner) -> None:
    verifier, _ = verifier_for(signer)
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": "authenticated",
        "exp": now + 60,
        "iat": now,
        "sub": str(USER),
        "role": "authenticated",
    }
    _refused(verifier, jwt.encode(payload, signer.private_key, algorithm="ES256"))


def test_a_refusal_never_repeats_the_token(signer: TestSigner, caplog: pytest.LogCaptureFixture) -> None:
    verifier, _ = verifier_for(signer)
    token = signer.token(USER, iss="https://otherproject.supabase.test/auth/v1")
    with caplog.at_level(logging.DEBUG):
        message = _refused(verifier, token)
    assert token not in message and token not in caplog.text and str(USER) not in caplog.text


# --- the key set --------------------------------------------------------------------------------------------------


def test_keys_are_cached(signer: TestSigner) -> None:
    clock = Clock()
    verifier, fetch = verifier_for(signer, clock=clock)
    for _ in range(5):
        verifier.verify(signer.token(USER))
    assert fetch.calls == 1
    clock.now += KEYS_KEEP_S + 1  # Supabase's edge caches the key set for 10 minutes: never keep it longer
    verifier.verify(signer.token(USER))
    assert fetch.calls == 2


def test_an_unknown_key_id_fetches_the_keys_again_once() -> None:
    """A key rotation: the token names a key published after the cache was filled."""
    old, new = TestSigner(kid="old"), TestSigner(kid="new")
    clock = Clock()
    fetch = FakeKeyFetch(jwks(old))
    verifier = TokenVerifier(SETTINGS, KeySet(fetch, clock=clock))
    verifier.verify(old.token(USER))
    clock.now += UNKNOWN_KEY_GAP_S
    fetch.document = jwks(old, new)
    assert verifier.verify(new.token(USER)).id == USER
    assert fetch.calls == 2


def test_an_unknown_key_id_is_refused_after_one_more_fetch_and_does_not_hammer_the_keys(signer: TestSigner) -> None:
    clock = Clock()
    verifier, fetch = verifier_for(signer, clock=clock)
    verifier.verify(signer.token(USER))
    clock.now += UNKNOWN_KEY_GAP_S
    stranger = TestSigner(kid="nobody-published-this")
    _refused(verifier, stranger.token(USER))
    assert fetch.calls == 2
    for _ in range(10):  # forged key ids never fetch again until the gap has passed
        _refused(verifier, stranger.token(USER))
    assert fetch.calls == 2
    clock.now += UNKNOWN_KEY_GAP_S
    _refused(verifier, stranger.token(USER))
    assert fetch.calls == 3


def test_keys_that_cannot_be_fetched_make_sign_in_unavailable(signer: TestSigner) -> None:
    clock = Clock()
    verifier, fetch = verifier_for(signer, clock=clock)
    fetch.fail = True
    with pytest.raises(AuthUnavailable):
        verifier.verify(signer.token(USER))
    with pytest.raises(AuthUnavailable):  # within the gap: no new fetch
        verifier.verify(signer.token(USER))
    assert fetch.calls == 1
    fetch.fail = False
    clock.now += UNKNOWN_KEY_GAP_S
    assert verifier.verify(signer.token(USER)).id == USER


def test_a_key_set_with_nothing_usable_makes_sign_in_unavailable(signer: TestSigner) -> None:
    fetch = FakeKeyFetch({"keys": [{"kty": "oct", "kid": "x", "k": "c2VjcmV0"}]})  # a shared secret is never used
    verifier = TokenVerifier(SETTINGS, KeySet(fetch, clock=Clock()))
    with pytest.raises(AuthUnavailable):
        verifier.verify(signer.token(USER))


def test_keys_still_in_date_are_used_when_a_refetch_for_an_unknown_id_fails(signer: TestSigner) -> None:
    clock = Clock()
    verifier, fetch = verifier_for(signer, clock=clock)
    verifier.verify(signer.token(USER))
    clock.now += UNKNOWN_KEY_GAP_S
    fetch.fail = True
    _refused(verifier, TestSigner(kid="unknown").token(USER))
    assert verifier.verify(signer.token(USER)).id == USER


# --- settings -----------------------------------------------------------------------------------------------------


def test_the_issuer_and_key_address_come_from_the_project_url() -> None:
    settings = auth_settings_from("https://abcdefghij.supabase.co/")
    assert settings is not None
    assert settings.issuer == "https://abcdefghij.supabase.co/auth/v1"
    assert settings.jwks_url == "https://abcdefghij.supabase.co/auth/v1/.well-known/jwks.json"
    assert settings.audience == "authenticated"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        "http://abcdefghij.supabase.co",
        "https://user:pw@abcdefghij.supabase.co",
        "abcdefghij",
        "https://",
    ],
)
def test_no_or_a_bad_project_url_turns_sign_in_off(url: str | None) -> None:
    assert auth_settings_from(url) is None


def test_settings_read_supabase_url_from_the_environment() -> None:
    assert load_auth_settings({"SUPABASE_URL": "https://abcdefghij.supabase.co"}) is not None
    assert load_auth_settings({}) is None


# --- from the review: stale keys, malformed keys, a slow fetch, a mismatched algorithm ---------------------------


def test_keys_stay_in_use_up_to_twenty_minutes_while_a_refresh_fails(signer: TestSigner) -> None:
    clock = Clock()
    verifier, fetch = verifier_for(signer, clock=clock)
    verifier.verify(signer.token(USER))
    fetch.fail = True
    clock.now += KEYS_KEEP_S + 1
    assert verifier.verify(signer.token(USER)).id == USER  # the refresh failed: the keys from 10 minutes ago
    clock.now = 1000.0 + KEYS_STALE_S
    with pytest.raises(AuthUnavailable):  # never older than Supabase's own caches keep them
        verifier.verify(signer.token(USER))


def test_a_malformed_key_is_skipped_and_the_others_still_work(signer: TestSigner) -> None:
    good = signer.public_jwk()
    bad_alg = {**good, "kid": "wrong-alg", "alg": "RS256"}  # an EC key that says RS256
    no_y = {key: value for key, value in good.items() if key != "y"} | {"kid": "no-y"}
    fetch = FakeKeyFetch({"keys": [bad_alg, no_y, good, "not-a-key", {"kid": 5}]})
    verifier = TokenVerifier(SETTINGS, KeySet(fetch, clock=Clock()))
    assert verifier.verify(signer.token(USER)).id == USER


def test_a_slow_key_fetch_gives_up_at_its_deadline(signer: TestSigner) -> None:
    def slow() -> dict[str, object]:
        time.sleep(2)
        return jwks(signer)

    verifier = TokenVerifier(SETTINGS, KeySet(slow, clock=Clock(), deadline_s=0.2))
    started = time.monotonic()
    with pytest.raises(AuthUnavailable):
        verifier.verify(signer.token(USER))
    assert time.monotonic() - started < 1.5


def test_a_token_whose_algorithm_differs_from_its_keys_is_refused(signer: TestSigner) -> None:
    rsa_signer = TestSigner(kid=signer.kid, kty="RSA")  # signs RS256 but names the ES256 key's id
    verifier, _ = verifier_for(signer)
    _refused(verifier, rsa_signer.token(USER))


def test_requests_with_usable_keys_never_wait_for_a_fetch(signer: TestSigner) -> None:
    """While one request fetches the key set, another that still has keys goes on with them."""
    import threading

    clock = Clock()
    release = threading.Event()
    calls = {"n": 0}

    def fetch() -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] > 1:
            release.wait(5)
        return jwks(signer)

    verifier = TokenVerifier(SETTINGS, KeySet(fetch, clock=clock))
    verifier.verify(signer.token(USER))
    clock.now += KEYS_KEEP_S + 1  # out of date: the next request fetches again
    first = threading.Thread(target=lambda: verifier.verify(signer.token(USER)))
    first.start()
    time.sleep(0.1)
    started = time.monotonic()
    assert verifier.verify(signer.token(USER)).id == USER
    assert time.monotonic() - started < 0.5
    release.set()
    first.join(5)
