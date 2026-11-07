"""Stage 3, interview (US-04 to US-08, US-39): one turn in, one step out."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from .common import Contract, Fraction, Score10, SessionId, Slug, StrictScore10
from .criteria import CriteriaSet
from .intake import Intent
from .rubric import Rubric

MAX_QUESTIONS = 10
MAX_UNCLEAR_PER_QUESTION = 2  # US-39: after two unclear or off-topic replies, skip and mark it


class RoughLevel(StrEnum):
    """A rough answer from the interview's buttons (US-08)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    GOOD_ENOUGH = "good_enough"


# The one table from rough answers to weights (US-08). The frontend reads the same table from
# shared/contracts/examples/rough_levels.json, so the two can never disagree.
ROUGH_LEVEL_WEIGHTS: dict[RoughLevel, float] = {
    RoughLevel.LOW: 2.0,
    RoughLevel.MEDIUM: 5.0,
    RoughLevel.HIGH: 8.0,
    RoughLevel.GOOD_ENOUGH: 3.0,
}


class RoughLevelWeights(Contract):
    """ROUGH_LEVEL_WEIGHTS as JSON, for the frontend."""

    weights: dict[RoughLevel, Score10]

    @model_validator(mode="after")
    def _check(self) -> RoughLevelWeights:
        if set(self.weights) != set(RoughLevel):
            raise ValueError("the table must give a weight for every level")
        return self


def check_one_answer_form(level: RoughLevel | None, exact_weight: float | None) -> None:
    """A reply is rough or exact. Both at once would leave the interview guessing which one was meant."""
    if level is not None and exact_weight is not None:
        raise ValueError("a reply carries a rough level or an exact weight, never both")


class ReplyType(StrEnum):
    ANSWER = "answer"
    QUESTION = "question"
    ANSWER_WITH_QUESTION = "answer_with_question"
    SKIP = "skip"
    CORRECTION = "correction"
    OFF_TOPIC = "off_topic"
    UNCLEAR = "unclear"


class InterviewQuestion(Contract):
    id: str = Field(pattern=r"^q_[a-z0-9_]{1,40}$")
    criterion_key: Slug
    text: str = Field(min_length=5, max_length=300)
    quick_replies: list[str] = Field(default_factory=list, max_length=6)
    why_asking: str | None = Field(default=None, max_length=200)


class ReplyClassification(Contract):
    reply_type: ReplyType
    weight: Score10 | None = Field(default=None, description="Weight implied by the answer, if any")
    user_question: str | None = Field(default=None, max_length=300)
    corrects_question_id: str | None = None
    confidence: Fraction = 1.0
    classified_by: Literal["llm", "fallback"] = "llm"

    @model_validator(mode="after")
    def _check(self) -> ReplyClassification:
        rt = self.reply_type
        if rt in (ReplyType.ANSWER, ReplyType.ANSWER_WITH_QUESTION, ReplyType.CORRECTION) and self.weight is None:
            raise ValueError(f"a {rt} reply must carry a weight")
        if rt in (ReplyType.QUESTION, ReplyType.ANSWER_WITH_QUESTION) and not self.user_question:
            raise ValueError(f"a {rt} reply must carry the user's question")
        if rt == ReplyType.CORRECTION and not self.corrects_question_id:
            raise ValueError("a correction must say which question it corrects")
        return self


class RememberedPreference(Contract):
    """A past preference found by meaning (US-06). Shown as 'remembered', changeable in one click."""

    text: str = Field(max_length=200)
    criterion_key: Slug | None = None
    weight: Score10 | None = None
    similarity: Fraction
    age_days: int = Field(ge=0)


class AskedQuestion(Contract):
    question: InterviewQuestion
    reply_text: str | None = Field(default=None, max_length=500)
    classification: ReplyClassification | None = None
    status: Literal["pending", "answered", "skipped", "remembered"]
    unclear_count: int = Field(default=0, ge=0, le=MAX_UNCLEAR_PER_QUESTION)


class InterviewState(Contract):
    """The whole interview so far. The server stores it; each turn returns a new copy."""

    session_id: SessionId
    intent: Intent
    criteria: CriteriaSet
    history: list[AskedQuestion] = Field(default_factory=list, max_length=40)
    remembered: list[RememberedPreference] = Field(default_factory=list, max_length=5)
    manual_weights: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> InterviewState:
        if not self.intent.ready_for_research:
            raise ValueError("the interview starts only after the category is known (US-01)")
        if self.intent.category != self.criteria.category:
            raise ValueError("intent and criteria are for different categories")
        return self


class InterviewTurn(Contract):
    state: InterviewState
    reply_text: str | None = Field(default=None, max_length=500, description="None on the first turn")
    level: RoughLevel | None = Field(default=None, description="A rough answer, or use exact_weight (US-08)")
    exact_weight: StrictScore10 | None = Field(default=None, description="An exact 0 to 10 answer, or use level")

    @model_validator(mode="after")
    def _check(self) -> InterviewTurn:
        check_one_answer_form(self.level, self.exact_weight)
        return self


StepKind = Literal[
    "question",  # ask the next question
    "answer_then_repeat",  # answer the user's question, then ask the same question again
    "rephrase",  # unclear reply: ask the same thing in other words, with buttons
    "redirect",  # off-topic reply: one line back to the question
    "finished",  # interview done; rubric attached
]


class InterviewStep(Contract):
    kind: StepKind
    message: str = Field(min_length=1, max_length=600, description="Exactly what the chat shows")
    question: InterviewQuestion | None = None
    answer_source: Literal["definition", "llm"] | None = Field(
        default=None, description="For answer_then_repeat: saved definition first, LLM only if needed"
    )
    rubric: Rubric | None = None
    state: InterviewState
    questions_asked: int = Field(ge=0, le=MAX_QUESTIONS)
    ordering: Literal["information_gain", "fixed_order"] = "fixed_order"

    @model_validator(mode="after")
    def _check(self) -> InterviewStep:
        if self.kind == "finished":
            if self.rubric is None or self.question is not None:
                raise ValueError("a finished step carries the rubric and no question")
        else:
            if self.question is None or self.rubric is not None:
                raise ValueError(f"a '{self.kind}' step carries a question and no rubric")
        if (self.kind == "answer_then_repeat") != (self.answer_source is not None):
            raise ValueError("answer_source is set exactly when the step answers a user question")
        return self
