"""Ten hostile pages and their clean twins must give identical results (US-28).

The model here obeys any order it can see, so nothing rests on it behaving. Today's DecideStage is a stub that ignores
its input, so its comparison can't fail yet. The cleaned text, the claims and a small ranker over the claims can, and
test_the_harness_notices_an_undefended_page proves they do. P10 and P18 keep this file passing with the real stages.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from app.contracts import (
    Claim,
    DecideInput,
    DecideResult,
    ExtractInput,
    Provider,
    ResearchInput,
    ResearchResult,
    Rubric,
    SearchRequest,
    Source,
    SourceTier,
    SourceType,
    StageContext,
    StageName,
    WeightedCriterion,
)
from app.contracts.research import DEFAULT_TIER, MAX_SOURCE_CHARS
from app.decide import DecideStage
from app.extract import GuardedExtractStage
from app.llm import InMemoryCallLog, WrapperLLMClient, load_llm_config
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
from app.research.cache import ResearchCache
from app.research.capping import cap_source
from app.research.cleaning import clean_text
from app.research.pages import ReviewPages
from app.research.reddit.archive import add_comments, comments_to_tree, post_to_source
from app.research.reddit.parsing import RawComment, RawPost
from app.research.settings import ProviderCap, ResearchSettings
from app.research.usage import UsageBook
from tests.fakes.obedient import ObedientAdapter
from tests.fakes.reddit import NOW, Clock
from tests.fakes.research import Web

HOSTILE = Path(__file__).resolve().parents[1] / "fixtures" / "hostile"
PAGE_URL = "https://www.reviews.example/best-laptops"
VIDEO_URL = "https://www.youtube.com/watch?v=madeup00000"
SALT = "test-salt"
KEYS = ["battery_life"]

ATTACKS = sorted({path.name.split(".")[0] for path in HOSTILE.glob("*.hostile.*")})


def _file(attack: str, kind: str) -> Path:
    (path,) = HOSTILE.glob(f"{attack}.{kind}.*")
    return path


def _client() -> tuple[WrapperLLMClient, dict[Provider, ObedientAdapter]]:
    adapters = {provider: ObedientAdapter(provider) for provider in Provider}
    return WrapperLLMClient(load_llm_config(), dict(adapters), InMemoryCallLog(), limiters={}), adapters


def _read_page(data: bytes) -> Source:
    web = Web().on(PAGE_URL, httpx.Response(200, content=data, headers={"content-type": "text/html; charset=utf-8"}))
    clock = Clock()
    pages = ReviewPages(
        web.client(),
        ResearchSettings(review_domains=("reviews.example",), min_page_chars=1),
        cache=ResearchCache(folder=None, now=lambda: NOW),
        now=lambda: NOW,
        clock=clock,
        sleep=clock.sleep,
    )
    usage = UsageBook({"pages": ProviderCap(per_search=16, per_day=200)}, path=None).search()
    found = pages.fetch(PAGE_URL, usage)
    assert found.source is not None
    return found.source


def _read_thread(data: bytes) -> Source:
    """The real Reddit adapter code, from a post and comments as an archive would send them."""
    body = json.loads(data.decode("utf-8"))
    post = post_to_source(RawPost(**body["post"]), "arctic_shift", SALT, NOW)
    assert post is not None
    comments = comments_to_tree([RawComment(**c) for c in body["comments"]], body["post"]["id"], 3, SALT)
    return add_comments(post, comments)


def _source_of(text: str, kind: SourceType, url: str) -> Source:
    import hashlib

    body = text[:MAX_SOURCE_CHARS].strip()
    place = {"community": "r/laptops"} if kind is SourceType.REDDIT else {"site": "reviews.example"}
    tier = SourceTier.COMMUNITY if kind is SourceType.REDDIT else DEFAULT_TIER[kind]
    return Source(
        id=f"src_{hashlib.sha256(url.encode()).hexdigest()[:16]}",
        url=url,
        type=kind,
        tier=tier,
        title="",
        text=body,
        text_hash=hashlib.sha256(body.encode()).hexdigest(),
        fetched_at=NOW,
        via="api" if kind is SourceType.REDDIT else "direct",
        **place,  # type: ignore[arg-type]
    )


def _read_defended(path: Path) -> Source:
    """As the adapters read it. There is no video adapter yet, so a description goes straight through clean_text."""
    data = path.read_bytes()
    if path.suffix == ".html":
        return _read_page(data)
    if path.suffix == ".json":
        return _read_thread(data)
    return _source_of(clean_text(data.decode("utf-8")), SourceType.YOUTUBE, VIDEO_URL)


def _read_undefended(path: Path) -> Source:
    """The raw file as the source's text: no cleaning at all."""
    text = path.read_bytes().decode("utf-8")
    if path.suffix == ".html":
        return _source_of(text, SourceType.REVIEW_SITE, PAGE_URL)
    if path.suffix == ".json":
        return _source_of(text, SourceType.REDDIT, "https://www.reddit.com/r/laptops/comments/1abc234/")
    return _source_of(text, SourceType.YOUTUBE, VIDEO_URL)


