"""Criterion titles keep their own case in interview questions (US-05, D-44).

The titles come from shared/fixtures/criterion_titles.json, which the frontend's tests read too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contracts import CriteriaSet, Criterion, InterviewState, InterviewStep, InterviewTurn, StageContext
from app.contracts import examples as ex
from app.interview import InterviewStage
from app.interview.questions import question_for

FIXTURE = Path(__file__).resolve().parents[4] / "shared" / "fixtures" / "criterion_titles.json"
TITLES: list[str] = json.loads(FIXTURE.read_text(encoding="utf-8"))
CTX = StageContext(search_id="srch_titles0001")


def question_text(title: str) -> str:
    return f"{title}: how much does it matter to you?"


def criterion(title: str, key: str = "x_criterion", vfm: bool = False) -> Criterion:
    return Criterion(
        key=key,
        label=title,
        definition="How well the product does on this, in daily use.",
        is_value_for_money=vfm,
    )


def state_with(titles: list[str]) -> InterviewState:
    last = len(titles) - 1
    criteria = CriteriaSet(
        category="laptop",
        source="generated",
        criteria=[criterion(t, f"criterion_{i}", vfm=i == last) for i, t in enumerate(titles)],
    )
    return InterviewState(session_id=ex.SESSION_ID, intent=ex.intent(), criteria=criteria)


def test_the_fixture_lists_the_titles_the_team_named() -> None:
    assert len(TITLES) == len(set(TITLES))
    assert set(TITLES) >= {
        "USB-C/Thunderbolt ports",
        "Battery life",
        "Bluetooth version",
        "MacBook compatibility",
        "iPhone compatibility",
        "Wi-Fi 7 support",
        "RAM (GB)",
        "Value for money",
    }


@pytest.mark.parametrize("title", TITLES)
def test_a_question_holds_the_title_character_for_character(title: str) -> None:
    text = question_for(criterion(title)).text
    assert title in text
    assert text == question_text(title)


@pytest.mark.parametrize("batch", [TITLES[:8], TITLES[-8:]], ids=["first-8", "last-8"])
def test_every_question_of_a_whole_interview_holds_its_title(batch: list[str]) -> None:
    stage = InterviewStage()
    step: InterviewStep = stage.run(InterviewTurn(state=state_with(batch)), CTX)
    asked: list[str] = []
    while step.kind != "finished":
        asked.append(step.message)
        step = stage.run(InterviewTurn(state=step.state, reply_text="Medium"), CTX)
    assert sorted(asked) == sorted(question_text(t) for t in batch)


def test_a_question_asked_again_after_going_back_keeps_its_title() -> None:
    stage = InterviewStage()
    first = stage.run(InterviewTurn(state=state_with(TITLES[:8])), CTX)
    assert first.question is not None
    second = stage.run(InterviewTurn(state=first.state, reply_text="High"), CTX)
    assert stage.go_back(second.state, first.question.id).message == first.message


def test_the_fallback_path_keeps_the_title_too() -> None:
    step = InterviewStage().fallback(InterviewTurn(state=state_with(TITLES[:8])), CTX, RuntimeError("forced"))
    assert step.message == question_text(TITLES[0])
