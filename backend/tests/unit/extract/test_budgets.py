"""A search's Reddit budget and source cap, with the coverage note that reports them (US-14 items 9 and 10)."""

from __future__ import annotations

import dataclasses
import json
import re
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

import app.extract as extract_package
import app.extract.stage as stage_module
from app.contracts import DataClass, ExtractInput, LLMTask, LLMUnavailable, Provider, Source, StageContext
from app.criteria.fallback import fallback_criteria
from app.extract import GuardedExtractStage
from app.extract.parsing import RawExtraction
from app.extract.settings import ExtractSettings, load_extract_settings
from app.extract.stage import MAX_TRACKED_SEARCHES
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.errors import ProviderError, ServerError
from app.llm.fitting import estimate_tokens
from app.llm.health import ProviderHealth
from app.llm.pool import ModelPool
from app.llm.providers import RawCompletion
from app.llm.usage import TokenMeter, time_already_spent
from tests.fakes.llm import FakeAdapter, FakeLLMClient
from tests.fakes.research import review_source, thread

CTX = StageContext(search_id="srch_a")
BLANK = json.dumps({"mentions": [], "claims": []})
FULL_SIZE_TOKENS = 3000  # research.yaml's max_source_tokens
REAL_OUTPUT_TOKENS = 600  # about what the reasoning models used in P10's live runs


