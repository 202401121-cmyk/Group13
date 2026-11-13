"""From the stage's contracts to the combiner and back (US-16): claims in, one resolved value per product and key.

A fact we filter on (the price, every hard rule's attribute) never rests on community words (S3-10b, DR-01): Reddit and
forums are good on experience and poor on specs. For a filter key, a community claim that carries a value is left out
before combining, so it forms no side, no alternative and no winner; with no other source the value is Unknown, and
decide says "can't tell". Community claims that carry a score still count, weighted by trust, so a key that is both a
criterion and a rule keeps its community score while its value comes only from other sources.
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence

from app.contracts import AlternativeValue, ResolvedValue, SourceTier, ValueState, VerifyInput, VerifyResult
from app.decide.rules import PRICE_KEY

from . import combine, sourced
from .combine import Evidence, Method, Resolution
from .trust import TrustTable, describe_sources

log = logging.getLogger(__name__)

FILTER_KEYS: frozenset[str] = frozenset({PRICE_KEY})  # always a filter key; the pipeline adds the hard rules' keys


def unsourced(inp: VerifyInput) -> list[str]:
    """Products with no source, dropped before scoring (US-40, FR-25): the ids app/verify/sourced.py finds, in order.

    This is the one place that decision plugs in: verify() leaves these products out of its values and lists them in
    VerifyResult.dropped_unsourced, and leaves their claims out before anything is combined. The verify stage logs
    each drop once per search.
    """
    return [product_id for product_id, _ in sourced.unsourced(inp)]


def _keys(inp: VerifyInput) -> list[str]:
    return list(dict.fromkeys(inp.criterion_keys))


def evidence_from(
    inp: VerifyInput, table: TrustTable, category: str | None = None, skip: Collection[str] = frozenset()
) -> list[Evidence]:
    """Every claim about a matched product and a requested key, with its source's site, tier and starting trust.
    A claim whose product was not matched, whose criterion was not asked for, or whose source is missing is left out,
    and so is one about a product in `skip`: verify() passes the products it dropped as unsourced, so their claims
    never feed trust learning or either side of a dispute (US-40). `category` adjusts a site's trust (trust.yaml);
    verify() passes VerifyInput.category."""
    sources = describe_sources(inp.sources, table, category)
    names = dict(inp.match.alias_map)
    folded = {raw.casefold(): pid for raw, pid in sorted(names.items())}
    keys = set(_keys(inp))
    out: list[Evidence] = []
    for claim in inp.claims:
        source = sources.get(claim.source_id)
        product = names.get(claim.product_mention) or folded.get(claim.product_mention.casefold())
        if source is None or product is None or product in skip or claim.criterion_key not in keys:
            continue
        site, tier, trust = source
        out.append(
            Evidence.of(
                claim.id,
                product,
                claim.criterion_key,
                site,
                tier,
                trust,
                value=claim.value,
                unit=claim.unit,
                score=claim.score,
                weight=claim.weight,
            )
        )
    return out


def without_community_values(evidence: Sequence[Evidence], filter_keys: Collection[str]) -> list[Evidence]:
    """The evidence, less every community claim that gives a value for a filter key (DR-01). Its scores stay."""
    return [e for e in evidence if not (e.key in filter_keys and e.tier is SourceTier.COMMUNITY and e.score is None)]


def _resolved(product_id: str, key: str, found: Resolution | None) -> ResolvedValue:
    """The contract's value. One that can't cite a claim is Unknown, never a crash: a single odd product and key must
    not cost the whole search its values (build rule 9). The combiner always gives the winning side a claim, so this
    is only a guard."""
    if found is None or found.state is ValueState.UNKNOWN or not found.supporting:
        return ResolvedValue(product_id=product_id, criterion_key=key, state=ValueState.UNKNOWN, confidence=0.0)
    state = found.state if found.conflicting else ValueState.KNOWN
    alternatives = [
        AlternativeValue(value=a.value, unit=a.unit, score=a.score, claim_ids=list(a.claim_ids))
        for a in found.alternatives
    ]
    return ResolvedValue(
        product_id=product_id,
        criterion_key=key,
        state=state,
        score=found.score,
        value=found.value,
        confidence=found.confidence,
        supporting_claim_ids=found.supporting,
        conflicting_claim_ids=found.conflicting,  # a Known value keeps its small minority on record too
        alternatives=alternatives if state is ValueState.DISPUTED else [],
    )


def verify(
    inp: VerifyInput, table: TrustTable, method: Method, filter_keys: Collection[str] = FILTER_KEYS
) -> VerifyResult:
    """One value for every product that has a source and every key: Known, Disputed or Unknown.

    `filter_keys` are the keys decide filters on (the price, every hard rule's attribute): community claims give them no
    value, only scores (DR-01). The price always is one.
    """
    dropped = set(unsourced(inp))
    evidence = without_community_values(evidence_from(inp, table, inp.category, dropped), {*filter_keys, PRICE_KEY})
    outcome = combine.resolve_all(evidence, table, method)
    values = [
        _resolved(product.id, key, outcome.resolutions.get((product.id, key)))
        for product in inp.match.products
        if product.id not in dropped
        for key in _keys(inp)
    ]
    return VerifyResult(values=values, dropped_unsourced=sorted(dropped), trust_method=method)


def all_unknown(inp: VerifyInput) -> VerifyResult:
    """The last resort when even tier order fails: every value Unknown, so the search goes on (build rule 9).

    It still leaves out the products no source names (build rule 5). If that check is what failed, it keeps them all,
    each with only Unknown values: a broken check must not cost the search every product.
    """
    try:
        dropped = set(unsourced(inp))
    except Exception as exc:  # the type only, never its text
        log.error("verify's source check failed (%s); no product is dropped", type(exc).__name__)
        dropped = set()
    values = [
        ResolvedValue(product_id=product.id, criterion_key=key, state=ValueState.UNKNOWN, confidence=0.0)
        for product in inp.match.products
        if product.id not in dropped
        for key in _keys(inp)
    ]
    return VerifyResult(values=values, dropped_unsourced=sorted(dropped), trust_method="tier_order")
