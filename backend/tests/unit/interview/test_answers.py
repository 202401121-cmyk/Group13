"""Reply understanding (US-05, US-08): the rough levels, exact weights and whole numbers; anything else is unclear."""

from __future__ import annotations

import pytest

from app.contracts import ROUGH_LEVEL_WEIGHTS, InterviewQuestion, ReplyType, RoughLevel
from app.interview.answers import QUICK_REPLIES, answer_weight, understand_reply

QUESTION = InterviewQuestion(
    id="q_battery_life",
    criterion_key="battery_life",
    text="How much do you care about battery life?",
    quick_replies=QUICK_REPLIES,
)


def test_every_question_offers_the_same_buttons() -> None:
    assert QUICK_REPLIES == ["Low", "Medium", "High", "Good enough", "Skip"]


def test_every_level_in_the_table_has_a_button() -> None:
    """Good enough is a rough answer like the others, so it sits beside them under every question (US-08)."""
    buttons = {understand_reply(button, QUESTION).weight for button in QUICK_REPLIES if button != "Skip"}
    assert buttons == set(ROUGH_LEVEL_WEIGHTS.values())


def test_every_button_but_skip_is_a_table_phrase() -> None:
    for button in QUICK_REPLIES:
        reply = understand_reply(button, QUESTION)
        expected = ReplyType.SKIP if button == "Skip" else ReplyType.ANSWER
        assert reply.reply_type is expected


@pytest.mark.parametrize("button", ["Low", "Medium", "High", "Good enough"])
def test_each_button_gives_the_weight_of_its_level(button: str) -> None:
    level = RoughLevel(button.lower().replace(" ", "_"))
    assert understand_reply(button, QUESTION).weight == ROUGH_LEVEL_WEIGHTS[level]


@pytest.mark.parametrize(
    ("level", "weight"),
    [(RoughLevel.LOW, 2.0), (RoughLevel.MEDIUM, 5.0), (RoughLevel.HIGH, 8.0), (RoughLevel.GOOD_ENOUGH, 3.0)],
)
def test_every_phrase_in_the_table_gives_its_weight(level: RoughLevel, weight: float) -> None:
    assert ROUGH_LEVEL_WEIGHTS[level] == weight
    assert answer_weight(level, None) == weight
    assert understand_reply(level.value.replace("_", " "), QUESTION).weight == weight


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("Low", RoughLevel.LOW),
        ("LOW", RoughLevel.LOW),
        ("Medium", RoughLevel.MEDIUM),
        ("  medium  ", RoughLevel.MEDIUM),
        ("HiGh", RoughLevel.HIGH),
        ("High.", RoughLevel.HIGH),
        ("good enough", RoughLevel.GOOD_ENOUGH),
        ("Good Enough", RoughLevel.GOOD_ENOUGH),
        ("GOOD ENOUGH", RoughLevel.GOOD_ENOUGH),
        ("good  enough", RoughLevel.GOOD_ENOUGH),
        ("good-enough", RoughLevel.GOOD_ENOUGH),
        ("Good-Enough", RoughLevel.GOOD_ENOUGH),
        (" good-enough ", RoughLevel.GOOD_ENOUGH),
        ("good_enough", RoughLevel.GOOD_ENOUGH),
        ("goodenough", RoughLevel.GOOD_ENOUGH),
        ("Good enough!", RoughLevel.GOOD_ENOUGH),
    ],
)
def test_a_phrase_matches_whatever_its_casing_spaces_or_hyphen(text: str, level: RoughLevel) -> None:
    reply = understand_reply(text, QUESTION)
    assert (reply.reply_type, reply.weight) == (ReplyType.ANSWER, ROUGH_LEVEL_WEIGHTS[level])


@pytest.mark.parametrize(
    "text",
    ["good enough for me", "not good enough", "very high", "medium-ish", "lowish", "good", "enough", "low medium"],
)
def test_other_free_text_keeps_todays_handling(text: str) -> None:
    assert understand_reply(text, QUESTION).reply_type is ReplyType.UNCLEAR


@pytest.mark.parametrize("level", list(RoughLevel))
def test_a_level_uses_the_table(level: RoughLevel) -> None:
    reply = understand_reply("whatever the button says", QUESTION, level=level)
    assert (reply.reply_type, reply.weight) == (ReplyType.ANSWER, ROUGH_LEVEL_WEIGHTS[level])


@pytest.mark.parametrize("weight", [0, 0.5, 7.5, 10])
def test_an_exact_weight_uses_that_number(weight: float) -> None:
    reply = understand_reply("whatever the slider says", QUESTION, exact_weight=weight)
    assert (reply.reply_type, reply.weight) == (ReplyType.ANSWER, weight)


@pytest.mark.parametrize("text", ["High", "Skip", "7", "what is this?"])
def test_the_fields_win_over_the_text(text: str) -> None:
    assert understand_reply(text, QUESTION, level=RoughLevel.LOW).weight == 2.0
    assert understand_reply(text, QUESTION, exact_weight=6.5).weight == 6.5


@pytest.mark.parametrize(
    ("text", "weight"),
    [("Low", 2.0), ("low", 2.0), ("MEDIUM", 5.0), (" medium ", 5.0), ("High", 8.0), ("high!", 8.0)],
)
def test_buttons_map_to_weights(text: str, weight: float) -> None:
    reply = understand_reply(text, QUESTION)
    assert (reply.reply_type, reply.weight, reply.classified_by) == (ReplyType.ANSWER, weight, "fallback")


@pytest.mark.parametrize("text", ["Skip", "skip", "SKIP."])
def test_skip(text: str) -> None:
    reply = understand_reply(text, QUESTION)
    assert (reply.reply_type, reply.weight) == (ReplyType.SKIP, None)


@pytest.mark.parametrize(
    ("text", "weight"),
    [("0", 0.0), ("7", 7.0), ("10", 10.0), ("7/10", 7.0), ("7 / 10", 7.0), ("10/10", 10.0), ("03", 3.0)],
)
def test_whole_numbers_from_0_to_10(text: str, weight: float) -> None:
    reply = understand_reply(text, QUESTION)
    assert (reply.reply_type, reply.weight) == (ReplyType.ANSWER, weight)


@pytest.mark.parametrize(
    "text",
    ["11", "-1", "7.5", "7/5", "seven", "very important", "what is battery life?", "", "   ", "lowish"],
)
def test_anything_else_is_unclear(text: str) -> None:
    reply = understand_reply(text, QUESTION)
    assert (reply.reply_type, reply.weight) == (ReplyType.UNCLEAR, None)
    assert reply.classified_by == "fallback"
