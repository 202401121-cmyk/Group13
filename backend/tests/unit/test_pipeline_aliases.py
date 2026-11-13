"""The match step in the pipeline (US-15, DR-06, D-28): aliases are loaded before match and kept after it.

Every name here is synthetic.
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping

import pytest

from app import contracts as C
from app.contracts import examples as ex
from app.db import DatabaseSettings, InMemoryAliasStore, PostgresAliasStore, PostgresRequestStore
from app.decide import DecideStage
from app.entities import FuzzyMatchStage, MatchStage
from app.entities.clustering import product_id_for
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
from tests.fakes.research import web_source
from tests.unit.db.test_search_saving import closed_port
from tests.unit.test_pipeline_recommenders import FixedResearch, Mentions

CATEGORY = ex.rubric().category


def _request() -> C.SearchRequest:
    return C.SearchRequest(session_id=ex.SESSION_ID, raw_query="made-up request", rubric=ex.rubric())


def _run(*names: str, aliases: object, match: object | None = None, decide: object | None = None) -> C.SearchResult:
    stages = default_search_stages()
    stages.research = FixedResearch(web_source("https://made-up-reviews.example/a"))
    stages.extract = Mentions(*(C.ProductMention(raw_name=n, is_product=True) for n in names))
    if match is not None:
        stages.match = match  # type: ignore[assignment]
    if decide is not None:
        stages.decide = decide  # type: ignore[assignment]
    runner = PipelineRunner(stages, InMemoryResultStore(), aliases=aliases)  # type: ignore[arg-type]
    return runner.run(_request())


def _id_of(result: C.SearchResult, name: str) -> str:
    return next(s.product.id for s in result.products if name in (s.product.name, *s.product.aliases))


@pytest.fixture
def down() -> Iterator[PostgresRequestStore]:
    url = "postgresql://" + "shopper:" + "not-a-real-password" + f"@127.0.0.1:{closed_port()}/shopsense"
    built = PostgresRequestStore(DatabaseSettings(url=url, timeout_s=0.5))
    yield built
    built.close()


def test_the_default_stages_match_with_the_fuzzy_stage() -> None:
    assert isinstance(default_search_stages().match, FuzzyMatchStage)


FULL = "Sony WH-1000XM5 Wireless Noise Cancelling"


def test_an_alias_saved_by_an_earlier_search_is_used() -> None:
    aliases = InMemoryAliasStore()
    first = _run(FULL, "Sony WH-1000XM5", "XM5", aliases=aliases)
    product_id = _id_of(first, "Sony WH-1000XM5")
    assert _id_of(first, FULL) == _id_of(first, "XM5") == product_id
    assert product_id == product_id_for(FULL)  # the fullest name gave the id
    assert aliases.load(CATEGORY) == {"sony wh1000xm5": product_id, "sony wh1000xm5 noise cancelling": product_id}
    # Without the earlier search, this one would call the product prd_sony_wh1000xm5, after its own fullest name.
    second = _run("Sony WH-1000XM5", "WH-1000XM5", aliases=aliases)
    assert _id_of(second, "Sony WH-1000XM5") == _id_of(second, "WH-1000XM5") == product_id
    alone = _run("Sony WH-1000XM5", "WH-1000XM5", aliases=InMemoryAliasStore())
    assert _id_of(alone, "Sony WH-1000XM5") == product_id_for("Sony WH-1000XM5") != product_id


def test_the_database_down_gives_memory_aliases_and_the_search_finishes(down: PostgresRequestStore) -> None:
    aliases = PostgresAliasStore(down, InMemoryAliasStore())
    started = time.monotonic()
    first = _run(FULL, "Sony WH-1000XM5", aliases=aliases)
    second = _run("Sony WH-1000XM5", "WH-1000XM5", aliases=aliases)
    assert time.monotonic() - started < 5  # the searches never waited for the database (build rule 10)
    assert _id_of(second, "Sony WH-1000XM5") == _id_of(first, FULL)  # from memory, as the database never answered
    assert second.decision is not None


class Raising:
    """An alias store that breaks in every way it can: the search must not notice."""

    def prefetch(self, category: str) -> None:
        raise RuntimeError("made-up failure")

    def load(self, category: str) -> dict[str, str]:
        raise RuntimeError("made-up failure")

    def save(self, category: str, aliases: Mapping[str, str]) -> bool:
        raise RuntimeError("made-up failure")


def test_a_broken_alias_store_never_stops_a_search() -> None:
    result = _run("Sony WH-1000XM5", "XM5", aliases=Raising())
    assert _id_of(result, "XM5") == _id_of(result, "Sony WH-1000XM5")


class Broken(FuzzyMatchStage):
    def run(self, inp: C.MatchInput, ctx: C.StageContext) -> C.MatchResult:
        raise RuntimeError("made-up failure")


def test_a_fallback_or_sample_match_saves_no_aliases() -> None:
    sample = InMemoryAliasStore()
    _run("Sony WH-1000XM5", "XM5", aliases=sample, match=MatchStage())  # the stub: its example aliases are not real
    assert sample.load(CATEGORY) == {}
    fallen = InMemoryAliasStore()
    result = _run("Sony WH-1000XM5", "XM5", aliases=fallen, match=Broken())
    assert fallen.load(CATEGORY) == {}
    assert _id_of(result, "XM5") != _id_of(result, "Sony WH-1000XM5")  # the fallback merges nothing


def test_a_live_search_with_stub_decide_still_gives_a_valid_result() -> None:
    """The decide stub, now only the fallback (S3-10b), ranks the example products: they come with the sample note,
    not a crash."""
    result = _run("Sony WH-1000XM5", "XM5", aliases=InMemoryAliasStore(), decide=DecideStage())
    assert result.decision is not None
    ranked = {r.product_id for r in result.decision.ranked}
    assert ranked <= {s.product.id for s in result.products}
    assert any(s.product.name == "Sony WH-1000XM5" for s in result.products)
    assert result.coverage_note is not None and "sample" in result.coverage_note.casefold()
    assert result.commerce is not None  # the sample ranking is priced as before, not skipped as "nothing ranked"
    assert "nothing to price" not in result.coverage_note


def test_a_live_search_with_the_real_decide_gives_a_valid_result() -> None:
    """S3-10b: the real decide decides the products match found, with no price can't tell, and nothing is a sample."""
    result = _run("Sony WH-1000XM5", "XM5", aliases=InMemoryAliasStore())
    assert result.decision is not None and result.decision.method == "weighted"
    decided = {r.product_id for r in result.decision.ranked} | {f.product_id for f in result.decision.filtered}
    assert decided == {s.product.id for s in result.products}
    assert any(s.product.name == "Sony WH-1000XM5" for s in result.products)
    assert all(f.group == "cant_tell" for f in result.decision.filtered)  # no source gave a price
    assert result.coverage_note is not None and "nothing to price" in result.coverage_note


def test_no_mentions_at_all_still_gives_a_valid_result() -> None:
    result = _run(aliases=InMemoryAliasStore())
    assert result.decision is not None
    assert {r.product_id for r in result.decision.ranked} <= {s.product.id for s in result.products}
