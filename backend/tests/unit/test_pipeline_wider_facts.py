"""US-41 (S5-23, D-102): when few products rank, the pipeline's fact look-ups reach the "can't tell" products, a fact
may come from another checked page, and a product it settles is ranked once verify and decide run again. Research and
extract are the made-up live-shaped run (tests/fakes/live_run.py): Nova Book 14, Kestrel Slim 15 and Orion Play 15
shown, Aurora Air 13 over the budget, Vela Pro 14 held for its price. Enrich is the real stage with fake searches,
pages, listings and model: no real API is called. Every product, site, page and value here is made up."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import app.contracts as C
from app.contracts import examples as ex
from app.decide.rules import PRICE_KEY
from app.enrich import PriceLookupStage
from app.enrich.facts import FactLookup
from app.enrich.parsing import RawFacts, RawPageFacts
from app.extract.parsing import RawClaim
from app.extract.settings import load_extract_settings
from app.pipeline import InMemoryResultStore, PipelineRunner
from app.research.cache import ResearchCache
from app.research.pages import OTHER_TIER, PageResult
from app.research.serper import SerperResult
from app.research.settings import ProviderCap, load_research_settings
from app.research.usage import SearchUsage, UsageBook
from tests.fakes.research import web_source
from tests.unit.enrich.test_price_lookup import FakeShopping
from tests.unit.test_pipeline_live_decide import live_stages

T = TypeVar("T")
SITE = "https://www.laptop-notes.example"


def _slug(name: str) -> str:
    return "-".join(name.lower().split())


def _page_text(name: str) -> str:
    return f"The {name} has 16 GB of RAM. In our tests the {name} did very well."


class Without:
    """The made-up extraction, less the claims `drop` names: so those values are unknown."""

    def __init__(self, inner: Any, drop: Any) -> None:
        self.inner, self.drop = inner, drop

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def run(self, inp: Any, ctx: Any) -> C.ExtractionResult:
        out = self.inner.run(inp, ctx)
        kept = [c.model_dump() for c in out.claims if not self.drop(c)]
        return C.ExtractionResult.model_validate({**out.model_dump(), "claims": kept})


class OtherSiteSearch:
    """A web search whose one link, for any product, is a page about it on a site no list names."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def search(self, query: str, region: str, usage: SearchUsage, sites: object = ()) -> SerperResult:
        self.asked.append(query)
        if not usage.take("serper"):
            return SerperResult()
        name = next(n for n in NAMES if query.startswith(n))
        return SerperResult(sources=(web_source(f"{SITE}/{_slug(name)}"),))


class OtherSitePages:
    """Reads a page on that site as research's reader reads another page: a web page of the lowest tier."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.read: list[str] = []

    def fetch(self, url: str, usage: SearchUsage) -> PageResult:
        self.read.append(url)
        name = next(n for n in self.names if url.endswith(_slug(n)))
        page = web_source(url, text=_page_text(name))
        fresh = {**page.model_dump(), "via": "direct", "tier": OTHER_TIER, "fetched_at": datetime.now(UTC)}
        return PageResult(source=C.Source.model_validate(fresh))

    def take_read(self, url: str, title: str, text: str, usage: SearchUsage) -> PageResult:
        return PageResult(reason="not_found")


class SpecReadingLLM:
    """The fact call: for each page, the RAM its page states (a spec, a value) and a score for each criterion asked."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, task: C.LLMTask, system: str, user: str, schema: type[T], **kwargs: Any) -> T:
        self.calls.append(user)
        keys = re.findall(r"^- ([a-z_0-9]+):", user, flags=re.MULTILINE)
        pages = []
        for number, (name, _asked) in enumerate(
            re.findall(r"^Page \d+, about (.+), for ([a-z_, 0-9]*):$", user, flags=re.MULTILINE), start=1
        ):
            claims = []
            for key in keys:
                if key == "ram_gb":
                    quote = f"The {name} has 16 GB of RAM."
                    claims.append(
                        RawClaim(
                            product_mention=name,
                            criterion_key=key,
                            value=16,
                            unit="GB",
                            quote=quote,
                            polarity="neutral",
                        )
                    )
                else:
                    quote = f"In our tests the {name} did very well."
                    claims.append(
                        RawClaim(product_mention=name, criterion_key=key, score=9, quote=quote, polarity="positive")
                    )
            pages.append(RawPageFacts(page=number, claims=claims))
        return RawFacts(pages=pages)  # type: ignore[return-value]


