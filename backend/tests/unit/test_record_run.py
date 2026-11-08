"""The recorder (US-32): capped, and it never writes a secret or Reddit text. Everything here is faked: no real call.

The Reddit text below is synthetic, written for this test. Real Reddit text is never stored, so tests never have any.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from pydantic import BaseModel

import app.presearch
from app.contracts import DataClass, Difficulty, LLMTask
from app.criteria import InMemoryCriteriaStore
from app.llm import WrapperLLMClient, build_llm_client
from app.research.reddit.cache import RedditCache
from scripts import record_run
from scripts.record_run import LINK_MARKER, MARKER, MAX_RUNS, REDDIT_REFUSED, main

FAKE_KEY = "gsk_fake-key-DO-NOT-WRITE-123456"
GROQ = "https://api.groq.com/openai/v1/chat/completions"
ARCHIVE = "https://arctic-shift.photon-reddit.com/api/posts/search"
TITLE = "Synthetic thread about the Nova Book 14 for coding work"
BODY = "Synthetic comment saying the Nova Book 14 keyboard feels great after a full day of typing"
QUOTE = "the Nova Book 14 keyboard feels great after a full day"
PERMALINK = "/r/SuggestALaptop/comments/1qx7aaa/synthetic_thread_about_the_nova_book/"
ENV = {
    "GROQ_API_KEY": FAKE_KEY,
    "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:9/postgres",
    "HF_TOKEN": "hf_fakeTOKENvalue99",
    "SUPABASE_SERVICE_ROLE_KEY": "fake-service-role-value",
    "PLAIN_SETTING": "a-long-plain-setting-value",
}

MODEL = "openai/gpt-oss-120b"  # a real id on purpose: it has as many words as a short title

Runner = Callable[[str], int | None]
Send = Callable[[httpx.Request], httpx.Response]


HTTP = httpx.Client()  # one for the file: a new client costs 0.4 s on Python 3.13, which CI's Windows job runs


def shared_client() -> AbstractContextManager[httpx.Client]:
    """The recorder wraps the transport for every client, so sharing one changes nothing the tests look at."""
    return nullcontext(HTTP)


class Answer(BaseModel):
    quote: str


def chat(text: str = '{"ok": true}', **headers: str) -> httpx.Response:
    body = {"id": "chatcmpl-1", "model": MODEL, "choices": [{"message": {"role": "assistant", "content": text}}]}
    return httpx.Response(200, json=body, headers=headers)


def answer_chat(_: httpx.Request) -> httpx.Response:
    return chat()


def counted(sent: list[httpx.Request]) -> Send:
    def send(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return chat()

    return send


def reddit_posts() -> httpx.Response:
    post = {
        "id": "1qx7aaa",
        "title": TITLE,
        "selftext": BODY,
        "author": "made_up_owner_one",
        "score": 21,
        "num_comments": 14,
        "created_utc": 1793491200,
        "permalink": PERMALINK,
    }
    return httpx.Response(200, json={"data": [post]})


def ask_llm(query: str) -> None:
    with shared_client() as http:
        http.post(
            GROQ,
            json={"model": MODEL, "messages": [{"role": "user", "content": query}]},
            headers={"Authorization": f"Bearer {FAKE_KEY}"},
        )


def run(
    root: Path,
    *args: str,
    runner: Runner = ask_llm,
    send: Send = answer_chat,
    env: dict[str, str] | None = None,
    name: str = "demo",
) -> int:
    argv = ["a made-up request", "--name", name, "--by", "Test Person", *args]
    return main(argv, env=ENV if env is None else env, send=send, runner=runner, root=root)


def run_with_reddit(root: Path, *args: str, **kwargs: Any) -> int:
    """No Reddit budget is ever allowed. This goes past that refusal on purpose, to prove the next line of defence:
    even if a Reddit reply reached the recorder, its text still could not be written.
    """
    with patch.dict(record_run.DEFAULT_BUDGET, {"reddit": 1}):
        return run(root, *args, **kwargs)


def calls(root: Path, service: str = "groq", name: str = "demo") -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((root / name / f"{service}.json").read_text(encoding="utf-8"))
    return data


def written(root: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file())


# --- caps ------------------------------------------------------------------------------------------


def test_the_budget_is_printed_before_the_first_call(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[str] = []

    def note_output(request: httpx.Request) -> httpx.Response:
        seen.append(capsys.readouterr().out)
        return chat()

    assert run(tmp_path, send=note_output) == 0
    assert "Call budget" in seen[0]
    assert "groq" in seen[0] and "reddit" in seen[0] and "serper" in seen[0]


def ten_calls(query: str) -> None:
    for _ in range(10):
        with contextlib.suppress(httpx.TransportError):  # as the app does with any network failure
            ask_llm(query)


def test_the_cap_stops_the_recorder_at_the_limit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sent: list[httpx.Request] = []

    assert run(tmp_path, "--keep-failures", runner=ten_calls, send=counted(sent)) == 0

    assert len(sent) == 6  # the default Groq budget: the other four were never sent
    rows = calls(tmp_path)
    assert [row["status"] for row in rows] == [200] * 6 + [None] * 4
    assert [row.get("error") for row in rows[6:]] == ["Refused"] * 4  # so a replay meets what the app met
    assert "groq budget of 6 is spent" in capsys.readouterr().out
    manifest = json.loads((tmp_path / "demo" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["calls"] == {"groq": 6}
    assert manifest["refused"] == {"the groq budget of 6 is spent": 4}


def test_a_run_stopped_by_the_cap_writes_nothing_by_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, runner=ten_calls) == 1
    assert list(tmp_path.iterdir()) == []
    assert "Nothing was written" in capsys.readouterr().out


def test_a_refusal_says_how_to_give_the_service_a_budget(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def runner(query: str) -> None:
        ask_llm(query)
        with contextlib.suppress(httpx.TransportError), shared_client() as http:
            http.post("https://api.mistral.ai/v1/chat/completions", json={})

    assert run(tmp_path, runner=runner) == 1
    assert "--budget mistral=N" in capsys.readouterr().out


def test_two_threads_cannot_both_take_the_last_call(tmp_path: Path) -> None:
    sent: list[httpx.Request] = []

    def racing(query: str) -> None:
        threads = [threading.Thread(target=ten_calls, args=(query,)) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    run(tmp_path, "--keep-failures", runner=racing, send=counted(sent))
    assert len(sent) == 6


def test_a_call_still_running_when_the_app_ends_is_waited_for_and_its_retry_refused(tmp_path: Path) -> None:
    """A stage that times out leaves its thread running: its reply and its retry come after the app has finished."""
    started, release = threading.Event(), threading.Event()
    sent: list[httpx.Request] = []
    retried: list[str] = []

    def slow(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        started.set()
        release.wait(5)
        return chat()

    def abandoned() -> None:
        ask_llm("first try")
        time.sleep(0.1)  # no call is running now, but this thread isn't done: the recorder must still be in place
        try:
            ask_llm("the retry")
        except httpx.TransportError as error:
            retried.append(str(error))

    def runner(query: str) -> None:
        threading.Thread(target=abandoned).start()
        started.wait(5)
        threading.Timer(0.1, release.set).start()

    assert run(tmp_path, "--keep-failures", runner=runner, send=slow) == 0
    assert len(sent) == 1
    assert retried and "finished" in retried[0]
    assert calls(tmp_path)[0]["status"] == 200  # complete, never a half-written row


def test_a_call_that_never_ends_stops_the_write_and_stays_wrapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(record_run, "WAIT_FOR_CALLS_S", 0.05)
    before = httpx.HTTPTransport.handle_request
    for target, name in ((httpx.HTTPTransport, "handle_request"), (WrapperLLMClient, "complete"), (RedditCache, "get")):
        monkeypatch.setattr(target, name, getattr(target, name))  # it stays wrapped on purpose: this puts them back
    started, release = threading.Event(), threading.Event()

    def hangs(request: httpx.Request) -> httpx.Response:
        started.set()
        release.wait(5)
        return chat()

    def runner(query: str) -> None:
        threading.Thread(target=ask_llm, args=(query,), daemon=True).start()
        started.wait(5)

    try:
        assert run(tmp_path, runner=runner, send=hangs) == 1
        assert list(tmp_path.iterdir()) == []
        assert "still running" in capsys.readouterr().out
        assert httpx.HTTPTransport.handle_request is not before  # a late retry must still meet the budget
    finally:
        release.set()


def test_a_failed_run_writes_nothing_and_keeps_the_old_recording(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 0
    before = written(tmp_path)

    assert run(tmp_path, "--force", send=lambda r: httpx.Response(401, json={"error": "bad key"})) == 1

    assert written(tmp_path) == before
    out = capsys.readouterr().out
    assert "Nothing was written" in out and "401" in out


def test_what_was_spent_is_printed_even_when_nothing_is_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Quota is gone either way, so a failed attempt must still say what it cost."""
    assert run(tmp_path, send=lambda r: httpx.Response(401, json={"error": "bad key"})) == 1
    out = capsys.readouterr().out
    assert "Calls sent: groq 1" in out
    assert "groq 401 in" in out


