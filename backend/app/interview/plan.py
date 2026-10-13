"""Which question to ask next, Sprint 1 fixed order (US-05), from config/interview_rules.yaml.

plan_questions() is a pure function of the criteria, the intent and the answers so far. It is worked out again on
every turn and never stored, so when a user goes back and changes an answer, the later questions update by
themselves: a question removed by a low answer comes back when that answer goes up. US-04 replaces the order.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts import MAX_QUESTIONS, AskedQuestion, CriteriaSet, Criterion, Intent
from app.intake.lexicon import load_lexicon

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config" / "interview_rules.yaml"

Word = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)*$")]


class InterviewConfigError(ValueError):
    """config/interview_rules.yaml is missing, unreadable or breaks a rule."""


class InterviewRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    low_answer_weight: float = Field(ge=0, le=10)
    topic_groups: list[list[Word]] = Field(default_factory=list)
    budget_thresholds_inr: dict[str, float] = Field(default_factory=dict)
    premium_words: list[Word] = Field(default_factory=list)


@dataclass(frozen=True)
class Plan:
    """The questions still to ask, and what the topic and budget rules decided without asking."""

    to_ask: tuple[str, ...]  # criterion keys still to ask, in order (a pending question is included)
    inferred: Mapping[str, float] = field(default_factory=dict)  # settled by a low answer on the same topic
    premium_skipped: frozenset[str] = frozenset()  # not asked because the budget is below the premium tier

    @property
    def next_key(self) -> str | None:
        return self.to_ask[0] if self.to_ask else None


def load_interview_rules(path: Path | str | None = None) -> InterviewRules:
    """Read and check config/interview_rules.yaml (or `path`). Raises InterviewConfigError with the reason."""
    return _load(str(path) if path is not None else str(DEFAULT_RULES_PATH))


@lru_cache(maxsize=4)
def _load(path: str) -> InterviewRules:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InterviewConfigError(f"cannot read interview rules {path}: {exc}") from exc
    try:
        rules = InterviewRules.model_validate(raw)
    except ValidationError as exc:
        raise InterviewConfigError(f"invalid interview rules {path}:\n{exc}") from exc
    if any(not group for group in rules.topic_groups):
        raise InterviewConfigError(f"{path}: every topic group needs at least one word")
    unknown = sorted(set(rules.budget_thresholds_inr) - set(load_lexicon().categories))
    if unknown:
        raise InterviewConfigError(f"{path}: budget thresholds for categories not in categories.yaml: {unknown}")
    if any(amount <= 0 for amount in rules.budget_thresholds_inr.values()):
        raise InterviewConfigError(f"{path}: budget thresholds must be above zero")
    return rules


def default_order(criteria: CriteriaSet) -> list[Criterion]:
    """Highest default weight first; ties keep the order of the criteria list (a stable sort)."""
    return sorted(criteria.criteria, key=lambda c: -c.default_weight)


def answered_weights(history: Sequence[AskedQuestion]) -> dict[str, float]:
    return {
        entry.question.criterion_key: entry.classification.weight
        for entry in history
        if entry.status == "answered" and entry.classification and entry.classification.weight is not None
    }


def plan_questions(
    criteria: CriteriaSet,
    intent: Intent,
    history: Sequence[AskedQuestion],
    rules: InterviewRules | None = None,
    max_questions: int = MAX_QUESTIONS,
) -> Plan:
    """The questions still to ask, worked out from scratch from the criteria and the answers so far."""
    rules = rules or load_interview_rules()
    order = default_order(criteria)
    done = {e.question.criterion_key for e in history if e.status in ("answered", "skipped")}
    asked = done | {e.question.criterion_key for e in history if e.status == "pending"}
    answers = answered_weights(history)

    premium = frozenset(
        c.key
        for c in order
        if c.key not in asked and _below_premium_budget(criteria, intent, rules) and is_premium(c, rules)
    )
    inferred: dict[str, float] = {}
    for settled in order:
        weight = answers.get(settled.key)
        if weight is None or weight > rules.low_answer_weight:
            continue
        for other in order:
            if other.key not in asked and other.key not in inferred and shares_topic(settled, other, rules):
                inferred[other.key] = weight

    planned = [c.key for c in order if c.key not in premium and c.key not in inferred][:max_questions]
    return Plan(
        to_ask=tuple(k for k in planned if k not in done),
        inferred=inferred,
        premium_skipped=premium,
    )


def shares_topic(a: Criterion, b: Criterion, rules: InterviewRules) -> bool:
    """True when two different criteria fall in the same topic group."""
    return a.key != b.key and any(
        any(mentions(w, a) for w in group) and any(mentions(w, b) for w in group) for group in rules.topic_groups
    )


def is_premium(criterion: Criterion, rules: InterviewRules) -> bool:
    return any(mentions(word, criterion) for word in rules.premium_words)


def mentions(word: str, criterion: Criterion) -> bool:
    """Whether the key or the label contains `word` as whole words ("low_light" matches "Low-light performance")."""
    needle = word.split("_")
    return any(_contains(_tokens(text), needle) for text in (criterion.key, criterion.label))


def _below_premium_budget(criteria: CriteriaSet, intent: Intent, rules: InterviewRules) -> bool:
    threshold = rules.budget_thresholds_inr.get(criteria.category)
    top = intent.budget.max if intent.budget else None
    return threshold is not None and top is not None and top.currency == "INR" and top.amount < threshold


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _contains(tokens: list[str], needle: list[str]) -> bool:
    size = len(needle)
    return any(tokens[i : i + size] == needle for i in range(len(tokens) - size + 1))
