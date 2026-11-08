"""A search's Reddit budget and source cap, with the coverage note that reports them (US-14 items 9 and 10)."""

from __future__ import annotations

import dataclasses
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import app.extract as extract_package
from app.contracts import DataClass, ExtractInput, LLMTask, Provider, Source, StageContext
from app.criteria.fallback import fallback_criteria
from app.extract import GuardedExtractStage
from app.extract.parsing import RawExtraction
from app.extract.settings import ExtractSettings
from app.extract.stage import MAX_TRACKED_SEARCHES
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.llm.health import ProviderHealth
from app.llm.pool import ModelPool
from tests.fakes.llm import FakeAdapter, FakeLLMClient
from tests.fakes.research import review_source, thread, web_source

CTX = StageContext(search_id="srch_a")
BLANK = json.dumps({"mentions": [], "claims": []})
FULL_SIZE_TOKENS = 3000  # research.yaml's max_source_tokens


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


def _stage(llm: FakeLLMClient | WrapperLLMClient, budget: int = 18_000, cap: int = 40) -> GuardedExtractStage:
    settings = ExtractSettings(reddit_tokens_per_search=budget, max_sources_per_search=cap)
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
    adapter = FakeAdapter(Provider.GROQ, replies=(BLANK,))
    client = WrapperLLMClient(config, {Provider.GROQ: adapter}, InMemoryCallLog(), limiters={})
    source = _reddit("aaa", tokens=FULL_SIZE_TOKENS)
    stage = GuardedExtractStage(client)
    stage.run(_inp(source), CTX)
    assert len(adapter.calls) == 1
    booked = sum(client.pool.tokens_this_minute(m) for m in config.models if client.pool.has(m))
    assert booked == stage.booked_tokens(_inp(source))


def test_web_sources_do_not_use_the_reddit_budget() -> None:
    llm = _llm()
    stage = _stage(llm, budget=1)
    stage.run(_inp(web_source("https://a.example/")), CTX)
    stage.run(_inp(review_source("https://b.example/")), CTX)
    assert len(llm.calls) == 2
    assert stage.coverage_note("srch_a") is None


def test_web_sources_leave_the_reddit_budget_untouched() -> None:
    llm = _llm()
    reddit = _reddit("aaa")
    stage = _stage(llm, budget=_stage(llm).booked_tokens(_inp(reddit)))
    stage.run(_inp(web_source("https://a.example/")), CTX)
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
        stage.run(_inp(web_source(f"https://{name}.example/")), CTX)
    assert len(llm.calls) == 2
    assert "1 source was not read" in _note(stage)


def test_a_reddit_source_over_budget_does_not_use_the_cap() -> None:
    llm = _llm()
    stage = _stage(llm, budget=1, cap=1)
    stage.run(_inp(_reddit("aaa")), CTX)
    stage.run(_inp(web_source("https://a.example/")), CTX)
    assert len(llm.calls) == 1
    assert llm.calls[0].data_class is DataClass.PUBLIC


def test_a_reddit_source_past_the_cap_books_nothing() -> None:
    llm = _llm()
    stage = _stage(llm, cap=1)
    stage.run(_inp(web_source("https://a.example/")), CTX)
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
    past_the_cap.run(_inp(web_source("https://a.example/")), CTX)
    past_the_cap.run(_inp(web_source("https://b.example/")), CTX)
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
    stage.run(_inp(web_source("https://a.example/")), CTX)  # no answer
    stage.run(_inp(web_source("https://b.example/")), CTX)  # past the cap
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
    source = web_source("https://a.example/", title="A made-up title", text="A made-up sentence from the page.")
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


def test_about_three_full_threads_fit_the_budget() -> None:
    clock = Clock()
    stage, _ = _pooled(clock, budget=load_llm_config().pool.reddit_tokens_per_search)
    booked = stage.booked_tokens(_laptop(_reddit("aaa", tokens=FULL_SIZE_TOKENS)))
    assert 3 <= 18_000 // booked <= 4
    assert booked <= 6000  # it must fit a member's minute once the reserve is kept free


# --- nothing is kept -------------------------------------------------------------------------------


def test_the_stage_keeps_no_text_after_a_search() -> None:
    source = web_source("https://a.example/", title="A made-up title", text="A made-up sentence from the page.")
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
