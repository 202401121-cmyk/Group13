"""What the model's answer about one source must look like, and what code then accepts from it (US-14, US-28).

These models are strict, unlike other stages' parsing models, because the text behind this answer may be hostile. Extra
fields, wrong types and missing keys are refused, so the wrapper retries once and the source is then dropped. Nothing is
repaired. They have no field for a claim's type or URL: code sets those from the source.

Then code checks each claim against the source. A quote must be in its text or one comment, whatever its case or
spacing, and not its title, which can be invisible on the page (build rule 4). The product must be named there or in the
title, and the criterion must be one this call asked about. One that fails is dropped alone, and the log holds counts.

A name that is only a brand and generic words ("Dell", "Lenovo laptops") names no product (US-15, S4-23): it is never
kept as a mention, and its claims are dropped as "brand_only", so no product is made of it and nothing attaches to one.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import Claim, ClaimType, ExtractionResult, ProductMention, Source, SourceTier
from app.decide.rules import PRICE_KEY
from app.entities.normalise import Vocabulary, load_vocabulary, parse_name, plain_name
from app.intake.lexicon import load_lexicon
from app.llm.output import looks_like_instruction

from .tiers import claim_type_for

log = logging.getLogger(__name__)

MAX_MENTIONS = 100
MAX_CLAIMS = 200
YES_OR_NO = {"yes", "no"}
_POSSESSIVE = re.compile("['’]s\\b")  # "Dell's": the recommender count's rule (app/research/reddit/recommenders.py)
_MARKS = str.maketrans(dict.fromkeys("™®©", " "))  # trademark, registered and copyright signs


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


def _category_names(category: str) -> set[str]:
    """What a known category is called, in either number: "laptop" is not a laptop you can buy, whatever the model says.

    Only a category the lexicon knows, as a shopper can search for "kindle", which is a product. And not its synonyms,
    which mix kinds with real products such as MacBook and AirPods: those stay the model's call.
    """
    lexicon = load_lexicon()
    if category not in lexicon.categories:
        return set()
    names = {_name_key(category.replace("_", " ")), _name_key(lexicon.label(category))}
    return {form for name in names for form in (name, f"{name}s", f"{name}es", name.removesuffix("s"))}


def brand_only(name: str, vocabulary: Vocabulary | None = None, kinds: Iterable[str] = ()) -> bool:
    """Is this name nothing but a brand and generic words, such as "Dell", "Lenovo laptops" or "Samsung phone"?

    Such a name is no product you can buy (US-15, S4-23). A series or model word, a model code or a number keeps a name:
    "MacBook Pro", "Galaxy S25", "IdeaPad Slim 3", "Dell 15 3530". Variant attributes don't: "Dell 16GB RAM" is still
    only Dell. The counting lists in config/reddit_quality.yaml decide what a brand and a generic word are, never the
    model's `brand` field: so "Infinix" alone is brand only because the list holds it, and a brand the list doesn't
    hold, such as "Lava", stays a product. `kinds` are the names of the search's own category, generic words too:
    "Philips air fryer" in a search for air fryers. A possessive or a trademark sign changes nothing: "Dell's", "Dell™".
    """
    text = plain_name(_POSSESSIVE.sub("", name.translate(_MARKS)))
    for kind in sorted({plain_name(k) for k in kinds} - {""}, key=len, reverse=True):
        text = re.sub(rf"(?<!\S){re.escape(kind)}(?!\S)", " ", text)
    parsed = parse_name(text, None, vocabulary or load_vocabulary())
    return bool(parsed.brands) and parsed.is_vague


def _brand_check(category: str) -> Callable[[str], bool]:
    """brand_only() for one source's names, with its search's category, each name parsed once."""
    known = load_vocabulary()
    kinds = _category_names(category)
    seen: dict[str, bool] = {}

    def check(name: str) -> bool:
        key = _name_key(name)
        if key not in seen:
            seen[key] = brand_only(name, known, kinds)
        return seen[key]

    return check


def _product_types(raw: RawExtraction, category: str) -> set[str]:
    """The names the model called a kind of product anywhere: once is enough to keep one off the product list."""
    from_mentions = {_name_key(m.raw_name) for m in raw.mentions if not m.is_product}
    from_claims = {_name_key(c.product_mention) for c in raw.claims if c.is_product_type}
    return from_mentions | from_claims | _category_names(category)


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
    category: str = "",
    room: int | None = None,
    first: Sequence[str] = (),
    others: int | None = None,
) -> ExtractionResult:
    """The claims and mentions code can stand behind.

    With a `room` (a public call asked for the price or rule keys, S4-1), an answer past it is trimmed: the claims on
    the `first` keys are kept first, in that order, then the rest in the model's order, at most `others` of them, so the
    room above `others` stays for the price and rule keys. Only an answer past those limits loses claims: one that
    arrived whole within them keeps them all. With no room, every claim code accepts is kept, as before."""
    parts = [source.text, *(comment.text for comment in source.comments)]
    lowered = _name_key(" ".join([source.title, *parts]))
    types = _product_types(raw, category)
    only_brand = _brand_check(category)
    claim_type = claim_type_for(source, domains or {})
    claims: dict[str, Claim] = {}
    dropped: Counter[str] = Counter()
    price_dropped: Counter[str] = Counter()  # the price's own, for the live funnel (US-14)
    for item in raw.claims:
        quote = found_quote(item.quote, parts)
        reason: str | None = None
        if item.criterion_key is None or item.criterion_key not in criterion_keys:
            reason = "criterion"
        elif not _names(item.product_mention, lowered) or looks_like_instruction(item.product_mention):
            reason = "product"  # a hostile page can hold the words, so being in the source isn't enough
        elif only_brand(item.product_mention):
            reason = "brand_only"  # names no product, so the claim is about none (US-15, S4-23)
        elif quote is None or looks_like_instruction(quote):
            reason = "quote"
        else:
            kind = _name_key(item.product_mention) in types
            claim = _claim(item, source, quote, kind, claim_type, now)
            if claim is None:
                reason = "contract"
            elif claim.id in claims:
                reason = "duplicate"  # the same claim again: merged into the first, as before
            else:
                claims[claim.id] = claim
        if reason is not None:
            dropped[reason] += 1
            if item.criterion_key == PRICE_KEY:
                price_dropped[reason] += 1
    if room is not None:
        claims = _within_room(claims, room, room if others is None else others, first, dropped, price_dropped)
    # The counts only: the text would repeat what the page or the model said.
    if dropped:
        log.info("extract: claims dropped from a %s source: %s", source.type.value, dict(dropped))
    if price_dropped:
        log.info("extract: price claims dropped from a %s source: %s", source.type.value, dict(price_dropped))
    mentions, brand_names = _mentions(raw, lowered, types, claims.values(), only_brand)
    if brand_names:  # the count only, never a name; shaped as the claims' line, so the live funnel can read it too
        log.info("extract: names dropped from a %s source: %s", source.type.value, {"brand_only": brand_names})
    return ExtractionResult(source_id=source.id, mentions=mentions, claims=list(claims.values()))


def _within_room(
    claims: Mapping[str, Claim],
    room: int,
    others: int,
    first: Sequence[str],
    dropped: Counter[str],
    price_dropped: Counter[str],
) -> dict[str, Claim]:
    """The claims to keep (US-14, S4-1): those on the `first` keys first (in `first`'s order, then the model's), up to
    `room`; then the rest in the model's order, at most `others` and never past `room`. Claims past those limits are
    counted as dropped for "room", the price's own too, for the live funnel."""
    order = {key: n for n, key in enumerate(first)}
    asked_first = sorted(
        (c for c in claims.values() if c.criterion_key in order), key=lambda c: order[c.criterion_key or ""]
    )
    rest = [c for c in claims.values() if c.criterion_key not in order]
    kept_first = asked_first[:room]
    kept_rest = rest[: max(0, min(others, room - len(kept_first)))]
    gone = [*asked_first[room:], *rest[len(kept_rest) :]]
    if not gone:
        return dict(claims)  # within its limits: kept whole, in the model's own order
    for claim in gone:
        dropped["room"] += 1
        if claim.criterion_key == PRICE_KEY:
            price_dropped["room"] += 1
    return {claim.id: claim for claim in [*kept_first, *kept_rest]}


def _mentions(
    raw: RawExtraction, lowered: str, types: set[str], claims: Iterable[Claim], only_brand: Callable[[str], bool]
) -> tuple[list[ProductMention], int]:
    """Each name once, whoever gave it. A claim's product counts too, as code has found its name in the source.

    A name that is only a brand is left out, whether the model called it a product or a kind of product. With the
    mentions comes how many distinct names were left out so (its claims are already gone)."""
    kept: dict[str, ProductMention] = {}
    brand_names: set[str] = set()
    for item in raw.mentions:
        key = _name_key(item.raw_name)
        if key in kept or key in brand_names:
            continue
        if not _names(item.raw_name, lowered) or looks_like_instruction(item.raw_name):
            continue
        if only_brand(item.raw_name):
            brand_names.add(key)
            continue
        mention = _mention(item.raw_name, key not in types, item.brand, item.recommends)
        if mention is not None:
            kept[key] = mention
    for claim in claims:
        key = _name_key(claim.product_mention)
        if key not in kept:
            kept[key] = ProductMention(raw_name=claim.product_mention, is_product=not claim.is_product_type)
    return list(kept.values())[:MAX_MENTIONS], len(brand_names)


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