def test_a_refused_call_is_listed_but_not_counted_as_sent(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def runner(query: str) -> None:
        ask_llm(query)
        with contextlib.suppress(httpx.TransportError), shared_client() as http:
            http.get(ARCHIVE)

    assert run(tmp_path, runner=runner) == 1
    out = capsys.readouterr().out
    assert "Calls sent: groq 1\n" in out
    assert "reddit Refused" in out


def test_a_run_the_app_ended_with_an_error_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def asks_then_gives_up(query: str) -> int:
        ask_llm(query)
        return 1

    assert run(tmp_path, runner=asks_then_gives_up) == 1
    assert list(tmp_path.iterdir()) == []
    assert "run 1 ended with code 1" in capsys.readouterr().out


def test_keep_failures_writes_them_and_says_so(tmp_path: Path) -> None:
    assert run(tmp_path, "--keep-failures", send=lambda r: httpx.Response(429, json={"error": "slow down"})) == 0
    assert calls(tmp_path)[0]["status"] == 429
    manifest = json.loads((tmp_path / "demo" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["problems"] == ["groq call answered 429"]
    assert "failed" in (tmp_path / "demo" / "README.md").read_text(encoding="utf-8")


def test_a_refused_call_fails_like_a_network_error(tmp_path: Path) -> None:
    def calls_reddit(query: str) -> None:
        with shared_client() as http, pytest.raises(httpx.TransportError, match="budget"):
            http.get(ARCHIVE)

    run(tmp_path, runner=calls_reddit)


def test_a_service_with_no_budget_is_never_called(tmp_path: Path) -> None:
    sent: list[httpx.Request] = []

    def tries_everything(query: str) -> None:
        with shared_client() as http:
            for url in (ARCHIVE, "https://google.serper.dev/search", "https://www.googleapis.com/youtube/v3/videos"):
                with pytest.raises(httpx.TransportError):
                    http.get(url)

    run(tmp_path, runner=tries_everything, send=counted(sent))
    assert sent == []


def test_a_budget_can_be_given_for_one_service(tmp_path: Path) -> None:
    def calls_serper(query: str) -> None:
        with shared_client() as http:
            http.post("https://google.serper.dev/search", json={"q": "laptops"})

    def send(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organic": []})

    assert run(tmp_path, "--budget", "serper=1", runner=calls_serper, send=send) == 0
    assert len(calls(tmp_path, "serper")) == 1


@pytest.mark.parametrize("value", ["reddit=1", "reddit=5"])
def test_a_reddit_budget_above_0_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str], value: str) -> None:
    sent: list[httpx.Request] = []

    with pytest.raises(SystemExit):
        run(tmp_path, "--budget", value, send=counted(sent))

    message = " ".join(capsys.readouterr().err.split())
    assert "Reddit data never goes into the repository" in message
    assert "D-53 caps Reddit caching at 24 hours" in message
    assert "synthetic threads" in message
    assert sent == []  # refused before a single call was spent
    assert list(tmp_path.iterdir()) == []


def test_a_reddit_budget_of_0_changes_nothing(tmp_path: Path) -> None:
    assert run(tmp_path, "--budget", "reddit=0") == 0


def test_a_refused_reddit_call_never_suggests_a_budget(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def runner(query: str) -> None:
        ask_llm(query)
        with contextlib.suppress(httpx.TransportError):
            fetch_archive(query)

    assert run(tmp_path, runner=runner) == 1
    out = capsys.readouterr().out
    assert "--budget reddit" not in out
    assert "REDDIT_SOURCE=off" in out
    assert "row with no reply" in out
    assert "never recorded" not in out  # --keep-failures does write a row


def test_an_unknown_host_is_refused(tmp_path: Path) -> None:
    sent: list[httpx.Request] = []

    def unknown(query: str) -> None:
        with shared_client() as http, pytest.raises(httpx.TransportError, match="not a known service"):
            http.get("https://example.com/")

    run(tmp_path, runner=unknown, send=counted(sent))
    assert sent == []


def test_a_run_with_no_calls_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(tmp_path, runner=lambda query: None) == 1
    assert list(tmp_path.iterdir()) == []
    assert "No calls" in capsys.readouterr().out


def test_a_crash_while_writing_leaves_the_old_recording_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(tmp_path) == 0
    before = written(tmp_path)
    real_write = Path.write_text

    def fails_on_the_service_file(path: Path, *args: Any, **kwargs: Any) -> int:
        if path.name == "groq.json":
            raise OSError("disk full")
        return real_write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fails_on_the_service_file)
    with pytest.raises(OSError, match="disk full"):
        run(tmp_path, "--force")
    monkeypatch.undo()
    assert written(tmp_path) == before


def test_more_than_5_runs_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert MAX_RUNS == 5
    with pytest.raises(SystemExit):
        run(tmp_path, "--runs", "6")
    assert "at most 5" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_exactly_5_runs_are_allowed(tmp_path: Path) -> None:
    assert run(tmp_path, "--runs", str(MAX_RUNS)) == 0
    assert [call["run"] for call in calls(tmp_path)] == [1, 2, 3, 4, 5]


def test_each_run_is_numbered(tmp_path: Path) -> None:
    assert run(tmp_path, "--runs", "2") == 0
    assert [c["run"] for c in calls(tmp_path)] == [1, 2]


def test_an_existing_recording_is_not_overwritten_without_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path) == 0
    before = written(tmp_path)
    sent: list[httpx.Request] = []

    assert run(tmp_path, send=counted(sent)) == 1
    assert sent == []  # refused before a single call was spent
    assert written(tmp_path) == before
    assert "--force" in capsys.readouterr().out


def test_force_replaces_a_recording(tmp_path: Path) -> None:
    assert run(tmp_path) == 0
    assert run(tmp_path, "--force", send=lambda r: chat("replaced")) == 0
    assert "replaced" in written(tmp_path)


@pytest.mark.parametrize("name", ["../outside", "a/b", "Has Spaces", ""])
def test_a_name_cannot_leave_the_recordings_folder(tmp_path: Path, name: str) -> None:
    with pytest.raises(SystemExit):
        run(tmp_path, name=name)


# --- secrets ---------------------------------------------------------------------------------------


def test_keys_tokens_and_cookies_are_never_written(tmp_path: Path) -> None:
    def cookies(request: httpx.Request) -> httpx.Response:
        return chat(**{"set-cookie": "session=abc123secret", "x-ratelimit-limit-tokens": "8000"})

    assert run(tmp_path, send=cookies) == 0
    text = written(tmp_path)
    assert FAKE_KEY not in text and "abc123secret" not in text
    assert "authorization" not in text.lower() and "set-cookie" not in text.lower()
    assert calls(tmp_path)[0]["response_headers"]["x-ratelimit-limit-tokens"] == "8000"


def test_query_strings_are_redacted(tmp_path: Path) -> None:
    def with_query(query: str) -> None:
        with shared_client() as http:
            http.post(f"{GROQ}?key={FAKE_KEY}&q=secret-search-words", json={})

    assert run(tmp_path, runner=with_query) == 0
    url = calls(tmp_path)[0]["url"]
    assert url == f"{GROQ}?key=[redacted]&q=[redacted]"
    assert "secret-search-words" not in written(tmp_path)


def test_a_recording_never_contains_a_value_from_the_environment(tmp_path: Path) -> None:
    env = {**os.environ, **ENV}

    def leaky(query: str) -> None:
        everything = " ".join(ENV.values())
        with shared_client() as http:
            http.post(GROQ, json={"messages": [{"role": "user", "content": everything}]})

    assert run(tmp_path, runner=leaky, send=lambda r: chat(" ".join(ENV.values())), env=env) == 0
    text = written(tmp_path)
    # Names only, so a failure never prints a value. The 8 is the test's own: it doesn't ask the recorder what to scan.
    assert [name for name, value in ENV.items() if value in text] == []
    assert [name for name, value in env.items() if len(value.strip()) >= 8 and value.strip() in text] == []
    assert [name for name in ENV if name in text] == []
    assert "[redacted]" in text


def test_the_names_of_replaced_values_are_printed_and_never_the_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(tmp_path, send=lambda r: chat(f"{ENV['PLAIN_SETTING']} and {ENV['PLAIN_SETTING']}")) == 0
    out = capsys.readouterr().out
    assert "PLAIN_SETTING (2)" in out
    assert ENV["PLAIN_SETTING"] not in out


def test_the_name_given_with_by_is_kept_even_if_a_variable_holds_it(tmp_path: Path) -> None:
    assert run(tmp_path, env={**ENV, "USERNAME": "Test Person"}) == 0
    manifest = json.loads((tmp_path / "demo" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["recorded_by"] == "Test Person"


def test_the_name_given_with_by_never_shields_a_secret(tmp_path: Path) -> None:
    assert run(tmp_path, env={**ENV, "SOME_API_KEY": "Test Person"}) == 0
    manifest = json.loads((tmp_path / "demo" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["recorded_by"] == "[redacted]"


def test_part_of_the_name_given_with_by_is_not_exempt(tmp_path: Path) -> None:
    assert run(tmp_path, env={**ENV, "USERNAME": "Test Person Two"}, send=lambda r: chat("Test Person Two")) == 0
    assert "Test Person Two" not in written(tmp_path)


def test_it_refuses_to_write_if_a_secret_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(record_run, "hide_values", lambda value, secrets: value)

    def leaky(query: str) -> None:
        with shared_client() as http:
            http.post(GROQ, json={"messages": [{"role": "user", "content": FAKE_KEY}]})

    assert run(tmp_path, runner=leaky) == 1
    assert list(tmp_path.iterdir()) == []  # nothing was written
    out = capsys.readouterr().out
    assert "GROQ_API_KEY" in out and FAKE_KEY not in out


# --- Reddit ----------------------------------------------------------------------------------------


def reddit_then_llm(prompt: str, reply: str) -> tuple[Runner, Send]:
    def runner(query: str) -> None:
        with shared_client() as http:
            http.get(ARCHIVE)
            http.post(GROQ, json={"messages": [{"role": "user", "content": prompt}]})

    def send(request: httpx.Request) -> httpx.Response:
        return reddit_posts() if request.url.host != "api.groq.com" else chat(reply)

    return runner, send


def fetch_archive(query: str) -> None:
    with shared_client() as http:
        http.get(ARCHIVE)


def run_reddit(root: Path, prompt: str = "A plain prompt.", reply: str = '{"ok": true}') -> int:
    runner, send = reddit_then_llm(prompt, reply)
    return run_with_reddit(root, runner=runner, send=send)


def test_reddit_text_is_replaced_by_the_marker(tmp_path: Path) -> None:
    assert run_reddit(tmp_path) == 0
    (post,) = calls(tmp_path, "reddit")[0]["response_body"]["data"]
    assert post["title"] == MARKER and post["selftext"] == MARKER
    text = written(tmp_path)
    assert TITLE not in text and BODY not in text


def test_reddit_items_keep_their_ids_scores_times_and_urls(tmp_path: Path) -> None:
    assert run_reddit(tmp_path) == 0
    (post,) = calls(tmp_path, "reddit")[0]["response_body"]["data"]
    assert (post["id"], post["score"], post["num_comments"]) == ("1qx7aaa", 21, 14)
    assert post["created_utc"] == 1793491200
    assert post["permalink"].startswith("/r/SuggestALaptop/comments/1qx7aaa")


def test_a_reddit_url_loses_its_title_slug(tmp_path: Path) -> None:
    assert run_reddit(tmp_path) == 0
    (post,) = calls(tmp_path, "reddit")[0]["response_body"]["data"]
    assert post["permalink"] == "/r/SuggestALaptop/comments/1qx7aaa"
    assert "synthetic_thread" not in written(tmp_path)


def test_usernames_never_reach_a_recording(tmp_path: Path) -> None:
    assert run_reddit(tmp_path) == 0
    assert "made_up_owner_one" not in written(tmp_path)


def test_an_unknown_reddit_field_is_not_kept(tmp_path: Path) -> None:
    def new_field(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "x", "a_field_we_never_saw": "some reddit words"}]})

    def runner(query: str) -> None:
        with shared_client() as http:
            http.get(ARCHIVE)

    assert run_with_reddit(tmp_path, runner=runner, send=new_field) == 0
    assert "some reddit words" not in written(tmp_path)


def test_a_reddit_reply_that_is_not_json_is_not_kept(tmp_path: Path) -> None:
    def runner(query: str) -> None:
        with shared_client() as http:
            http.get(ARCHIVE)

    def send(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"<html>{BODY}</html>")

    assert run_with_reddit(tmp_path, runner=runner, send=send) == 0
    assert BODY not in written(tmp_path)
    assert calls(tmp_path, "reddit")[0]["response_body"] == MARKER


def test_an_llm_prompt_with_reddit_text_is_replaced(tmp_path: Path) -> None:
    assert run_reddit(tmp_path, prompt=f"Extract claims from this thread: {BODY}", reply=f'{{"quote": "{QUOTE}"}}') == 0
    call = calls(tmp_path)[0]
    assert BODY not in written(tmp_path)
    assert call["request_body"]["messages"][0]["content"] == MARKER
    assert call["response_body"]["choices"][0]["message"]["content"] == MARKER


def test_a_quote_from_reddit_in_an_llm_reply_is_replaced(tmp_path: Path) -> None:
    """The prompt doesn't carry the thread here, so only the quote itself gives it away."""
    assert run_reddit(tmp_path, reply=f'{{"quote": "{QUOTE}"}}') == 0
    assert QUOTE not in written(tmp_path)
    assert MARKER in calls(tmp_path)[0]["response_body"]["choices"][0]["message"]["content"]


def test_an_llm_call_before_the_reddit_call_is_scrubbed_too(tmp_path: Path) -> None:
    def runner(query: str) -> None:
        with shared_client() as http:
            http.post(GROQ, json={"messages": [{"role": "user", "content": QUOTE}]})
            http.get(ARCHIVE)

    def send(request: httpx.Request) -> httpx.Response:
        return reddit_posts() if request.url.host != "api.groq.com" else chat()

    assert run_with_reddit(tmp_path, runner=runner, send=send) == 0
    assert QUOTE not in written(tmp_path)


def test_text_passed_on_from_a_reddit_derived_reply_is_replaced(tmp_path: Path) -> None:
    """A paraphrase matches nothing in the thread, but it came from Reddit, so a later prompt carrying it is marked."""
    paraphrase = "Owners praise how comfortable the keys remain during long sessions"

    def runner(query: str) -> None:
        with shared_client() as http:
            http.get(ARCHIVE)
            http.post(GROQ, json={"messages": [{"role": "user", "content": f"Summarise this: {BODY}"}]})
            http.post(GROQ, json={"messages": [{"role": "user", "content": f"Explain this claim: {paraphrase}"}]})

    def send(request: httpx.Request) -> httpx.Response:
        return reddit_posts() if request.url.host != "api.groq.com" else chat(paraphrase)

    assert run_with_reddit(tmp_path, runner=runner, send=send) == 0
    assert paraphrase not in written(tmp_path)


def test_a_username_in_an_llm_reply_is_replaced(tmp_path: Path) -> None:
    assert run_reddit(tmp_path, reply="MADE_UP_OWNER_ONE liked it") == 0
    assert "made_up_owner_one" not in written(tmp_path).lower()


def test_a_quote_in_another_script_is_replaced_too(tmp_path: Path) -> None:
    hindi = "यह लैपटॉप कोडिंग के लिए बहुत अच्छा है"

    def send(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.groq.com":
            return chat(f"quote: {hindi}")
        return httpx.Response(200, json={"data": [{"id": "x", "selftext": hindi}]})

    runner, _ = reddit_then_llm("A plain prompt.", "unused")
    assert run_with_reddit(tmp_path, runner=runner, send=send) == 0
    assert hindi not in written(tmp_path)


def test_a_reddit_user_url_is_not_kept(tmp_path: Path) -> None:
    def send(request: httpx.Request) -> httpx.Response:
        item = {"id": "x", "url": "https://www.reddit.com/user/made_up_owner_one/"}
        return httpx.Response(200, json={"data": [item, {"id": "y", "permalink": "/u/made_up_owner_one"}]})

    assert run_with_reddit(tmp_path, runner=fetch_archive, send=send) == 0
    assert "made_up_owner_one" not in written(tmp_path)


def test_a_profile_post_url_does_not_keep_the_username(tmp_path: Path) -> None:
    def send(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"id": "x", "permalink": "/r/u_made_up_owner_one/comments/abc/slug/"}]}
        )

    assert run_with_reddit(tmp_path, runner=fetch_archive, send=send) == 0
    assert "made_up_owner_one" not in written(tmp_path)


