"""US-41 (S5-6): the fact look-up's and enrich stage's edges, so every line and branch of the change runs (D-97). A
failure anywhere adds nothing and never stops the search (build rule 9). Fakes only: no real API is called.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from concurrent.futures import Future
from datetime import timedelta
from pathlib import Path
from time import monotonic
from typing import Any

import pytest

from app.contracts import EnrichInput, EnrichTarget, LLMTask, Product, Source, StageContext
from app.enrich import PriceLookupStage
from app.enrich.facts import NOTHING, FactLookup, FactsFound
from app.enrich.stage import facts_note
from app.extract.settings import load_extract_settings
from app.llm.retry import RetryClock
from app.research.cache import ENRICH_TTL, ResearchCache, enrich_cache_dir
from app.research.pages import PageResult, ReviewPages
from app.research.readers import FirecrawlClient
from app.research.serper import SerperResult, SerperSource
from app.research.settings import load_research_settings
from app.research.usage import SearchUsage
from tests.fakes.llm import FakeLLMClient
from tests.unit.enrich.test_fact_lookup import (
    ASK,
    GOOD,
    NOW,
    QUERY,
    REDDIT,
    REVIEW,
    TARGET,
    VELA,
    FakePages,
    FakeSearch,
    _answer,
    _lookup,
    _usage,
    short_window,
)


def _find(lookup: FactLookup, targets: Sequence[tuple[EnrichTarget, Product]] = ((TARGET, VELA),), **kw: Any) -> Any:
    return lookup.find(list(targets), ASK, _usage(), kw.get("ends", monotonic() + 30), "srch_edges01")


class Garbled(ResearchCache):
    """A cache whose kept search links or answer can't be read: the look-up asks again, as if it had nothing."""

    def __init__(self, folder: Path, kind: str) -> None:
        super().__init__(folder=folder, ttl=timedelta(days=7), reddit_ttl=timedelta(days=7))
        self.kind = kind

    def get_text(self, key: Any) -> str | None:
        return "not json at all" if key[0] == self.kind else super().get_text(key)


class RaisingSearch(FakeSearch):
    def search(self, query: str, region: str, usage: SearchUsage, sites: object = ()) -> SerperResult:
        self.asked.append(query)
        raise RuntimeError("made-up search failure")


class RaisingPages(FakePages):
    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        raise RuntimeError("made-up page failure")


class RedirectedToReddit(FakePages):
    """The review page's address redirects to a Reddit thread: the page read is Reddit's, so it is never kept."""

    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        found = super().fetch(url, usage).source
        assert found is not None
        return PageResult(source=Source.model_validate({**found.model_dump(), "url": REDDIT}))


class RaisingLLM:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise RuntimeError("made-up provider bug")


def _good() -> FakeLLMClient:
    return FakeLLMClient({LLMTask.ENRICH_EXTRACT: _answer(GOOD)})


# --- the look-up ----------------------------------------------------------------------------------------------------


def test_no_target_and_no_searchable_target_look_nothing_up(tmp_path: Path) -> None:
    search = FakeSearch([REVIEW])
    lookup = _lookup(tmp_path, search, FakePages(), _good())
    assert _find(lookup, targets=()) is NOTHING
    made_up = EnrichTarget(product_id=VELA.id, product_name=VELA.name, criterion_key="made_up_criterion")
    assert _find(lookup, targets=((made_up, VELA),)) is NOTHING
    assert search.asked == []


class SlowSearch(FakeSearch):
    """Answers after the time for reading pages is up."""

    def search(self, query: str, region: str, usage: SearchUsage, sites: object = ()) -> SerperResult:
        time.sleep(0.3)
        return super().search(query, region, usage, sites)


def test_a_search_that_answers_after_the_reading_time_reads_no_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    short_window(monkeypatch, 0.2)  # pages may be read for 0.2 s
    pages, search, llm = FakePages(), SlowSearch([REVIEW]), _good()
    found = _find(_lookup(tmp_path, search, pages, llm))
    time.sleep(0.5)  # the search's own thread ends: it found the link, and gave the page up
    assert (found.claims, pages.read, search.asked, llm.calls) == ([], [], [QUERY], [])


def test_kept_search_links_that_cant_be_read_are_searched_again(tmp_path: Path) -> None:
    search = FakeSearch([REVIEW])
    lookup = _lookup(tmp_path, search, FakePages(), _good())
    lookup.cache = Garbled(tmp_path / "enrich", "fact_search")
    assert [c.criterion_key for c in _find(lookup).claims] == ["battery_life"]
    assert search.asked == [QUERY]


def test_a_search_or_a_page_that_raises_adds_nothing(tmp_path: Path) -> None:
    llm = _good()
    raising = _find(_lookup(tmp_path / "a", RaisingSearch([REVIEW]), FakePages(), llm))
    assert (raising.claims, raising.searches) == ([], 1)  # it was sent, then failed
    assert _find(_lookup(tmp_path / "b", FakeSearch([REVIEW]), RaisingPages(), llm)).claims == []
    assert llm.calls == []


def test_a_page_redirected_to_reddit_is_never_kept_or_sent(tmp_path: Path) -> None:
    llm = _good()
    assert _find(_lookup(tmp_path, FakeSearch([REVIEW]), RedirectedToReddit(), llm)).claims == []
    assert llm.calls == []


