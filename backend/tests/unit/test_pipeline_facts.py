"""US-41 (S5-6): after a first ranking, the top products' missing facts are looked up, verify and decide run again,
and only what is still missing is estimated. Research and extract are the made-up live-shaped run
(tests/fakes/live_run.py). Enrich is the real stage, with a fake web search, fake pages and a fake model that answers
from what each page says: no real API is called. Every product, page and value here is made up.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pytest

import app.contracts as C
from app.contracts import EnrichTarget, ValueState
from app.contracts import examples as ex
from app.decide.rules import PRICE_KEY
from app.enrich import PriceLookupStage
from app.enrich.facts import FactLookup
from app.enrich.parsing import RawFacts, RawPageFacts
from app.enrich.stage import facts_note
from app.enrich.targets import FACTS_PER_PRODUCT, PRICES_WITH_FACTS, fact_targets, shared_targets
from app.extract.parsing import RawClaim
from app.extract.settings import load_extract_settings
from app.pipeline import InMemoryResultStore, PipelineRunner
from app.research.cache import ResearchCache
from app.research.settings import ProviderCap, load_research_settings
from app.research.usage import UsageBook
from tests.unit.enrich.test_fact_lookup import FakePages, FakeSearch
from tests.unit.test_pipeline_live_decide import live_stages

T = TypeVar("T")
REVIEW = "https://www.91mobiles.com/{slug}-review"


class PageReadingLLM:
    """Answers the fact call from the pages it is sent: for each page, a score of 9 on every listed criterion, quoting
    the page's own sentence. Code keeps only what was looked up for the page's product."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, task: C.LLMTask, system: str, user: str, schema: type[T], **kwargs: Any) -> T:
        self.calls.append(user)
        keys = re.findall(r"^- ([a-z_]+):", user, flags=re.MULTILINE)
        pages = []
        found = re.findall(r"^Page \d+, about (.+), for ([a-z_, ]*):$", user, flags=re.MULTILINE)
        for number, (name, _asked) in enumerate(found, start=1):
            claims = [  # every criterion, not only those asked for the page: code keeps only what was looked up
                RawClaim(product_mention=name, criterion_key=key, score=9, quote=_sentence(name), polarity="positive")
                for key in keys
            ]
            pages.append(RawPageFacts(page=number, claims=claims))
        return RawFacts(pages=pages)  # type: ignore[return-value]


def _sentence(name: str) -> str:
    return f"In our tests the {name} did very well."


class PagesByName(FakePages):
    def fetch(self, url: str, usage: Any) -> Any:
        found = super().fetch(url, usage)
        name = url.rsplit("/", 1)[-1].removesuffix("-review").replace("-", " ").title()
        return type(found)(source=C.Source.model_validate({**found.source.model_dump(), "text": _sentence(name)}))


NAMES = ("Nova Book 14", "Kestrel Slim 15", "Orion Play 15", "Vela Pro 14", "Aurora Air 13")  # the made-up run's
MISSING = "memory_and_storage"  # a fixed laptop criterion the made-up sources are made to say nothing about


class SearchByName(FakeSearch):
    """A review page for the product the query names, at an address that spells its name."""

    def search(self, query: str, region: str, usage: Any, sites: object = ()) -> Any:
        name = next(n for n in NAMES if query.startswith(n))
        self.links = [REVIEW.format(slug="-".join(name.split()).lower())]
        return super().search(query, region, usage, sites)


class WithoutKey:
    """The made-up extraction, with no claim on one criterion: so every product's value on it is unknown."""

    def __init__(self, inner: Any, key: str) -> None:
        self.inner, self.key = inner, key

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def run(self, inp: Any, ctx: Any) -> C.ExtractionResult:
        out = self.inner.run(inp, ctx)
        kept = [c.model_dump() for c in out.claims if c.criterion_key != self.key]
        return C.ExtractionResult.model_validate({**out.model_dump(), "claims": kept})