def test_a_call_declared_reddit_is_replaced_even_with_no_reddit_call(tmp_path: Path) -> None:
    """The thread could have come from a cache, so nothing in the recording would recognise it."""
    client = build_llm_client(env={"GROQ_API_KEY": FAKE_KEY})

    def runner(query: str) -> None:
        client.complete(
            LLMTask.EXTRACT_CLAIMS,
            "Pull out claims.",
            BODY,
            Answer,
            data_class=DataClass.REDDIT,
            difficulty=Difficulty.LONG,
        )

    assert run(tmp_path, runner=runner, send=lambda r: chat(f'{{"quote": "{QUOTE}"}}')) == 0
    assert BODY not in written(tmp_path) and QUOTE not in written(tmp_path)
    assert calls(tmp_path)[0]["request_body"]["model"] != MARKER  # the shape survives, only the text goes


def test_a_declared_class_stays_with_its_own_thread(tmp_path: Path) -> None:
    client = build_llm_client(env={"GROQ_API_KEY": FAKE_KEY})

    def runner(query: str) -> None:
        client.complete(
            LLMTask.DETECT_INTENT,
            "Read this.",
            query,
            Answer,
            data_class=DataClass.PRIVATE,
            difficulty=Difficulty.SHORT,
        )
        ask_llm("A plain prompt.")

    assert run(tmp_path, runner=runner, send=lambda r: chat('{"quote": "fine"}')) == 0
    assert all(MARKER not in json.dumps(call) for call in calls(tmp_path))


