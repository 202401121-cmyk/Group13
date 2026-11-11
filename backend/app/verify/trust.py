"""Where a source's trust starts (US-16, DR-03): backend/config/trust.yaml, the one table of known sites.

A site's starting score (0 to 100) is its row in the table, moved by the product category's adjustment, or the default
for its source type when the site is not listed. An affiliate page starts lower than the same site's reviews. Reddit
and forums are always community (app/extract/tiers.py decides tiers for both stages); a forum on a site's own domain
is a voice of its own, apart from the site.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.contracts import Source, SourceTier, SourceType
from app.extract.tiers import tier_of

log = logging.getLogger(__name__)

TRUST_PATH = Path(__file__).resolve().parents[2] / "config" / "trust.yaml"
_DOMAIN = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")
_ALWAYS_COMMUNITY = ("reddit.com", "redd.it")
# Second-level names under a country code ("example.co.in"): the main domain then keeps three labels.
_SECOND_LEVEL = frozenset({"co", "com", "net", "org", "ac", "gov", "edu", "nic"})

TierName = Literal["manufacturer", "lab", "editorial", "community", "retailer"]

_DEFAULTS: dict[SourceType, float] = {
    SourceType.MANUFACTURER: 95,
    SourceType.LAB: 85,
    SourceType.REVIEW_SITE: 70,
    SourceType.WEB_SEARCH: 55,
    SourceType.YOUTUBE: 50,
    SourceType.REDDIT: 45,
    SourceType.RETAILER: 40,
}


class SiteTrust(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    score: float = Field(ge=0, le=100)
    tier: TierName
    categories: dict[str, float] = Field(default_factory=dict, description="Points added for a product category")


class Tolerance(BaseModel):
    """How far a value may be from the consensus and still agree: a share of it, or a fixed amount in base units."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    relative: float = Field(default=0.0, ge=0)
    absolute: float = Field(default=0.0, ge=0)

    def allows(self, value: float, consensus: float) -> bool:
        return abs(value - consensus) <= max(self.absolute, self.relative * abs(consensus)) + 1e-9


class TrustTable(BaseModel):
    """trust.yaml, checked. Every default here is the team's choice that trust.yaml repeats."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sites: dict[str, SiteTrust] = Field(default_factory=dict)
    defaults: dict[SourceType, float] = Field(default_factory=lambda: dict(_DEFAULTS))
    affiliate_folders: list[str] = Field(
        default_factory=lambda: ["advisor", "deals", "deal", "offers", "coupons", "affiliate"]
    )
    affiliate_penalty: float = Field(default=15, ge=0, le=100)
    unknown_below: float = Field(default=40, ge=0, le=100)
    dispute_share: float = Field(default=0.30, gt=0, le=1)
    score_tolerance: float = Field(default=2.0, ge=0, le=10)
    value_tolerance: float = Field(default=0.05, ge=0)
    value_tolerance_by_key: dict[str, float] = Field(default_factory=dict)
    prior_strength: float = Field(default=10, gt=0)
    max_rounds: int = Field(default=10, ge=1, le=100)
    min_weight_change: float = Field(default=0.01, ge=0)
    min_sites_to_learn: int = Field(default=3, ge=2)

    @field_validator("sites")
    @classmethod
    def _check_sites(cls, sites: dict[str, SiteTrust]) -> dict[str, SiteTrust]:
        for domain, site in sites.items():
            if not _DOMAIN.fullmatch(domain):
                raise ValueError(f"{domain} is not a plain lowercase domain")
            covers = [d for d in _ALWAYS_COMMUNITY if domain == d or domain.endswith(f".{d}")]
            if covers and site.tier != "community":
                raise ValueError(f"{domain} is always community evidence, never {site.tier} (US-16)")
        return sites

    @model_validator(mode="after")
    def _check_defaults(self) -> TrustTable:
        missing = set(SourceType) - set(self.defaults)
        if missing:
            raise ValueError(f"defaults has no score for {sorted(m.value for m in missing)}")
        if any(not 0 <= score <= 100 for score in self.defaults.values()):
            raise ValueError("a default score is outside 0 to 100")
        return self

    def domain_tiers(self) -> dict[str, SourceTier]:
        return {domain: SourceTier[site.tier.upper()] for domain, site in self.sites.items()}

    def tolerance(self, key: str) -> Tolerance:
        return Tolerance(relative=self.value_tolerance_by_key.get(key, self.value_tolerance))


def load_trust_table(path: Path | str | None = None) -> TrustTable:
    """trust.yaml, or the built-in defaults (with one log line) when it is missing or broken: verify never stops."""
    where = Path(path) if path is not None else TRUST_PATH
    try:
        data = yaml.safe_load(where.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a mapping")
        return TrustTable.model_validate(data)
    except (OSError, yaml.YAMLError, ValueError, TypeError) as exc:  # ValidationError is a ValueError
        log.warning("%s is not usable (%s); using the built-in trust defaults", where.name, type(exc).__name__)
        return TrustTable()


@lru_cache(maxsize=1)
def default_trust_table() -> TrustTable:
    return load_trust_table()


# --- one source ------------------------------------------------------------------------------------------------------


def _host(source: Source) -> str:
    return (source.url.host or source.site or "").lower().rstrip(".").removeprefix("www.")


def _listed(host: str, table: TrustTable) -> str | None:
    """The longest listed domain the host is on, if any."""
    labels = host.split(".")
    for start in range(len(labels)):
        domain = ".".join(labels[start:])
        if domain in table.sites:
            return domain
    return None


def main_domain(host: str) -> str:
    """ "store.nova.example" -> "nova.example"; "shop.example.co.in" -> "example.co.in"."""
    labels = host.lower().rstrip(".").removeprefix("www.").split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def site_key(source: Source, table: TrustTable) -> str:
    """Who is speaking, for independence: one site counts once per product and criterion. A subreddit for Reddit."""
    if source.type is SourceType.REDDIT:
        return f"reddit:{(source.community or 'unknown').lower()}"
    host = _host(source)
    return _listed(host, table) or main_domain(host)


def source_tier(source: Source, table: TrustTable) -> SourceTier:
    """Reddit and forums are community; else the table's tier for the site; else the tier research gave it."""
    return tier_of(source, table.domain_tiers())


