"""Stage 1, intake (US-01, US-02): the user's request in, a validated Intent out.

The LLM reads the request first; if every provider fails, the keyword rules layer answers instead, so a search
never stops at intake. Either way code owns the budget, and a category is never guessed: an unclear request
gets one question, and an unclear reply to that question gets example requests (US-01).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from pydantic import ValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from app.contracts import (
    DataClass,
    Difficulty,
    IntakeInput,
    Intent,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    StageContext,
    StageName,
)

from .parsing import RawIntent, to_intent
from .prompts import SYSTEM_PROMPT, user_message
from .rules import detect_by_rules, no_category_intent

log = logging.getLogger(__name__)

# Shown when the reply to the follow-up question is still unclear (US-01). The frontend has its own copy.
EXAMPLE_REQUESTS = [
    "gaming laptop under 80k",
    "earbuds for the gym under ₹2,000",
    "fridge between 20k and 30k for a family of four",
    "pram for a newborn",
]

# Intent.raw_query must hold text, so the answer to blank text carries this. It is never saved or sent to an LLM.
BLANK_REQUEST = "(nothing typed)"

# What a shopper's edit may change (US-02). Everything else stays as intake set it, so an edit can never leave the
# Intent disagreeing with itself. Code decides this; no LLM is asked.
EDITABLE_FIELDS = frozenset({"use_case", "budget", "region"})
EDIT_REFUSED = "intent_edit_refused"  # the error type of a refused edit; its message is a sentence to show as it is
PRODUCT_EDIT_REFUSED = "To change the product, start a new request."
RULES_EDIT_REFUSED = "Must-haves and leave-outs can't be changed here yet. Start a new request with the change instead."
_REFUSALS = {
    "category": PRODUCT_EDIT_REFUSED,
    "category_label": PRODUCT_EDIT_REFUSED,
    "hard_rules": RULES_EDIT_REFUSED,
    "exclusions": RULES_EDIT_REFUSED,
}


class IntakeStage:
    """Detects category, use, budget and rules. `llm=None` means no LLM is configured: rules only."""

    name = StageName.INTAKE
    is_placeholder = False

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm

    def run(self, inp: IntakeInput, ctx: StageContext) -> Intent:
        text = detection_text(inp)
        if not any(ch.isalpha() for ch in text):
            # "???" or "123": nothing to read, so ask, and spend no LLM call on it.
            intent = no_category_intent(inp.raw_query, text, "rules")
        else:
            intent = self._detect(text, inp.raw_query, ctx)
        return apply_clarification_rules(intent, inp)

    def fallback(self, inp: IntakeInput, ctx: StageContext, error: Exception) -> Intent:
        return apply_clarification_rules(detect_by_rules(detection_text(inp), inp.raw_query), inp)

    def _detect(self, text: str, raw_query: str, ctx: StageContext) -> Intent:
        if self.llm is None:
            return detect_by_rules(text, raw_query)
        search_id = ctx.search_id if ctx.search_id.startswith("srch_") else None
        try:
            # The shopper's own words: private, so only providers that never train on inputs see them (US-46).
            raw = self.llm.complete(
                LLMTask.DETECT_INTENT,
                SYSTEM_PROMPT,
                user_message(text),
                RawIntent,
                data_class=DataClass.PRIVATE,
                difficulty=Difficulty.SHORT,
                search_id=search_id,
            )
        except LLMUnavailable:
            log.info("intake: no LLM provider answered; using the keyword rules")
            return detect_by_rules(text, raw_query)
        return to_intent(raw, text, raw_query)


def blank_request_intent(unclear_attempts: int = 0) -> Intent:
    """Blank text has nothing to read: ask what kind of product, as for "???", and spend no LLM call (US-01)."""
    inp = IntakeInput(raw_query=BLANK_REQUEST, unclear_attempts=unclear_attempts)
    return apply_clarification_rules(no_category_intent(inp.raw_query, "", "rules"), inp)


def detection_text(inp: IntakeInput) -> str:
    """The request plus the reply to our follow-up question, if there was one."""
    if inp.previous_clarification_reply:
        return f"{inp.raw_query}\n{inp.previous_clarification_reply}"
    return inp.raw_query


def apply_clarification_rules(intent: Intent, inp: IntakeInput) -> Intent:
    """US-01: if the reply to our question is still unclear, show example requests; never guess a category."""
    if intent.needs_clarification and inp.unclear_attempts >= 1:
        return Intent.model_validate(
            {
                **intent.model_dump(),
                "category": None,
                "category_label": None,
                "clarification_question": None,
                "clarification_options": [],
                "show_example_requests": True,
            }
        )
    return intent


def revise_intent(intent: Intent, changes: Mapping[str, object]) -> Intent:
    """Apply a user's edits to the detected intent, with full validation (US-05 exposes this over HTTP).

    Only EDITABLE_FIELDS may change. A new product needs a new request, because the criteria and the interview
    depend on it; must-haves and leave-outs wait for US-09 and US-10. Repeating a current value is not a change.

    Raises pydantic.ValidationError if the edit is refused (error type EDIT_REFUSED) or breaks a contract rule.
    """
    for field in Intent.model_fields:
        if field in changes and field not in EDITABLE_FIELDS and _is_a_change(intent, field, changes[field]):
            raise _refused(field)
    return Intent.model_validate({**intent.model_dump(), **changes})


def _is_a_change(intent: Intent, field: str, value: object) -> bool:
    """Compare after the contract has cleaned the value (spaces, left-out defaults), so a repeat is never refused."""
    try:
        repeat = Intent.model_validate({**intent.model_dump(), field: value})
    except ValidationError:
        return True  # not even valid, so it can't be the current value
    return bool(getattr(repeat, field) != getattr(intent, field))


def _refused(field: str) -> ValidationError:
    """A ValidationError, like any other bad edit, so a caller that misses the type still answers with a 422."""
    message = _REFUSALS.get(
        field, f"That change doesn't work: {field} - only the use, budget and region can be edited."
    )
    error: InitErrorDetails = {"type": PydanticCustomError(EDIT_REFUSED, message), "loc": (field,), "input": None}
    return ValidationError.from_exception_data("Intent", [error])
