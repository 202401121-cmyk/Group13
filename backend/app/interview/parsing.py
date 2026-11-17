"""The classifier's answer about one reply, read leniently, then made a ReplyClassification by code's rules (US-39).

Nothing is guessed. The LLM names a type and, for an answer, a rough level or a number it read; code turns those into
the weight through the one table (answers.answer_weight). Anything the rules cannot place is Refused, and the stage
then treats the reply as an answer the shopper can correct (criterion 11).
- the type is one of the seven;
- a number counts only when the reply itself writes it (digits or a word), from 0 to 10, so no weight is invented;
- a question must be there, short, and not read as an order (build rule 3);
- a correction must name a question asked earlier and settled; naming the current question makes it an answer.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

from app.contracts import AskedQuestion, InterviewState, ReplyClassification, ReplyType, RoughLevel
from app.llm.output import looks_like_instruction

from .answers import answer_weight
from .questions import question_id

MAX_QUESTION_CHARS = 300  # ReplyClassification.user_question limit
MAX_ANSWER_WORDS = 60  # DECIDED 2
MAX_ANSWER_CHARS = 400  # so the answer and the question fit the step's 600 characters
NEEDS_WEIGHT = {ReplyType.ANSWER, ReplyType.ANSWER_WITH_QUESTION, ReplyType.CORRECTION}
NEEDS_QUESTION = {ReplyType.QUESTION, ReplyType.ANSWER_WITH_QUESTION}

_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
# A level as a word of its own: never half of a compound ("low-light", "high-end"), so no hyphen on either side.
_LEVEL_WORDS = {
    level: re.compile(r"(?<![\w-])" + r"[\s_-]+".join(level.value.split("_")) + r"(?![\w-])", re.IGNORECASE)
    for level in RoughLevel
}
# A level negated within the three words before it ("not high", "isn't really low") says what it is not: no reading.
_NEGATED = re.compile(r"\b(?:not|no|never|isn't|isnt|wasn't|don't|doesn't|nothing)\b(?:\W+\w+){0,2}\W+$", re.IGNORECASE)
# An order to a model in a shopper's question: the injection forms only. The pages' "rank X first" rule
# (looks_like_instruction) is left out here: "should I rank CPU performance first?" is a fair question.
_ORDER = re.compile(
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules|prompts?)"
    r"|disregard\s+(?:all\s+|the\s+)?(?:previous|prior|above)|system\s+prompt|you\s+are\s+now",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\d.])\d+(?:\.\d+)?|\b(?:" + "|".join(_WORDS) + r")\b", re.IGNORECASE)


class RawReply(BaseModel):
    """What the classifier answers. Lenient: a missing field is None, an extra one is ignored."""

    model_config = ConfigDict(extra="ignore")

    reply_type: str
    level: str | None = None
    number: float | None = None
    user_question: str | None = None
    corrects: str | None = None


class RawAnswer(BaseModel):
    """The LLM's short answer to the shopper's question."""

    model_config = ConfigDict(extra="ignore")

    answer: str


class Refused(Exception):
    """Why the answer was not used, in code's own words: never the LLM's or the shopper's text."""


def numbers_written(text: str) -> list[float]:
    """Every number the text writes, as digits or a word from zero to ten, in order."""
    found = []
    for match in _NUMBER.finditer(text):
        word = match.group(0).lower()
        found.append(float(_WORDS[word]) if word in _WORDS else float(word))
    return found


def to_classification(raw: RawReply, text: str, state: InterviewState, pending: AskedQuestion) -> ReplyClassification:
    """The classifier's answer as a ReplyClassification (classified_by "llm"), or Refused."""
    try:
        reply_type = ReplyType(raw.reply_type.strip().lower())
    except ValueError:
        raise Refused("an unknown reply type") from None
    fields: dict[str, object] = {"reply_type": reply_type, "classified_by": "llm"}
    if reply_type is ReplyType.ANSWER_WITH_QUESTION and not _has_weight(raw, text):
        # A question with no answer we can read is a question: answer it and ask again, never count it (criterion 8).
        reply_type = ReplyType.QUESTION
        fields["reply_type"] = reply_type
    if reply_type in NEEDS_WEIGHT:
        fields["weight"] = _weight(raw, text)
    if reply_type in NEEDS_QUESTION:
        fields["user_question"] = _question(raw.user_question)
    if reply_type is ReplyType.CORRECTION:
        corrects = _corrected(raw.corrects, state, pending)
        if corrects == pending.question.id:  # it changes the question being asked: that is its answer
            fields["reply_type"] = ReplyType.ANSWER
        else:
            fields["corrects_question_id"] = corrects
    return ReplyClassification.model_validate(fields)


def to_answer(raw: RawAnswer) -> str:
    """The LLM's answer to the shopper's question: at most 60 words, and nothing that reads as an order."""
    answer = " ".join(raw.answer.split())
    if not answer:
        raise Refused("an empty answer")
    if len(answer.split()) > MAX_ANSWER_WORDS or len(answer) > MAX_ANSWER_CHARS:
        raise Refused("an answer over 60 words")
    if looks_like_instruction(answer):
        raise Refused("an answer that reads as an instruction")
    return answer


def _weight(raw: RawReply, text: str) -> float:
    if raw.number is not None:
        if 0 <= raw.number <= 10 and any(abs(raw.number - n) < 1e-9 for n in numbers_written(text)):
            weight = answer_weight(None, raw.number)
            assert weight is not None
            return weight
        if raw.level is None:
            raise Refused("a number the reply does not state")
    # The classifier's level; or, when it left one out, the one level the reply itself names ("should be low"). The
    # live run showed a correction naming its question but no level (16 Nov 2026). Two levels named: never a guess.
    level = _level(raw.level) if raw.level is not None else level_written(text)
    if level is None:
        raise Refused("an answer with no level or number")
    weight = answer_weight(level, None)
    assert weight is not None
    return weight


def _has_weight(raw: RawReply, text: str) -> bool:
    try:
        _weight(raw, text)
    except Refused:
        return False
    return True


def level_written(text: str) -> RoughLevel | None:
    """The one rough level the reply names as a word of its own ("low", "medium", "high", "good enough"), or None.

    None as well when two levels are named, or the one named is negated ("not high"): never a guess.
    """
    found = [(level, m) for level in RoughLevel for m in _LEVEL_WORDS[level].finditer(text)]
    if len({level for level, _ in found}) != 1 or any(_NEGATED.search(text[: m.start()]) for _, m in found):
        return None
    return found[0][0]


def _level(raw: str | None) -> RoughLevel | None:
    if raw is None:
        return None
    key = re.sub(r"[\s-]+", "_", raw.strip().lower())
    return next((level for level in RoughLevel if level.value == key), None)


def _question(raw: str | None) -> str:
    question = " ".join((raw or "").split())
    if not question:
        raise Refused("a question with no question")
    if len(question) > MAX_QUESTION_CHARS:
        raise Refused("a question too long to keep")
    if _ORDER.search(question):
        raise Refused("a question that reads as an instruction")
    return question


def _corrected(raw: str | None, state: InterviewState, pending: AskedQuestion) -> str:
    """The id of the question a correction changes: an earlier, settled one, or the pending one. Never a guess."""
    name = (raw or "").strip().lower()
    if not name:
        raise Refused("a correction that names no question")
    wanted = name if name.startswith("q_") else question_id(name)
    if wanted == pending.question.id:
        return wanted
    settled = {e.question.id for e in state.history if e.status in ("answered", "skipped")}
    if wanted not in settled:
        raise Refused("a correction of a question not asked yet")
    return wanted
