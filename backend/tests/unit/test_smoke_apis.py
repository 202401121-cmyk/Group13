"""The all-services smoke command (W2), offline: httpx.MockTransport stands in for every service."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import httpx
import pytest

from scripts.smoke_apis import SERVICES, main

FAKE = "fake-key-DO-NOT-LEAK-123"
ALL_KEYS = {
    "GROQ_API_KEY": FAKE + "-groq",
    "GEMINI_API_KEY": FAKE + "-gemini",
    "OPENROUTER_API_KEY": FAKE + "-openrouter",
    "CEREBRAS_API_KEY": FAKE + "-cerebras",
    "MISTRAL_API_KEY": FAKE + "-mistral",
    "SERPER_API_KEY": FAKE + "-serper",
    "REDDIT_CLIENT_ID": FAKE + "-reddit-id",
    "REDDIT_CLIENT_SECRET": FAKE + "-reddit-secret",
    "REDDIT_USER_AGENT": "script:shopsense:0.1 (by /u/shopsense_test)",
    "YOUTUBE_API_KEY": FAKE + "-youtube",
    "JINA_API_KEY": FAKE + "-jina",
    "HF_TOKEN": FAKE + "-hf",
}
Handler = Callable[[httpx.Request], httpx.Response]


def chat(text: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": {}})


def healthy(request: httpx.Request) -> httpx.Response:
    """Every service answering as it should."""
    host, path = request.url.host, request.url.path
    if path.endswith("/chat/completions"):
        return chat("OK")
    if host == "google.serper.dev":
        return httpx.Response(200, json={"organic": [{"title": "x"}], "credits": 1})
    if host == "www.reddit.com":
        return httpx.Response(200, json={"access_token": "t0ken", "token_type": "bearer"})
    if host == "oauth.reddit.com":
        return httpx.Response(200, json={"data": {"children": []}}, headers={"x-ratelimit-remaining": "99"})
    if host == "www.googleapis.com":
        return httpx.Response(200, json={"items": [{"id": "jNQXAC9IVRw"}]})
    if host == "r.jina.ai":
        return httpx.Response(200, text="Title: Example Domain")
    if host == "huggingface.co":
        return httpx.Response(200, json={"auth": {"accessToken": {"role": "read"}}})
    return httpx.Response(599)


def run(
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    env: dict[str, str],
    handler: Handler = healthy,
) -> tuple[int, str, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    code = main(argv, env=env, http=httpx.Client(transport=httpx.MockTransport(record)))
    return code, capsys.readouterr().out, seen


def status_of(out: str, service: str) -> str:
    line = next(line for line in out.splitlines() if line.split() and line.split()[0] == service)
    return line.split()[1]


def test_every_configured_service_passes_and_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, ["--include", "serper"], ALL_KEYS)
    assert code == 0
    for service in SERVICES:
        assert status_of(out, service) == "ok", out


def test_a_service_without_a_key_is_skipped_not_failed(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, seen = run(capsys, ["--include", "serper"], {})
    assert code == 0
    for service in SERVICES:
        if service != "jina":  # Jina Reader also works without a key
            assert status_of(out, service) == "skipped", out
    assert "not configured (GROQ_API_KEY not set)" in out
    assert {r.url.host for r in seen} == {"r.jina.ai"}


def test_reddit_needs_both_its_id_and_secret(capsys: pytest.CaptureFixture[str]) -> None:
    env = {"REDDIT_CLIENT_ID": FAKE}
    code, out, seen = run(capsys, ["--only", "reddit"], env)
    assert (code, status_of(out, "reddit"), seen) == (0, "skipped", [])


def test_one_failure_exits_1_and_the_others_still_run(capsys: pytest.CaptureFixture[str]) -> None:
    def groq_refuses(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.groq.com":
            return httpx.Response(401, json={"error": f"bad key {FAKE}-groq"})
        return healthy(request)

    code, out, _ = run(capsys, [], ALL_KEYS, groq_refuses)
    assert code == 1
    assert status_of(out, "groq") == "failed"
    assert "HTTP 401" in out
    for service in ("gemini", "openrouter", "cerebras", "mistral", "reddit", "youtube", "jina", "huggingface"):
        assert status_of(out, service) == "ok", out


def test_a_timeout_or_a_crash_fails_only_that_service(capsys: pytest.CaptureFixture[str]) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com":
            raise httpx.ReadTimeout("slow", request=request)
        if request.url.host == "huggingface.co":
            return httpx.Response(200, text="not json")
        return healthy(request)

    code, out, _ = run(capsys, ["--only", "youtube,huggingface,jina"], ALL_KEYS, broken)
    assert code == 1
    assert status_of(out, "youtube") == "failed" and "no answer within 20 s" in out
    assert status_of(out, "huggingface") == "failed" and "JSONDecodeError during the check" in out
    assert status_of(out, "jina") == "ok"


def test_serper_is_never_called_without_include(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, seen = run(capsys, [], ALL_KEYS)
    assert code == 0
    assert status_of(out, "serper") == "skipped"
    assert "google.serper.dev" not in {r.url.host for r in seen}
    _, out, seen = run(capsys, ["--only", "serper", "--include", "serper"], ALL_KEYS)
    assert [r.url.host for r in seen] == ["google.serper.dev"]
    assert status_of(out, "serper") == "ok"


def test_the_llm_check_sends_the_fixed_prompt_with_5_output_tokens(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, seen = run(capsys, ["--only", "groq"], ALL_KEYS)
    body = json.loads(seen[0].content)
    assert code == 0 and len(seen) == 1
    assert body["max_tokens"] == 5
    assert body["messages"][1]["content"] == "Reply with the single word OK."
    assert body["model"] == "openai/gpt-oss-120b"
    assert "response_format" not in body


def test_an_empty_reply_from_a_reasoning_model_is_retried_once(capsys: pytest.CaptureFixture[str]) -> None:
    def thinks(request: httpx.Request) -> httpx.Response:
        tokens = json.loads(request.content)["max_tokens"]
        return chat("" if tokens == 5 else "OK")

    code, out, seen = run(capsys, ["--only", "cerebras"], ALL_KEYS, thinks)
    assert code == 0
    assert [json.loads(r.content)["max_tokens"] for r in seen] == [5, 200]
    assert "it reasons first" in out


def test_a_thinking_model_with_no_message_text_is_retried_once(capsys: pytest.CaptureFixture[str]) -> None:
    def no_content(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["max_tokens"] == 5:
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant"}}]})
        return chat("OK")

    code, out, seen = run(capsys, ["--only", "gemini"], ALL_KEYS, no_content)
    assert (code, len(seen)) == (0, 2)
    assert status_of(out, "gemini") == "ok"


def test_a_real_error_on_the_retry_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, seen = run(capsys, ["--only", "openrouter"], ALL_KEYS, lambda request: httpx.Response(400))
    assert (code, len(seen)) == (1, 2)
    assert "openrouter: HTTP 400" in out


def test_a_reply_without_ok_fails(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, ["--only", "mistral"], ALL_KEYS, lambda request: chat("Sure thing"))
    assert code == 1
    assert "did not contain OK" in out


def test_youtube_uses_videos_list_with_the_key_in_a_header(capsys: pytest.CaptureFixture[str]) -> None:
    _, _, seen = run(capsys, ["--only", "youtube"], ALL_KEYS)
    (request,) = seen
    assert request.url.path == "/youtube/v3/videos"
    assert "key" not in request.url.params
    assert request.headers["X-Goog-Api-Key"] == ALL_KEYS["YOUTUBE_API_KEY"]


def test_reddit_gets_an_app_only_token_then_one_listing(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, seen = run(capsys, ["--only", "reddit"], ALL_KEYS)
    assert [(r.method, r.url.host) for r in seen] == [("POST", "www.reddit.com"), ("GET", "oauth.reddit.com")]
    assert seen[1].url.params["limit"] == "1"
    assert all(r.headers["User-Agent"].startswith("script:shopsense") for r in seen)
    assert "x-ratelimit-remaining: 99" in out


def test_a_placeholder_reddit_user_agent_fails(capsys: pytest.CaptureFixture[str]) -> None:
    env = {**ALL_KEYS, "REDDIT_USER_AGENT": "script:shopsense:0.1 (by /u/<your-reddit-username>)"}
    code, out, seen = run(capsys, ["--only", "reddit"], env)
    assert (code, seen) == (1, [])
    assert "REDDIT_USER_AGENT" in out


def test_a_write_token_for_hugging_face_is_flagged(capsys: pytest.CaptureFixture[str]) -> None:
    def write_token(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"auth": {"accessToken": {"role": "write"}}})

    _, out, _ = run(capsys, ["--only", "huggingface"], ALL_KEYS, write_token)
    assert "use a read-only or fine-grained read token" in out


def test_no_key_ever_reaches_the_output_or_the_logs(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    def echoes_keys(request: httpx.Request) -> httpx.Response:
        sent = request.headers.get("Authorization", "") + request.headers.get("X-API-KEY", "")
        return httpx.Response(403, text=f"refused {sent}", headers={"x-ratelimit-note": sent})

    with caplog.at_level(logging.DEBUG):
        code, out, _ = run(capsys, ["--include", "serper"], ALL_KEYS, echoes_keys)
    assert code == 1
    assert FAKE not in out
    assert FAKE not in caplog.text


def test_an_unknown_service_name_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--only", "groq,bing"], env={})
    assert "unknown service(s): bing" in capsys.readouterr().err
