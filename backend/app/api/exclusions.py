"""Exclusions read from the shopper's own words (US-10): POST /api/presearch/exclusions/parse.

Stateless: it reads the text and answers the exclusions and the words it could not read as one, in code with no LLM
(app/intake/exclusions.py). The rubric editor (S3-14) then adds them to the session through its own route. Text over
200 characters, or blank, answers the contract's 422.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.contracts import ExclusionParseRequest, ExclusionParseResponse
from app.intake.exclusions import parse_exclusions

router = APIRouter(prefix="/api/presearch", tags=["pre-search"])


@router.post("/exclusions/parse", response_model=ExclusionParseResponse)
def parse(body: ExclusionParseRequest) -> ExclusionParseResponse:
    """The exclusions in the text ("no Xiaomi or Realme, not touchscreen"), and anything not understood, as typed."""
    return parse_exclusions(body.text)