class Clock:
    """A clock whose sleep() moves it on, so a wait takes no real time."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _reddit(name: str, tokens: int = 50) -> Source:
    return thread(name, text=f"Made-up thread {name}. " + "a" * (tokens * 3))


def _inp(source: Source) -> ExtractInput:
    return ExtractInput(source=source, criterion_keys=["battery_life"], category_label="laptop")


def _laptop(source: Source) -> ExtractInput:
    """A real prompt's size: a laptop's fixed criteria, as docs/api-setup.md counts them."""
    keys = [c.key for c in fallback_criteria("laptop").criteria]
    return ExtractInput(source=source, criterion_keys=keys, category_label="laptop")


def _llm() -> FakeLLMClient:
    return FakeLLMClient({LLMTask.EXTRACT_CLAIMS: RawExtraction(mentions=[], claims=[])})


def _stage(
    llm: FakeLLMClient | WrapperLLMClient, budget: int = 18_000, cap: int = 40, real_use: bool = False
) -> GuardedExtractStage:
    settings = ExtractSettings(reddit_tokens_per_search=budget, max_sources_per_search=cap, book_real_use=real_use)
    return GuardedExtractStage(llm, settings)


def _note(stage: GuardedExtractStage, search_id: str = "srch_a") -> str:
    note = stage.coverage_note(search_id)
    assert note is not None
    return note


# --- the Reddit budget -----------------------------------------------------------------------------


def test_reddit_sources_past_the_budget_are_skipped_with_no_call() -> None:
    llm = _llm()
    one = _stage(llm).booked_tokens(_inp(_reddit("aaa")))
    stage = _stage(llm, budget=one * 2 + 1)
    for name in ("aaa", "bbb", "ccc", "ddd"):
        result = stage.run(_inp(_reddit(name)), CTX)
        assert (result.claims, result.mentions) == ([], [])
    assert len(llm.calls) == 2
    assert "2 Reddit threads were not read" in _note(stage)


def test_a_source_that_fits_exactly_is_read() -> None:
    llm = _llm()
    one = _stage(llm).booked_tokens(_inp(_reddit("aaa")))
    _stage(llm, budget=one).run(_inp(_reddit("aaa")), CTX)
    assert len(llm.calls) == 1


def test_one_token_short_is_skipped() -> None:
    llm = _llm()
    one = _stage(llm).booked_tokens(_inp(_reddit("aaa")))
    _stage(llm, budget=one - 1).run(_inp(_reddit("aaa")), CTX)
    assert llm.calls == []


def test_a_smaller_thread_may_still_fit_after_a_bigger_one_did_not() -> None:
    llm = _llm()
    big, small = _reddit("big", tokens=2000), _reddit("sma", tokens=10)
    stage = _stage(llm, budget=_stage(llm).booked_tokens(_inp(small)))
    stage.run(_inp(big), CTX)
    stage.run(_inp(small), CTX)
    assert len(llm.calls) == 1
    assert "sma" in llm.calls[0].user


def test_the_budget_counts_what_the_pool_books() -> None:
    config = load_llm_config()
    while_running: list[int] = []

    def reply(_: str) -> str:  # what the pool holds for a call that is still running
        while_running.append(sum(client.pool.tokens_this_minute(m) for m in config.models if client.pool.has(m)))
        return BLANK

    adapter = FakeAdapter(Provider.GROQ, replies=(reply,))
    client = WrapperLLMClient(config, {Provider.GROQ: adapter}, InMemoryCallLog(), limiters={})
    source = _reddit("aaa", tokens=FULL_SIZE_TOKENS)
    stage = GuardedExtractStage(client)
    stage.run(_inp(source), CTX)
    assert len(adapter.calls) == 1
    assert while_running == [stage.booked_tokens(_inp(source))]


def test_web_sources_do_not_use_the_reddit_budget() -> None:
    llm = _llm()
    stage = _stage(llm, budget=1)
    stage.run(_inp(review_source("https://a.example/")), CTX)
    stage.run(_inp(review_source("https://b.example/")), CTX)
    assert len(llm.calls) == 2
    assert stage.coverage_note("srch_a") is None


def test_web_sources_leave_the_reddit_budget_untouched() -> None:
    llm = _llm()
    reddit = _reddit("aaa")
    stage = _stage(llm, budget=_stage(llm).booked_tokens(_inp(reddit)))
    stage.run(_inp(review_source("https://a.example/")), CTX)
    stage.run(_inp(reddit), CTX)
    assert len(llm.calls) == 2
    assert stage.coverage_note("srch_a") is None


def test_sources_running_together_cannot_pass_the_budget() -> None:
    llm = _llm()
    reddit = _reddit("aaa")
    stage = _stage(llm, budget=_stage(llm).booked_tokens(_inp(reddit)) * 3)
    start = threading.Barrier(16, timeout=5)

    def read(_: int) -> None:
        start.wait()
        stage.run(_inp(reddit), CTX)

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(read, range(16)))
    assert len(llm.calls) == 3
    assert "13 Reddit threads were not read" in _note(stage)


def test_two_searches_have_separate_budgets() -> None:
    llm = _llm()
    one = _stage(llm).booked_tokens(_inp(_reddit("aaa")))
    stage = _stage(llm, budget=one)
    stage.run(_inp(_reddit("aaa")), StageContext(search_id="srch_a"))
    stage.run(_inp(_reddit("aaa")), StageContext(search_id="srch_b"))
    assert len(llm.calls) == 2


# --- the source cap --------------------------------------------------------------------------------


def test_sources_past_the_cap_are_skipped() -> None:
    llm = _llm()
    stage = _stage(llm, cap=2)
    for name in "abc":
        stage.run(_inp(review_source(f"https://{name}.example/")), CTX)
    assert len(llm.calls) == 2
    assert "1 source was not read" in _note(stage)


def test_a_reddit_source_over_budget_does_not_use_the_cap() -> None:
    llm = _llm()
    stage = _stage(llm, budget=1, cap=1)
    stage.run(_inp(_reddit("aaa")), CTX)
    stage.run(_inp(review_source("https://a.example/")), CTX)
    assert len(llm.calls) == 1
    assert llm.calls[0].data_class is DataClass.PUBLIC


def test_a_reddit_source_past_the_cap_books_nothing() -> None:
    llm = _llm()
    stage = _stage(llm, cap=1)
    stage.run(_inp(review_source("https://a.example/")), CTX)
    stage.run(_inp(_reddit("aaa")), CTX)
    assert len(llm.calls) == 1
    note = _note(stage)
    assert "1 source was not read" in note
    assert "Reddit" not in note


# --- the coverage note -----------------------------------------------------------------------------


def test_each_skip_gives_a_coverage_note() -> None:
    over_budget = _stage(_llm(), budget=1)
    over_budget.run(_inp(_reddit("aaa")), CTX)
    assert "1 Reddit thread was not read, to stay within this search's" in _note(over_budget)

    no_answer = _stage(FakeLLMClient())
    no_answer.run(_inp(_reddit("aaa")), CTX)
    assert "1 source could not be read" in _note(no_answer)

    past_the_cap = _stage(_llm(), cap=1)
    past_the_cap.run(_inp(review_source("https://a.example/")), CTX)
    past_the_cap.run(_inp(review_source("https://b.example/")), CTX)
    assert "1 source was not read, to keep the search from running too long" in _note(past_the_cap)


def test_no_note_when_every_source_was_read() -> None:
    stage = _stage(_llm())
    stage.run(_inp(_reddit("aaa")), CTX)
    assert stage.coverage_note("srch_a") is None


def test_a_search_with_no_reads_has_no_note() -> None:
    assert _stage(_llm()).coverage_note("srch_never_ran") is None


def test_the_note_says_each_kind_of_skip_once() -> None:
    llm = FakeLLMClient()
    stage = _stage(llm, budget=1, cap=1)
    stage.run(_inp(_reddit("aaa")), CTX)  # over the Reddit budget
    stage.run(_inp(review_source("https://a.example/")), CTX)  # no answer
    stage.run(_inp(review_source("https://b.example/")), CTX)  # past the cap
    note = _note(stage)
    assert note.count("Reddit") == 1
    assert note.count("could not be read") == 1
    assert note.count("to keep the search from running too long") == 1


def test_the_note_is_handed_back_once_and_forgotten() -> None:
    stage = _stage(_llm(), budget=1)
    stage.run(_inp(_reddit("aaa")), CTX)
    assert stage.coverage_note("srch_a") is not None
    assert stage.coverage_note("srch_a") is None


def test_the_note_holds_counts_and_no_text() -> None:
    stage = _stage(FakeLLMClient())
    source = review_source("https://a.example/", title="A made-up title", text="A made-up sentence from the page.")
    stage.run(_inp(source), CTX)
    note = _note(stage)
    assert "made-up" not in note
    assert "a.example" not in note


# --- what fits a minute ----------------------------------------------------------------------------


def _pooled(clock: Clock, budget: int = 1_000_000) -> tuple[GuardedExtractStage, FakeAdapter]:
    config = load_llm_config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 9))
    adapter = FakeAdapter(Provider.GROQ, replies=(BLANK,))
    client = WrapperLLMClient(
        config, {Provider.GROQ: adapter}, InMemoryCallLog(), health=ProviderHealth(clock), limiters={}, pool=pool
    )
    return _stage(client, budget=budget), adapter


def test_three_full_size_sources_fit_a_minute() -> None:
    clock = Clock()
    stage, adapter = _pooled(clock)
    for name in ("aaa", "bbb", "ccc", "ddd"):
        stage.run(_laptop(_reddit(name, tokens=FULL_SIZE_TOKENS)), CTX)
    assert len(adapter.calls) == 3
    assert {call.model for call in adapter.calls} == {m.model for m in load_llm_config().pool.members}
    assert "1 source could not be read" in _note(stage)


def test_six_short_threads_fit_a_minute() -> None:
    clock = Clock()
    stage, adapter = _pooled(clock)
    for name in ("aaa", "bbb", "ccc", "ddd", "eee", "fff", "ggg"):
        stage.run(_laptop(_reddit(name, tokens=250)), CTX)
    assert len(adapter.calls) == 6
    assert "1 source could not be read" in _note(stage)


class _RealUse(FakeAdapter):
    """Answers with usage like a real full-size call's: far less than the pool reserved."""

    def call(
        self,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_s: float,
        extra: Mapping[str, object] | None = None,
    ) -> RawCompletion:
        reply = super().call(model, system, user, max_output_tokens, timeout_s, extra)
        return RawCompletion(reply.text, input_tokens=estimate_tokens(user) * 3 // 4, output_tokens=REAL_OUTPUT_TOKENS)


def _pooled_real_use(
    clock: Clock, budget: int = 1_000_000, real_use: bool = True, adapter: FakeAdapter | None = None
) -> tuple[GuardedExtractStage, FakeAdapter]:
    config = load_llm_config()
    pool = ModelPool(config.pool, clock=clock, sleep=clock.sleep, today=lambda: date(2026, 11, 9))
    adapter = adapter if adapter is not None else _RealUse(Provider.GROQ, replies=(BLANK,))
    client = WrapperLLMClient(
        config, {Provider.GROQ: adapter}, InMemoryCallLog(), health=ProviderHealth(clock), limiters={}, pool=pool
    )
    return _stage(client, budget=budget, real_use=real_use), adapter


def test_the_full_size_threads_that_fit_a_minute() -> None:
    """Real use frees tokens, but the next call must still reserve 5,697, so a member takes only one a minute."""
    stage, adapter = _pooled_real_use(Clock())
    for name in ("aaa", "bbb", "ccc", "ddd"):
        stage.run(_laptop(_reddit(name, tokens=FULL_SIZE_TOKENS)), CTX)
    assert len(adapter.calls) == 3


# --- the Reddit budget counts real use, the way the pool does -----------------------------------------


EIGHT = ("aaa", "bbb", "ccc", "ddd", "eee", "fff", "ggg", "hhh")


@dataclass
class _FixedUse(FakeAdapter):
    """Reports one fixed use for each call, or fails. It can also take a moment, so calls run side by side."""

    used: int = 1000
    reported: bool = True
    error: Exception | None = None
    delay_s: float = 0.0

    def call(
        self,
        model: str,
        system: str,
        user: str,
        max_output_tokens: int,
        timeout_s: float,
        extra: Mapping[str, object] | None = None,
    ) -> RawCompletion:
        reply = super().call(model, system, user, max_output_tokens, timeout_s, extra)
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        return RawCompletion(reply.text, self.used - 1, 1, usage_reported=self.reported)


def _hold(tokens: int = 250) -> int:
    return _stage(_llm()).booked_tokens(_laptop(_reddit("aaa", tokens=tokens)))


def _read(stage: GuardedExtractStage, names: tuple[str, ...]) -> None:
    for name in names:
        stage.run(_laptop(_reddit(name, tokens=250)), CTX)


def test_the_reddit_budget_counts_real_use() -> None:
    """These threads hold about 2,960 each and really use about 1,100. As holds 6 fit the 18,000; as real use all 8."""
    stage, adapter = _pooled_real_use(Clock(), budget=load_llm_config().pool.reddit_tokens_per_search)
    _read(stage, EIGHT)
    assert len(adapter.calls) == 8
    assert stage.coverage_note("srch_a") is None


def test_with_the_switch_off_the_reddit_budget_counts_holds() -> None:
    budget = load_llm_config().pool.reddit_tokens_per_search
    stage, adapter = _pooled_real_use(Clock(), budget=budget, real_use=False)
    _read(stage, EIGHT)
    assert len(adapter.calls) == 6
    assert "2 Reddit threads were not read" in _note(stage)


def test_the_reddit_budget_is_still_enforced_with_real_use() -> None:
    """A thread goes only if its whole hold fits above what the earlier ones really used."""
    adapter = _FixedUse(Provider.GROQ, replies=(BLANK,), used=1000)
    stage, _ = _pooled_real_use(Clock(), budget=2000 + _hold(), adapter=adapter)
    _read(stage, ("aaa", "bbb", "ccc", "ddd"))
    assert len(adapter.calls) == 3
    assert "1 Reddit thread was not read" in _note(stage)


def test_a_failed_reddit_call_gives_its_tokens_back_to_the_budget() -> None:
    adapter = _FixedUse(Provider.GROQ, replies=(BLANK,), error=ServerError(Provider.GROQ, "HTTP 500"))
    stage, _ = _pooled_real_use(Clock(), budget=_hold(), adapter=adapter)
    _read(stage, ("aaa", "bbb"))
    note = _note(stage)
    assert "2 sources could not be read" in note
    assert "not read, to stay within" not in note


class _NoModelFree:
    """A client whose every call ends before anything is sent."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *args: object, **kwargs: object) -> RawExtraction:
        self.calls += 1
        raise LLMUnavailable("no model was free")


def test_a_call_that_never_went_out_gives_its_hold_back() -> None:
    llm = _NoModelFree()
    stage = _stage(llm, budget=_hold(), real_use=True)  # type: ignore[arg-type]
    _read(stage, ("aaa", "bbb"))
    assert llm.calls == 2
    note = _note(stage)
    assert "2 sources could not be read" in note
    assert "not read, to stay within" not in note


def test_a_finished_call_no_longer_holds_up_a_thread() -> None:
    """Nothing is running, so there is nothing to wait for."""
    adapter = _FixedUse(Provider.GROQ, replies=(BLANK,), used=1000)
    stage, _ = _pooled_real_use(Clock(), budget=999 + _hold(), adapter=adapter)
    started = time.monotonic()
    _read(stage, ("aaa", "bbb"))
    assert time.monotonic() - started < 1
    assert len(adapter.calls) == 1


def test_with_the_switch_off_a_thread_is_skipped_at_once() -> None:
    stage = _stage(_llm(), budget=1000, real_use=False)
    source = _reddit("aaa")
    assert stage._may_send("srch_a", source, 1000)
    started = time.monotonic()
    assert stage._may_send("srch_a", source, 500) is False
    assert time.monotonic() - started < 1


def test_a_reply_with_no_text_counts_what_it_used_in_the_budget() -> None:
    no_text = ProviderError(Provider.GROQ, "the reply had no message text", used_tokens=2000)
    adapter = _FixedUse(Provider.GROQ, replies=(BLANK,), error=no_text)
    stage, _ = _pooled_real_use(Clock(), budget=_hold(), adapter=adapter)
    _read(stage, ("aaa", "bbb"))
    assert "1 Reddit thread was not read" in _note(stage)


def test_a_reply_with_no_usage_keeps_its_hold_in_the_budget() -> None:
    adapter = _FixedUse(Provider.GROQ, replies=(BLANK,), used=100, reported=False)
    stage, _ = _pooled_real_use(Clock(), budget=_hold() * 2, adapter=adapter)
    _read(stage, ("aaa", "bbb", "ccc"))
    assert len(adapter.calls) == 2
    assert "1 Reddit thread was not read" in _note(stage)


def test_a_retry_counts_in_the_budget() -> None:
    """Asked twice on each of the three members, at 1,000 a time: 6,000 in all, so the next hold no longer fits."""
    adapter = _FixedUse(Provider.GROQ, replies=("not json",), used=1000)
    stage, _ = _pooled_real_use(Clock(), budget=5999 + _hold(), adapter=adapter)
    _read(stage, ("aaa", "bbb"))
    assert "1 Reddit thread was not read" in _note(stage)


class _Broken:
    def complete(self, *args: object, **kwargs: object) -> RawExtraction:
        raise RuntimeError("a bug")


def test_an_unexpected_error_keeps_the_hold_and_ends_the_running_call() -> None:
    stage = _stage(_Broken(), budget=18_000, real_use=True)  # type: ignore[arg-type]
    hold = _hold()
    with pytest.raises(RuntimeError):
        stage.run(_laptop(_reddit("aaa", tokens=250)), CTX)
    counts = stage._counts["srch_a"]
    assert (counts.reddit_booked, counts.reddit_running) == (hold, 0)


class _Spy:
    def __init__(self) -> None:
        self.spent = 0.0

    def complete(self, *args: object, **kwargs: object) -> RawExtraction:
        self.spent = time_already_spent()
        return RawExtraction(mentions=[], claims=[])


def test_a_thread_that_waited_takes_that_time_off_its_calls_limit() -> None:
    """The runner's 15 seconds started before the wait, so the wrapper's must not start after it."""
    spy = _Spy()
    hold = _hold()
    stage = _stage(spy, budget=hold, real_use=True)  # type: ignore[arg-type]
    running = _reddit("zzz")
    assert stage._may_send("srch_a", running, hold)
    finish = threading.Timer(0.2, stage._book_real_use, ("srch_a", running, hold, TokenMeter(1, 0), False))
    finish.start()
    stage.run(_laptop(_reddit("aaa", tokens=250)), CTX)
    finish.join()
    assert spy.spent >= 0.15


def test_a_thread_that_did_not_wait_has_its_whole_limit() -> None:
    spy = _Spy()
    stage = _stage(spy, real_use=True)  # type: ignore[arg-type]
    stage.run(_laptop(_reddit("aaa", tokens=250)), CTX)
    assert spy.spent < 0.1


def test_a_client_that_reports_no_use_keeps_the_hold() -> None:
    llm = _llm()
    stage = _stage(llm, budget=_hold() * 2 + 1, real_use=True)
    _read(stage, ("aaa", "bbb", "ccc", "ddd"))
    assert len(llm.calls) == 2


def _side_by_side(real_use: bool) -> tuple[GuardedExtractStage, FakeAdapter]:
    config = load_llm_config()
    adapter = _FixedUse(Provider.GROQ, replies=(BLANK,), used=100, delay_s=0.1)
    client = WrapperLLMClient(
        config, {Provider.GROQ: adapter}, InMemoryCallLog(), limiters={}, pool=ModelPool(config.pool)
    )
    stage = _stage(client, budget=_hold(50) * 2, real_use=real_use)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda name: stage.run(_laptop(_reddit(name)), CTX), EIGHT[:6]))
    return stage, adapter


def test_a_thread_waits_while_running_calls_may_free_the_budget() -> None:
    """Sources start together, so the budget fills with holds that will shrink. Skipping now would waste it."""
    started = time.monotonic()
    stage, adapter = _side_by_side(real_use=True)
    assert len(adapter.calls) == 6
    assert stage.coverage_note("srch_a") is None
    assert time.monotonic() - started < 4  # a finished call wakes the others: none waits out its 6 seconds


def test_with_the_switch_off_a_thread_never_waits_for_the_budget() -> None:
    stage, adapter = _side_by_side(real_use=False)
    assert len(adapter.calls) == 2
    assert "4 Reddit threads were not read" in _note(stage)


def test_a_thread_is_skipped_at_once_when_no_finish_could_free_the_budget() -> None:
    stage = _stage(_llm(), budget=1000, real_use=True)
    source = _reddit("aaa")
    assert stage._may_send("srch_a", source, 900)
    started = time.monotonic()
    assert stage._may_send("srch_a", source, 1001) is False  # over the whole budget, whatever the running call uses
    assert time.monotonic() - started < 1
    assert "1 Reddit thread was not read" in _note(stage)


def test_a_finished_calls_real_use_counts_against_a_thread_that_would_wait() -> None:
    stage = _stage(_llm(), budget=1000, real_use=True)
    source = _reddit("aaa")
    assert stage._may_send("srch_a", source, 600)
    stage._counts["srch_a"].reddit_running = 0  # that call has finished, and really used its 600
    assert stage._may_send("srch_a", source, 300)  # running: 300
    started = time.monotonic()
    assert stage._may_send("srch_a", source, 500) is False  # 600 + 500 is over, even if the running call used nothing
    assert time.monotonic() - started < 1


def test_the_wait_for_the_budget_has_a_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stage_module, "BUDGET_WAIT_S", 0.05)
    stage = _stage(_llm(), budget=1000, real_use=True)
    source = _reddit("aaa")
    assert stage._may_send("srch_a", source, 1000)
    assert stage._may_send("srch_a", source, 500) is False  # the running call never finishes
    assert "1 Reddit thread was not read" in _note(stage)


def test_the_wait_leaves_a_full_attempts_time() -> None:
    assert pytest.approx(5) == stage_module.BUDGET_WAIT_S  # of the stage's 15 s: 9 for an attempt, and a margin of 1


def test_the_stage_takes_the_switch_from_models_yaml() -> None:
    assert load_extract_settings(env={}).book_real_use is True
    assert load_extract_settings(path="no-such-file.yaml", env={}).book_real_use is True
    assert ExtractSettings().book_real_use is False


def test_about_three_full_threads_fit_the_budget() -> None:
    clock = Clock()
    stage, _ = _pooled(clock, budget=load_llm_config().pool.reddit_tokens_per_search)
    booked = stage.booked_tokens(_laptop(_reddit("aaa", tokens=FULL_SIZE_TOKENS)))
    assert 3 <= 18_000 // booked <= 4
    assert booked <= 6000  # it must fit a member's minute once the reserve is kept free


# --- nothing is kept -------------------------------------------------------------------------------


def test_the_stage_keeps_no_text_after_a_search() -> None:
    source = review_source("https://a.example/", title="A made-up title", text="A made-up sentence from the page.")
    stage = _stage(_llm())
    stage.run(_inp(source), CTX)
    (counts,) = stage._counts.values()
    assert all(isinstance(value, int) for value in dataclasses.asdict(counts).values())
    stage.coverage_note("srch_a")
    assert not stage._counts


def test_old_searches_are_forgotten() -> None:
    """A search that never asked for its note must not grow the stage's memory for ever."""
    stage = _stage(_llm())
    for i in range(MAX_TRACKED_SEARCHES + 5):
        stage.run(_inp(_reddit("aaa")), StageContext(search_id=f"srch_{i}"))
    assert len(stage._counts) == MAX_TRACKED_SEARCHES
    assert "srch_0" not in stage._counts


def test_the_extract_folder_never_writes_a_file() -> None:
    """Reddit text and the claims drawn from it stay in memory for the run (D-53)."""
    folder = Path(extract_package.__file__).parent
    code = "\n".join(path.read_text(encoding="utf-8") for path in folder.glob("*.py"))
    assert not re.search(r"\bopen\(|write_text|write_bytes|\.dump\(|\bsqlite|\bshelve|\bpickle", code)


# --- the counts, for the live funnel (US-14 yield) ----------------------------------------------------------------


def test_the_stage_counts_survive_until_read_and_are_a_snapshot() -> None:
    stage = _stage(_llm(), cap=1)
    for name in ("a", "b"):
        stage.run(_inp(review_source(f"https://www.reviews.example/{name}", text="Nova Book 14 page.")), CTX)
    counts = stage.counts_for(CTX.search_id)
    assert counts is not None
    assert (counts["sent"], counts["over_source_cap"], counts["over_reddit_budget"]) == (1, 1, 0)
    counts["sent"] = 99  # a snapshot: changing it changes nothing in the stage
    again = stage.counts_for(CTX.search_id)
    assert again is not None and again["sent"] == 1  # reading it twice is fine, unlike the coverage note
    assert stage.coverage_note(CTX.search_id) is not None
    assert stage.counts_for(CTX.search_id) is None  # the note clears them, as before
    assert stage.counts_for("srch_never") is None


def test_a_public_source_with_no_criteria_is_counted_and_still_sent() -> None:
    llm = _llm()
    stage = _stage(llm)
    page = review_source("https://www.reviews.example/n", text="Nova Book 14 page.")
    # A generated key the category has no stand-in for, no budget and no specs: the public prompt has no criteria.
    stage.run(ExtractInput(source=page, criterion_keys=["made_up_key"], category_label="laptop"), CTX)
    counts = stage.counts_for(CTX.search_id)
    assert counts is not None
    assert (counts["sent"], counts["no_criteria"]) == (1, 1)
    assert len(llm.calls) == 1  # still sent, as before: only counted


# --- Reddit's share of the cap (US-14 yield) ------------------------------------------------------------------------


def _shared(cap: int, share: float = 0.5) -> tuple[FakeLLMClient, GuardedExtractStage]:
    llm = _llm()
    return llm, GuardedExtractStage(llm, ExtractSettings(max_sources_per_search=cap, reddit_share_of_sources=share))


def test_reddit_threads_leave_room_for_pages_when_the_cap_is_small() -> None:
    """Research lists Reddit first. Only pages give the prices and specs decide needs (DR-01), so with a small cap
    Reddit may take only its share, and the pages after it are still read."""
    llm, stage = _shared(cap=4)
    for name in ("aaa", "bbb", "ccc", "ddd"):
        stage.run(_inp(_reddit(name)), CTX)
    for name in ("a", "b", "c"):
        stage.run(_inp(review_source(f"https://{name}.example/")), CTX)
    assert [call.data_class for call in llm.calls] == [DataClass.REDDIT] * 2 + [DataClass.PUBLIC] * 2
    counts = stage.counts_for(CTX.search_id)
    assert counts is not None
    assert (counts["sent"], counts["over_reddit_share"], counts["over_source_cap"]) == (4, 2, 1)
    note = _note(stage)
    assert "1 source was not read, to keep the search from running too long." in note
    assert "2 Reddit threads were not read, to leave room for web pages." in note


def test_reddit_share_changes_nothing_under_the_usual_cap() -> None:
    llm, stage = _shared(cap=40)
    for index in range(6):  # more threads than the Reddit token budget usually lets through
        stage.run(_inp(_reddit(f"t{index}")), CTX)
    assert len(llm.calls) == 6
    assert stage.coverage_note(CTX.search_id) is None


def test_reddit_share_is_rounded_up_and_never_below_one() -> None:
    for cap, share, threads in ((1, 0.5, 1), (3, 0.5, 2), (8, 0.5, 4), (5, 0.1, 1), (4, 1.0, 4)):
        llm, stage = _shared(cap=cap, share=share)
        for index in range(cap + 1):
            stage.run(_inp(_reddit(f"t{index}")), CTX)
        assert len(llm.calls) == threads, (cap, share)


def test_reddit_share_comes_from_config() -> None:
    assert load_extract_settings(env={}).reddit_share_of_sources == 0.5
    for bad in (0.0, 1.5):
        with pytest.raises(ValueError):
            ExtractSettings(reddit_share_of_sources=bad)


def test_a_thread_that_waited_for_the_budget_still_keeps_to_the_share() -> None:
    """The wait lets go of the lock, so other threads may take the last of the share meanwhile: it is checked again."""
    settings = ExtractSettings(max_sources_per_search=8, reddit_tokens_per_search=10_000, book_real_use=True)
    stage = GuardedExtractStage(_llm(), settings)  # room for 4 Reddit threads
    search = "srch_waits"
    with stage._settled:
        counts = stage._for(search)
        counts.sent = counts.reddit_sent = 3
        counts.reddit_booked = counts.reddit_running = 9_000  # the running calls hold the budget, so a thread waits
    answer: list[bool] = []
    waiter = threading.Thread(target=lambda: answer.append(stage._may_send(search, _reddit("w"), 2_000)))
    waiter.start()
    time.sleep(0.2)
    with stage._settled:
        counts.sent = counts.reddit_sent = 4  # another thread took the last of the share
        counts.reddit_booked = counts.reddit_running = 1_000  # and the running calls finished
        stage._settled.notify_all()
    waiter.join(timeout=10)
    assert answer == [False]
    after = stage.counts_for(search)
    assert after is not None
    assert (after["over_reddit_share"], after["sent"]) == (1, 4)


def test_the_share_follows_the_cap_from_the_environment() -> None:
    settings = load_extract_settings(env={"EXTRACT_MAX_SOURCES": "8"})
    assert GuardedExtractStage(_llm(), settings)._reddit_room() == 4


def test_the_share_at_the_usual_cap_is_researchs_reddit_thread_limit() -> None:
    """Under the usual cap the share never holds a thread back: it equals the most threads research ever finds."""
    from app.research.settings import load_research_settings

    room = GuardedExtractStage(_llm(), load_extract_settings(env={}))._reddit_room()
    assert room == load_research_settings(env={}).max_reddit_threads == 20
    llm = _llm()
    roomy = GuardedExtractStage(llm, ExtractSettings(max_sources_per_search=40, reddit_tokens_per_search=10_000_000))
    for index in range(room):
        roomy.run(_inp(_reddit(f"t{index}")), CTX)
    assert len(llm.calls) == room  # all 20 sent