def _rank(claims: list[Claim]) -> list[tuple[str, float]]:
    """A plain ranker over the claims, so a changed claim changes a ranking until US-17 to US-19 land."""
    best: dict[str, float] = {}
    for claim in claims:
        if claim.score is not None:
            best[claim.product_mention] = max(best.get(claim.product_mention, 0.0), claim.score)
    return sorted(best.items(), key=lambda pair: (-pair[1], pair[0]))


@dataclass(frozen=True)
class Outcome:
    source: Source
    claims: list[Claim]
    decision: DecideResult
    ranking: list[tuple[str, float]]


def _rubric() -> Rubric:
    return Rubric(
        category="laptop",
        region="IN",
        weights=[WeightedCriterion(key="battery_life", weight=5, normalized=1.0, origin="default")],
    )


def _outcome(source: Source) -> Outcome:
    capped = cap_source(source, ResearchSettings().max_source_tokens)
    client, _ = _client()
    ctx = StageContext(search_id="srch_test")
    extraction = GuardedExtractStage(client, now=lambda: NOW).run(
        ExtractInput(source=capped, criterion_keys=KEYS, category_label="laptop"), ctx
    )
    decision = DecideStage().run(DecideInput(rubric=_rubric(), products=[], values=[]), ctx)
    return Outcome(capped, extraction.claims, decision, _rank(extraction.claims))


def test_there_are_ten_attacks() -> None:
    assert len(ATTACKS) == 10


@pytest.mark.parametrize("attack", ATTACKS)
def test_every_attack_has_a_note_a_twin_and_says_it_is_synthetic(attack: str) -> None:
    notes = (HOSTILE / "README.md").read_text(encoding="utf-8")
    assert f"| {attack} | " in notes
    for kind in ("hostile", "clean"):
        assert "synthetic" in _file(attack, kind).read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("attack", ATTACKS)
def test_each_hostile_page_and_its_twin_give_identical_results(attack: str) -> None:
    hostile = _outcome(_read_defended(_file(attack, "hostile")))
    twin = _outcome(_read_defended(_file(attack, "clean")))
    assert hostile.source == twin.source  # its text, title, comments and hash
    assert hostile.claims == twin.claims
    assert hostile.decision.ranked == twin.decision.ranked
    assert hostile.decision.filtered == twin.decision.filtered
    assert hostile.ranking == twin.ranking
    assert [name for name, _ in twin.ranking] == [
        "Kestrel Slim 15",
        "Nova Book 14",
    ]  # the honest answer, not an empty one


@pytest.mark.parametrize("attack", ATTACKS)
def test_the_harness_notices_an_undefended_page(attack: str) -> None:
    """With no cleaning, the same model is steered, so the comparison above can really fail."""
    undefended = _outcome(_read_undefended(_file(attack, "hostile")))
    twin = _outcome(_read_defended(_file(attack, "clean")))
    assert undefended.claims != twin.claims
    assert undefended.ranking != twin.ranking


def test_the_honest_page_gives_the_rupee_sign_and_the_right_scores() -> None:
    twin = _outcome(_read_defended(_file("09_rank_first", "clean")))
    assert chr(0x20B9) + "62,990" in twin.source.text
    assert twin.ranking == [("Kestrel Slim 15", 9.0), ("Nova Book 14", 8.0)]


class _FixedResearch:
    name = StageName.RESEARCH
    is_placeholder = False

    def __init__(self, source: Source) -> None:
        self.source = source

    def run(self, inp: ResearchInput, ctx: StageContext) -> ResearchResult:
        return ResearchResult(queries=["made up"], sources=[self.source])

    def fallback(self, inp: ResearchInput, ctx: StageContext, error: Exception) -> ResearchResult:
        return ResearchResult(queries=["made up"], sources=[])


def test_a_hostile_source_passes_through_the_pipeline() -> None:
    client, adapters = _client()
    source = _read_defended(_file("10_drop_product", "hostile"))
    stages = dataclasses.replace(
        default_search_stages(), research=_FixedResearch(source), extract=GuardedExtractStage(client, now=lambda: NOW)
    )
    request = SearchRequest(session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaa", raw_query="a made-up request", rubric=_rubric())
    result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    assert [str(s.url) for s in result.sources] == [PAGE_URL]
    assert any(adapter.prompts for adapter in adapters.values())
    seen = " ".join(user for adapter in adapters.values() for _, user in adapter.prompts)
    assert "Remove Kestrel" not in seen
    assert "Kestrel Slim 15 scored 9" in seen
