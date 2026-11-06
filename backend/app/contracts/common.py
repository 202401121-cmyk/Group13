"""Shared building blocks for every stage contract.

Rules for everything in app/contracts/:
- Contracts are the ONLY way stages talk to each other. Never pass raw dicts between stages.
- Contract models are strict (extra fields are rejected) and immutable (frozen).
- LLM output is never parsed straight into a contract. Parsers use their own lenient models,
  clean the data, then build the contract. Invalid data becomes a fallback, never a guess.
- Changing a contract is a team decision: bump SCHEMA_VERSION and update the tests and fixtures.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.3.0"


class Contract(BaseModel):
    """Base class for every contract model."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        use_enum_values=False,
        # Every field is always present in the JSON we send, so the frontend types mark them required.
        json_schema_serialization_defaults_required=True,
    )


def _require_tz(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware (use datetime.now(timezone.utc))")
    return value


AwareDatetime = Annotated[datetime, AfterValidator(_require_tz)]
"""A timezone-aware timestamp. Every time in the system is stored in UTC."""

Slug = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$", max_length=64)]
"""Lower-case identifier such as 'laptop' or 'battery_life'."""

Region = Annotated[str, Field(pattern=r"^(?:[A-Z]{2}|GLOBAL)$")]
"""ISO 3166 two-letter country code, or GLOBAL."""

CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
"""ISO 4217 currency code such as INR."""

Score10 = Annotated[float, Field(ge=0.0, le=10.0)]
"""A score on the 0 to 10 scale used for criteria and weights."""

Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
"""A value between 0 and 1, such as confidence or coverage."""

# Identifiers are plain strings so they serialise cleanly. The prefix tells you the kind.
SearchId = Annotated[str, Field(pattern=r"^srch_[A-Za-z0-9]{6,40}$")]
SessionId = Annotated[str, Field(pattern=r"^sess_[A-Za-z0-9]{6,40}$")]
SourceId = Annotated[str, Field(pattern=r"^src_[a-f0-9]{8,64}$")]
ClaimId = Annotated[str, Field(pattern=r"^clm_[a-f0-9]{8,64}$")]
ProductId = Annotated[str, Field(pattern=r"^prd_[A-Za-z0-9_-]{3,64}$")]
VariantId = Annotated[str, Field(pattern=r"^var_[A-Za-z0-9_-]{3,64}$")]


class Money(Contract):
    amount: float = Field(ge=0)
    currency: CurrencyCode = "INR"


class SourceType(StrEnum):
    REDDIT = "reddit"
    YOUTUBE = "youtube"
    WEB_SEARCH = "web_search"
    REVIEW_SITE = "review_site"
    MANUFACTURER = "manufacturer"
    LAB = "lab"
    RETAILER = "retailer"


class SourceTier(IntEnum):
    """DR-03: lower number = more authoritative for factual specs."""

    MANUFACTURER = 1
    LAB = 2
    EDITORIAL = 3
    COMMUNITY = 4
    RETAILER = 5


class ClaimType(StrEnum):
    """DR-05: community posts are experience or opinion, never spec, unless verified."""

    SPEC = "spec"
    EXPERIENCE = "experience"
    OPINION = "opinion"


class StageName(StrEnum):
    INTAKE = "intake"
    CRITERIA = "criteria"
    INTERVIEW = "interview"
    RESEARCH = "research"
    EXTRACT = "extract"
    MATCH = "match"
    VERIFY = "verify"
    ENRICH = "enrich"
    DECIDE = "decide"
    EXPLAIN = "explain"
    COMMERCE = "commerce"


class StageStatus(StrEnum):
    OK = "ok"
    FALLBACK = "fallback"  # the better version failed; the baseline ran instead
    SKIPPED = "skipped"  # nothing to do, or deliberately switched off
    TIMEOUT = "timeout"  # hit its time limit; best result so far was kept
    FAILED = "failed"  # even the fallback failed; the stage produced an empty result


# Seconds each stage may run before the pipeline stops it (plan §6, stage contracts).
STAGE_TIME_LIMITS_S: dict[StageName, float] = {
    StageName.INTAKE: 5,
    StageName.CRITERIA: 10,
    StageName.INTERVIEW: 5,  # per turn
    StageName.RESEARCH: 40,
    StageName.EXTRACT: 15,  # per source, run in parallel
    StageName.MATCH: 5,
    StageName.VERIFY: 2,
    StageName.ENRICH: 20,
    StageName.DECIDE: 2,
    StageName.EXPLAIN: 15,
    StageName.COMMERCE: 15,
}
SEARCH_TIME_LIMIT_S: float = 150
"""A whole search never runs longer than this; the best result so far is returned, marked partial."""


class StageReport(Contract):
    """What happened in one stage. Drives the coverage note and the diagnostics page."""

    stage: StageName
    status: StageStatus
    duration_ms: int = Field(ge=0)
    fallback_reason: str | None = Field(default=None, max_length=300)
    notes: list[str] = Field(default_factory=list, max_length=20)
