"""What enrich looks up (US-41). Code decides; nothing here is sent anywhere.

Prices (P-34 item 1): only when the price can hold a product back, the rubric having a budget or a hard rule on the
price. Then every kept product whose price decide can't read (no value, an Unknown or Estimated one, or text that is no
price) is a target. The products closest to being ranked come first (D-95 c): the fewest of the rubric's top weights
with no evidence, then the ones many sources talk about. That orders the look-ups only and never scores anything
(DR-10). At most MAX_ENRICH_SEARCHES, one Serper shopping query each.

Facts (US-41, DECIDED 1): after the first ranking, each of the top ENRICH_TOP_N products' criteria with no evidence
(Unknown or Estimated), highest weight first, at most FACTS_PER_PRODUCT each, and only a criterion with a fixed public
stand-in, as only that may be searched for (D-51). One web search each.

The two share enrich's MAX_ENRICH_SEARCHES: prices first, but at most PRICES_WITH_FACTS when there are fact gaps
(`shared_targets`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from app.contracts import EnrichTarget, Product, RankedProduct, ResolvedValue, Rubric, ValueState
from app.contracts.enrich import ENRICH_TOP_N, MAX_ENRICH_SEARCHES
from app.decide.rules import PRICE_KEY
from app.decide.units import rupee_price

MAX_TARGETS = MAX_ENRICH_SEARCHES
MAX_NAME = 150  # EnrichTarget.product_name and Claim.product_mention
_READABLE = (ValueState.KNOWN, ValueState.DISPUTED)  # what decide's budget check reads a price from
TOP_WEIGHTS = 3  # D-95 (c): a price goes first to the product with the fewest of these with no evidence
FACTS_PER_PRODUCT = 2  # a top product's highest-weight gaps looked up, at most
PRICES_WITH_FACTS = 3  # DECIDED 1: price look-ups when there are fact gaps too; facts get the rest


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
    top = top_weights(rubric)
    found: list[tuple[int, int, int, EnrichTarget]] = []
    for order, product in enumerate(products):
        if readable_price(prices.get(product.id)):
            continue
        name = mention_for(product, alias_map)
        if name is None:
            continue
        sources = _distinct_sources(product.id, values, source_of)
        target = EnrichTarget(product_id=product.id, product_name=name, criterion_key=PRICE_KEY)
        found.append((unknowns_among(product.id, top, values), -sources, order, target))
    found.sort(key=lambda each: (each[0], each[1], each[2]))
    return [target for *_, target in found[:MAX_TARGETS]]


def top_weights(rubric: Rubric) -> list[str]:
    """The rubric's TOP_WEIGHTS highest-weight criteria, the price aside: ties by key, so the order is fixed."""
    weights = sorted((w for w in rubric.weights if w.key != PRICE_KEY), key=lambda w: (-w.normalized, w.key))
    return [w.key for w in weights[:TOP_WEIGHTS]]


def unknowns_among(product_id: str, keys: Sequence[str], values: Sequence[ResolvedValue]) -> int:
    """How many of these criteria have no evidence for this product: no value, or an Unknown or Estimated one."""
    known = {v.criterion_key for v in values if v.product_id == product_id and v.state in _READABLE}
    return sum(1 for key in keys if key not in known)


def fact_targets(
    rubric: Rubric,
    ranked: Sequence[RankedProduct],
    products: Mapping[str, Product],
    alias_map: Mapping[str, str],
    searchable: Callable[[str], bool],
) -> list[EnrichTarget]:
    """The top ENRICH_TOP_N ranked products' criteria with no evidence, highest weight first (then the higher rank),
    at most FACTS_PER_PRODUCT a product. `searchable` says a criterion has a fixed public stand-in (D-51): only those
    can be searched for. The price is left to price_targets."""
    weight = {w.key: w.normalized for w in rubric.weights}
    found: list[tuple[float, int, str, EnrichTarget]] = []
    for item in ranked[:ENRICH_TOP_N]:
        product = products.get(item.product_id)
        name = mention_for(product, alias_map) if product is not None else None
        if product is None or name is None:
            continue
        gaps = sorted(
            {
                c.criterion_key
                for c in item.contributions
                if c.state in (ValueState.UNKNOWN, ValueState.ESTIMATED)
                and c.criterion_key != PRICE_KEY
                and weight.get(c.criterion_key, 0) > 0
                and searchable(c.criterion_key)
            },
            key=lambda key: (-weight[key], key),
        )
        for key in gaps[:FACTS_PER_PRODUCT]:
            target = EnrichTarget(product_id=product.id, product_name=name, criterion_key=key)
            found.append((-weight[key], item.rank, key, target))
    found.sort(key=lambda each: (each[0], each[1], each[2]))
    return [target for *_, target in found]


def shared_targets(prices: Sequence[EnrichTarget], facts: Sequence[EnrichTarget]) -> list[EnrichTarget]:
    """Enrich's MAX_ENRICH_SEARCHES, shared (DECIDED 1): prices first, but at most PRICES_WITH_FACTS when there are
    fact gaps; facts get the rest. Never more than MAX_ENRICH_SEARCHES in all."""
    taken = list(prices[: PRICES_WITH_FACTS if facts else MAX_ENRICH_SEARCHES])
    return taken + list(facts[: MAX_ENRICH_SEARCHES - len(taken)])


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
