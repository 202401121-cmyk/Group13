"""From the stage's contracts to the combiner and back (US-16): claims in, one resolved value per product and key."""

from __future__ import annotations

from app.contracts import ResolvedValue, ValueState, VerifyInput, VerifyResult

from . import combine
from .combine import Evidence, Method, Resolution
from .trust import TrustTable, describe_sources


def unsourced(inp: VerifyInput) -> list[str]:
    """Products with no source, dropped before scoring (US-40, FR-25). S3-7 fills this; until then nothing is dropped.

    This is the one place that decision plugs in: verify() leaves these products out of its values and lists them in
    VerifyResult.dropped_unsourced.
    """
    return []


def _keys(inp: VerifyInput) -> list[str]:
    return list(dict.fromkeys(inp.criterion_keys))


def evidence_from(inp: VerifyInput, table: TrustTable, category: str | None = None) -> list[Evidence]:
    """Every claim about a matched product and a requested key, with its source's site, tier and starting trust.
    A claim whose product was not matched, whose criterion was not asked for, or whose source is missing is left out.
    `category` adjusts a site's trust (trust.yaml); VerifyInput carries none yet, so the stage passes None."""
    sources = describe_sources(inp.sources, table, category)
    names = dict(inp.match.alias_map)
    folded = {raw.casefold(): pid for raw, pid in sorted(names.items())}
    keys = set(_keys(inp))
    out: list[Evidence] = []
    for claim in inp.claims:
        source = sources.get(claim.source_id)
        product = names.get(claim.product_mention) or folded.get(claim.product_mention.casefold())
        if source is None or product is None or claim.criterion_key not in keys:
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


def _resolved(product_id: str, key: str, found: Resolution | None) -> ResolvedValue:
    """The contract's value. One that can't cite a claim is Unknown, never a crash: a single odd product and key must
    not cost the whole search its values (build rule 9). The combiner always gives the winning side a claim, so this
    is only a guard."""
    if found is None or found.state is ValueState.UNKNOWN or not found.supporting:
        return ResolvedValue(product_id=product_id, criterion_key=key, state=ValueState.UNKNOWN, confidence=0.0)
    return ResolvedValue(
        product_id=product_id,
        criterion_key=key,
        state=found.state if found.conflicting else ValueState.KNOWN,
        score=found.score,
        value=found.value,
        confidence=found.confidence,
        supporting_claim_ids=found.supporting,
        conflicting_claim_ids=found.conflicting,  # a Known value keeps its small minority on record too
    )


def verify(inp: VerifyInput, table: TrustTable, method: Method) -> VerifyResult:
    """One value for every product that has a source and every key: Known, Disputed or Unknown."""
    dropped = set(unsourced(inp))
    outcome = combine.resolve_all(evidence_from(inp, table), table, method)
    values = [
        _resolved(product.id, key, outcome.resolutions.get((product.id, key)))
        for product in inp.match.products
        if product.id not in dropped
        for key in _keys(inp)
    ]
    return VerifyResult(values=values, dropped_unsourced=sorted(dropped), trust_method=method)


def all_unknown(inp: VerifyInput) -> VerifyResult:
    """The last resort when even tier order fails: every value Unknown, so the search goes on (build rule 9)."""
    values = [
        ResolvedValue(product_id=product.id, criterion_key=key, state=ValueState.UNKNOWN, confidence=0.0)
        for product in inp.match.products
        for key in _keys(inp)
    ]
    return VerifyResult(values=values, trust_method="tier_order")
