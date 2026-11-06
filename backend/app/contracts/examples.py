"""One complete, valid example of every contract, for a single search.

Used by:
- the walking skeleton (US-52): stub stages return these objects;
- the contract tests: every example must validate;
- the frontend: `python -m scripts.export_contracts` writes them to JSON as mock data.

The products and brands are fictional on purpose, so nobody mistakes fixture data for real facts.
The example deliberately includes the awkward cases the UI must handle: a Disputed value, an
Estimated value, a tie, a filtered-out product, a can't-tell product and a failed source.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from .claims import Claim, ProductMention
from .commerce import CommerceResult, Offer
from .common import ClaimType, Money, SourceTier, SourceType, StageName, StageReport, StageStatus
from .criteria import CriteriaSet, Criterion, Direction
from .decide import Contribution, DecideResult, FilteredProduct, RankedProduct
from .entities import MatchResult, Product, Variant
from .explain import Explanation, ExplanationSentence
from .intake import Budget, Exclusion, HardRule, Intent
from .pipeline import ProductSummary, SearchRequest, SearchResult
from .research import ResearchResult, Source, SourceFailure
from .rubric import Rubric, WeightedCriterion, normalize
from .verify import CommunitySignal, ResolvedValue, ValueState, VerifyResult

T0 = datetime(2026, 10, 3, 10, 0, 0, tzinfo=UTC)
QUERY = "best laptop for coding under 70k, at least 16 GB RAM, not too heavy, no Zenith"
SESSION_ID = "sess_demo0001"
SEARCH_ID = "srch_demo0001"


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- 1 intake -------------------------------------------------------------------------------


def intent() -> Intent:
    return Intent(
        raw_query=QUERY,
        category="laptop",
        category_label="Laptop",
        use_case="coding",
        budget=Budget(max=Money(amount=70000)),
        region="IN",
        hard_rules=[
            HardRule(attribute="ram_gb", operator=">=", value=16, unit="GB", source_text="at least 16 GB RAM"),
        ],
        exclusions=[Exclusion(kind="brand", value="Zenith")],
        detected_by="llm",
    )


def unclear_intent() -> Intent:
    """US-01: 'something for my mom' has no product type, so the app asks first."""
    return Intent(
        raw_query="something for my mom",
        needs_clarification=True,
        clarification_question="What kind of product are you looking for?",
        clarification_options=["Kitchen appliance", "Phone", "Smartwatch", "Something else"],
        detected_by="llm",
    )


# --- 2 criteria -----------------------------------------------------------------------------

_CRITERIA = [
    (
        "cpu_performance",
        "CPU performance",
        "How fast it compiles code and runs tools like Docker and IDEs.",
        Direction.HIGHER_IS_BETTER,
        None,
        8.0,
    ),
    (
        "battery_life",
        "Battery life",
        "Hours of real work on one charge, away from a plug.",
        Direction.HIGHER_IS_BETTER,
        "h",
        7.0,
    ),
    (
        "keyboard_comfort",
        "Keyboard comfort",
        "How pleasant the keys are for long typing sessions.",
        Direction.HIGHER_IS_BETTER,
        None,
        6.0,
    ),
    (
        "display_quality",
        "Display quality",
        "Sharpness, brightness and eye comfort of the screen.",
        Direction.HIGHER_IS_BETTER,
        None,
        5.0,
    ),
    (
        "weight",
        "Weight",
        "How heavy it is to carry every day; lighter is better.",
        Direction.LOWER_IS_BETTER,
        "kg",
        5.0,
    ),
    (
        "build_durability",
        "Build durability",
        "How well the body, hinge and ports hold up over years.",
        Direction.HIGHER_IS_BETTER,
        None,
        4.0,
    ),
    (
        "value_for_money",
        "Value for money",
        "How much laptop you get for the price you pay.",
        Direction.HIGHER_IS_BETTER,
        None,
        6.0,
    ),
]


def criteria() -> CriteriaSet:
    return CriteriaSet(
        category="laptop",
        use_case="coding",
        source="generated",
        criteria=[
            Criterion(
                key=k,
                label=label,
                definition=d,
                direction=direction,
                unit=unit,
                default_weight=w,
                is_value_for_money=(k == "value_for_money"),
            )
            for k, label, d, direction, unit, w in _CRITERIA
        ],
    )


# --- rubric (end of the interview) ----------------------------------------------------------

_WEIGHTS = {
    "cpu_performance": (9.0, "answered"),
    "battery_life": (7.0, "answered"),
    "keyboard_comfort": (8.0, "answered"),
    "display_quality": (4.0, "default"),
    "weight": (6.0, "remembered"),
    "build_durability": (4.0, "skipped"),
    "value_for_money": (6.0, "default"),
}


def rubric() -> Rubric:
    shares = normalize({k: w for k, (w, _) in _WEIGHTS.items()})
    it = intent()
    return Rubric(
        category="laptop",
        use_case="coding",
        region="IN",
        budget=it.budget,
        hard_rules=it.hard_rules,
        exclusions=it.exclusions,
        weights=[
            WeightedCriterion(
                key=k,
                weight=w,
                normalized=shares[k],
                origin=origin,
                uncertainty=0.2 if origin == "answered" else 0.5,
            )
            for k, (w, origin) in _WEIGHTS.items()
        ],
    )


# --- 3 research -----------------------------------------------------------------------------

_SOURCE_TEXT = {
    "reddit": "Thread: best coding laptop under 70k? Nova Book 14 has been great for Android Studio. "
    "Kestrel Slim 15 battery is fine but I get about 6 hours, not 10.",
    "review": "Kestrel Slim 15 review: rated for 10 hours of battery. The Nova Book 14 keyboard is excellent. "
    "Orion Play 15 is fast but heavy at 2.3 kg.",
    "maker": "Kestrel Slim 15 specifications: battery rated up to 10 hours, 16 GB RAM, 1.6 kg.",
}


def sources() -> list[Source]:
    def src(
        key: str,
        url: str,
        typ: SourceType,
        tier: SourceTier,
        title: str,
        community: str | None = None,
        via: str = "api",
    ) -> Source:
        text = _SOURCE_TEXT[key]
        return Source(
            id=f"src_{_h(url)[:16]}",
            url=url,
            type=typ,
            tier=tier,
            title=title,
            text=text,
            text_hash=_h(text),
            fetched_at=T0,
            community=community,
            quality=0.8,
            via=via,
        )

    return [
        src(
            "reddit",
            "https://www.reddit.com/r/developersIndia/comments/abc123/",
            SourceType.REDDIT,
            SourceTier.COMMUNITY,
            "Best coding laptop under 70k?",
            community="r/developersIndia",
        ),
        src(
            "review",
            "https://example-reviews.in/kestrel-slim-15-review",
            SourceType.REVIEW_SITE,
            SourceTier.EDITORIAL,
            "Kestrel Slim 15 review",
            via="direct",
        ),
        src(
            "maker",
            "https://kestrel.example.com/slim-15/specs",
            SourceType.MANUFACTURER,
            SourceTier.MANUFACTURER,
            "Kestrel Slim 15 specifications",
            via="direct",
        ),
    ]


def research() -> ResearchResult:
    return ResearchResult(
        queries=["best laptop for coding under 70000 India", "coding laptop 16GB RAM lightweight reddit"],
        sources=sources(),
        failures=[SourceFailure(source_type=SourceType.YOUTUBE, reason="timeout", detail="no reply in 40 s")],
    )


# --- 5 extract ------------------------------------------------------------------------------

PRODUCTS = {
    "prd_nova_book_14": ("Nova Book 14", "Nova"),
    "prd_kestrel_slim_15": ("Kestrel Slim 15", "Kestrel"),
    "prd_orion_play_15": ("Orion Play 15", "Orion"),
    "prd_aurora_air_13": ("Aurora Air 13", "Aurora"),
    "prd_vela_pro_14": ("Vela Pro 14", "Vela"),
}
RANKED = ["prd_nova_book_14", "prd_kestrel_slim_15", "prd_orion_play_15"]

# (product, criterion) -> score out of 10. Missing pairs are handled below as Estimated.
_SCORES = {
    "prd_nova_book_14": {
        "cpu_performance": 7.5,
        "battery_life": 8.0,
        "keyboard_comfort": 9.0,
        "display_quality": 7.0,
        "weight": 7.5,
        "build_durability": 7.0,
        "value_for_money": 8.0,
    },
    "prd_kestrel_slim_15": {
        "cpu_performance": 8.0,
        "battery_life": 7.0,
        "keyboard_comfort": 7.5,
        "display_quality": 7.5,
        "weight": 8.0,
        "build_durability": 7.5,
        "value_for_money": 7.5,
    },
    "prd_orion_play_15": {
        "cpu_performance": 9.0,
        "battery_life": 5.0,
        "keyboard_comfort": 6.5,
        "display_quality": 8.0,
        "weight": 4.0,
        "value_for_money": 7.0,
    },
}


def _claim(
    source: Source,
    product_id: str,
    key: str,
    score: float | None,
    quote: str,
    claim_type: ClaimType = ClaimType.EXPERIENCE,
    value: float | None = None,
) -> Claim:
    name = PRODUCTS[product_id][0]
    return Claim(
        id=f"clm_{_h(source.id + product_id + key + quote)[:16]}",
        source_id=source.id,
        product_raw_name=name,
        criterion_key=key,
        score=score,
        value=value,
        quote=quote,
        claim_type=claim_type,
        polarity="neutral" if score is None else ("positive" if score >= 6 else "negative"),
        retrieved_at=T0,
    )


def claims() -> list[Claim]:
    reddit, review, maker = sources()
    out: list[Claim] = []
    for pid, scores in _SCORES.items():
        for key, score in scores.items():
            if pid == "prd_kestrel_slim_15" and key == "battery_life":
                continue  # built separately below as a real disagreement
            src = reddit if key in ("keyboard_comfort", "value_for_money") else review
            out.append(_claim(src, pid, key, score, f"{PRODUCTS[pid][0]}: {key.replace('_', ' ')} rated {score}/10."))
    # Disputed: the maker says 10 hours, an owner on Reddit says about 6.
    out.append(
        _claim(
            maker,
            "prd_kestrel_slim_15",
            "battery_life",
            9.0,
            "battery rated up to 10 hours",
            ClaimType.SPEC,
            value=10.0,
        )
    )
    out.append(
        _claim(
            reddit,
            "prd_kestrel_slim_15",
            "battery_life",
            5.5,
            "I get about 6 hours, not 10",
            ClaimType.EXPERIENCE,
            value=6.0,
        )
    )
    # Hard-rule attribute: RAM, known for three products, unknown for Vela Pro 14.
    for pid, ram in [("prd_nova_book_14", 16.0), ("prd_kestrel_slim_15", 16.0), ("prd_orion_play_15", 16.0)]:
        src = maker if pid == "prd_kestrel_slim_15" else review
        out.append(
            _claim(src, pid, "ram_gb", None, f"{PRODUCTS[pid][0]} ships with {int(ram)} GB RAM.", ClaimType.SPEC, ram)
        )
    return out


def mentions() -> list[ProductMention]:
    return [ProductMention(raw_name=name, is_product=True, brand=brand) for name, brand in PRODUCTS.values()] + [
        ProductMention(raw_name="Nova 14", is_product=True, brand="Nova"),
        ProductMention(raw_name="thin-and-light laptops", is_product=False),
    ]


# --- 6 match --------------------------------------------------------------------------------


def match() -> MatchResult:
    products = [
        Product(
            id=pid,
            name=name,
            brand=brand,
            aliases=["Nova 14"] if pid == "prd_nova_book_14" else [],
            variants=[
                Variant(
                    id=f"var_{pid[4:]}_16_512_in",
                    label="16 GB / 512 GB / India",
                    attributes={"ram": "16GB", "storage": "512GB", "region": "IN"},
                )
            ],
            recommender_count={"prd_nova_book_14": 9, "prd_kestrel_slim_15": 6}.get(pid, 2),
            weighted_mentions={"prd_nova_book_14": 7.4, "prd_kestrel_slim_15": 5.1}.get(pid, 1.6),
        )
        for pid, (name, brand) in PRODUCTS.items()
    ]
    alias_map = {name: pid for pid, (name, _) in PRODUCTS.items()} | {"Nova 14": "prd_nova_book_14"}
    return MatchResult(products=products, alias_map=alias_map, method="model")


# --- 7 verify -------------------------------------------------------------------------------


def verify() -> VerifyResult:
    cl = claims()
    by_pair: dict[tuple[str, str], list[Claim]] = {}
    ids = {name: pid for pid, (name, _) in PRODUCTS.items()}
    for c in cl:
        by_pair.setdefault((ids[c.product_raw_name], c.criterion_key or ""), []).append(c)

    values: list[ResolvedValue] = []
    for pid in RANKED:
        for key, *_ in _CRITERIA:
            found = by_pair.get((pid, key), [])
            if pid == "prd_kestrel_slim_15" and key == "battery_life":
                spec, exp = found
                values.append(
                    ResolvedValue(
                        product_id=pid,
                        criterion_key=key,
                        state=ValueState.DISPUTED,
                        score=7.0,
                        value=None,
                        confidence=0.5,
                        supporting_claim_ids=[spec.id],
                        conflicting_claim_ids=[exp.id],
                    )
                )
            elif found:
                values.append(
                    ResolvedValue(
                        product_id=pid,
                        criterion_key=key,
                        state=ValueState.KNOWN,
                        score=found[0].score,
                        confidence=0.8,
                        supporting_claim_ids=[c.id for c in found],
                    )
                )
            else:  # Orion Play 15 build durability: enrichment found nothing, so peer average
                peers = [_SCORES[p][key] for p in RANKED if key in _SCORES[p]]
                values.append(
                    ResolvedValue(
                        product_id=pid,
                        criterion_key=key,
                        state=ValueState.ESTIMATED,
                        score=round(sum(peers) / len(peers), 2),
                        confidence=0.3,
                    )
                )
        ram = by_pair.get((pid, "ram_gb"), [])
        values.append(
            ResolvedValue(
                product_id=pid,
                criterion_key="ram_gb",
                state=ValueState.KNOWN,
                value=ram[0].value,
                confidence=0.9,
                supporting_claim_ids=[ram[0].id],
            )
        )
    values.append(
        ResolvedValue(product_id="prd_vela_pro_14", criterion_key="ram_gb", state=ValueState.UNKNOWN, confidence=0.0)
    )
    return VerifyResult(
        values=values,
        community=[
            CommunitySignal(product_id="prd_nova_book_14", label="consistent", communities=["r/developersIndia"]),
            CommunitySignal(
                product_id="prd_orion_play_15",
                label="single_source",
                communities=["r/developersIndia"],
                note="Only one community discussed it, so confidence is one step lower.",
            ),
        ],
        trust_method="learned",
    )


# --- 9 decide -------------------------------------------------------------------------------


def decision() -> DecideResult:
    rb = rubric()
    vals = {(v.product_id, v.criterion_key): v for v in verify().values}
    chance = {"prd_nova_book_14": 0.46, "prd_kestrel_slim_15": 0.41, "prd_orion_play_15": 0.13}
    ranked: list[RankedProduct] = []
    totals: dict[str, float] = {}
    for pid in RANKED:
        contribs = []
        for w in rb.weights:
            v = vals[(pid, w.key)]
            score = v.score or 0.0
            contribs.append(
                Contribution(
                    criterion_key=w.key,
                    score=v.score,
                    normalized_weight=w.normalized,
                    contribution=round(score * w.normalized, 4),
                    state=v.state,
                )
            )
        totals[pid] = sum(c.contribution for c in contribs) * 10
        evidenced = sum(c.contribution for c in contribs if c.state in (ValueState.KNOWN, ValueState.DISPUTED))
        cov = evidenced / (totals[pid] / 10)
        ranked.append(
            RankedProduct(
                product_id=pid,
                rank=RANKED.index(pid) + 1,
                score=round(totals[pid], 2),
                contributions=contribs,
                coverage=round(cov, 3),
                chance_first=chance[pid],
                confidence="medium" if pid != "prd_orion_play_15" else "low",
                label={0: "best_match", 1: "runner_up"}.get(RANKED.index(pid)),
                gap_to_leader=0.0,
            )
        )
    # model_copy(update=...) skips validation, so rebuild through model_validate instead.
    leader = max(r.score for r in ranked)
    ties = {"prd_nova_book_14": ["prd_kestrel_slim_15"], "prd_kestrel_slim_15": ["prd_nova_book_14"]}
    ranked = [
        RankedProduct.model_validate(
            {
                **r.model_dump(),
                "gap_to_leader": round(max(0.0, leader - r.score), 2),
                "tied_with": ties.get(r.product_id, []),
                "separating_criterion": "battery_life" if r.product_id in ties else None,
            }
        )
        for r in ranked
    ]
    return DecideResult(
        ranked=ranked,
        filtered=[
            FilteredProduct(
                product_id="prd_aurora_air_13",
                group="filtered_out",
                reasons=["₹84,990 is above your ₹70,000 budget"],
                rule_attribute="price",
            ),
            FilteredProduct(
                product_id="prd_vela_pro_14",
                group="cant_tell",
                reasons=["We couldn't confirm it has at least 16 GB RAM"],
                rule_attribute="ram_gb",
            ),
        ],
        method="monte_carlo",
    )


# --- 10 explain -----------------------------------------------------------------------------


def explanations() -> list[Explanation]:
    cl = claims()

    def cite(product: str, key: str) -> list[str]:
        return [c.id for c in cl if c.product_raw_name == product and c.criterion_key == key]

    return [
        Explanation(
            product_id="prd_nova_book_14",
            sentences=[
                ExplanationSentence(
                    text="Its keyboard is the most comfortable of the three, which matters most for long coding.",
                    claim_ids=cite("Nova Book 14", "keyboard_comfort"),
                    supported=True,
                ),
                ExplanationSentence(
                    text="Battery life is strong, so you can work a full day away from a plug.",
                    claim_ids=cite("Nova Book 14", "battery_life"),
                    supported=True,
                ),
            ],
            trade_off="The Orion Play 15 compiles faster, but it is much heavier.",
            method="llm_checked",
        ),
        Explanation(
            product_id="prd_kestrel_slim_15",
            sentences=[
                ExplanationSentence(
                    text="It is the lightest of the three and slightly faster than the Nova Book 14.",
                    claim_ids=cite("Kestrel Slim 15", "weight") + cite("Kestrel Slim 15", "cpu_performance"),
                    supported=True,
                ),
            ],
            trade_off="Its battery life is disputed: the maker says 10 hours, an owner reports about 6.",
            unverified=["battery_life"],
            method="llm_checked",
        ),
    ]


# --- 11 commerce ----------------------------------------------------------------------------


def commerce() -> CommerceResult:
    return CommerceResult(
        offers=[
            Offer(
                product_id="prd_nova_book_14",
                variant_id="var_nova_book_14_16_512_in",
                store="Example Store",
                url="https://store.example.com/nova-book-14-16gb-512gb",
                listing_title="Nova Book 14 (16GB RAM, 512GB SSD) Laptop",
                price=Money(amount=64990),
                availability="in_stock",
                as_of=T0 - timedelta(hours=2),
                title_matched=True,
                variant_matched=True,
            ),
            Offer(
                product_id="prd_kestrel_slim_15",
                store="Another Store",
                url="https://shop.example.org/kestrel-slim-15",
                listing_title="Kestrel Slim 15 Laptop",
                price=Money(amount=67490),
                availability="in_stock",
                as_of=T0 - timedelta(hours=30),
                title_matched=True,
            ),
        ],
        no_offer={"prd_orion_play_15": "https://www.google.com/search?tbm=shop&q=Orion+Play+15"},
    )


# --- the whole search -----------------------------------------------------------------------


def search_result() -> SearchResult:
    vr = verify()
    match_result = match()
    comm = {c.product_id: c for c in vr.community}
    products = [
        ProductSummary(product=p, values=[v for v in vr.values if v.product_id == p.id], community=comm.get(p.id))
        for p in match_result.products
    ]

    def rep(stage: StageName, ms: int, status: StageStatus = StageStatus.OK, reason: str | None = None) -> StageReport:
        return StageReport(stage=stage, status=status, duration_ms=ms, fallback_reason=reason)

    return SearchResult(
        search_id=SEARCH_ID,
        request=SearchRequest(session_id=SESSION_ID, raw_query=QUERY, rubric=rubric()),
        started_at=T0,
        finished_at=T0 + timedelta(seconds=94),
        products=products,
        decision=decision(),
        explanations=explanations(),
        commerce=commerce(),
        sources=sources(),
        stage_reports=[
            rep(StageName.RESEARCH, 38_000),
            rep(StageName.EXTRACT, 21_000),
            rep(StageName.MATCH, 900),
            rep(StageName.VERIFY, 300),
            rep(
                StageName.ENRICH,
                14_000,
                StageStatus.FALLBACK,
                "No page confirmed Orion Play 15's build durability; estimated from similar laptops.",
            ),
            rep(StageName.DECIDE, 450),
            rep(StageName.EXPLAIN, 9_000),
            rep(StageName.COMMERCE, 8_000),
        ],
        coverage_note="YouTube didn't respond in time, and one value is estimated. Results use Reddit, "
        "a review site and the maker's page.",
        saved=True,  # the normal case: the frontend's mock results cover the "not saved" notice
    )


ALL_EXAMPLES = {
    "intent": intent,
    "intent_unclear": unclear_intent,
    "criteria": criteria,
    "rubric": rubric,
    "research": research,
    "claims": claims,
    "match": match,
    "verify": verify,
    "decision": decision,
    "explanations": explanations,
    "commerce": commerce,
    "search_result": search_result,
}
