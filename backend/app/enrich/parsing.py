"""What the fact look-up's one batched call may answer (US-41): per page, claims in extraction's own shape (US-14).

A reply that doesn't fit is invalid output: the wrapper retries it once, then nothing is added. Nothing is repaired.
Each page's claims then go through extraction's own parser (app/extract/parsing.py, US-40's rule): a quote not in that
page word for word, or a product the page doesn't name, is dropped there.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.enrich import MAX_ENRICH_SEARCHES
from app.extract.parsing import RawClaim, RawExtraction

MAX_PAGES = MAX_ENRICH_SEARCHES  # one page for each fact search, at most
MAX_PAGE_CLAIMS = 10


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RawPageFacts(_Strict):
    page: int = Field(ge=1, le=MAX_PAGES)
    claims: list[RawClaim] = Field(default_factory=list, max_length=MAX_PAGE_CLAIMS)


class RawFacts(_Strict):
    pages: list[RawPageFacts] = Field(default_factory=list, max_length=MAX_PAGES)


def page_answers(raw: RawFacts, sent: int) -> dict[int, RawExtraction]:
    """Each page's claims as extraction's own answer, by the page's number. A number that wasn't sent is left out, and
    a page answered twice keeps its first answer."""
    answers: dict[int, RawExtraction] = {}
    for page in raw.pages:
        if 1 <= page.page <= sent and page.page not in answers:
            answers[page.page] = RawExtraction(mentions=[], claims=page.claims)
    return answers