def test_the_reddit_cache_is_not_read_while_recording(tmp_path: Path) -> None:
    """A cache hit would hide a call and bring Reddit text in with nothing to recognise it."""
    cache = RedditCache(folder=None)
    cache.put(("key",), [])
    assert cache.get(("key",)) == ()
    seen: list[object] = []

    def runner(query: str) -> None:
        seen.append(cache.get(("key",)))
        ask_llm(query)

    assert run(tmp_path, runner=runner) == 0
    assert seen == [None]
    assert cache.get(("key",)) == ()  # and it is put back


def test_a_model_name_is_not_treated_as_reddit_text(tmp_path: Path) -> None:
    """Otherwise one marked call would wipe every later call to the same model."""

    def runner(query: str) -> None:
        with shared_client() as http:
            http.get(ARCHIVE)
        ask_llm(f"Summarise this: {BODY}")
        ask_llm("A plain prompt.")

    def send(request: httpx.Request) -> httpx.Response:
        return reddit_posts() if request.url.host != "api.groq.com" else chat()

    assert run_with_reddit(tmp_path, runner=runner, send=send) == 0
    marked, plain = calls(tmp_path)
    assert marked["request_body"]["model"] == MODEL and marked["response_body"]["model"] == MODEL
    assert plain["request_body"]["model"] == MODEL
    assert plain["request_body"]["messages"][0]["content"] == "A plain prompt."