class Recording:
    """The enrich stage, keeping each input it was given."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.inputs = inner, []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def run(self, inp: Any, ctx: Any) -> Any:
        self.inputs.append(inp)
        return self.inner.run(inp, ctx)


NAMES = ["Nova Book 14", "Kestrel Slim 15", "Orion Play 15", "Aurora Air 13", "Vela Pro 14"]


def _stages(tmp_path: Path, drop: Any, llm: SpecReadingLLM | None = None) -> tuple[Any, Recording, OtherSiteSearch]:
    stages = live_stages()
    stages.extract = Without(stages.extract, drop)
    search = OtherSiteSearch()
    facts = FactLookup(
        llm or SpecReadingLLM(),  # type: ignore[arg-type]
        search,
        OtherSitePages(NAMES),
        ResearchCache(folder=tmp_path / "enrich", ttl=timedelta(days=7), reddit_ttl=timedelta(days=7)),
        research=load_research_settings(),
        extract=load_extract_settings(env={}),
        now=lambda: datetime.now(UTC),
    )
    usage = UsageBook({"serper": ProviderCap(per_search=6, per_day=60)}, path=None)
    stages.enrich = enrich = Recording(PriceLookupStage(FakeShopping({}), usage=usage, facts=facts))
    return stages, enrich, search


def _run(stages: Any) -> C.SearchResult:
    request = C.SearchRequest(session_id=ex.SESSION_ID, raw_query="a made-up request", rubric=ex.rubric())
    return PipelineRunner(stages, InMemoryResultStore()).run(request)


def _names(result: C.SearchResult) -> dict[str, str]:
    return {p.product.id: p.product.name for p in result.products}


def _targets(enrich: Recording, result: C.SearchResult) -> list[tuple[str, str]]:
    (inp,) = enrich.inputs
    names = _names(result)
    return [(names[t.product_id], t.criterion_key) for t in inp.targets]


def _price(claim: C.Claim) -> bool:
    return claim.criterion_key == PRICE_KEY


def _no_battery(claim: C.Claim) -> bool:
    return claim.criterion_key == "battery_life"  # a criterion no source settles: a gap for every product


def test_with_nothing_ranked_the_look_ups_go_to_the_cant_tell_products_prices_first(tmp_path: Path) -> None:
    stages, enrich, _ = _stages(tmp_path, lambda c: _price(c) or _no_battery(c))  # nothing priced: all "can't tell"
    result = _run(stages)
    targets = _targets(enrich, result)
    assert len(targets) == 6 and len({name for name, _ in targets}) == 5
    assert all(key == PRICE_KEY for _, key in targets[:5])  # what held each one back goes first
    assert targets[5][1] == "battery_life"  # then a held product's own gap: never looked up before D-102


def test_three_ranked_and_two_from_cant_tell(tmp_path: Path) -> None:
    def drop(claim: C.Claim) -> bool:
        return _price(claim) and claim.product_mention == "Aurora Air 13"  # Aurora's price unknown: held, not filtered

    stages, enrich, _ = _stages(tmp_path, lambda c: drop(c) or _no_battery(c))
    result = _run(stages)
    targets = _targets(enrich, result)
    assert {targets[0], targets[1]} == {("Aurora Air 13", PRICE_KEY), ("Vela Pro 14", PRICE_KEY)}
    assert [key for _, key in targets[2:5]] == ["battery_life"] * 3  # the three ranked ones' gaps
    assert {name for name, _ in targets[2:5]} == {"Nova Book 14", "Kestrel Slim 15", "Orion Play 15"}
    assert targets[5][1] == "battery_life" and targets[5][0] in ("Aurora Air 13", "Vela Pro 14")  # a held one's
    assert result.decision is not None and len(result.decision.ranked) == 3


def test_a_fact_from_another_page_raises_a_held_product_into_the_ranking(tmp_path: Path) -> None:
    def drop(claim: C.Claim) -> bool:
        return claim.criterion_key == "ram_gb" and claim.product_mention == "Kestrel Slim 15"

    from app.enrich import EnrichStage

    plain, _, _ = _stages(tmp_path / "before", drop)
    plain.enrich = EnrichStage()  # no look-up at all
    before = _run(plain)
    assert before.decision is not None
    names = _names(before)
    held = {names[f.product_id]: f.rule_attribute for f in before.decision.filtered if f.group == "cant_tell"}
    # Without enrich's fact Kestrel's RAM is unknown, so the 16 GB rule can't tell. (The stub listings price nothing.)
    llm = SpecReadingLLM()
    stages, enrich, search = _stages(tmp_path, drop, llm)
    result = _run(stages)
    assert ("Kestrel Slim 15", "ram_gb") in _targets(enrich, result)
    assert "Kestrel Slim 15 ram" in search.asked
    assert result.decision is not None
    shown = {_names(result)[r.product_id] for r in result.decision.ranked}
    assert "Kestrel Slim 15" in shown  # its RAM, found on another page, let the filter pass it
    assert held["Kestrel Slim 15"] == "ram_gb"  # held for its RAM before
    note = result.coverage_note or ""
    assert "on another website, so it counts for less" in note or "other websites" in note
    ram = next(
        v
        for p in result.products
        for v in p.values
        if p.product.name == "Kestrel Slim 15" and v.criterion_key == "ram_gb"
    )
    assert ram.state in (C.ValueState.KNOWN, C.ValueState.DISPUTED)


def test_a_look_up_that_fails_leaves_the_stage_skipped_and_the_values_as_they_were(tmp_path: Path) -> None:
    def drop(claim: C.Claim) -> bool:
        return claim.criterion_key == "ram_gb" and claim.product_mention == "Kestrel Slim 15"

    stages, enrich, _ = _stages(tmp_path, drop)

    def broken(inp: Any, ctx: Any) -> Any:
        raise RuntimeError("made-up look-up bug")

    enrich.inner.run = broken  # type: ignore[method-assign]
    result = _run(stages)
    report = next(r for r in result.stage_reports if r.stage is C.StageName.ENRICH)
    assert report.status is C.StageStatus.FALLBACK  # the stage's fallback adds nothing
    assert result.decision is not None
    names = _names(result)
    held = {names[f.product_id] for f in result.decision.filtered if f.group == "cant_tell"}
    assert "Kestrel Slim 15" in held  # still unknown, never guessed
    assert "missing fact" not in (result.coverage_note or "")
    kestrel = next(p for p in result.products if p.product.name == "Kestrel Slim 15")
    ram = next(v for v in kestrel.values if v.criterion_key == "ram_gb")
    assert ram.state is C.ValueState.UNKNOWN  # a rule's value is never estimated (DR-09)


def test_picking_the_look_ups_from_cant_tell_failing_keeps_the_usual_ones_and_the_search_goes_on(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import app.pipeline as pipeline

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("made-up target bug")

    monkeypatch.setattr(pipeline, "filled_targets", broken)
    stages, enrich, _ = _stages(tmp_path, _price)
    result = _run(stages)
    assert result.decision is not None
    targets = _targets(enrich, result)
    assert targets and all(key == PRICE_KEY for _, key in targets)  # the prices, as before D-102
