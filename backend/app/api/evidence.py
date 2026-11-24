"""The sources behind one value (US-21): what GET /api/search/runs/{search_id}/evidence answers.

A finished result cites claims by ID, and SearchResult has no place for the claims themselves. So a search run wraps
the extract stage in a ClaimRecorder, and once the run ends it keeps, in memory, only the claims its values cite
(`cited_claims`). Like the run's result, they are served for at most SEARCH_RUN_KEEP_MINUTES (120 at most) after it
ended, even while a progress stream reads the run, go with the run and are never saved. `evidence_for` builds one
value's EvidenceResponse from the two.

A saved result (US-29) keeps what each cited claim said and when in its citations (1.11.0), with no Reddit words
(D-53): `saved_evidence_for` builds the same EvidenceResponse from the saved row alone, for
GET /api/me/searches/{id}/evidence. A Reddit claim comes with its source and time; its quote is withheld and the
response says so, and a text value it gave is the marker. A citation saved before 1.11.0 can't say what its claim
said: such a value's evidence answers 404 with SAVED_BEFORE, and the saved result itself still opens.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from app.contracts import (
    Citation,
    Claim,
    ClaimEvidence,
    EvidenceResponse,
    EvidenceSource,
    ExtractInput,
    ExtractionResult,
    ResolvedValue,
    SearchResult,
    Source,
    Stage,
    StageContext,
    StageName,
    ValueState,
)

log = logging.getLogger(__name__)

NO_PRODUCT = "This search didn't keep that product."
NO_VALUE = "That product has no value for that criterion."
SOURCES_GONE = "The sources for this value are no longer kept."
QUOTE_WITHHELD = "Some of these sources' words are not kept."
SAVED_BEFORE = "This search was saved before its sources' details were kept, so only its quotes can be shown."

MAX_CLAIMS = 200  # what one EvidenceResponse may hold
MAX_NAME_CHARS = 120  # EvidenceSource.name


class EvidenceUnavailable(LookupError):
    """Why a value's sources can't be shown: an HTTP status and one plain sentence."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- keeping the claims -------------------------------------------------------------------------


class ClaimRecorder:
    """Wraps one run's extract stage and keeps every claim it hands back, from its run or its fallback.

    Sources are read at once on several threads, so the claims are kept under a lock. Anything else the runner asks
    the stage, such as its coverage note or `ask_for_specs`, goes to the stage itself.
    """

    def __init__(self, inner: Stage[ExtractInput, ExtractionResult]) -> None:
        self.inner = inner
        self.name: StageName = inner.name
        self.is_placeholder = bool(getattr(inner, "is_placeholder", False))
        self._lock = threading.Lock()
        self._claims: dict[str, Claim] = {}

    def run(self, inp: ExtractInput, ctx: StageContext) -> ExtractionResult:
        return self._keep(self.inner.run(inp, ctx))

    def fallback(self, inp: ExtractInput, ctx: StageContext, error: Exception) -> ExtractionResult:
        return self._keep(self.inner.fallback(inp, ctx, error))

    def claims(self) -> dict[str, Claim]:
        """Every claim kept so far, by ID. A claim's ID comes from its source and its words: one ID is one claim."""
        with self._lock:
            return dict(self._claims)

    def keep_claims(self, claims: Sequence[Claim]) -> None:
        """Claims made after extract, such as enrich's prices from store listings (US-41): their sources open too."""
        with self._lock:
            for claim in claims:
                self._claims[claim.id] = claim

    def _keep(self, result: ExtractionResult) -> ExtractionResult:
        with self._lock:
            for claim in result.claims:
                self._claims[claim.id] = claim
        return result

    def __getattr__(self, name: str) -> Any:
        if name == "inner":  # not set yet (a copy being built): never look for it on itself
            raise AttributeError(name)
        return getattr(self.inner, name)


def cited_claim_ids(result: SearchResult) -> list[str]:
    """Every claim a value in the result cites: supporting, conflicting and each alternative's, once each."""
    return list(
        dict.fromkeys(
            claim_id
            for summary in result.products
            for value in summary.values
            for claim_id in (
                *value.supporting_claim_ids,
                *value.conflicting_claim_ids,
                *(i for alternative in value.alternatives for i in alternative.claim_ids),
            )
        )
    )


