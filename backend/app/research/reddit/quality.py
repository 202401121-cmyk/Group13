"""Scores Reddit threads and comments (US-13), so strong advice stands apart from noise. Plain Python, with no model.

The formulas and their numbers are in config/reddit_quality.yaml. Threads under the threshold are dropped, and each one
kept carries its score as Source.quality. Comments are scored and kept. Nothing random is used, inputs are sorted, and
ties go by id, so the same threads always give the same scores. Everything stays in memory (D-53).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts import Source, SourceComment

from .subreddits import subreddits_for

if TYPE_CHECKING:
    from .recommenders import ProductMatcher

log = logging.getLogger(__name__)

DEFAULT_QUALITY_PATH = Path(__file__).resolve().parents[3] / "config" / "reddit_quality.yaml"
SECONDS_PER_DAY = 86_400
MIDDLING = 0.5  # a count nobody in the search knows: not good, not bad (build rule 6)
_ROUND_TO = 6  # so a float's last digits can never decide a tie
_WEIGHTS_ADD_UP_TO = 1.0

# A token with letters and digits that isn't a model: an amount with its unit, or a name for a kind of part.
_TOKEN = re.compile(r"[^\W_]+(?:-[^\W_]+)*")
_LINK = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+)+/\S*")
_AMOUNT = re.compile(
    r"\d+(?:gb|tb|mb|kb|gbps|mbps|ghz|mhz|khz|hz|mah|wh|kwh|w|kw|v|a|kg|g|gm|lbs?|mm|cm|m|nm|ft|ml|l|in|inch|inches"
    r"|k|p|mp|fps|ms|db|rpm|nits?|bit|core|cores"
    r"|hrs?|hours?|mins?|yrs?|years?|months?|days?|am|pm|st|nd|rd|th|x|ton|tons|star|stars"
    r"|rs|inr|usd|krs|rupees?|rupaye|lakhs?|lacs?|cr|crores?)"
    r"|(?:rs|inr|usd)\d+k?"  # prices
    r"|\d+(?:x|in)\d+"  # 1920x1080, 24x7, 2in1
    r"|\d+[kp]\d+"  # 4K60, 1080p60
)
_PART_NAME = re.compile(r"(?:usb|hdmi|hdr|ddr|lpddr|gddr|wifi|fi|bt|pcie|gen|ipx?|qc|wpa|win|r32)\d*[a-z]?")
_LETTERS_THEN_NUMBER = re.compile(r"[A-Z]{2,}-\d+")
_MIN_MODEL_CHARS = 3


class ThreadWeights(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    upvotes_weight: float = Field(default=0.4, ge=0, le=1)
    comments_weight: float = Field(default=0.3, ge=0, le=1)
    relevance_weight: float = Field(default=0.3, ge=0, le=1)
    upvotes_full_at: int = Field(default=200, gt=0)
    comments_full_at: int = Field(default=100, gt=0)
    min_score: float = Field(default=0.25, ge=0, le=1)
    keep_if_none_pass: int = Field(default=5, ge=0)

    @model_validator(mode="after")
    def _adds_up(self) -> ThreadWeights:
        total = self.upvotes_weight + self.comments_weight + self.relevance_weight
        if not math.isclose(total, _WEIGHTS_ADD_UP_TO):
            raise ValueError("a thread's weights must add up to 1")
        return self


class CommentWeights(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    upvotes_weight: float = Field(default=0.4, ge=0, le=1)
    length_weight: float = Field(default=0.2, ge=0, le=1)
    model_weight: float = Field(default=0.4, ge=0, le=1)
    upvotes_full_at: int = Field(default=50, gt=0)
    length_full_at: int = Field(default=500, gt=0)

    @model_validator(mode="after")
    def _adds_up(self) -> CommentWeights:
        total = self.upvotes_weight + self.length_weight + self.model_weight
        if not math.isclose(total, _WEIGHTS_ADD_UP_TO):
            raise ValueError("a comment's weights must add up to 1")
        return self


class Relevance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    listed: float = Field(default=0.7, ge=0, le=1)
    unknown: float = Field(default=0.3, ge=0, le=1)
    categories: dict[str, dict[str, Annotated[float, Field(ge=0, le=1)]]] = Field(default_factory=dict)


class CountingWords(BaseModel):
    """What the recommender counts skip: a name made only of these words is not a product of its own."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    brands: tuple[str, ...] = ()
    generic_words: tuple[str, ...] = ()


class QualitySettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    counting: CountingWords = Field(default_factory=CountingWords)
    half_life_days: float = Field(default=180, gt=0)
    thread: ThreadWeights = Field(default_factory=ThreadWeights)
    comment: CommentWeights = Field(default_factory=CommentWeights)
    relevance: Relevance = Field(default_factory=Relevance)


def load_quality_settings(path: Path | str | None = None) -> QualitySettings:
    return _load(str(path) if path is not None else str(DEFAULT_QUALITY_PATH))


@lru_cache(maxsize=4)
def _load(path: str) -> QualitySettings:
    try:
        values = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("not a mapping")
        return QualitySettings.model_validate(values)
    except (OSError, yaml.YAMLError, ValueError, TypeError) as exc:  # ValidationError is a ValueError
        log.warning("%s is not usable (%s); using the defaults", Path(path).name, type(exc).__name__)
        return QualitySettings()


@dataclass(frozen=True)
class ThreadScore:
    """One thread's score, for the CLI. Its URL and numbers only, never its words."""

    url: str
    community: str
    score: float
    estimated: bool


