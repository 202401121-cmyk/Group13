"""What the model's answer about one source must look like, and what code then accepts from it (US-28).

These models are strict, unlike other stages' parsing models, because the text behind this answer may be hostile. Extra
fields, wrong types and missing keys are refused, so the wrapper retries once and the source is then dropped. Nothing is
repaired.

Then code checks each claim against the source. A quote must be the source's exact words, in its text or comments and
not its title, which can be invisible on the page (build rule 4). The product must be named there or in the title, and
the criterion must be one we asked about. One that fails is dropped alone.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import Claim, ClaimType, ExtractionResult, ProductMention, Source

from .prompts import source_text

log = logging.getLogger(__name__)

MAX_MENTIONS = 100
MAX_CLAIMS = 200


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RawMention(_Strict):
    raw_name: str
    is_product: bool
    brand: str | None = None
    recommends: bool | None = None


class RawClaim(_Strict):
    product_mention: str
    criterion_key: str | None = None
    value: float | str | None = None
    unit: str | None = None
    score: float | None = None
    quote: str
    claim_type: Literal["experience", "spec"]
    polarity: Literal["positive", "negative", "neutral"]


class RawExtraction(_Strict):
    mentions: list[RawMention] = Field(max_length=MAX_MENTIONS)
    claims: list[RawClaim] = Field(max_length=MAX_CLAIMS)


def _plain(text: str) -> str:
    return " ".join(text.split())


def _claim_id(source: Source, raw: RawClaim) -> str:
    key = "\n".join((source.id, raw.product_mention, raw.criterion_key or "", _plain(raw.quote)))
    return f"clm_{hashlib.sha256(key.encode()).hexdigest()[:24]}"


def to_extraction_result(
    raw: RawExtraction, source: Source, criterion_keys: Sequence[str], now: datetime
) -> ExtractionResult:
    readable = _plain(source_text(source))
    lowered = _plain(f"{source.title} {readable}").casefold()
    claims: dict[str, Claim] = {}
    for item in raw.claims:
        if _plain(item.quote) not in readable or _plain(item.product_mention).casefold() not in lowered:
            continue
        if item.criterion_key is not None and item.criterion_key not in criterion_keys:
            continue
        claim = _claim(item, source, now)
        if claim is not None:
            claims.setdefault(claim.id, claim)
    mentions = [
        mention
        for item in raw.mentions
        if _plain(item.raw_name).casefold() in lowered
        if (mention := _mention(item)) is not None
    ]
    return ExtractionResult(source_id=source.id, mentions=mentions, claims=list(claims.values()))


def _claim(item: RawClaim, source: Source, now: datetime) -> Claim | None:
    try:
        return Claim(
            id=_claim_id(source, item),
            source_id=source.id,
            source_url=source.url,
            product_mention=item.product_mention,
            criterion_key=item.criterion_key,
            value=item.value,
            unit=item.unit,
            score=item.score,
            quote=item.quote,
            claim_type=ClaimType(item.claim_type),
            polarity=item.polarity,
            retrieved_at=source.fetched_at,
            extracted_at=now,
        )
    except ValidationError as exc:
        # The count only: the error text would repeat the page's words.
        log.info("extract: a claim did not fit the contract (%d errors)", exc.error_count())
        return None


def _mention(item: RawMention) -> ProductMention | None:
    try:
        return ProductMention(
            raw_name=item.raw_name, is_product=item.is_product, brand=item.brand, recommends=item.recommends
        )
    except ValidationError as exc:
        log.info("extract: a mention did not fit the contract (%d errors)", exc.error_count())
        return None
