"""The all-services smoke command (W2), offline: httpx.MockTransport stands in for every service."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.contracts import Provider
from app.contracts.research import MAX_SOURCE_CHARS
from app.llm import load_llm_config
from app.llm.config import LLMConfigError
from scripts.smoke_apis import SERVICES, TIMEOUT_S, limit_differences, llm_check, main, recorded_thread

FAKE = "fake-key-DO-NOT-LEAK-123"
GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
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


def test_it_builds_its_client_the_way_the_app_does(monkeypatch: pytest.MonkeyPatch) -> None:
    """So the smoke command sees what the app sees, stall or no stall."""
    built: list[tuple[bool, dict[str, Any]]] = []

    def build(force_ipv4: bool, **options: Any) -> httpx.Client:
        built.append((force_ipv4, options))
        return httpx.Client(transport=httpx.MockTransport(healthy))

    monkeypatch.setattr("scripts.smoke_apis.build_http_client", build)
    assert main(["--only", "groq"], env=ALL_KEYS) == 0
    assert built == [(load_llm_config().force_ipv4, {"timeout": TIMEOUT_S})]


def test_a_broken_model_config_does_not_stop_the_other_checks(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The LLM rows report the broken file themselves, and one failure never stops the others."""
    built: list[bool] = []

    def broken() -> None:
        raise LLMConfigError("models.yaml is broken")

    def build(force_ipv4: bool, **options: Any) -> httpx.Client:
        built.append(force_ipv4)
        return httpx.Client(transport=httpx.MockTransport(healthy))

    monkeypatch.setattr("scripts.smoke_apis.load_llm_config", broken)
    monkeypatch.setattr("scripts.smoke_apis.build_http_client", build)

    code = main(["--only", "groq,youtube"], env=ALL_KEYS)

    out = capsys.readouterr().out
    assert status_of(out, "youtube") == "ok"
    assert status_of(out, "groq") == "failed"
    assert code == 1
    assert built == [True]


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


def test_the_llm_check_uses_the_providers_reasoning_setting() -> None:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        reply = {"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 9, "completion_tokens": 1}}
        return httpx.Response(200, json=reply)

    http = httpx.Client(transport=httpx.MockTransport(record))
    llm_check(Provider.GEMINI)({"GEMINI_API_KEY": "a-made-up-key"}, http)
    assert json.loads(seen[0].content)["reasoning_effort"] == "none"


def test_the_llm_check_sends_the_fixed_prompt_with_5_output_tokens(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, seen = run(capsys, ["--only", "gemini"], ALL_KEYS)
    body = json.loads(seen[0].content)
    assert code == 0 and len(seen) == 1
    assert body["max_tokens"] == 5
    assert body["messages"][1]["content"] == "Reply with the single word OK."
    assert body["model"] == "gemini-3.5-flash"
    assert "response_format" not in body


def test_groq_gets_one_call_per_pool_member(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, seen = run(capsys, ["--only", "groq"], ALL_KEYS)
    assert [json.loads(r.content)["model"] for r in seen] == GROQ_MODELS
    assert {json.loads(r.content)["max_tokens"] for r in seen} == {200}  # one call each: no 5-token try first
    assert code == 0
    assert [line.split()[1] for line in out.splitlines() if line.startswith("groq ")] == ["ok", "ok", "ok"]
    assert all(model in out for model in GROQ_MODELS)


def test_one_groq_model_failing_keeps_its_own_row(capsys: pytest.CaptureFixture[str]) -> None:
    def qwen_is_gone(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        return httpx.Response(404) if model.startswith("qwen") else chat("OK")

    code, out, seen = run(capsys, ["--only", "groq"], ALL_KEYS, qwen_is_gone)
    assert (code, len(seen)) == (1, 3)
    rows = [line.split()[1] for line in out.splitlines() if line.startswith("groq ")]
    assert rows == ["ok", "ok", "failed"]


def limits(tokens: str, requests: str) -> httpx.Response:
    headers = {"x-ratelimit-limit-tokens": tokens, "x-ratelimit-limit-requests": requests}
    return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}], "usage": {}}, headers=headers)


def test_each_models_headers_are_printed_and_match_the_config(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, ["--only", "groq"], ALL_KEYS, lambda request: limits("8000", "1000"))
    assert code == 0
    assert "headers differ" not in out
    for model in GROQ_MODELS:
        assert f"groq {model} rate-limit headers:" in out


def test_headers_that_differ_from_the_config_are_reported(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, ["--only", "groq"], ALL_KEYS, lambda request: limits("6000", "500"))
    assert code == 0  # the model answered: the difference is for a person to act on
    assert "tokens a minute: the API says 6000, models.yaml says 8000" in out
    assert "requests a day: the API says 500, models.yaml says 1000" in out


def test_the_request_header_is_compared_per_day_not_per_minute() -> None:
    member = load_llm_config().pool.members[0]
    assert limit_differences(member, {"x-ratelimit-limit-requests": "1000"}) == []
    assert limit_differences(member, {"x-ratelimit-limit-requests": "30"}) != []


def test_measure_extraction_makes_one_call_the_size_of_a_full_source(capsys: pytest.CaptureFixture[str]) -> None:
    def answered(request: httpx.Request) -> httpx.Response:
        usage = {"prompt_tokens": 2900, "completion_tokens": 300}
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": usage})

    code, out, seen = run(capsys, ["--measure-extraction"], ALL_KEYS, answered)
    assert (code, len(seen)) == (0, 1)
    body = json.loads(seen[0].content)
    assert body["model"] == GROQ_MODELS[0]
    assert len(body["messages"][1]["content"]) == MAX_SOURCE_CHARS
    assert "input 2,900 + output 300 = 3,200 tokens used" in out


def test_the_measurement_sends_thread_text_and_never_a_username() -> None:
    thread = recorded_thread()
    assert "Nova Book 14" in thread
    assert "made_up_" not in thread


def test_measure_extraction_needs_a_groq_key(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, seen = run(capsys, ["--measure-extraction"], {})
    assert (code, seen) == (1, [])
    assert "GROQ_API_KEY is not set" in out


def test_a_failed_measurement_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, ["--measure-extraction"], ALL_KEYS, lambda request: httpx.Response(429))
    assert code == 1
    assert "The measurement failed: groq: HTTP 429" in out


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