def test_a_kept_answer_that_cant_be_read_is_asked_for_again(tmp_path: Path) -> None:
    llm = _good()
    lookup = _lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), llm)
    lookup.cache = Garbled(tmp_path / "enrich", "fact_answer")
    assert [c.criterion_key for c in _find(lookup).claims] == ["battery_life"]
    assert len(llm.calls) == 1


def test_a_provider_bug_in_the_call_adds_nothing_and_counts_the_call(tmp_path: Path) -> None:
    llm = RaisingLLM()
    found = _find(_lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), llm))  # type: ignore[arg-type]
    assert (found.claims, found.llm_calls, llm.calls) == ([], 1, 1)


# --- the stage ------------------------------------------------------------------------------------------------------


class Closing:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class RaisingLookup:
    in_flight: list[Any] = []  # noqa: RUF012  # no page read is ever left going

    def find(self, *args: Any, **kwargs: Any) -> FactsFound:
        raise RuntimeError("made-up look-up bug")


def test_a_look_up_that_raises_adds_nothing_and_its_own_client_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    stage = PriceLookupStage(now=lambda: NOW)
    client = Closing()
    monkeypatch.setattr(stage, "_wire_facts", lambda retry: (RaisingLookup(), client))
    clock = RetryClock(time_left=lambda: 10.0)
    assert stage._find_facts([(TARGET, VELA)], ASK, _usage(), monotonic() + 10, clock, "srch_edges02") is NOTHING
    assert client.closed


def test_the_real_look_up_is_wired_with_both_searches_research_readers_and_a_seven_day_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("FIRECRAWL_API_KEY", "SERPER_API_KEY", "JINA_API_KEY"):
        monkeypatch.setenv(key, "made-up")  # set before .env is read, so no real key is used
    lookup, client = PriceLookupStage(now=lambda: NOW)._wire_facts(RetryClock(time_left=lambda: 10.0))
    try:
        assert isinstance(lookup.search, SerperSource) and isinstance(lookup.firecrawl, FirecrawlClient)
        assert isinstance(lookup.pages, ReviewPages)
        assert lookup.cache.folder == enrich_cache_dir() and lookup.cache.ttl == ENRICH_TTL
        assert lookup.cache.reddit_ttl == ENRICH_TTL  # nothing from Reddit is ever kept there
        assert lookup.timer is None  # the tests' fake LLM client measures nothing: the call is left CALL_S
    finally:
        client.close()
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")
    lookup, client = PriceLookupStage(now=lambda: NOW)._wire_facts(RetryClock(time_left=lambda: 10.0))
    client.close()
    assert isinstance(lookup.search, SerperSource) and lookup.firecrawl is None  # Serper alone then


def test_the_real_look_up_times_its_call_with_the_real_llm_clients_measures(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.llm import WrapperLLMClient, load_llm_config

    real = WrapperLLMClient(load_llm_config(), {})  # no adapters: nothing could be sent
    monkeypatch.setattr("app.presearch.default_llm", lambda: real)
    lookup, client = PriceLookupStage(now=lambda: NOW)._wire_facts(RetryClock(time_left=lambda: 10.0))
    client.close()
    assert lookup.timer is real and lookup.llm is real


def test_the_stage_wires_its_own_look_up_when_given_none(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    lookup = _lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), _good())
    client = Closing()
    stage = PriceLookupStage(now=lambda: NOW)
    monkeypatch.setattr(stage, "_wire_facts", lambda retry: (lookup, client))
    stage.use_products("srch_edges03", [VELA])
    stage.use_search("srch_edges03", ASK)
    out = stage.run(EnrichInput(targets=[TARGET]), StageContext(search_id="srch_edges03"))
    assert [c.criterion_key for c in out.new_claims] == ["battery_life"] and client.closed


def test_the_note_counts_one_fact_and_several() -> None:
    assert facts_note(1) == "1 missing fact was found on a maker's or review page."
    assert facts_note(3) == "3 missing facts were found on maker and review pages."


def test_a_finished_look_up_is_read_in_time() -> None:
    done: Future[FactsFound] = Future()
    done.set_result(NOTHING)
    assert PriceLookupStage._facts_by(done, monotonic() + 1) is NOTHING
    assert PriceLookupStage._facts_by(None, monotonic() + 1) is NOTHING


def test_research_settings_and_extract_settings_load_for_the_look_up() -> None:
    assert load_research_settings().caps and load_extract_settings(env={}).public_output_tokens > 0


def test_a_target_whose_name_no_claim_may_carry_leaves_its_fact_out_and_the_rest_goes_on(tmp_path: Path) -> None:
    """EnrichTarget.product_name may be empty (no minimum), but Claim.product_mention needs a character: the claim
    found for such a target is left out, never raised, so the stage's prices are kept."""
    nameless = EnrichTarget(product_id=VELA.id, product_name="", criterion_key="battery_life")
    llm = _good()
    found = _lookup(tmp_path, FakeSearch([REVIEW]), FakePages(), llm).find(
        [(nameless, VELA)], ASK, _usage(), monotonic() + 30, "srch_edges04"
    )
    assert found.claims == [] and len(llm.calls) == 1  # the page said it, and the claim couldn't be named
