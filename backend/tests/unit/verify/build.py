"""Small builders for the verify tests. Every site, product and claim here is made up."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.contracts import (
    Claim,
    ClaimType,
    MatchResult,
    Product,
    Source,
    SourceComment,
    SourceTier,
    SourceType,
    VerifyInput,
)

T0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
KEYS = ["battery_life", "ram_gb", "price_inr"]


def _hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def source(
    url: str,
    kind: SourceType = SourceType.REVIEW_SITE,
    tier: SourceTier | None = None,
    community: str | None = None,
    *,
    title: str = "A made-up page",
    text: str | None = None,
    comments: Sequence[str] = (),
) -> Source:
    """A page or a Reddit thread. The site is the URL's host, unless it is Reddit. A thread may have comments."""
    tiers = {
        SourceType.MANUFACTURER: SourceTier.MANUFACTURER,
        SourceType.LAB: SourceTier.LAB,
        SourceType.REDDIT: SourceTier.COMMUNITY,
        SourceType.RETAILER: SourceTier.RETAILER,
    }
    host = url.split("/")[2].removeprefix("www.")
    text = text if text is not None else f"Made-up page for tests: {url}"
    replies = tuple(
        SourceComment(id=f"c{i}", depth=1, upvotes=1, text=reply, parent_id=None) for i, reply in enumerate(comments)
    )
    return Source(
        id=f"src_{_hex(url)[:16]}",
        url=url,
        type=kind,
        tier=tier or tiers.get(kind, SourceTier.EDITORIAL),
        title=title,
        text=text,
        text_hash=_hex(text),
        fetched_at=T0,
        community=community if kind is SourceType.REDDIT else None,
        site=None if kind is SourceType.REDDIT else host,
        via="api" if kind is SourceType.REDDIT else "direct",
        comments=replies,
    )


def reddit(
    sub: str = "r/SuggestALaptop",
    post: str = "abc123",
    *,
    title: str = "A made-up page",
    text: str | None = None,
    comments: Sequence[str] = (),
) -> Source:
    url = f"https://www.reddit.com/{sub}/comments/{post}/"
    return source(url, SourceType.REDDIT, community=sub, title=title, text=text, comments=comments)


def claim(
    src: Source,
    mention: str,
    key: str | None,
    *,
    value: float | str | None = None,
    unit: str | None = None,
    score: float | None = None,
    claim_type: ClaimType | None = None,
    weight: float = 1.0,
    n: int = 0,
    quote: str | None = None,
) -> Claim:
    kind = claim_type or (
        ClaimType.SPEC if src.tier in (SourceTier.MANUFACTURER, SourceTier.LAB) else ClaimType.EXPERIENCE
    )
    return Claim(
        id="clm_" + _hex(f"{src.id}|{mention}|{key}|{value}|{score}|{n}")[:16],
        source_id=src.id,
        source_url=src.url,
        product_mention=mention,
        criterion_key=key,
        value=value,
        unit=unit,
        score=score,
        quote=quote if quote is not None else src.text[:600],  # word for word from its source, as extract checks
        claim_type=kind,
        polarity="neutral",
        weight=weight,
        retrieved_at=T0,
        extracted_at=T0,
    )


def match(*names: str, aliases: dict[str, str] | None = None) -> MatchResult:
    """One product per name, with the name as its only raw name, plus any extra aliases (raw name -> product name)."""
    ids = {name: "prd_" + "_".join(name.lower().split()) for name in names}
    alias_map = {name: pid for name, pid in ids.items()}
    alias_map |= {raw: ids[name] for raw, name in (aliases or {}).items()}
    products = [Product(id=pid, name=name) for name, pid in ids.items()]
    return MatchResult(products=products, alias_map=alias_map, method="fuzzy")


def verify_input(
    matched: MatchResult, claims: Sequence[Claim], sources: Sequence[Source], keys: Sequence[str] = KEYS
) -> VerifyInput:
    return VerifyInput(match=matched, claims=list(claims), sources=list(sources), criterion_keys=list(keys))


def value_of(result: Any, product: str, key: str) -> Any:
    return next(v for v in result.values if v.product_id == product and v.criterion_key == key)
