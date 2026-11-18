"""Which products enrich looks a price up for (US-41, P-34 item 1). Code decides; nothing here is sent anywhere.

Only when the price can hold a product back: the rubric has a budget, or a hard rule on the price. Then every kept
product whose price decide can't read (no value, an Unknown or Estimated one, or text that is no price) is a target.
The products many sources talk about come first; that orders the look-ups only and never scores anything (DR-10). At
most MAX_ENRICH_SEARCHES, one Serper shopping query each.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.contracts import EnrichTarget, Product, ResolvedValue, Rubric, ValueState
from app.contracts.enrich import MAX_ENRICH_SEARCHES
from app.decide.rules import PRICE_KEY
from app.decide.units import rupee_price

MAX_TARGETS = MAX_ENRICH_SEARCHES
MAX_NAME = 150  # EnrichTarget.product_name and Claim.product_mention
_READABLE = (ValueState.KNOWN, ValueState.DISPUTED)  # what decide's budget check reads a price from


def wants_prices(rubric: Rubric) -> bool:
    """A budget, or a hard rule on the price: only then can an unknown price hold a product back."""
    return rubric.budget is not None or any(rule.attribute == PRICE_KEY for rule in rubric.hard_rules)


def price_targets(
    rubric: Rubric,
    products: Sequence[Product],
    values: Sequence[ResolvedValue],
    alias_map: Mapping[str, str],
    source_of: Mapping[str, str],
) -> list[EnrichTarget]:
    """The kept products with no readable price, most-talked-about first, at most MAX_TARGETS.

    `alias_map` is match's (raw name -> product id): each target's name is one of the names verify knows the product by,
    so a claim about it finds its product. `source_of` maps a claim's ID to its source's, to count distinct sources.
    """
    if not wants_prices(rubric):
        return []
    prices = {v.product_id: v for v in values if v.criterion_key == PRICE_KEY}
    found: list[tuple[int, int, EnrichTarget]] = []
    for order, product in enumerate(products):
        if readable_price(prices.get(product.id)):
            continue
        name = mention_for(product, alias_map)
        if name is None:
            continue
        sources = _distinct_sources(product.id, values, source_of)
        target = EnrichTarget(product_id=product.id, product_name=name, criterion_key=PRICE_KEY)
        found.append((-sources, order, target))
    found.sort(key=lambda each: (each[0], each[1]))
    return [target for _, _, target in found[:MAX_TARGETS]]


def mention_for(product: Product, alias_map: Mapping[str, str]) -> str | None:
    """A name verify maps to this product: its own name if match knows it, else its first alias. None if none fits."""
    names = sorted(raw for raw, product_id in alias_map.items() if product_id == product.id and len(raw) <= MAX_NAME)
    own = product.name.casefold()
    return next((raw for raw in names if raw.casefold() == own), names[0] if names else None)


def readable_price(value: ResolvedValue | None) -> bool:
    """Can decide's budget check read a price from this value? Known or Disputed, and a price."""
    return value is not None and value.state in _READABLE and rupee_price(value.value) is not None


def _distinct_sources(product_id: str, values: Sequence[ResolvedValue], source_of: Mapping[str, str]) -> int:
    cited = (
        claim_id
        for value in values
        if value.product_id == product_id
        for claim_id in (*value.supporting_claim_ids, *value.conflicting_claim_ids)
    )
    return len({source_of[claim_id] for claim_id in cited if claim_id in source_of})