def _plain() -> Any:
    stages = live_stages()
    stages.extract = WithoutKey(stages.extract, MISSING)
    return stages


def _stages(tmp_path: Path, llm: PageReadingLLM) -> Any:
    stages = _plain()
    usage = UsageBook({"serper": ProviderCap(per_search=6, per_day=60)}, path=None)
    cache = ResearchCache(folder=tmp_path / "enrich", ttl=timedelta(days=7), reddit_ttl=timedelta(days=7))
    facts = FactLookup(
        llm,  # type: ignore[arg-type]
        SearchByName([]),
        PagesByName(),
        cache,
        research=load_research_settings(),
        extract=load_extract_settings(env={}),
        now=lambda: datetime.now(UTC),
    )
    stages.enrich = PriceLookupStage(None, usage=usage, facts=facts)
    return stages


def _rubric() -> C.Rubric:
    """The example rubric, and memory and storage, a fixed laptop criterion the made-up sources say nothing about."""
    base = ex.rubric()
    weights = [*(w.weight for w in base.weights), 8.0]
    total = sum(weights)
    rows = [{**w.model_dump(), "normalized": w.weight / total} for w in base.weights]
    rows.append({**rows[0], "key": MISSING, "weight": 8.0, "normalized": 8.0 / total})
    return C.Rubric.model_validate({**base.model_dump(), "weights": rows})


def _run(stages: Any) -> C.SearchResult:
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=_rubric())
    return PipelineRunner(stages, InMemoryResultStore()).run(request)


def test_the_top_products_missing_facts_are_found_and_only_the_rest_stays_estimated(tmp_path: Path) -> None:
    before = _run(_plain())
    llm = PageReadingLLM()
    after = _run(_stages(tmp_path, llm))
    assert before.decision is not None and after.decision is not None
    assert len(llm.calls) == 1  # one batched call for every page
    found = sum(
        1
        for summary in after.products
        for value in summary.values
        if value.criterion_key != PRICE_KEY and value.state is ValueState.KNOWN
    ) - sum(
        1
        for summary in before.products
        for value in summary.values
        if value.criterion_key != PRICE_KEY and value.state is ValueState.KNOWN
    )
    assert found > 0
    assert facts_note(found) in (after.coverage_note or "")
    top = [r.product_id for r in before.decision.ranked[:5]]
    assert evidence_share(_rubric(), top, after) > evidence_share(_rubric(), top, before)  # more rests on evidence


def evidence_share(rubric: C.Rubric, ids: list[str], result: C.SearchResult) -> float:
    """The share of the shopper's whole weight these products' values have evidence for (Known or Disputed), on
    average. Unlike decide's coverage, a criterion no product has counts too: that is what a look-up can fill."""
    weight = {w.key: w.normalized for w in rubric.weights}
    values = {(v.product_id, v.criterion_key): v.state for p in result.products for v in p.values}
    shares = [
        sum(w for key, w in weight.items() if values.get((pid, key)) in (ValueState.KNOWN, ValueState.DISPUTED))
        for pid in ids
    ]
    return sum(shares) / len(shares)


