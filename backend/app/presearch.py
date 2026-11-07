"""The pre-search flow (US-52): intake -> criteria -> interview, one call per turn, ending in a Rubric.

Unlike the search pipeline, these run turn by turn while the user chats, so they are plain functions.
Each still goes through `run_stage`, so it gets its time limit and falls back instead of crashing.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TypeVar

from app.contracts import (
    STAGE_TIME_LIMITS_S,
    CriteriaInput,
    CriteriaSet,
    IntakeInput,
    Intent,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    LLMClient,
    Stage,
    StageContext,
)
from app.criteria import CriteriaStage, CriteriaStore, FileCriteriaStore
from app.intake import IntakeStage, blank_request_intent
from app.interview import InterviewStage
from app.llm import build_llm_client
from app.pipeline import run_stage

In = TypeVar("In")
Out = TypeVar("Out")


@lru_cache(maxsize=1)
def default_llm() -> LLMClient:
    """The real LLM client, built once. With no API keys it has no providers, so intake uses its rules layer.

    Tests replace this with a fake (tests/conftest.py), so no test can reach a real provider.
    """
    return build_llm_client()


@lru_cache(maxsize=1)
def default_criteria_store() -> CriteriaStore:
    """Saved criteria in backend/.cache/criteria/ (US-03). Tests use an in-memory store instead (tests/conftest.py)."""
    return FileCriteriaStore()


def start_request(
    query: str,
    previous_reply: str | None = None,
    unclear_attempts: int = 0,
    llm: LLMClient | None = None,
) -> Intent:
    """Understand the user's request, or their reply to our follow-up question (US-01, US-02).

    Blank text gets the follow-up question here: IntakeInput refuses it, so it must never reach the stage.
    """
    if not query.strip():
        return blank_request_intent(unclear_attempts)
    inp = IntakeInput(raw_query=query, previous_clarification_reply=previous_reply, unclear_attempts=unclear_attempts)
    return _run(IntakeStage(llm if llm is not None else default_llm()), inp)


def make_criteria(intent: Intent, llm: LLMClient | None = None, store: CriteriaStore | None = None) -> CriteriaSet:
    """Criteria for the detected category. Only call once the intent is ready for research."""
    if intent.category is None or intent.category_label is None:
        raise ValueError("the request has no product category yet; ask the clarification question first")
    inp = CriteriaInput(category=intent.category, category_label=intent.category_label, use_case=intent.use_case)
    stage = CriteriaStage(
        llm if llm is not None else default_llm(), store if store is not None else default_criteria_store()
    )
    return _run(stage, inp)


def interview_turn(turn: InterviewTurn) -> InterviewStep:
    """One interview turn: the user's reply (None on the first turn) in, the next step out (US-05)."""
    return _run(InterviewStage(), turn)


def interview_back(state: InterviewState, question_id: str) -> InterviewStep:
    """Ask an earlier question again so the user can change the answer (US-05).

    Raises ValueError if that question has not been answered yet.
    """
    return InterviewStage().go_back(state, question_id)


def _run(stage: Stage[In, Out], inp: In) -> Out:
    ctx = StageContext(search_id="presearch", time_limit_s=STAGE_TIME_LIMITS_S[stage.name])
    out, report = run_stage(stage, inp, ctx, STAGE_TIME_LIMITS_S[stage.name])
    if out is None:
        raise RuntimeError(f"{stage.name} failed and so did its fallback: {report.fallback_reason}")
    return out
