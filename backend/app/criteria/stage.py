"""Stage 2, criteria (US-03, DR-07): category and use in, a CriteriaSet out.

Saved criteria come first, so each category and use case is generated once. Otherwise the LLM writes them; if
fewer than 6 usable criteria come back it is asked once more, and then the product family's fixed set is used.
The interview always gets valid criteria, even with no LLM at all.
"""

from __future__ import annotations

import logging

from app.contracts import (
    CriteriaInput,
    CriteriaSet,
    Difficulty,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    StageContext,
    StageName,
)

from .fallback import fallback_set
from .parsing import RawCriteria, to_criteria_set
from .prompts import SYSTEM_PROMPT, brief_data_class, user_message
from .store import CriteriaStore, InMemoryCriteriaStore

log = logging.getLogger(__name__)

LLM_ATTEMPTS = 2  # the first answer, plus one retry when fewer than 6 usable criteria come back (US-03)
# Room for the whole answer. Reasoning models (gpt-oss on Groq) spend part of it thinking first: at the wrapper's
# 1,024-token default the JSON was cut off, so every request with a use case fell back (Sprint 1 review check).
CRITERIA_MAX_OUTPUT_TOKENS = 4096


class CriteriaStage:
    """Generates category- and use-specific criteria. `llm=None` means no LLM is configured: fallback sets only."""

    name = StageName.CRITERIA
    is_placeholder = False

    def __init__(self, llm: LLMClient | None = None, store: CriteriaStore | None = None) -> None:
        self.llm = llm
        self.store: CriteriaStore = store if store is not None else InMemoryCriteriaStore()

    def run(self, inp: CriteriaInput, ctx: StageContext) -> CriteriaSet:
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
                self.store.put(generated)
                return generated
        log.info("criteria: still fewer than 6 usable criteria after a retry; using the fallback set")
        return fallback_set(inp)

    def fallback(self, inp: CriteriaInput, ctx: StageContext, error: Exception) -> CriteriaSet:
        return fallback_set(inp)
