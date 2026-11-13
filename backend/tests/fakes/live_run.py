"""A live-shaped search for tests (S3-10b): sources and claims shaped as the real research and extract stages give them.

The stub stages give the example claims, which carry no prices, so with a budget the real decide can only say "can't
tell" for every product. These two stages give a small made-up run instead, where the real verify and decide find:

- shown: Nova Book 14, Kestrel Slim 15 and Orion Play 15, in that order (two review sites agree on each price);
- filtered out: Aurora Air 13, whose price is above a 70,000 budget;
- can't tell: Vela Pro 14, whose price only a Reddit thread gives (community words alone never settle a price).

Claims are made for whatever criteria the search asks for, so any rubric works. Both stages say they are real
(`is_placeholder = False`), as a live run's would. Every site, product, price and quote here is made up.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from app.contracts import (
    Claim,
    ClaimType,
    ExtractInput,
    ExtractionResult,
    ProductMention,
    ResearchInput,
    ResearchResult,
    Source,
    SourceTier,
    SourceType,
    StageContext,
    StageName,
)

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)

# name -> (brand, price on the review sites or None, price on Reddit or None, RAM in GB, score for every criterion)
PRODUCTS: dict[str, tuple[str, float | None, float | None, float, float]] = {
    "Nova Book 14": ("Nova", 64990, None, 16, 8.5),
    "Kestrel Slim 15": ("Kestrel", 67490, None, 16, 7.5),
    "Orion Play 15": ("Orion", 69490, None, 16, 6.5),
    "Aurora Air 13": ("Aurora", 84990, None, 16, 9.0),
    "Vela Pro 14": ("Vela", None, 62990, 16, 7.0),
}
SHOWN = ["Nova Book 14", "Kestrel Slim 15", "Orion Play 15"]
FILTERED_OUT = "Aurora Air 13"
CANT_TELL = "Vela Pro 14"


def _hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _source(url: str, kind: SourceType, tier: SourceTier, title: str, community: str | None = None) -> Source:
    text = f"{title}. " + " ".join(f"{name} is a laptop." for name in PRODUCTS)
    reddit = kind is SourceType.REDDIT
    return Source(
        id=f"src_{_hex(url)[:16]}",
        url=url,
        type=kind,
        tier=tier,
        title=title,
        text=text,
        text_hash=_hex(text),
        fetched_at=T0,
        community=community,
        site=None if reddit else url.split("/")[2],
        via="api" if reddit else "direct",
    )


def sources() -> list[Source]:
    return [
        _source(
            "https://made-up-reviews.example/best-coding-laptops",
            SourceType.REVIEW_SITE,
            SourceTier.EDITORIAL,
            "Best coding laptops, tested",
        ),
        _source(
            "https://another-made-up-review.example/laptop-prices",
            SourceType.REVIEW_SITE,
            SourceTier.EDITORIAL,
            "Laptop prices this month",
        ),
        _source(
            "https://www.reddit.com/r/SuggestALaptop/comments/mdup01/",
            SourceType.REDDIT,
            SourceTier.COMMUNITY,
            "Which laptop for coding?",
            community="r/SuggestALaptop",
        ),
    ]


def _claim(
    source: Source,
    name: str,
    key: str,
    quote: str,
    *,
    value: float | None = None,
    unit: str | None = None,
    score: float | None = None,
) -> Claim:
    return Claim(
        id="clm_" + _hex(f"{source.id}|{name}|{key}")[:16],
        source_id=source.id,
        source_url=source.url,
        product_mention=name,
        criterion_key=key,
        value=value,
        unit=unit,
        score=score,
        quote=quote,
        claim_type=ClaimType.SPEC if score is None else ClaimType.EXPERIENCE,
        polarity="neutral" if score is None else "positive",
        retrieved_at=T0,
        extracted_at=T0,
    )


def claims_for(source: Source, criterion_keys: list[str]) -> list[Claim]:
    """What the made-up run reads from one source."""
    first, second, thread = sources()
    out: list[Claim] = []
    for name, (_, price, reddit_price, ram, score) in PRODUCTS.items():
        if source.id == first.id:
            out.append(_claim(source, name, "ram_gb", f"{name} has {ram:g} GB of RAM.", value=ram, unit="GB"))
            out += [
                _claim(source, name, key, f"{name}: {key.replace('_', ' ')} {score:g}/10.", score=score)
                for key in criterion_keys
            ]
        if source.id in (first.id, second.id) and price is not None:
            out.append(
                _claim(source, name, "price_inr", f"{name} sells for {price:g} rupees.", value=price, unit="INR")
            )
        if source.id == thread.id and reddit_price is not None:
            quote = f"Got my {name} for {reddit_price:g} rupees."
            out.append(_claim(source, name, "price_inr", quote, value=reddit_price, unit="INR"))
    return out


class LiveShapedResearch:
    """Research as a live run gives it: the three made-up sources and no failures."""

    name = StageName.RESEARCH
    is_placeholder = False

    def run(self, inp: ResearchInput, ctx: StageContext) -> ResearchResult:
        return ResearchResult(queries=["best laptop for coding"], sources=sources())

    def fallback(self, inp: ResearchInput, ctx: StageContext, error: Exception) -> ResearchResult:
        return ResearchResult(queries=["best laptop for coding"], sources=[])


class LiveShapedExtract:
    """Extract as a live run gives it: each source's own claims, for the criteria asked, and the names it holds."""

    name = StageName.EXTRACT
    is_placeholder = False

    def run(self, inp: ExtractInput, ctx: StageContext) -> ExtractionResult:
        found = claims_for(inp.source, list(inp.criterion_keys))
        names = sorted({c.product_mention for c in found})
        mentions = [ProductMention(raw_name=n, is_product=True, brand=PRODUCTS[n][0]) for n in names]
        return ExtractionResult(source_id=inp.source.id, claims=found, mentions=mentions)

    def fallback(self, inp: ExtractInput, ctx: StageContext, error: Exception) -> ExtractionResult:
        return ExtractionResult(source_id=inp.source.id)


def use_live_shaped_run(monkeypatch: Any) -> None:
    """Make default_search_stages() research and extract like the made-up run (tests/conftest.py gives the stubs)."""
    monkeypatch.setattr("app.pipeline.default_research_stage", LiveShapedResearch)
    monkeypatch.setattr("app.pipeline.default_extract_stage", LiveShapedExtract)
