"""The hook after extraction (US-13): counts come from the run's products, reach Match and print with --claims.

Every thread and name here is synthetic.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
from typing import Any

import pytest

from app import contracts as C
from app.cli import main, reddit_block, salt_is_set
from app.contracts import ExtractionResult, Source, examples
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
from app.research import ResearchStage
from app.research.reddit.quality import ThreadScore
from tests.fakes.research import rubric, thread, web_source

ANN, BEN = "a" * 64, "b" * 64


def _thread(name: str, *comments: tuple[str | None, str]) -> Source:
    source = thread(name, text="A made-up post.")
    rows = tuple(
        C.SourceComment(id=f"m{i}", depth=1, upvotes=1, text=text, author_hash=who)
        for i, (who, text) in enumerate(comments)
    )
    return Source.model_validate({**source.model_dump(), "comments": rows})


class FixedResearch:
    name = C.StageName.RESEARCH
    is_placeholder = False

    def __init__(self, *sources: Source) -> None:
        self.sources = sources

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        return C.ResearchResult(queries=["made up"], sources=list(self.sources))

    def fallback(self, inp: C.ResearchInput, ctx: C.StageContext, error: Exception) -> C.ResearchResult:
        return C.ResearchResult(queries=["made up"], sources=[])


class Mentions:
    """An extract stage that finds the same mentions on one chosen source."""

    name = C.StageName.EXTRACT
    is_placeholder = False

    def __init__(self, *mentions: C.ProductMention) -> None:
        self.mentions = mentions

    def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> ExtractionResult:
        return ExtractionResult(source_id=inp.source.id, mentions=list(self.mentions))

    def fallback(self, inp: C.ExtractInput, ctx: C.StageContext, error: Exception) -> ExtractionResult:
        return ExtractionResult(source_id=inp.source.id)


class SpyMatch:
    name = C.StageName.MATCH
    is_placeholder = True

    def __init__(self, real: Any) -> None:
        self.real = real
        self.counts: list[C.RecommenderCount] | None = None

    def run(self, inp: C.MatchInput, ctx: C.StageContext) -> C.MatchResult:
        self.counts = list(inp.recommender_counts)
        result: C.MatchResult = self.real.run(inp, ctx)
        return result

    def fallback(self, inp: C.MatchInput, ctx: C.StageContext, error: Exception) -> C.MatchResult:
        result: C.MatchResult = self.real.fallback(inp, ctx, error)
        return result


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=rubric())


def _search(extract: Any, *sources: Source) -> tuple[C.SearchResult, SpyMatch]:
    defaults = default_search_stages()
    spy = SpyMatch(defaults.match)
    stages = dataclasses.replace(defaults, research=FixedResearch(*sources), extract=extract, match=spy)
    return PipelineRunner(stages, InMemoryResultStore()).run(_request()), spy


def _mention(name: str, is_product: bool = True) -> C.ProductMention:
    return C.ProductMention(raw_name=name, is_product=is_product)


def test_counts_reach_the_match_stage() -> None:
    threads = [_thread("aaa", (ANN, "The XM5."), (BEN, "The XM5 too."))]
    _, spy = _search(Mentions(_mention("XM5")), *threads)
    assert [(c.product_mention, c.distinct_recommenders, c.threads) for c in spy.counts or []] == [("XM5", 2, 1)]


def test_a_product_found_on_any_source_is_counted_in_the_threads() -> None:
    """Extraction may read a thread or not. Every thread still counts for every product the run found."""
    page = web_source("https://a.example/", text="The G14 is a laptop.")
    reddit = _thread("aaa", (ANN, "I use a G14."))

    class OnlyThePage(Mentions):
        def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> ExtractionResult:
            found = list(self.mentions) if inp.source.id == page.id else []
            return ExtractionResult(source_id=inp.source.id, mentions=found)

    _, spy = _search(OnlyThePage(_mention("G14")), reddit, page)
    assert [c.product_mention for c in spy.counts or []] == ["G14"]


def test_product_types_are_not_counted() -> None:
    _, spy = _search(
        Mentions(_mention("memory foam", False), _mention("XM5")), _thread("aaa", (ANN, "Memory foam XM5."))
    )
    assert [c.product_mention for c in spy.counts or []] == ["XM5"]


def test_a_search_with_no_products_gives_no_counts() -> None:
    _, spy = _search(Mentions(), _thread("aaa", (ANN, "The XM5.")))
    assert spy.counts == []


def test_a_bug_in_the_counting_does_not_stop_the_search(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(*_: object, **__: object) -> list[C.RecommenderCount]:
        raise RuntimeError("a made-up secret bug")

    monkeypatch.setattr("app.pipeline.count_recommenders", broken)
    with caplog.at_level(logging.WARNING, logger="app.pipeline"):
        result, spy = _search(Mentions(_mention("XM5")), _thread("aaa", (ANN, "The XM5.")))
    assert spy.counts == []
    assert result.decision is not None
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text


def test_a_sample_search_keeps_the_sample_counts() -> None:
    defaults = default_search_stages()
    spy = SpyMatch(defaults.match)
    PipelineRunner(dataclasses.replace(defaults, match=spy), InMemoryResultStore()).run(_request())
    assert spy.counts == examples.recommender_counts()


def test_the_result_has_no_counts_of_its_own() -> None:
    """SearchResult's shape is unchanged: counts stay on the run's working state."""
    result, _ = _search(Mentions(_mention("XM5")), _thread("aaa", (ANN, "The XM5.")))
    assert "recommender_counts" not in result.model_dump_json()


# --- what --claims prints ---------------------------------------------------------------------------


def test_the_block_shows_scores_the_dropped_threads_and_counts() -> None:
    kept = Source.model_validate({**thread("aaa", text="A made-up post.").model_dump(), "quality": 0.412})
    gone = ThreadScore(
        url="https://www.reddit.com/r/pics/comments/zzz/", community="r/pics", score=0.041, estimated=True
    )
    counts = [C.RecommenderCount(product_mention="XM5", distinct_recommenders=9, threads=3)]
    block = reddit_block([kept, web_source("https://a.example/")], [gone], counts)
    assert "Reddit threads (1 kept, 1 dropped):" in block
    assert f"kept     0.412  r/laptops  {kept.url}" in block
    assert "dropped  0.041  r/pics  https://www.reddit.com/r/pics/comments/zzz/  (estimated)" in block
    assert "XM5: 9 people in 3 threads" in block
    assert "a.example" not in block


def test_a_kept_thread_with_an_estimated_score_is_marked() -> None:
    kept = Source.model_validate({**thread("aaa", text="A made-up post.").model_dump(), "quality": 0.412})
    block = reddit_block([kept], [], [], frozenset({str(kept.url)}))
    assert f"kept     0.412  r/laptops  {kept.url}  (estimated)" in block
    assert "(estimated)" not in reddit_block([kept], [], [])


def test_the_block_says_so_when_the_search_stopped_before_the_counts_were_used() -> None:
    block = reddit_block([], [], None)
    assert "The search stopped before the counts were used." in block
    assert "No products were counted." not in block


def test_the_block_says_so_when_nothing_was_counted() -> None:
    block = reddit_block([], [], [])
    assert "No Reddit threads." in block
    assert "No products were counted." in block


def test_the_block_says_why_when_no_author_is_known() -> None:
    """With no salt set, Reddit sources carry no author hash, so nobody can be counted. It must say so, not show 0."""
    no_authors = _thread("aaa", (None, "The XM5."))
    assert "REDDIT_USER_SALT is not set" in reddit_block([no_authors], [], [], salt_set=False)
    assert "REDDIT_USER_SALT" not in reddit_block([_thread("bbb", (ANN, "The XM5."))], [], [], salt_set=False)
    assert "REDDIT_USER_SALT" not in reddit_block([], [], [], salt_set=False)


def test_the_note_says_cached_threads_carry_no_hashes_when_the_salt_is_set() -> None:
    no_authors = _thread("aaa", (None, "The XM5."))
    block = reddit_block([no_authors], [], [], salt_set=True)
    assert "these threads carry no author hashes (they were cached before the salt was set)" in block
    assert "REDDIT_USER_SALT" not in block


def test_the_note_names_the_salt_only_when_it_is_not_set() -> None:
    no_authors = _thread("aaa", (None, "The XM5."))
    block = reddit_block([no_authors], [], [], salt_set=False)
    assert "REDDIT_USER_SALT is not set" in block
    assert "no author hashes" not in block


def test_no_note_when_an_author_is_known() -> None:
    known = _thread("bbb", (ANN, "The XM5."))
    for salt_set in (True, False):
        block = reddit_block([known], [], [], salt_set=salt_set)
        assert "REDDIT_USER_SALT" not in block
        assert "no author hashes" not in block


def test_the_claims_option_tells_the_block_whether_the_salt_is_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    told: list[bool] = []

    def spy(*args: Any, **kwargs: Any) -> str:
        told.append(kwargs["salt_set"])
        return reddit_block(*args, **kwargs)

    monkeypatch.setattr("app.cli.reddit_block", spy)
    monkeypatch.setenv("REDDIT_USER_SALT", "made-up-salt")
    assert main(["best laptop for coding under 70k", "--claims", "--no-db"]) == 0
    monkeypatch.setenv("REDDIT_USER_SALT", "")
    assert main(["best laptop for coding under 70k", "--claims", "--no-db"]) == 0
    assert told == [True, False]


def test_the_salt_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDDIT_USER_SALT", "made-up-salt")
    assert salt_is_set() is True
    monkeypatch.setenv("REDDIT_USER_SALT", "  ")
    assert salt_is_set() is False
    monkeypatch.delenv("REDDIT_USER_SALT")
    assert salt_is_set() is False


def test_one_person_in_one_thread_reads_right() -> None:
    counts = [C.RecommenderCount(product_mention="XM5", distinct_recommenders=1, threads=1)]
    assert "XM5: 1 person in 1 thread" in reddit_block([], [], counts)


def test_the_claims_option_prints_the_reddit_block(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["best laptop for coding under 70k", "--claims", "--no-db"]) == 0
    out = capsys.readouterr().out
    assert "Reddit threads (1 kept, 0 dropped):" in out
    assert "Recommenders per product:" in out
    assert "Nova Book 14: 9 people in 1 thread" in out  # the sample run's counts


def test_the_claims_option_prints_the_threads_research_dropped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    gone = ThreadScore(
        url="https://www.reddit.com/r/pics/comments/zzz/", community="r/pics", score=0.041, estimated=False
    )

    class Reported(ResearchStage):
        last_dropped_threads = (gone,)
        last_estimated_threads = frozenset({str(examples.sources()[0].url)})

    def stages() -> Any:
        return dataclasses.replace(default_search_stages(), research=Reported())

    monkeypatch.setattr("app.cli.default_search_stages", stages)
    assert main(["best laptop for coding under 70k", "--claims", "--no-db"]) == 0
    out = capsys.readouterr().out
    assert "Reddit threads (1 kept, 1 dropped):" in out
    assert "dropped  0.041  r/pics  https://www.reddit.com/r/pics/comments/zzz/" in out
    assert f"{examples.sources()[0].url}  (estimated)" in out


def test_a_search_stopped_before_match_says_so_through_the_real_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("app.cli.PipelineRunner", functools.partial(PipelineRunner, search_time_limit_s=0.0))
    assert main(["best laptop for coding under 70k", "--claims", "--no-db"]) == 0
    out = capsys.readouterr().out
    assert "The search stopped before the counts were used." in out
    assert "No products were counted." not in out


def test_without_the_option_the_block_is_not_printed(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["best laptop for coding under 70k", "--no-db"]) == 0
    assert "Recommenders per product:" not in capsys.readouterr().out