@pytest.mark.parametrize(
    "reply",
    ['{"quote": "keys feel great here", "sentiment": "positive"}', '```json\n{"quote": "keys feel great here"}\n```'],
)
def test_a_short_quote_passed_on_from_a_marked_reply_is_replaced(tmp_path: Path, reply: str) -> None:
    def runner(query: str) -> None:
        with shared_client() as http:
            http.get(ARCHIVE)
        ask_llm(f"Summarise this: {BODY}")
        ask_llm("Explain: keys feel great here.")

    def send(request: httpx.Request) -> httpx.Response:
        return reddit_posts() if request.url.host != "api.groq.com" else chat(reply)

    assert run_with_reddit(tmp_path, runner=runner, send=send) == 0
    assert "keys feel great here" not in written(tmp_path)


def test_the_shoppers_own_words_are_never_reddit_text(tmp_path: Path) -> None:
    """A thread can share its title with the request, and the row that matters most must survive."""
    request = "best laptop for coding under 70k"

    def send(request_sent: httpx.Request) -> httpx.Response:
        if request_sent.url.host == "api.groq.com":
            return chat('{"category": "laptop"}')
        return httpx.Response(200, json={"data": [{"id": "x", "title": "Laptop for coding under 70k?"}]})

    def runner(query: str) -> None:
        fetch_archive(query)
        ask_llm(f"Read this request: {query}")

    argv = [request, "--name", "demo", "--by", "Test Person"]
    with patch.dict(record_run.DEFAULT_BUDGET, {"reddit": 1}):
        assert main(argv, env=ENV, send=send, runner=runner, root=tmp_path) == 0
    assert request in calls(tmp_path)[0]["request_body"]["messages"][0]["content"]
    assert calls(tmp_path)[0]["response_body"]["choices"][0]["message"]["content"] == '{"category": "laptop"}'


