"""What the model's answer about one source must look like, and what code then accepts from it (US-14, US-28).

These models are strict, unlike other stages' parsing models, because the text behind this answer may be hostile. Extra
fields, wrong types and missing keys are refused, so the wrapper retries once and the source is then dropped. Nothing is
repaired. They have no field for a claim's type or URL: code sets those from the source.

Then code checks each claim against the source. A quote must be in its text or one comment, whatever its case or
spacing, and not its title, which can be invisible on the page (build rule 4). The product must be named there or in the
title, and the criterion must be one this call asked about. One that fails is dropped alone, and the log holds counts.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import Claim, ClaimType, ExtractionResult, ProductMention, Source, SourceTier

from .tiers import claim_type_for

log = logging.getLogger(__name__)

MAX_MENTIONS = 100
MAX_CLAIMS = 200
YES_OR_NO = {"yes", "no"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RawMention(_Strict):
    raw_name: str
    is_product: bool
    brand: str | None = None
    recommends: bool | None = None


class RawClaim(_Strict):
    product_mention: str
    is_product_type: bool = False
    criterion_key: str | None = None
    value: float | bool | str | None = None
    unit: str | None = None
    score: float | None = None
    quote: str
    polarity: Literal["positive", "negative", "neutral"]


class RawExtraction(_Strict):
    mentions: list[RawMention] = Field(max_length=MAX_MENTIONS)
    claims: list[RawClaim] = Field(max_length=MAX_CLAIMS)


def _plain(text: str) -> str:
    return " ".join(text.split())


def _name_key(name: str) -> str:
    return _plain(name).casefold()


def _claim_id(source: Source, item: RawClaim, quote: str) -> str:
    key = "\n".join((source.id, item.product_mention, item.criterion_key or "", _name_key(quote)))
    return f"clm_{hashlib.sha256(key.encode()).hexdigest()[:24]}"


def found_quote(quote: str, parts: Sequence[str]) -> str | None:
    """The source's own words for this quote, whatever its case or spacing. None if no one part holds all of it.

    Each part is searched alone, so a quote can't be stitched together from two people's comments.
    """
    words = quote.split()
    if not words:
        return None
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words), re.IGNORECASE)
    for part in parts:
        match = pattern.search(part)
        if match is not None:
            return match.group(0)
    return None


def _names(name: str, lowered: str) -> bool:
    """Is this name in the text as whole words? "Nova" is not named by "innovative"."""
    wanted = _name_key(name)
    return bool(wanted) and re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", lowered) is not None


def _product_types(raw: RawExtraction) -> set[str]:
    """The names the model called a kind of product anywhere: once is enough to keep one off the product list."""
    from_mentions = {_name_key(m.raw_name) for m in raw.mentions if not m.is_product}
    return from_mentions | {_name_key(c.product_mention) for c in raw.claims if c.is_product_type}


def _fact(value: float | bool | str | None) -> float | str | None:
    """A yes or no fact is stored as text, because a contract value can't be a boolean."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        text = value.strip()
        return text.casefold() if text.casefold() in YES_OR_NO else text
    return value


def to_extraction_result(
    raw: RawExtraction,
    source: Source,
    criterion_keys: Sequence[str],
    now: datetime,
    domains: Mapping[str, SourceTier] | None = None,
) -> ExtractionResult:
    parts = [source.text, *(comment.text for comment in source.comments)]
    lowered = _name_key(" ".join([source.title, *parts]))
    types = _product_types(raw)
    claim_type = claim_type_for(source, domains or {})
    claims: dict[str, Claim] = {}
    dropped: Counter[str] = Counter()
    for item in raw.claims:
        quote = found_quote(item.quote, parts)
        if item.criterion_key is None or item.criterion_key not in criterion_keys:
            dropped["criterion"] += 1
        elif not _names(item.product_mention, lowered):
            dropped["product"] += 1
        elif quote is None:
            dropped["quote"] += 1
        else:
            kind = _name_key(item.product_mention) in types
            claim = _claim(item, source, quote, kind, claim_type, now)
            if claim is None:
                dropped["contract"] += 1
            else:
                claims.setdefault(claim.id, claim)
    if dropped:
        # The counts only: the text would repeat what the page or the model said.
        log.info("extract: claims dropped from a %s source: %s", source.type.value, dict(dropped))
    mentions = _mentions(raw, lowered, types, claims.values())
    return ExtractionResult(source_id=source.id, mentions=mentions, claims=list(claims.values()))


def _mentions(raw: RawExtraction, lowered: str, types: set[str], claims: Iterable[Claim]) -> list[ProductMention]:
    """Each name once, whoever gave it. A claim's product counts too, as code has found its name in the source."""
    kept: dict[str, ProductMention] = {}
    for item in raw.mentions:
        key = _name_key(item.raw_name)
        if key not in kept and _names(item.raw_name, lowered):
            mention = _mention(item.raw_name, key not in types, item.brand, item.recommends)
            if mention is not None:
                kept[key] = mention
    for claim in claims:
        key = _name_key(claim.product_mention)
        if key not in kept:
            kept[key] = ProductMention(raw_name=claim.product_mention, is_product=not claim.is_product_type)
    return list(kept.values())[:MAX_MENTIONS]


def _claim(
    item: RawClaim, source: Source, quote: str, is_product_type: bool, claim_type: ClaimType, now: datetime
) -> Claim | None:
    try:
        return Claim(
            id=_claim_id(source, item, quote),
            source_id=source.id,
            source_url=source.url,
            product_mention=item.product_mention,
            is_product_type=is_product_type,
            criterion_key=item.criterion_key,
            value=_fact(item.value),
            unit=item.unit,
            score=item.score,
            quote=quote,
            claim_type=claim_type,
            polarity=item.polarity,
            retrieved_at=source.fetched_at,
            extracted_at=now,
        )
    except ValidationError as exc:
        # The count only: the error text would repeat the page's words.
        log.info("extract: a claim did not fit the contract (%d errors)", exc.error_count())
        return None


def _mention(name: str, is_product: bool, brand: str | None, recommends: bool | None) -> ProductMention | None:
    try:
        return ProductMention(raw_name=name, is_product=is_product, brand=brand, recommends=recommends)
    except ValidationError as exc:
        log.info("extract: a mention did not fit the contract (%d errors)", exc.error_count())
        return None