@dataclass(frozen=True)
class ScoredThreads:
    kept: list[Source]  # best first, each with its score as its quality
    dropped: list[ThreadScore]
    scores: list[ThreadScore]  # all of them, kept and dropped


def _log_scale(count: int, full_at: int) -> float:
    return min(1.0, math.log1p(max(count, 0)) / math.log1p(full_at))


def age_weight(age_days: float, half_life_days: float) -> float:
    return float(0.5 ** (max(age_days, 0.0) / half_life_days))


def _age_days(thread: Source) -> float | None:
    if thread.published_at is None:
        return None
    return (thread.fetched_at - thread.published_at).total_seconds() / SECONDS_PER_DAY


def _mean(values: Sequence[float | None], *, default: float) -> float:
    known = sorted(value for value in values if value is not None)
    return sum(known) / len(known) if known else default


def _relevance(thread: Source, category: str, settings: Relevance) -> float:
    name = (thread.community or "").removeprefix("r/").casefold()
    rows = {sub.casefold(): value for sub, value in settings.categories.get(category, {}).items()}
    if name in rows:
        return rows[name]
    listed = {sub.casefold() for sub in subreddits_for(category)}
    return settings.listed if name in listed else settings.unknown


def score_threads(threads: Sequence[Source], category: str, settings: QualitySettings) -> ScoredThreads:
    """Each thread's score. An unknown count or date takes the average of the others' (build rule 6)."""
    weights = settings.thread
    ordered = sorted(threads, key=lambda t: t.id)
    upvotes = [None if t.upvotes is None else _log_scale(t.upvotes, weights.upvotes_full_at) for t in ordered]
    comments = [
        None if t.comment_count is None else _log_scale(t.comment_count, weights.comments_full_at) for t in ordered
    ]
    ages = [_age_days(t) for t in ordered]
    aged = [None if age is None else age_weight(age, settings.half_life_days) for age in ages]
    usual_upvotes = _mean(upvotes, default=MIDDLING)
    usual_comments = _mean(comments, default=MIDDLING)
    usual_age = _mean(aged, default=1.0)

    scored: list[tuple[float, Source, ThreadScore]] = []
    for index, thread in enumerate(ordered):
        known = (upvotes[index], comments[index], aged[index])
        raw = (
            weights.upvotes_weight * (usual_upvotes if known[0] is None else known[0])
            + weights.comments_weight * (usual_comments if known[1] is None else known[1])
            + weights.relevance_weight * _relevance(thread, category, settings.relevance)
        ) * (usual_age if known[2] is None else known[2])
        score = round(raw, _ROUND_TO)
        row = ThreadScore(str(thread.url), thread.community or "", score, estimated=None in known)
        scored.append((score, thread, row))
    scored.sort(key=lambda item: (-item[0], item[1].id))

    # Best first, so what passes comes first. When nothing does, the best few stay, as an old archive's threads can't
    # pass, and an empty result would say nothing about why. Their low scores go with them, for later stages to weigh.
    passing = sum(1 for score, _, _ in scored if score >= weights.min_score)
    cut = passing or min(weights.keep_if_none_pass, len(scored))
    kept = [Source.model_validate({**thread.model_dump(), "quality": score}) for score, thread, _ in scored[:cut]]
    return ScoredThreads(kept, [row for _, _, row in scored[cut:]], [row for _, _, row in scored])


def _is_model(token: str) -> bool:
    lowered = token.casefold()
    return (
        len(lowered) >= _MIN_MODEL_CHARS
        and any(c.isdigit() for c in lowered)
        and any(c.isascii() and c.isalpha() for c in lowered)
        and _AMOUNT.fullmatch(lowered) is None
        and _PART_NAME.fullmatch(lowered) is None
    )


def names_a_model(text: str) -> bool:
    """Does this hold a token like XM5, G14 or WH-1000XM5? "16GB", "70k", "2-in-1" and "USB-C" are not models."""
    for token in _TOKEN.findall(_LINK.sub(" ", text)):
        parts = token.split("-")
        if any(_is_model(part) for part in parts):
            return True
        joined = "".join(parts[:2]).casefold()
        if _LETTERS_THEN_NUMBER.match(token) and not (_PART_NAME.fullmatch(joined) or _AMOUNT.fullmatch(joined)):
            return True
    return False


def comment_score(comment: SourceComment, settings: QualitySettings, names: ProductMatcher | None = None) -> float:
    weights = settings.comment
    upvotes = _log_scale(comment.upvotes, weights.upvotes_full_at)
    length = min(len(comment.text), weights.length_full_at) / weights.length_full_at
    named = names_a_model(comment.text) or (names is not None and bool(names.find(comment.text)))
    raw = weights.upvotes_weight * upvotes + weights.length_weight * length + (weights.model_weight if named else 0.0)
    return round(raw, _ROUND_TO)


def score_comments(thread: Source, settings: QualitySettings, names: ProductMatcher | None = None) -> Source:
    """The same thread, with each comment's score as its quality. In id order, so the token cap, which keeps the order
    of comments with equal votes, keeps the same ones however the archive happened to list them."""
    comments = tuple(
        SourceComment.model_validate({**comment.model_dump(), "quality": comment_score(comment, settings, names)})
        for comment in sorted(thread.comments, key=lambda c: c.id)
    )
    return Source.model_validate({**thread.model_dump(), "comments": comments})