def reddit_author(name: str, reply: str = '{"ok": true}', **fields: str) -> Send:
    def send(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.groq.com":
            return chat(reply)
        return httpx.Response(200, json={"data": [{"id": "x", "author": name, **fields}]})

    return send


@pytest.mark.parametrize("name", ["laptop", "Laptop"])
def test_a_plain_word_username_does_not_wipe_every_prompt(tmp_path: Path, name: str) -> None:
    def runner(query: str) -> None:
        fetch_archive(query)
        ask_llm("Compare this laptop.")

    assert run_with_reddit(tmp_path, runner=runner, send=reddit_author(name)) == 0
    assert calls(tmp_path)[0]["request_body"]["messages"][0]["content"] == "Compare this laptop."


def test_a_plain_word_username_is_still_replaced_after_u_slash(tmp_path: Path) -> None:
    def runner(query: str) -> None:
        fetch_archive(query)
        ask_llm("A plain prompt.")

    send = reddit_author("rahulsharma", reply="As u/rahulsharma said, it works.")
    assert run_with_reddit(tmp_path, runner=runner, send=send) == 0
    assert "rahulsharma" not in written(tmp_path)


def test_a_kept_url_loses_the_authors_handle(tmp_path: Path) -> None:
    send = reddit_author("made_up_owner_one", url="https://example-reviews.in/made_up_owner_one/post")
    assert run_with_reddit(tmp_path, runner=fetch_archive, send=send) == 0
    assert "made_up_owner_one" not in written(tmp_path)


def test_a_url_inside_reddit_text_does_not_damage_a_kept_url(tmp_path: Path) -> None:
    """A kept link in a search result is checked for Reddit text, so the words of a URL must never count as such."""
    link = "https://example-reviews.in/nova-book-14/full-review"

    def runner(query: str) -> None:
        fetch_archive(query)
        with shared_client() as http:
            http.post("https://google.serper.dev/search", json={"q": "laptops"})

    def send(request: httpx.Request) -> httpx.Response:
        if request.url.host == "google.serper.dev":
            return httpx.Response(200, json={"organic": [{"title": "A review", "link": link}]})
        return httpx.Response(200, json={"data": [{"id": "1qx7aaa", "selftext": f"See {link} for more."}]})

    assert run_with_reddit(tmp_path, "--budget", "serper=1", runner=runner, send=send) == 0
    assert calls(tmp_path, "serper")[0]["response_body"]["organic"][0]["link"] == link


def test_the_number_of_marked_calls_is_printed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_reddit(tmp_path, prompt=f"Summarise this: {BODY}") == 0
    assert "1 LLM call" in capsys.readouterr().out


@pytest.mark.parametrize(
    "url",
    [
        f"https://r.jina.ai/https://www.reddit.com{PERMALINK}",
        "https://r.jina.ai/https://old.reddit.com/r/SuggestALaptop/comments/1qx7aaa/",
        "https://r.jina.ai/https://redd.it/1qx7aaa",
        "https://r.jina.ai/reddit.com/r/SuggestALaptop/comments/1qx7aaa/",
        "https://r.jina.ai/?url=https%3A%2F%2Fwww.reddit.com%2Fr%2FSuggestALaptop%2Fcomments%2F1qx7aaa",
        "https://r.jina.ai/?url=https%253A%252F%252Fold.reddit.com%252Fr%252FSuggestALaptop%252F1qx7aaa",
        "https://r.jina.ai/?url=https%25253A%25252F%25252Fwww.reddit.com%25252Fr%25252FSuggestALaptop%25252F1qx7aaa",
        "https://r.jina.ai/https://WWW.Reddit.COM/r/SuggestALaptop/comments/1qx7aaa/",
        "https://r.jina.ai/https://www.reddit.com:443/r/SuggestALaptop/comments/1qx7aaa/",
        "https://r.jina.ai/https://someone@www.reddit.com/r/SuggestALaptop/comments/1qx7aaa/",
        "https://r.jina.ai/https://www.reddit.com./r/SuggestALaptop/comments/1qx7aaa/",
        "https://r.jina.ai/?urls=https://example-reviews.in,reddit.com/r/SuggestALaptop/1qx7aaa",
        "https://r.jina.ai/https://arctic-shift.photon-reddit.com/api/posts/search?subreddit=SuggestALaptop&id=1qx7aaa",
        "https://r.jina.ai/https://api.pullpush.io/reddit/search/submission/?subreddit=SuggestALaptop&id=1qx7aaa",
        "https://www.reddit.com/r/SuggestALaptop/comments/1qx7aaa/",
        "https://old.reddit.com/r/SuggestALaptop/comments/1qx7aaa/",
        "https://redd.it/1qx7aaa",
    ],
)
def test_a_reddit_url_is_refused_through_any_service(tmp_path: Path, url: str) -> None:
    """Even with a budget for the service, and with the same message as a Reddit budget."""
    sent: list[httpx.Request] = []

    def runner(query: str) -> None:
        ask_llm(query)
        with shared_client() as http, pytest.raises(httpx.TransportError) as refused:
            http.get(url)
        assert str(refused.value) == REDDIT_REFUSED

    assert run(tmp_path, "--budget", "jina=1", "--keep-failures", runner=runner, send=counted(sent)) == 0
    assert [request.url.host for request in sent] == ["api.groq.com"]
    assert "SuggestALaptop" not in written(tmp_path) and "1qx7aaa" not in written(tmp_path)


@pytest.mark.parametrize("address", [f"https://www.reddit.com{PERMALINK}", "reddit.com/r/SuggestALaptop/1qx7aaa"])
def test_a_reddit_url_in_a_request_body_is_refused(tmp_path: Path, address: str) -> None:
    """The reader also takes the page's address in a POST body, with or without its scheme."""
    sent: list[httpx.Request] = []

    def runner(query: str) -> None:
        ask_llm(query)
        with shared_client() as http, pytest.raises(httpx.TransportError):
            http.post("https://r.jina.ai/", json={"url": address})

    assert run(tmp_path, "--budget", "jina=1", "--keep-failures", runner=runner, send=counted(sent)) == 0
    assert [request.url.host for request in sent] == ["api.groq.com"]
    assert "SuggestALaptop" not in written(tmp_path) and "1qx7aaa" not in written(tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "https://r.jina.ai/https://example-reviews.in/reddit-review",
        "https://r.jina.ai/https://notreddit.com/page",
        "https://r.jina.ai/https://reddit.com.example.in/page",
    ],
)
def test_a_page_that_only_looks_like_reddit_is_still_fetched(tmp_path: Path, url: str) -> None:
    def runner(query: str) -> None:
        with shared_client() as http:
            http.get(url)

    assert run(tmp_path, "--budget", "jina=1", runner=runner, send=lambda r: httpx.Response(200, text="A page.")) == 0
    assert calls(tmp_path, "jina")[0]["response_body"] == "A page."