class NotingDecide:
    """Decide, with a note of its own for each run, kept by the search ID it ran under: as the real one keeps it."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.notes, self.runs = inner, {}, []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def run(self, inp: Any, ctx: Any) -> Any:
        self.runs.append(ctx.search_id)
        self.notes[ctx.search_id] = f"Decide run {len(self.runs)} said so."
        return self.inner.run(inp, ctx)

    def coverage_note(self, search_id: str) -> str | None:
        return self.notes.pop(search_id, None)


def test_the_first_ranking_runs_apart_and_its_note_never_reaches_the_result(tmp_path: Path) -> None:
    stages = _stages(tmp_path, PageReadingLLM())
    stages.decide = decide = NotingDecide(stages.decide)
    result = _run(stages)
    first, real = decide.runs
    assert first != real and first.startswith(real)  # its own search ID, even for a note kept late
    note = result.coverage_note or ""
    assert "Decide run 2 said so." in note
    assert "Decide run 1" not in note


def _ranked(product_id: str, rank: int, gaps: list[str], known: list[str]) -> C.RankedProduct:
    contributions = [
        C.Contribution(criterion_key=k, score=None, normalized_weight=0.1, contribution=0.0, state=ValueState.UNKNOWN)
        for k in gaps
    ] + [
        C.Contribution(criterion_key=k, score=7, normalized_weight=0.1, contribution=0.7, state=ValueState.KNOWN)
        for k in known
    ]
    return C.RankedProduct(
        product_id=product_id,
        rank=rank,
        score=None,
        contributions=contributions,
        coverage=0.5,
        confidence="medium",
        gap_to_leader=0.0,
    )


def test_fact_targets_are_the_top_products_highest_weight_gaps_with_a_stand_in() -> None:
    rubric = ex.rubric()  # cpu_performance 0.20, keyboard_comfort 0.18, battery_life 0.16, weight 0.14, ...
    products = {f"prd_made_up_{n}": C.Product(id=f"prd_made_up_{n}", name=f"Made-up Laptop {n}") for n in range(7)}
    aliases = {p.name: p.id for p in products.values()}
    ranked = [
        _ranked(f"prd_made_up_{n}", n + 1, ["battery_life", "cpu_performance", "weight"], ["keyboard_comfort"])
        for n in range(7)
    ]
    targets = fact_targets(rubric, ranked, products, aliases, searchable=lambda key: key != "weight")
    assert {t.product_id for t in targets} == {f"prd_made_up_{n}" for n in range(5)}  # the top 5 only
    assert targets[0] == EnrichTarget(
        product_id="prd_made_up_0", product_name="Made-up Laptop 0", criterion_key="cpu_performance"
    )
    assert all(t.criterion_key in {"cpu_performance", "battery_life"} for t in targets)  # no stand-in: not searched
    assert len(targets) == 5 * FACTS_PER_PRODUCT
    assert [t.criterion_key for t in targets[:5]] == ["cpu_performance"] * 5  # highest weight first, then rank


def test_prices_take_at_most_three_searches_when_there_are_fact_gaps_and_six_in_all() -> None:
    price = [EnrichTarget(product_id=f"prd_p_{n}", product_name=f"P {n}", criterion_key=PRICE_KEY) for n in range(6)]
    fact = [
        EnrichTarget(product_id=f"prd_f_{n}", product_name=f"F {n}", criterion_key="battery_life") for n in range(6)
    ]
    shared = shared_targets(price, fact)
    assert [t.criterion_key for t in shared] == [PRICE_KEY] * PRICES_WITH_FACTS + ["battery_life"] * 3
    assert shared_targets(price, []) == price  # no fact gaps: prices keep all six
    assert shared_targets(price[:1], fact) == [price[0], *fact[:5]]  # facts get the rest


def test_research_and_enrich_sources_fit_verifys_sixty_snippets_making_room_first() -> None:
    from app.pipeline import MAX_VERIFY_SOURCES, fit_sources
    from tests.fakes.research import review_source, web_source

    pages = [review_source(f"https://www.91mobiles.com/page-{n}") for n in range(38)]
    snippets = [
        C.Source.model_validate({**web_source(f"https://www.example.com/{n}").model_dump(), "via": "search_snippet"})
        for n in range(20)
    ]
    added = [review_source(f"https://www.digit.in/added-{n}") for n in range(6)]
    kept, fitted = fit_sources([*pages, *snippets], added, [])
    assert len(kept) + len(fitted) == MAX_VERIFY_SOURCES
    assert fitted == added and all(p in kept for p in pages)  # only uncited snippets made room
    no_room, only = fit_sources(
        [*pages, *(review_source(f"https://www.smartprix.com/{n}") for n in range(20))], added, []
    )
    assert len(no_room) == 58 and only == added[:2]  # no snippet to spare: what fits goes, in enrich's order


def test_a_ranked_product_match_doesnt_know_or_cant_name_is_never_a_fact_target() -> None:
    rubric = ex.rubric()
    named = C.Product(id="prd_made_up_1", name="Made-up Laptop 1")
    nameless = C.Product(id="prd_made_up_2", name="Made-up Laptop 2")
    ranked = [
        _ranked(p, n + 1, ["battery_life"], []) for n, p in enumerate(["prd_gone_9", "prd_made_up_2", "prd_made_up_1"])
    ]
    targets = fact_targets(
        rubric, ranked, {p.id: p for p in (named, nameless)}, {named.name: named.id}, searchable=lambda key: True
    )
    assert [t.product_id for t in targets] == ["prd_made_up_1"]  # one not kept, one with no name verify knows


def _first_ranking_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.pipeline as pipeline

    real = pipeline.run_stage

    def run(stage: Any, inp: Any, ctx: Any, limit: float) -> Any:
        if ctx.search_id.endswith(pipeline.FIRST_RANKING):
            raise RuntimeError("made-up first-ranking bug")
        return real(stage, inp, ctx, limit)

    monkeypatch.setattr(pipeline, "run_stage", run)


def test_a_first_ranking_that_raises_looks_no_fact_up_and_the_search_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _first_ranking_raises(monkeypatch)
    llm = PageReadingLLM()
    result = _run(_stages(tmp_path, llm))
    assert result.decision is not None and result.decision.ranked
    assert llm.calls == []


def test_facts_with_no_room_beside_researchs_sources_are_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify takes 60 sources at most: when no enrich source fits beside research's, its claims are left out."""
    monkeypatch.setattr("app.pipeline.fit_sources", lambda research, added, claims: (list(research), []))
    llm = PageReadingLLM()
    result = _run(_stages(tmp_path, llm))
    assert len(llm.calls) == 1  # a fact was found
    assert "missing fact" not in (result.coverage_note or "")  # and none was taken