def site_trust(
    host: str,
    path: str,
    source_type: SourceType,
    tier: SourceTier,
    table: TrustTable,
    category: str | None = None,
) -> float:
    """The starting trust of one page, from 0 to 100. Used for a Source here, and for a row of conflicts.csv."""
    listed = _listed(host.lower().removeprefix("www."), table)
    site = table.sites.get(listed) if listed else None
    if tier is SourceTier.COMMUNITY and (site is None or site.tier != "community"):
        score = table.defaults[SourceType.REDDIT]  # a forum keeps community trust, even on a maker's own domain
    elif site is not None:
        score = site.score + (site.categories.get(category, 0.0) if category else 0.0)
    else:
        score = table.defaults[source_type]
    folders = {part.lower() for part in path.split("/") if part}
    if folders & set(table.affiliate_folders):
        score -= table.affiliate_penalty
    return max(0.0, min(100.0, score))


def _path(source: Source) -> str:
    """The path the affiliate rule reads. Never a Reddit one: r/deals is a subreddit, not an affiliate page."""
    return "" if source.type is SourceType.REDDIT else source.url.path or ""


def source_trust(source: Source, table: TrustTable, category: str | None = None) -> float:
    return site_trust(_host(source), _path(source), source.type, source_tier(source, table), table, category)


def _voice(source: Source, table: TrustTable, tier: SourceTier) -> str:
    """The site key, split by kind of page: a forum on a maker's or a review site's own domain is an owner's post, a
    voice apart from the site's spec or review pages, so it never borrows their tier (DR-03)."""
    key = site_key(source, table)
    if source.type is SourceType.REDDIT:
        return key
    listed = _listed(_host(source), table)
    usual = SourceTier[table.sites[listed].tier.upper()] if listed else source.tier
    return key if tier == usual else f"{key}#{tier.name.lower()}"


def describe_sources(
    sources: Sequence[Source], table: TrustTable, category: str | None = None
) -> dict[str, tuple[str, SourceTier, float]]:
    """Each source's (site key, tier, starting trust), worked out once per run rather than once per claim."""
    domains = table.domain_tiers()
    out: dict[str, tuple[str, SourceTier, float]] = {}
    for source in sources:
        tier = tier_of(source, domains)
        trust = site_trust(_host(source), _path(source), source.type, tier, table, category)
        out[source.id] = (_voice(source, table, tier), tier, trust)
    return out
