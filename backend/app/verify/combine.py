"""Combining the claims about one product and criterion (US-16, DR-03, DR-09). Plain code: no model decides.

- One site counts once per product and criterion: its claims become one site claim (its median value or mean score).
  Each claim is still listed as supporting or conflicting by what it says itself.
- Values: a weighted median after converting to one unit; a claim within tolerance of it agrees. A bare number with no
  unit takes the unit the group's other claims use most. Text: a weighted vote.
- Scores: a weighted mean is the score; claims within the score tolerance of the weighted median site agree, so the
  side the weight is on always has a claim to cite.
- "tier_order": the most authoritative tier among the trustworthy claims decides, weighted by trust, so trust breaks
  ties inside it.
- "learned": each site's reliability starts from its trust as a Beta prior, and is updated by how often the site agrees
  with the consensus, across every product and criterion in the run where enough sources speak. At most max_rounds
  rounds, or until no weight moves by min_weight_change.
- Disputed: independent sites disagree beyond tolerance and the losing side carries at least dispute_share of the
  weight. Unknown: no claim from a source starting at unknown_below or more. Never a score of zero.
- A dispute keeps its losing side as alternatives (S3-10b): the outvoted site claims, grouped by what they say (within
  tolerance of the heaviest), each with its value and the unit its claims gave, or its weighted mean score, and the
  claims that say it. Decide checks every side, so a price disputed across the budget is "can't tell".
Everything is sorted before it is summed, so the same claims in any order give the same result.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

from app.contracts import SourceTier, ValueState
from app.decide.units import (
    Quantity,
    attribute_unit,
    in_base,
    known_unit,
    number_text,
    quantity,
    unit_key,
    unit_label,
)

from .trust import Tolerance, TrustTable

__all__ = ["Alternative", "Evidence", "Outcome", "Resolution", "Tolerance", "learn_weights", "measure", "resolve_all"]

Method = Literal["learned", "tier_order"]
Kind = Literal["value", "score"]
ToleranceFor = Callable[[str, str], Tolerance]

_WORD = re.compile(r"[a-z0-9]+")
_TEXT = "text"
_BARE = "n:"  # the form of a number with no unit at all


@dataclass(frozen=True)
class Measured:
    """One claimed value, read: a number (in its own unit and in the base unit of its dimension), or text."""

    raw: float | None = None
    unit: str | None = None
    number: float | None = None
    dimension: str | None = None
    text: str | None = None


def measure(value: float | str | None, unit: str | None, key: str) -> Measured:
    """A bare number takes the claim's unit, else the one its key ends with ("ram_gb"). Text whose one number has a
    unit we know is a number ("up to 10 hours"); text with a number among other words stays text ("Wi-Fi 6")."""
    if value is None or isinstance(value, bool):
        return Measured()
    if isinstance(value, int | float):
        raw, own = float(value), unit_key(unit) or attribute_unit(key)
    else:
        parsed = quantity(value)
        if parsed is None or not (parsed.plain or known_unit(parsed.unit)):
            words = _WORD.findall(value.casefold())
            return Measured(text=" ".join(words) or None)
        raw, own = parsed.number, parsed.unit or unit_key(unit) or attribute_unit(key)
    dimension, base = in_base(Quantity(raw, own), None)
    return Measured(raw=raw, unit=own, number=base, dimension=dimension)


@dataclass(frozen=True)
class Evidence:
    """One claim, ready to weigh: who said it, how far they are trusted, and what they said."""

    claim_id: str
    product_id: str
    key: str
    site: str
    tier: SourceTier
    trust: float
    weight: float = 1.0
    score: float | None = None
    number: float | None = None
    dimension: str | None = None
    text: str | None = None
    shown: float | str | None = None
    raw: float | None = None
    unit: str | None = None
    given_unit: str | None = None  # the claim's own unit, for a number: an alternative shows it as the claim gave it

    @classmethod
    def of(
        cls,
        claim_id: str,
        product_id: str,
        key: str,
        site: str,
        tier: SourceTier,
        trust: float,
        *,
        value: float | str | None = None,
        unit: str | None = None,
        score: float | None = None,
        weight: float = 1.0,
    ) -> Evidence:
        if score is not None:
            return cls(claim_id, product_id, key, site, tier, trust, weight, score=float(score))
        read = measure(value, unit, key)
        given_unit: str | None = None
        if isinstance(value, int | float) and not isinstance(value, bool):
            shown: float | str | None = f"{number_text(float(value))} {unit.strip()}" if unit else float(value)
            given_unit = (unit or "").strip() or None
        else:
            shown = value.strip() if isinstance(value, str) else None
        return cls(
            claim_id,
            product_id,
            key,
            site,
            tier,
            trust,
            weight,
            None,
            read.number,
            read.dimension,
            read.text,
            shown,
            read.raw,
            read.unit,
            given_unit=given_unit,
        )

    @property
    def usable(self) -> bool:
        return self.score is not None or self.number is not None or self.text is not None

    @property
    def form(self) -> str:
        """What it can be compared with: a dimension of numbers, or text."""
        return _TEXT if self.text is not None else f"n:{self.dimension}"


@dataclass(frozen=True)
class _Member:
    """One claim inside a site claim: counted once with its site, but listed by what it says itself."""

    claim_id: str
    number: float | None = None
    text: str | None = None


@dataclass(frozen=True)
class _SiteClaim:
    """Everything one site says about one product and criterion, counted once."""

    site: str
    tier: SourceTier
    trust: float
    factor: float
    members: tuple[_Member, ...]
    form: str = ""
    number: float | None = None
    text: str | None = None
    score: float | None = None
    shown: float | str | None = None
    raw: float | None = None
    given_unit: str | None = None

    @property
    def trustworthy_at(self) -> float:
        return self.trust if self.factor > 0 else 0.0


@dataclass(frozen=True)
class _Group:
    product_id: str
    key: str
    kind: Kind
    claims: tuple[_SiteClaim, ...]


@dataclass(frozen=True)
class Alternative:
    """One losing side of a dispute: a value (with the unit its claims gave) or a score, and the claims behind it."""

    claim_ids: tuple[str, ...]
    value: float | str | None = None
    unit: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class Resolution:
    product_id: str
    key: str
    state: ValueState
    score: float | None = None
    value: float | str | None = None
    confidence: float = 0.0
    supporting: list[str] = field(default_factory=list)
    conflicting: list[str] = field(default_factory=list)
    alternatives: list[Alternative] = field(default_factory=list)


@dataclass(frozen=True)
class Outcome:
    resolutions: dict[tuple[str, str], Resolution]
    weights: dict[str, float]  # each site's weight: learned reliability, or its starting trust for tier order
    rounds: int
    method: Method


# --- one site counts once --------------------------------------------------------------------------------------------


def _mean(numbers: Sequence[float]) -> float:
    return sum(numbers) / len(numbers) if numbers else 0.0


def _site_value(site: str, items: Sequence[Evidence]) -> _SiteClaim:
    """A site's value claims as one: its most common form, then its median (the lower of two middles) or its most
    common text. Claims in another form are left out of the site claim."""
    forms = Counter(e.form for e in items)
    form = min(forms, key=lambda f: (-forms[f], f))
    kept = sorted((e for e in items if e.form == form), key=lambda e: (e.number or 0.0, e.text or "", e.claim_id))
    if form == _TEXT:
        texts = Counter(e.text for e in kept)
        best = min(texts, key=lambda t: (-texts[t], t or ""))
        pick = next(e for e in kept if e.text == best)
    else:
        pick = kept[(len(kept) - 1) // 2]
    return _SiteClaim(
        site=site,
        tier=min(e.tier for e in kept),
        trust=_mean([e.trust for e in kept]),
        factor=_mean([e.weight for e in kept]),
        members=tuple(_Member(e.claim_id, e.number, e.text) for e in kept),
        form=form,
        number=pick.number,
        text=pick.text,
        shown=pick.shown,
        raw=pick.raw,
        given_unit=pick.given_unit,
    )


def _site_score(site: str, items: Sequence[Evidence]) -> _SiteClaim:
    ordered = sorted(items, key=lambda e: e.claim_id)
    return _SiteClaim(
        site=site,
        tier=min(e.tier for e in ordered),
        trust=_mean([e.trust for e in ordered]),
        factor=_mean([e.weight for e in ordered]),
        members=tuple(_Member(e.claim_id) for e in ordered),
        score=_mean([e.score for e in ordered if e.score is not None]),
    )


def _units_for_bare(items: Sequence[Evidence]) -> list[Evidence]:
    """A number with no unit at all ("10" for battery_life) takes the unit the group's other numbers use most, so it is
    compared with them instead of being left out. With no other unit, it stays a plain number. The unit it took is
    kept as its given unit too, so an alternative made from it is read by decide as verify compared it."""
    units = Counter(e.unit for e in items if e.unit is not None and e.number is not None)
    if not units or not any(e.form == _BARE for e in items):
        return list(items)
    unit = min(units, key=lambda u: (-units[u], u))
    out = []
    for e in items:
        if e.form == _BARE and e.raw is not None:
            dimension, base = in_base(Quantity(e.raw, unit), None)
            e = replace(e, number=base, dimension=dimension, unit=unit, given_unit=e.given_unit or unit_label(unit))
        out.append(e)
    return out


def _groups(evidence: Sequence[Evidence]) -> list[_Group]:
    by: dict[tuple[str, str, Kind], list[Evidence]] = defaultdict(list)
    for e in evidence:
        if e.usable:
            by[(e.product_id, e.key, "score" if e.score is not None else "value")].append(e)
    groups = []
    for (product_id, key, kind), items in sorted(by.items()):
        sites: dict[str, list[Evidence]] = defaultdict(list)
        for e in items if kind == "score" else _units_for_bare(items):
            sites[e.site].append(e)
        build = _site_score if kind == "score" else _site_value
        claims = tuple(build(site, found) for site, found in sorted(sites.items()))
        groups.append(_Group(product_id, key, kind, claims))
    return groups


# --- the consensus of one group --------------------------------------------------------------------------------------


Weigh = Callable[[_SiteClaim], float]


def _weighted_median(ordered: Sequence[_SiteClaim], weigh: Weigh) -> _SiteClaim:
    weights = [weigh(c) for c in ordered]
    if sum(weights) <= 0:
        weights = [1.0] * len(ordered)
    half, running = sum(weights) / 2, 0.0
    for claim, weight in zip(ordered, weights, strict=True):
        running += weight
        if running >= half - 1e-12:
            return claim
    return ordered[-1]


def _value_consensus(claims: Sequence[_SiteClaim], weigh: Weigh) -> _SiteClaim:
    """The heaviest form, then its weighted median (numbers) or its weighted vote (text)."""
    totals: dict[str, float] = defaultdict(float)
    for c in claims:
        totals[c.form] += weigh(c)
    form = min(totals, key=lambda f: (-totals[f], f))
    same = [c for c in claims if c.form == form]
    if form == _TEXT:
        votes: dict[str, float] = defaultdict(float)
        for c in same:
            votes[c.text or ""] += weigh(c)
        best = min(votes, key=lambda t: (-votes[t], t))
        return min((c for c in same if (c.text or "") == best), key=lambda c: (-weigh(c), c.site))
    return _weighted_median(sorted(same, key=lambda c: (c.number or 0.0, c.site)), weigh)


def _score_consensus(claims: Sequence[_SiteClaim], weigh: Weigh) -> tuple[float, float]:
    """(the weighted mean, which is the score; the weighted median site's score, which the agreeing side is near)."""
    weights = [weigh(c) for c in claims]
    if sum(weights) <= 0:
        weights = [1.0] * len(claims)
    mean = sum((c.score or 0.0) * w for c, w in zip(claims, weights, strict=True)) / sum(weights)
    anchor = _weighted_median(sorted(claims, key=lambda c: (c.score or 0.0, c.site)), weigh).score or 0.0
    return mean, anchor


@dataclass(frozen=True)
class _Verdict:
    """How one group came out: who agrees, who doesn't, and what the consensus is."""

    agree: tuple[_SiteClaim, ...]
    disagree: tuple[_SiteClaim, ...]
    value: float | str | None = None
    score: float | None = None
    form: str = ""
    number: float | None = None
    text: str | None = None


def _split(claims: Sequence[_SiteClaim], keep: Sequence[bool]) -> tuple[tuple[_SiteClaim, ...], tuple[_SiteClaim, ...]]:
    agree = tuple(c for c, ok in zip(claims, keep, strict=True) if ok)
    disagree = tuple(c for c, ok in zip(claims, keep, strict=True) if not ok)
    return agree, disagree


def _judge(
    group: _Group, weigh: Weigh, candidates: Sequence[_SiteClaim], tolerance: Tolerance, score_tol: float
) -> _Verdict:
    """The consensus from `candidates`, and every site claim of the group set against it. A claim in another form (a
    weight against a time) can't be compared, so it neither agrees nor disagrees."""
    if group.kind == "score":
        mean, anchor = _score_consensus(candidates, weigh)
        agree, disagree = _split(
            group.claims, [abs((c.score or 0.0) - anchor) <= score_tol + 1e-9 for c in group.claims]
        )
        return _Verdict(agree, disagree, score=mean)
    pick = _value_consensus(candidates, weigh)
    comparable = [c for c in group.claims if c.form == pick.form]
    if pick.form == _TEXT:
        keep = [c.text == pick.text for c in comparable]
    else:
        keep = [tolerance.allows(c.number or 0.0, pick.number or 0.0) for c in comparable]
    agree, disagree = _split(comparable, keep)
    return _Verdict(agree, disagree, value=pick.shown, form=pick.form, number=pick.number, text=pick.text)


# --- the two methods -------------------------------------------------------------------------------------------------


def _site_trusts(groups: Sequence[_Group]) -> dict[str, float]:
    """Each site's starting trust over the run: the mean of its site claims'."""
    seen: dict[str, list[float]] = defaultdict(list)
    for group in groups:
        for c in group.claims:
            seen[c.site].append(c.trust)
    return {site: _mean(trusts) for site, trusts in sorted(seen.items())}


def _weigher(reliability: Mapping[str, float], site_trust: Mapping[str, float]) -> Weigh:
    """A site claim's weight: its site's reliability, scaled by how its own pages compare with the site's usual trust
    (an affiliate page counts for less), times its claims' weight (US-42)."""

    def weigh(c: _SiteClaim) -> float:
        usual = site_trust.get(c.site, 0.0)
        return reliability.get(c.site, 0.0) * (c.trust / usual if usual > 0 else 0.0) * c.factor

    return weigh


def learn_weights(
    groups: Sequence[_Group], table: TrustTable, tolerance_for: ToleranceFor
) -> tuple[dict[str, float], int]:
    """Iterative truth discovery. Each site's reliability starts from its trust as a Beta prior worth prior_strength
    claims; each round it is updated by the site's agreements and disagreements with the consensus. Only products and
    criteria with at least min_sites_to_learn independent sites count: with two, the heavier is the consensus by
    definition, so their disagreement says nothing about who is reliable."""
    site_trust = _site_trusts(groups)
    k = table.prior_strength
    alpha = {s: t / 100 * k for s, t in site_trust.items()}
    beta = {s: (1 - t / 100) * k for s, t in site_trust.items()}
    weights = {s: t / 100 for s, t in site_trust.items()}
    informative = [g for g in groups if len({c.site for c in g.claims}) >= table.min_sites_to_learn]
    rounds = 0
    for _ in range(table.max_rounds):
        rounds += 1
        weigh = _weigher(weights, site_trust)
        agree: dict[str, int] = defaultdict(int)
        disagree: dict[str, int] = defaultdict(int)
        for group in informative:
            tolerance = tolerance_for(group.product_id, group.key)
            verdict = _judge(group, weigh, group.claims, tolerance, table.score_tolerance)
            for c in verdict.agree:
                agree[c.site] += 1
            for c in verdict.disagree:
                disagree[c.site] += 1
        new = {s: (alpha[s] + agree[s]) / (alpha[s] + beta[s] + agree[s] + disagree[s]) for s in site_trust}
        moved = max((abs(new[s] - weights[s]) for s in site_trust), default=0.0)
        weights = new
        if moved < table.min_weight_change:
            break
    return weights, rounds


def _member_ids(group: _Group, verdict: _Verdict, tolerance: Tolerance) -> tuple[list[str], list[str]]:
    """Which claims agree with the consensus and which don't. A value claim is judged by its own value, so a shop's one
    page saying 150 min is not listed as backing 200; a score claim goes with its site's mean."""
    agree: list[str] = []
    disagree: list[str] = []
    if group.kind == "score":
        return [m.claim_id for c in verdict.agree for m in c.members], [
            m.claim_id for c in verdict.disagree for m in c.members
        ]
    for site_claim in (*verdict.agree, *verdict.disagree):  # only what can be compared
        for member in site_claim.members:
            if verdict.form == _TEXT:
                ok = member.text == verdict.text
            else:
                ok = member.number is not None and tolerance.allows(member.number, verdict.number or 0.0)
            (agree if ok else disagree).append(member.claim_id)
    return agree, disagree


MAX_ALTERNATIVES = 20  # ResolvedValue.alternatives holds at most 20


def _same_side(kind: Kind, head: _SiteClaim, other: _SiteClaim, tolerance: Tolerance, score_tol: float) -> bool:
    """Does `other` say what `head` says? Scores within the score tolerance; values in the same form, within tolerance
    (numbers) or word for word (text)."""
    if kind == "score":
        return abs((other.score or 0.0) - (head.score or 0.0)) <= score_tol + 1e-9
    if other.form != head.form:
        return False
    if head.form == _TEXT:
        return other.text == head.text
    return tolerance.allows(other.number or 0.0, head.number or 0.0)


def _alternatives(
    group: _Group, verdict: _Verdict, said_no: Sequence[str], weigh: Weigh, tolerance: Tolerance, score_tol: float
) -> list[Alternative]:
    """The losing side of one disputed group, heaviest first. Each outvoted site claim joins the heaviest one before it
    that says the same; each side cites only its own claims that disagree with the consensus."""
    sides: list[list[_SiteClaim]] = []
    for claim in sorted(verdict.disagree, key=lambda c: (-weigh(c), c.site)):
        side = next((s for s in sides if _same_side(group.kind, s[0], claim, tolerance, score_tol)), None)
        if side is None:
            sides.append([claim])
        else:
            side.append(claim)
    disagreeing = set(said_no)
    out: list[Alternative] = []
    for side in sides:
        ids = tuple(sorted({m.claim_id for c in side for m in c.members} & disagreeing))
        head = side[0]
        if not ids:
            continue
        if group.kind == "score":
            weights = [weigh(c) for c in side]
            if sum(weights) <= 0:
                weights = [1.0] * len(side)
            mean = sum((c.score or 0.0) * w for c, w in zip(side, weights, strict=True)) / sum(weights)
            out.append(Alternative(ids, score=round(min(10.0, max(0.0, mean)), 2)))
        elif head.given_unit is not None and head.raw is not None:
            out.append(Alternative(ids, value=head.raw, unit=head.given_unit))
        elif head.shown is not None:
            out.append(Alternative(ids, value=head.shown))
    return out


def _candidates(group: _Group, method: Method, table: TrustTable) -> tuple[_SiteClaim, ...]:
    """Who decides. Learned: everyone, by weight. Tier order: the top tier among the trustworthy claims."""
    if method == "learned":
        return group.claims
    trusted = [c for c in group.claims if c.trustworthy_at >= table.unknown_below] or list(group.claims)
    top = min(c.tier for c in trusted)
    return tuple(c for c in trusted if c.tier == top)


def _resolve(
    product_id: str,
    key: str,
    groups: Sequence[_Group],
    trustworthy: bool,
    weigh: Weigh,
    method: Method,
    tolerance: Tolerance,
    table: TrustTable,
) -> Resolution:
    if not trustworthy or not groups:
        return Resolution(product_id, key, ValueState.UNKNOWN)
    state, value, score = ValueState.KNOWN, None, None
    agreed_weight = total_weight = 0.0
    supporting: list[str] = []
    conflicting: list[str] = []
    alternatives: list[Alternative] = []
    for group in groups:
        verdict = _judge(group, weigh, _candidates(group, method, table), tolerance, table.score_tolerance)
        if group.kind == "score":
            score = round(min(10.0, max(0.0, verdict.score or 0.0)), 2)
        else:
            value = verdict.value
        said_yes, said_no = _member_ids(group, verdict, tolerance)
        supporting += said_yes
        conflicting += said_no
        won = sum(weigh(c) for c in verdict.agree)
        lost = sum(weigh(c) for c in verdict.disagree)
        agreed_weight, total_weight = agreed_weight + won, total_weight + won + lost
        sites = {c.site for c in (*verdict.agree, *verdict.disagree)}
        if (
            verdict.disagree
            and len(sites) >= 2
            and won + lost > 0
            and lost / (won + lost) >= table.dispute_share - 1e-9
        ):
            state = ValueState.DISPUTED
            alternatives += _alternatives(group, verdict, said_no, weigh, tolerance, table.score_tolerance)
    return Resolution(
        product_id,
        key,
        state,
        score=score,
        value=value,
        confidence=round(agreed_weight / total_weight, 3) if total_weight > 0 else 0.0,
        supporting=sorted(supporting),
        conflicting=sorted(conflicting),
        alternatives=alternatives[:MAX_ALTERNATIVES],
    )


def resolve_all(
    evidence: Sequence[Evidence],
    table: TrustTable,
    method: Method,
    tolerance_for: ToleranceFor | None = None,
) -> Outcome:
    """Every product and criterion the evidence speaks to, resolved by one method over the whole run."""
    tolerance = tolerance_for or (lambda _product, key: table.tolerance(key))
    groups = _groups(evidence)
    site_trust = _site_trusts(groups)
    if method == "learned":
        weights, rounds = learn_weights(groups, table, tolerance)
    else:
        weights, rounds = {s: t / 100 for s, t in site_trust.items()}, 0
    weigh = _weigher(weights, site_trust)
    by_pair: dict[tuple[str, str], list[_Group]] = defaultdict(list)
    for group in groups:
        by_pair[(group.product_id, group.key)].append(group)
    trusted = {(e.product_id, e.key) for e in evidence if e.usable and e.weight > 0 and e.trust >= table.unknown_below}
    resolutions = {
        pair: _resolve(*pair, items, pair in trusted, weigh, method, tolerance(*pair), table)
        for pair, items in sorted(by_pair.items())
    }
    return Outcome(resolutions=resolutions, weights=weights, rounds=rounds, method=method)