def test_verify_failing_again_keeps_the_first_values_and_says_no_fact_was_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.pipeline as pipeline

    search_class = next(c for c in vars(pipeline).values() if isinstance(c, type) and hasattr(c, "_verify_input"))
    real = search_class._verify_input
    calls: list[int] = []

    def verify_input(self: Any, claims: Any, sources: Any) -> Any:
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("made-up verify bug")
        return real(self, claims, sources)

    monkeypatch.setattr(search_class, "_verify_input", verify_input)
    result = _run(_stages(tmp_path, PageReadingLLM()))
    assert len(calls) == 2 and result.decision is not None
    assert "missing fact" not in (result.coverage_note or "")


class Recording:
    """A stage that keeps what it was told and returned, and otherwise is the stage it wraps."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.outputs, self.asks = inner, [], []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def run(self, inp: Any, ctx: Any) -> Any:
        out = self.inner.run(inp, ctx)
        self.outputs.append(out)
        return out

    def use_search(self, search_id: str, ask: Any) -> None:
        self.asks.append(ask)
        self.inner.use_search(search_id, ask)


def test_the_first_rankings_order_reaches_the_fact_look_up(tmp_path: Path) -> None:
    """D-101 splits the look-ups by the first ranking's order: the pipeline hands it over, best first."""
    stages = _stages(tmp_path, PageReadingLLM())
    stages.decide = decide = Recording(stages.decide)
    stages.enrich = enrich = Recording(stages.enrich)
    _run(stages)
    (ask,) = enrich.asks
    first = [r.product_id for r in decide.outputs[0].ranked]
    assert ask.ranked and list(ask.ranked) == [pid for pid in first if pid in ask.ranked]  # its order, best first