@pytest.mark.parametrize(
    "query", ["best laptop site:reddit.com", "best laptop site:www.reddit.com", "reddit.com laptops"]
)
def test_a_search_that_mentions_reddit_is_not_a_fetch(tmp_path: Path, query: str) -> None:
    """Search results may point at Reddit (D-53), and they are cleaned like any other."""

    def runner(request_text: str) -> None:
        with shared_client() as http:
            http.post("https://google.serper.dev/search", json={"q": query})

    def send(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organic": []})

    assert run(tmp_path, "--budget", "serper=1", runner=runner, send=send) == 0


def test_an_llm_prompt_naming_reddit_is_not_a_fetch(tmp_path: Path) -> None:
    prompt = "Compare what people say at https://www.reddit.com/r/laptops"
    assert run(tmp_path, runner=lambda query: ask_llm(prompt)) == 0
    assert calls(tmp_path)[0]["request_body"]["messages"][0]["content"].endswith(
        f"https://www.reddit.com/{LINK_MARKER}"
    )


@pytest.mark.parametrize(
    ("link", "kept"),
    [
        (f"https://www.reddit.com{PERMALINK}", "https://www.reddit.com/"),
        ("https://old.reddit.com/r/SuggestALaptop/comments/1qx7aaa/", "https://old.reddit.com/"),
        ("https://redd.it/1qx7aaa", "https://redd.it/"),
        ("www.reddit.com/r/SuggestALaptop/comments/1qx7aaa", "https://www.reddit.com/"),
    ],
)
def test_a_reddit_link_in_an_llm_call_loses_its_path(tmp_path: Path, link: str, kept: str) -> None:
    """The app gets the real search reply, so a later prompt can carry a link the search row no longer shows."""
    assert (
        run(
            tmp_path, runner=lambda query: ask_llm(f"Read {link} and compare."), send=lambda r: chat(f"See {link} too.")
        )
        == 0
    )
    (call,) = calls(tmp_path)
    assert call["request_body"]["messages"][0]["content"] == f"Read {kept}{LINK_MARKER} and compare."
    assert call["response_body"]["choices"][0]["message"]["content"] == f"See {kept}{LINK_MARKER} too."
    text = written(tmp_path)
    assert "SuggestALaptop" not in text and "1qx7aaa" not in text and "synthetic_thread" not in text


def test_hiding_a_link_leaves_the_json_around_it_whole(tmp_path: Path) -> None:
    reply = '{"contact":"me@reddit.com/help","keep":"this"}'
    assert run(tmp_path, send=lambda r: chat(reply)) == 0
    content = calls(tmp_path)[0]["response_body"]["choices"][0]["message"]["content"]
    assert json.loads(content) == {"contact": f"https://reddit.com/{LINK_MARKER}", "keep": "this"}


def test_a_web_page_quoting_a_reddit_link_loses_its_path(tmp_path: Path) -> None:
    def runner(query: str) -> None:
        with shared_client() as http:
            http.get("https://r.jina.ai/https://example-reviews.in/nova")

    def send(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=f"As https://www.reddit.com{PERMALINK} said, it is good.")

    assert run(tmp_path, "--budget", "jina=1", runner=runner, send=send) == 0
    assert calls(tmp_path, "jina")[0]["response_body"] == f"As https://www.reddit.com/{LINK_MARKER} said, it is good."


def test_a_refused_reddit_url_never_suggests_a_budget_for_the_service(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def runner(query: str) -> None:
        ask_llm(query)
        with contextlib.suppress(httpx.TransportError), shared_client() as http:
            http.get(f"https://r.jina.ai/https://www.reddit.com{PERMALINK}")

    assert run(tmp_path, runner=runner) == 1
    out = capsys.readouterr().out
    assert "--budget jina" not in out
    assert "REDDIT_SOURCE=off" not in out  # it wouldn't stop a fetch through the reader, so it isn't offered
    assert "through jina" in out
    assert "row with no reply" in out


def test_a_refused_reddit_call_is_kept_as_a_row_with_no_reply(tmp_path: Path) -> None:
    def runner(query: str) -> None:
        ask_llm(query)
        with contextlib.suppress(httpx.TransportError), shared_client() as http:
            http.get(f"{ARCHIVE}?subreddit=SuggestALaptop&q=secret-search-words")

    assert run(tmp_path, "--keep-failures", runner=runner) == 0
    (row,) = calls(tmp_path, "reddit")
    assert (row["error"], row["status"], row["elapsed_ms"]) == ("Refused", None, 0)
    assert row["url"] == f"{ARCHIVE}?subreddit=[redacted]&q=[redacted]"
    assert row["request_body"] is None
    assert row["response_body"] is None and row["response_headers"] == {}
    text = written(tmp_path)
    assert "SuggestALaptop" not in text and "secret-search-words" not in text


def test_a_refused_reddit_url_keeps_only_the_scheme_and_host_of_the_service(tmp_path: Path) -> None:
    def runner(query: str) -> None:
        ask_llm(query)
        with contextlib.suppress(httpx.TransportError), shared_client() as http:
            http.get(f"https://r.jina.ai/https://www.reddit.com{PERMALINK}")

    assert run(tmp_path, "--budget", "jina=1", "--keep-failures", runner=runner) == 0
    (row,) = calls(tmp_path, "jina")
    assert row["url"] == f"https://r.jina.ai/{LINK_MARKER}"
    assert row["error"] == "Refused"


def test_a_reddit_time_given_as_text_is_kept(tmp_path: Path) -> None:
    def send(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "x", "utc_datetime_str": "2026-11-01 10:30:00"}]})

    assert run_with_reddit(tmp_path, runner=fetch_archive, send=send) == 0
    assert calls(tmp_path, "reddit")[0]["response_body"]["data"][0]["utc_datetime_str"] == "2026-11-01 10:30:00"