def cited_claims(result: SearchResult, recorded: Mapping[str, Claim]) -> dict[str, Claim]:
    """Only the recorded claims the result's values cite. The rest are dropped as soon as the run ends."""
    return {claim_id: recorded[claim_id] for claim_id in cited_claim_ids(result) if claim_id in recorded}


# --- one value's evidence -----------------------------------------------------------------------


def evidence_for(
    result: SearchResult, claims: Mapping[str, Claim], product_id: str, criterion_key: str
) -> EvidenceResponse:
    """The claims behind one value, each with its source. Raises EvidenceUnavailable (404) when it can't be shown.

    Supporting claims come first, then conflicting ones: a disputed value shows both sides, an estimated one only the
    claims that conflicted (it has none of its own), an unknown one none. A claim that was not kept, or whose source
    is not among the result's, is left out; if that leaves too little to answer validly, the answer is 404.

    Reddit quotes are served here although the saved result drops them (app/research/retention.py): these claims live
    only in the run's memory, which keeps a run for at most SEARCH_RUN_KEEP_MINUTES (120 at most) after it ends, even
    while a progress stream reads it, far less than the 24 hours D-53 allows. They are never saved.
    """
    summary = next((p for p in result.products if p.product.id == product_id), None)
    if summary is None:
        raise EvidenceUnavailable(404, NO_PRODUCT)
    value = next((v for v in summary.values if v.criterion_key == criterion_key), None)
    if value is None:
        raise EvidenceUnavailable(404, NO_VALUE)

    supporting, conflicting = _sides(value)
    sources = {source.id: source for source in result.sources}
    winning = _shown(supporting, claims, sources)
    others = _shown([i for i in conflicting if i not in supporting], claims, sources)
    return _response(result, product_id, criterion_key, value, winning, others)


def saved_evidence_for(result: SearchResult, product_id: str, criterion_key: str) -> EvidenceResponse:
    """The claims behind one value of a saved result, from its citations and sources alone (US-29). The same answer,
    claim for claim, as `evidence_for` gave while the run was in memory, but for the words D-53 never lets a saved row
    keep: a withheld quote (the response says why) and a text value from Reddit or a video's comments (the marker).

    Raises EvidenceUnavailable (404): NO_PRODUCT or NO_VALUE as the run route; SAVED_BEFORE when a cited claim's
    reading was not saved (a row from before 1.11.0); SOURCES_GONE when too little is left to answer validly."""
    summary = next((p for p in result.products if p.product.id == product_id), None)
    if summary is None:
        raise EvidenceUnavailable(404, NO_PRODUCT)
    value = next((v for v in summary.values if v.criterion_key == criterion_key), None)
    if value is None:
        raise EvidenceUnavailable(404, NO_VALUE)
    supporting, conflicting = _sides(value)
    citations = {citation.claim_id: citation for citation in result.citations}
    wanted = [*supporting, *(i for i in conflicting if i not in supporting)]
    if wanted and (not citations or any(i in citations and not _read(citations[i]) for i in wanted)):
        raise EvidenceUnavailable(404, SAVED_BEFORE)  # citations came in 1.7.0, what they said in 1.11.0
    sources = {source.id: source for source in result.sources}
    winning = _cited(supporting, citations, sources)
    others = _cited([i for i in conflicting if i not in supporting], citations, sources)
    return _response(result, product_id, criterion_key, value, winning, others)


def _read(citation: Citation) -> bool:
    """Was what the claim said saved with its citation (1.11.0)?"""
    has_reading = (citation.value is None) != (citation.score is None)
    return has_reading and citation.polarity is not None and citation.claim_type is not None


def _cited(
    claim_ids: Sequence[str], citations: Mapping[str, Citation], sources: Mapping[str, Source]
) -> list[ClaimEvidence]:
    shown: list[ClaimEvidence] = []
    for claim_id in dict.fromkeys(claim_ids):
        citation = citations.get(claim_id)
        source = sources.get(citation.source_id) if citation is not None else None
        if citation is None or source is None:
            continue
        try:
            shown.append(_citation_evidence(citation, source))
        except ValidationError as exc:  # left out, like a claim that was not kept
            log.warning("a saved claim was left out of the evidence: %s", type(exc).__name__)
    return shown


