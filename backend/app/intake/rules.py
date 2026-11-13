"""The keyword rules layer (US-02): category and budget with no LLM at all.

Used whenever every LLM provider fails, and as the stage's fallback. It promises the category, the budget, the
region, and the exclusions typed in words ("no Xiaomi", US-10) through the same parser the rubric editor uses; use case
and hard rules need the LLM. It never guesses a category: an ambiguous word or a request with no product gets one clear
question instead (US-01).
"""

from __future__ import annotations

from typing import Literal

from app.contracts import Budget, Intent

from .budget import parse_budget, region_for
from .exclusions import parse_exclusions
from .lexicon import Lexicon, load_lexicon

NO_CATEGORY_QUESTION = "What kind of product are you looking for?"
MAX_OPTIONS = 4

DetectedBy = Literal["llm", "rules"]


def detect_by_rules(text: str, raw_query: str | None = None, lexicon: Lexicon | None = None) -> Intent:
    """Category from the lexicon, budget and region from code. `raw_query` is what the user typed first."""
    lexicon = lexicon or load_lexicon()
    query = raw_query or text
    scan = lexicon.scan(text)
    budget, region = parse_budget(text), region_for(text)
    if scan.category:
        return Intent(
            raw_query=query,
            category=scan.category,
            category_label=lexicon.label(scan.category),
            budget=budget,
            region=region,
            exclusions=parse_exclusions(text, require_trigger=True).exclusions,
            detected_by="rules",
        )
    if scan.ambiguous:
        options = lexicon.labels(scan.ambiguous.options)
        return clarifying_intent(query, scan.ambiguous.question, options, budget, region, "rules")
    return no_category_intent(query, text, "rules", lexicon)


def no_category_intent(raw_query: str, text: str, detected_by: DetectedBy, lexicon: Lexicon | None = None) -> Intent:
    """The request names no product: ask what kind, offering a few general categories."""
    lexicon = lexicon or load_lexicon()
    options = lexicon.labels(lexicon.data.suggestions)
    return clarifying_intent(
        raw_query, NO_CATEGORY_QUESTION, options, parse_budget(text), region_for(text), detected_by
    )


def clarifying_intent(
    raw_query: str,
    question: str,
    options: list[str],
    budget: Budget | None,
    region: str,
    detected_by: DetectedBy,
) -> Intent:
    """An Intent that asks one question before research. A budget already given is kept, so it is not asked twice."""
    return Intent(
        raw_query=raw_query,
        budget=budget,
        region=region,
        needs_clarification=True,
        clarification_question=question,
        clarification_options=options[:MAX_OPTIONS],
        detected_by=detected_by,
    )