@pytest.mark.parametrize(
    ("link", "kept"),
    [
        (f"https://www.reddit.com{PERMALINK}", "https://www.reddit.com/"),
        ("https://old.reddit.com/r/SuggestALaptop/comments/1qx7aaa/slug/", "https://old.reddit.com/"),
        ("https://redd.it/1qx7aaa", "https://redd.it/"),
        ("https://www.reddit.com./r/SuggestALaptop/comments/1qx7aaa/", "https://www.reddit.com/"),
        ("HTTPS://WWW.REDDIT.COM/r/SuggestALaptop/comments/1qx7aaa/", "https://www.reddit.com/"),
        ("www.reddit.com/r/SuggestALaptop/comments/1qx7aaa", "https://www.reddit.com/"),
    ],
)
def test_a_search_result_pointing_at_reddit_keeps_only_its_scheme_and_host(
    tmp_path: Path, link: str, kept: str
) -> None:
    """A synthetic search reply. The path names the subreddit and the thread, so it goes with the title and snippet."""
    organic = [
        {"title": TITLE, "link": link, "snippet": BODY, "position": 1},
        {"title": "Nova Book 14 review", "link": "https://example-reviews.in/nova", "snippet": "A web review."},
    ]

    def runner(query: str) -> None:
        with shared_client() as http:
            http.post("https://google.serper.dev/search", json={"q": query})

    def send(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organic": organic})

    assert run(tmp_path, "--budget", "serper=1", runner=runner, send=send) == 0
    first, second = calls(tmp_path, "serper")[0]["response_body"]["organic"]
    assert first["link"] == f"{kept}{LINK_MARKER}" == "".join((kept, "[reddit link not stored]"))
    assert first["title"] == MARKER and first["snippet"] == MARKER and first["position"] == 1
    assert second["title"] == "Nova Book 14 review" and second["snippet"] == "A web review."
    assert second["link"] == "https://example-reviews.in/nova"
    text = written(tmp_path)
    assert "SuggestALaptop" not in text and "1qx7aaa" not in text


def test_a_search_result_with_brackets_in_its_title_is_left_alone(tmp_path: Path) -> None:
    """A value like this can't be split as a URL, and that must not stop the recording."""
    organic = [{"title": "[Review", "link": "https://example-reviews.in/nova"}]

    def runner(query: str) -> None:
        with shared_client() as http:
            http.post("https://google.serper.dev/search", json={"q": query})

    def send(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organic": organic})

    assert run(tmp_path, "--budget", "serper=1", runner=runner, send=send) == 0
    assert calls(tmp_path, "serper")[0]["response_body"]["organic"] == organic


def test_short_common_words_are_not_treated_as_reddit_text(tmp_path: Path) -> None:
    assert run_reddit(tmp_path, prompt="Compare the keyboard on this laptop.") == 0
    assert calls(tmp_path)[0]["request_body"]["messages"][0]["content"] == "Compare the keyboard on this laptop."


def test_the_official_reddit_source_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sent: list[httpx.Request] = []

    assert run(tmp_path, env={**ENV, "REDDIT_SOURCE": "official"}, send=counted(sent)) == 1
    assert sent == []
    out = capsys.readouterr().out
    assert "REDDIT_SOURCE=off" in out
    assert "archive" not in out


# --- what is written -------------------------------------------------------------------------------


def test_each_call_keeps_request_response_and_timing(tmp_path: Path) -> None:
    assert run(tmp_path) == 0
    (call,) = calls(tmp_path)
    assert call["service"] == "groq" and call["run"] == 1
    assert (call["method"], call["url"], call["status"]) == ("POST", GROQ, 200)
    assert call["request_body"]["messages"][0]["content"] == "a made-up request"
    assert call["response_body"]["choices"][0]["message"]["content"] == '{"ok": true}'
    assert call["elapsed_ms"] >= 0


def test_the_app_gets_the_real_reply_while_recording(tmp_path: Path) -> None:
    got: list[str] = []

    def reads(query: str) -> None:
        with shared_client() as http:
            got.append(http.post(GROQ, json={}).json()["choices"][0]["message"]["content"])

    assert run(tmp_path, runner=reads, send=lambda r: chat(f"the key is {FAKE_KEY}")) == 0
    assert got == [f"the key is {FAKE_KEY}"]  # redaction is for the file, never for the running app


def test_a_call_that_fails_is_kept_with_its_error(tmp_path: Path) -> None:
    def fails(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no reply")

    def runner(query: str) -> None:
        with pytest.raises(httpx.ConnectTimeout):
            ask_llm(query)

    assert run(tmp_path, "--keep-failures", runner=runner, send=fails) == 0
    (call,) = calls(tmp_path)
    assert call["status"] is None and call["error"] == "ConnectTimeout"


def test_a_compressed_reply_reaches_the_app_and_the_file_decoded(tmp_path: Path) -> None:
    got: list[str] = []

    def send(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()
        return httpx.Response(200, headers={"content-encoding": "gzip"}, content=gzip.compress(body))

    def reads(query: str) -> None:
        with shared_client() as http:
            got.append(http.post(GROQ, json={}).json()["choices"][0]["message"]["content"])

    assert run(tmp_path, runner=reads, send=send) == 0
    assert got == ["hello"]
    assert calls(tmp_path)[0]["response_body"]["choices"][0]["message"]["content"] == "hello"


def test_with_no_fake_it_wraps_the_real_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hosts: list[str] = []

    def real(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return chat()

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", real)
    argv = ["a made-up request", "--name", "demo", "--by", "Test Person"]
    assert main(argv, env=ENV, runner=ask_llm, root=tmp_path) == 0
    assert hosts == ["api.groq.com"]
    assert len(calls(tmp_path)) == 1


def test_a_whole_cli_run_is_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real CLI, wrapper and adapter: only the last hop is fake. A reply fitting no schema makes a stage retry."""
    monkeypatch.setattr("app.presearch.default_llm", lambda: build_llm_client(env={"GROQ_API_KEY": FAKE_KEY}))
    argv = ["best laptop for coding under 70k", "--name", "demo", "--by", "Test Person"]

    assert main(argv, env=ENV, send=answer_chat, root=tmp_path) == 0

    recorded = calls(tmp_path)
    assert 2 <= len(recorded) <= 6
    assert {call["url"] for call in recorded} == {GROQ}
    assert "best laptop for coding under 70k" in recorded[0]["request_body"]["messages"][1]["content"]
    assert FAKE_KEY not in written(tmp_path)


def test_the_transport_is_put_back_afterwards(tmp_path: Path) -> None:
    before = httpx.HTTPTransport.handle_request
    assert run(tmp_path) == 0
    assert httpx.HTTPTransport.handle_request is before


def test_the_transport_is_put_back_after_a_crash(tmp_path: Path) -> None:
    before = httpx.HTTPTransport.handle_request

    def crashes(query: str) -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        run(tmp_path, runner=crashes)
    assert httpx.HTTPTransport.handle_request is before


def test_the_readme_line_says_what_when_and_by_whom(tmp_path: Path) -> None:
    assert run(tmp_path) == 0
    line = (tmp_path / "demo" / "README.md").read_text(encoding="utf-8")
    assert '"a made-up request"' in line and "Test Person" in line
    assert "groq: 1" in line
    index = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "demo" in index and "Test Person" in index


def test_a_failed_swap_puts_the_old_recording_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run(tmp_path) == 0
    before = written(tmp_path)
    real_rename = Path.rename

    def fails_on_the_new_folder(path: Path, target: Path) -> Path:
        if path.name.endswith(".writing"):
            raise PermissionError("locked by another program")
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", fails_on_the_new_folder)
    with pytest.raises(PermissionError):
        run(tmp_path, "--force", send=lambda r: chat("replaced"))
    monkeypatch.undo()
    assert sorted(path.name for path in tmp_path.iterdir() if path.is_dir()) == ["demo"]
    assert written(tmp_path) == before


def test_recording_again_replaces_its_line_in_the_index(tmp_path: Path) -> None:
    assert run(tmp_path) == 0
    assert run(tmp_path, "--force") == 0
    index = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert index.count("- `demo`") == 1


def test_the_manifest_records_the_run(tmp_path: Path) -> None:
    assert run(tmp_path, "--runs", "2") == 0
    manifest = json.loads((tmp_path / "demo" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["request"] == "a made-up request"
    assert manifest["runs"] == 2 and manifest["recorded_by"] == "Test Person"
    assert manifest["calls"] == {"groq": 2}
    assert manifest["budget"]["groq"] == 6 and manifest["budget"]["reddit"] == 0


def test_the_database_is_never_used(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_cli(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr("app.cli.main", fake_cli)
    assert record_run.run_app("best laptop") == 0
    assert seen == [["best laptop", "--no-db"]]


def test_a_saved_criteria_set_cannot_hide_a_call(monkeypatch: pytest.MonkeyPatch) -> None:
    stores: list[object] = []

    def fake_cli(argv: list[str]) -> int:
        stores.append(app.presearch.default_criteria_store())
        return 0

    monkeypatch.setattr("app.cli.main", fake_cli)
    before = app.presearch.default_criteria_store
    record_run.run_app("best laptop")
    record_run.run_app("best laptop")
    assert isinstance(stores[0], InMemoryCriteriaStore)
    assert stores[0] is not stores[1]
    assert app.presearch.default_criteria_store is before