def _citation_evidence(citation: Citation, source: Source) -> ClaimEvidence:
    return ClaimEvidence(
        claim_id=citation.claim_id,
        quote=citation.quote,
        value=citation.value,
        unit=citation.unit,
        score=citation.score,
        polarity=citation.polarity,
        claim_type=citation.claim_type,
        retrieved_at=citation.retrieved_at or source.fetched_at,
        source=_evidence_source(source),
    )


def _response(
    result: SearchResult,
    product_id: str,
    criterion_key: str,
    value: ResolvedValue,
    winning: list[ClaimEvidence],
    others: list[ClaimEvidence],
) -> EvidenceResponse:
    """The answer, from one value's two sides, as both routes give it."""
    winning, others = _within_limit(winning, others)
    if value.state in (ValueState.KNOWN, ValueState.DISPUTED) and not winning:
        raise EvidenceUnavailable(404, SOURCES_GONE)
    if value.state is ValueState.DISPUTED and not others:
        raise EvidenceUnavailable(404, SOURCES_GONE)
    shown = [*winning, *others]
    withheld = any(claim.quote is None for claim in shown)
    try:
        return EvidenceResponse(
            search_id=result.search_id,
            product_id=product_id,
            criterion_key=criterion_key,
            state=value.state,
            winning_claim_ids=[claim.claim_id for claim in winning],
            claims=shown,
            quote_withheld_reason=QUOTE_WITHHELD if withheld else None,
        )
    except ValidationError as exc:  # never a 500: the value just can't be shown
        log.warning("evidence for search %s not shown: %s", result.search_id, type(exc).__name__)
        raise EvidenceUnavailable(404, SOURCES_GONE) from None


def _sides(value: ResolvedValue) -> tuple[list[str], list[str]]:
    """The claim IDs on each side: those the value rests on, and those that disagree."""
    if value.state is ValueState.UNKNOWN:
        return [], []
    if value.state is ValueState.ESTIMATED:  # a peer average has no claims of its own
        return [], list(value.conflicting_claim_ids)
    return list(value.supporting_claim_ids), list(value.conflicting_claim_ids)


def _shown(claim_ids: Sequence[str], claims: Mapping[str, Claim], sources: Mapping[str, Source]) -> list[ClaimEvidence]:
    shown: list[ClaimEvidence] = []
    for claim_id in dict.fromkeys(claim_ids):
        claim = claims.get(claim_id)
        source = sources.get(claim.source_id) if claim is not None else None
        if claim is None or source is None:
            continue
        try:
            shown.append(_claim_evidence(claim, source))
        except ValidationError as exc:  # left out, like a claim that was not kept
            log.warning("a claim was left out of the evidence: %s", type(exc).__name__)
    return shown


def _claim_evidence(claim: Claim, source: Source) -> ClaimEvidence:
    return ClaimEvidence(
        claim_id=claim.id,
        quote=claim.quote,
        value=claim.value,
        unit=claim.unit,
        score=claim.score,
        polarity=claim.polarity,
        claim_type=claim.claim_type,
        retrieved_at=claim.retrieved_at,
        source=_evidence_source(source),
    )


def _evidence_source(source: Source) -> EvidenceSource:
    return EvidenceSource(
        id=source.id,
        url=source.url,
        name=source_name(source),
        type=source.type,
        tier=source.tier,
        fetched_at=source.fetched_at,
    )


def source_name(source: Source) -> str:
    """The site's domain, else the community (such as "r/..."), else the address's host."""
    name = source.site or source.community or source.url.host or str(source.url)
    return name[:MAX_NAME_CHARS]


def _within_limit(
    winning: list[ClaimEvidence], others: list[ClaimEvidence]
) -> tuple[list[ClaimEvidence], list[ClaimEvidence]]:
    """At most MAX_CLAIMS in all. When both sides are long, each keeps at least half, so neither side disappears."""
    take = min(len(winning), max(MAX_CLAIMS // 2, MAX_CLAIMS - len(others)))
    return winning[:take], others[: MAX_CLAIMS - take]
