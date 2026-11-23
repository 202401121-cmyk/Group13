"""Made-up products, rankings and claims for the explain tests (US-20). No real product or shopper."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.contracts import (
    Claim,
    ClaimType,
    Contribution,
    ExplainInput,
    RankedProduct,
    StageContext,
    ValueState,
)
from app.entities.clustering import product_id_for

AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
CTX = StageContext(search_id="srch_explain01")
SOURCE = "src_" + "1" * 16


def contribution(
    key: str,
    score: float | None,
    weight: float,
    state: ValueState = ValueState.KNOWN,
    basis: str | None = "opinion",
) -> Contribution:
    """One criterion's part of a score, as decide makes it: contribution = score x weight."""
    return Contribution.model_validate(
        {
            "criterion_key": key,
            "score": score,
            "normalized_weight": weight,
            "contribution": 0.0 if score is None else round(score * weight, 4),
            "state": state,
            "basis": None if score is None else basis,
        }
    )


def ranked(name: str, rank: int, *contributions: Contribution, scored: bool = True) -> RankedProduct:
    """A ranked product named `name`, with its score made from its contributions as decide makes it."""
    exact = sum((c.score or 0.0) * c.normalized_weight for c in contributions)
    return RankedProduct.model_validate(
        {
            "product_id": product_id_for(name),
            "rank": rank,
            "score": round(min(100.0, exact * 10), 2) if scored else None,
            "contributions": [c.model_dump() for c in contributions],
            "coverage": 1.0,
            "confidence": "high" if scored else "low",
            "label": ("best_match" if rank == 1 else "runner_up" if rank == 2 else None) if scored else None,
            "gap_to_leader": 0.0,
        }
    )


def claim(
    name: str,
    key: str | None,
    n: int = 0,
    *,
    score: float | None = 7.0,
    value: float | str | None = None,
    product_type: bool = False,
    quote: str = "A made-up quote about it.",
    url: str = "https://www.reviews.example/a",
) -> Claim:
    """A claim about the product called `name`, as extraction writes it (the name as written, no product id)."""
    where = "" if url == "https://www.reviews.example/a" else f"|{url}"  # the default keeps the ids tests already use
    digest = hashlib.sha256(f"{name}|{key}|{n}|{quote}{where}".encode()).hexdigest()[:16]
    return Claim.model_validate(
        {
            "id": f"clm_{digest}",
            "source_id": SOURCE,
            "source_url": url,
            "product_mention": name,
            "is_product_type": product_type,
            "criterion_key": key,
            "value": value,
            "unit": None,
            "score": None if value is not None else score,
            "quote": quote,
            "claim_type": ClaimType.EXPERIENCE,
            "polarity": "positive",
            "retrieved_at": AT,
            "extracted_at": AT,
        }
    )


# Three made-up laptops, ranked as decide would rank them on five criteria.
NOVA = "Nova Book 14"
KESTREL = "Kestrel Slim 15"
ORION = "Orion Play 15"
FOURTH = "Lumen Air 13"


def nova(rank: int = 1) -> RankedProduct:
    return ranked(
        NOVA,
        rank,
        contribution("battery_life", 8.0, 0.3),
        contribution("cpu_performance", 7.5, 0.25),
        contribution("keyboard_comfort", 9.0, 0.2),
        contribution("weight", 4.0, 0.15),
        contribution("ports", 6.0, 0.1, ValueState.ESTIMATED, "estimated"),
    )


def kestrel(rank: int = 2) -> RankedProduct:
    return ranked(
        KESTREL,
        rank,
        contribution("battery_life", 7.0, 0.3, ValueState.DISPUTED),
        contribution("cpu_performance", 8.0, 0.25, basis="compared"),
        contribution("keyboard_comfort", 7.5, 0.2),
        contribution("weight", 8.0, 0.15),
        contribution("ports", 7.0, 0.1),
    )


def orion(rank: int = 3) -> RankedProduct:
    return ranked(
        ORION,
        rank,
        contribution("battery_life", 5.0, 0.3),
        contribution("cpu_performance", 9.0, 0.25),
        contribution("keyboard_comfort", 6.5, 0.2),
        contribution("weight", 3.0, 0.15),
        contribution("ports", None, 0.0, ValueState.UNKNOWN),
    )


def fourth(rank: int = 4) -> RankedProduct:
    return ranked(
        FOURTH,
        rank,
        contribution("battery_life", 6.0, 0.3),
        contribution("cpu_performance", 6.0, 0.25),
        contribution("keyboard_comfort", 6.0, 0.2),
        contribution("weight", 6.0, 0.15),
        contribution("ports", 6.0, 0.1),
    )


def claims_for(name: str, *keys: str) -> list[Claim]:
    return [claim(name, key) for key in keys]


def every_claim() -> list[Claim]:
    keys = ("battery_life", "cpu_performance", "keyboard_comfort", "weight", "ports")
    return [*claims_for(NOVA, *keys[:4]), *claims_for(KESTREL, *keys), *claims_for(ORION, *keys[:4])]


def explain_input(*products: RankedProduct, claims: list[Claim] | None = None) -> ExplainInput:
    shown = list(products) or [nova(), kestrel(), orion()]
    return ExplainInput(
        ranked=shown,
        claims=every_claim() if claims is None else claims,
        user_priorities=["battery_life", "cpu_performance", "keyboard_comfort"],
    )
