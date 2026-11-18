"""Stage 2, criteria (US-03, DR-07): category and use in, a CriteriaSet out.

Saved criteria come first, so each category and use case is generated once. Otherwise the LLM writes them; if
fewer than 6 usable criteria come back it is asked once more, and then the product family's fixed set is used.
The interview always gets valid criteria, even with no LLM at all.

Definitions are shown to the shopper (US-07), so they must be plain (S4-22, P-25): grade 9 or under, with no other
technical word. Those that are not go back once, in one batched call on the same task, data class and route, inside
this stage's time limit. A rewrite that is plain replaces its definition; otherwise the original is kept, and the
interview uses the word's reviewed plain explanation instead. Then the set is saved.
"""

from __future__ import annotations

import logging
import time

from pydantic import ValidationError

from app.contracts import (
    CriteriaInput,
    CriteriaSet,
    DataClass,
    Difficulty,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    StageContext,
    StageName,
)
from app.contracts.common import STAGE_TIME_LIMITS_S
from app.interview.terms import is_plain_definition, jargon_in, label_terms
from app.llm.usage import TokenMeter

from .fallback import fallback_set
from .parsing import RawCriteria, plainer_definitions, to_criteria_set, with_definitions
from .prompts import PLAIN_PROMPT, SYSTEM_PROMPT, brief_data_class, plain_message, user_message
from .store import CriteriaStore, InMemoryCriteriaStore

log = logging.getLogger(__name__)

LLM_ATTEMPTS = 2  # the first answer, plus one retry when fewer than 6 usable criteria come back (US-03)
# Room for the whole answer. Reasoning models (gpt-oss on Groq) spend part of it thinking first: at the wrapper's
# 1,024-token default the JSON was cut off, so every request with a use case fell back (Sprint 1 review check).
CRITERIA_MAX_OUTPUT_TOKENS = 4096
STAGE_LIMIT_S = STAGE_TIME_LIMITS_S[StageName.CRITERIA]
# The re-ask (S4-22) keeps this much of the stage's limit back for the runner's start, parsing and saving, as the
# interview's replies do, and is sent only with at least REASK_MIN_S left after that: else the set is kept as it is.
HEADROOM_S = 1.0
REASK_MIN_S = 3.0


class CriteriaStage:
    """Generates category- and use-specific criteria. `llm=None` means no LLM is configured: fallback sets only."""

    name = StageName.CRITERIA
    is_placeholder = False

    def __init__(self, llm: LLMClient | None = None, store: CriteriaStore | None = None) -> None:
        self.llm = llm
        self.store: CriteriaStore = store if store is not None else InMemoryCriteriaStore()

    def run(self, inp: CriteriaInput, ctx: StageContext) -> CriteriaSet:
        started = time.monotonic()  # the runner's clock for this stage is already running
        saved = self.store.get(inp.category, inp.use_case)
        if saved is not None:
            return CriteriaSet.model_validate({**saved.model_dump(), "use_case": inp.use_case, "source": "cached"})
        if self.llm is None:
            return fallback_set(inp)

        search_id = ctx.search_id if ctx.search_id.startswith("srch_") else None
        data_class = brief_data_class(inp)  # public with no use case, so cheaper providers may answer (US-46)
        for attempt in range(LLM_ATTEMPTS):
            try:
                raw = self.llm.complete(
                    LLMTask.GENERATE_CRITERIA,
                    SYSTEM_PROMPT,
                    user_message(inp, retry=attempt > 0),
                    RawCriteria,
                    data_class=data_class,
                    difficulty=Difficulty.SHORT,
                    search_id=search_id,
                    max_output_tokens=CRITERIA_MAX_OUTPUT_TOKENS,
                )
            except LLMUnavailable:
                log.info("criteria: no LLM provider answered; using the %s fallback set", inp.category)
                return fallback_set(inp)
            generated = to_criteria_set(raw, inp)
            if generated is not None:
                generated = self._plainer(generated, inp, data_class, search_id, started)
                self.store.put(generated)
                return generated
        log.info("criteria: still fewer than 6 usable criteria after a retry; using the fallback set")
        return fallback_set(inp)

    def _plainer(
        self, generated: CriteriaSet, inp: CriteriaInput, data_class: DataClass, search_id: str | None, started: float
    ) -> CriteriaSet:
        """The set, with definitions that were not plain rewritten once, all in one call (P-25 item 3)."""
        hard = [c for c in generated.criteria if not is_plain_definition(c)]
        if not hard or self.llm is None:
            return generated
        spent = time.monotonic() - started + HEADROOM_S
        if STAGE_LIMIT_S - spent < REASK_MIN_S:
            log.info("criteria: no time left to ask again for plain words; %d definitions stay", len(hard))
            return generated
        asked = [(c, jargon_in(c.definition, label_terms(c))) for c in hard]
        try:
            # The wrapper's deadline is the stage's limit less what is already spent and the headroom, so this call
            # can never run past the runner's limit and cost the criteria already made (build rule 9).
            with TokenMeter(already_spent_s=spent).counting():
                raw = self.llm.complete(
                    LLMTask.GENERATE_CRITERIA,
                    PLAIN_PROMPT,
                    plain_message(inp, asked),
                    RawCriteria,
                    data_class=data_class,
                    difficulty=Difficulty.SHORT,
                    search_id=search_id,
                    max_output_tokens=CRITERIA_MAX_OUTPUT_TOKENS,
                )
        except LLMUnavailable:
            log.info("criteria: no answer to the plain-words re-ask; %d definitions stay as they were", len(hard))
            return generated
        try:
            new = plainer_definitions(raw, hard)
            plainer = with_definitions(generated, new) if new else generated
        except ValidationError as exc:  # never lose a good set to its rewrite
            log.warning("criteria: the plain-words rewrite did not fit the set (%d errors); kept", exc.error_count())
            return generated
        log.info("criteria: %d of %d definitions rewritten in plain words", len(new), len(hard))
        return plainer

    def fallback(self, inp: CriteriaInput, ctx: StageContext, error: Exception) -> CriteriaSet:
        return fallback_set(inp)
