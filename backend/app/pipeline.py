"""Search pipeline runner (US-52): research, extract, match, verify, enrich, decide, explain, commerce.

Every stage runs through `run_stage`, which enforces its time limit and swaps in its fallback when it fails,
so no stage can hang or crash a search (build rule 9). State stays in memory and is saved once at the end, inside
the search's time limit (D-21). `saved` is true only when the row was committed (D-27), and a save that does not
happen only sets `saved=False` (build rule 10).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Protocol, TypeVar
from uuid import uuid4

from app import db
from app.commerce import CommerceStage
from app.contracts import (
    SEARCH_TIME_LIMIT_S,
    STAGE_TIME_LIMITS_S,
    Claim,
    CommerceInput,
    CommerceResult,
    DecideInput,
    DecideResult,
    EnrichInput,
    EnrichResult,
    ExplainInput,
    ExplainResult,
    ExtractInput,
    ExtractionResult,
    MatchInput,
    MatchResult,
    ProductMention,
    ProductSummary,
    ResearchInput,
    ResearchResult,
    SearchRequest,
    SearchResult,
    SourceFailure,
    SourceType,
    Stage,
    StageContext,
    StageName,
    StageReport,
    StageStatus,
    VerifyInput,
    VerifyResult,
)
from app.decide import DecideStage
from app.enrich import EnrichStage
from app.entities import MatchStage
from app.explain import ExplainStage
from app.extract import ExtractStage
from app.research import ResearchStage
from app.verify import VerifyStage

log = logging.getLogger(__name__)

In = TypeVar("In")
Out = TypeVar("Out")

MAX_REASON_CHARS = 300  # StageReport.fallback_reason limit
MAX_NOTE_CHARS = 400  # SearchResult.coverage_note limit
MAX_MENTIONS = 1000  # MatchInput.mentions limit
MAX_CLAIMS = 5000  # VerifyInput.claims limit
MAX_VERIFY_KEYS = 30  # VerifyInput.criterion_keys limit
MAX_EXPLAINED = 10  # ExplainInput.ranked limit
MAX_PRICED = 10  # CommerceInput.products limit
TOP_PRIORITIES = 3

OUT_OF_TIME = "the search ran out of time"
EARLIER_FAILURE = "an earlier step failed"

# Worst status wins when several reports are merged into one (extract runs once per source).
_SEVERITY = {
    StageStatus.OK: 0,
    StageStatus.SKIPPED: 1,
    StageStatus.FALLBACK: 2,
    StageStatus.TIMEOUT: 3,
    StageStatus.FAILED: 4,
}

SOURCE_LABELS = {
    SourceType.REDDIT: "Reddit",
    SourceType.YOUTUBE: "YouTube",
    SourceType.WEB_SEARCH: "Web search",
    SourceType.REVIEW_SITE: "Review sites",
    SourceType.MANUFACTURER: "Maker pages",
    SourceType.LAB: "Lab tests",
    SourceType.RETAILER: "Store pages",
}
FAILURE_WORDS = {
    "timeout": "timed out",
    "rate_limited": "hit its rate limit",
    "blocked": "was blocked",
    "not_found": "found nothing",
    "quota_exhausted": "ran out of quota",
    "error": "had an error",
}


# --- storage ------------------------------------------------------------------------------------


class ResultStore(Protocol):
    """Where a finished search is saved. Returns False (or raises) when saving did not work.

    A store whose save can block also has `time_limit_s`, the longest one save may take. The runner sets that time
    aside from the search's limit. A store without it is taken to be instant.
    """

    def save(self, result: SearchResult) -> bool: ...


class InMemoryResultStore:
    """Keeps results in a list, for tests. The API and the CLI use DatabaseResultStore."""

    def __init__(self) -> None:
        self.results: list[SearchResult] = []

    def save(self, result: SearchResult) -> bool:
        self.results.append(result)
        return True


class DatabaseResultStore:
    """Saves a finished search through the database module, so `saved` tells the truth (D-27).

    save_search() answers True only when the row was committed, never raises, and gives up after DB_TIMEOUT_SECONDS.
    A database that is off, down, slow or failing answers False, and the search still finishes (build rule 10).
    """

    def __init__(self, requests: db.RequestStore, time_limit_s: float | None = None) -> None:
        self.requests = requests
        # os.environ, because the default also loads .env: default_request_store() has done that if it was needed.
        self.time_limit_s = db.load_settings(os.environ).timeout_s if time_limit_s is None else time_limit_s

    def save(self, result: SearchResult) -> bool:
        request = result.request
        # mode="json": a plain dump holds datetimes and URLs, which the database module refuses.
        return self.requests.save_search(
            request.session_id, request.rubric.model_dump(mode="json"), result.model_dump(mode="json")
        )


# --- stages -------------------------------------------------------------------------------------


@dataclass
class SearchStages:
    """The eight search stages. Tests swap one field for a failing or slow version."""

    research: Stage[ResearchInput, ResearchResult]
    extract: Stage[ExtractInput, ExtractionResult]
    match: Stage[MatchInput, MatchResult]
    verify: Stage[VerifyInput, VerifyResult]
    enrich: Stage[EnrichInput, EnrichResult]
    decide: Stage[DecideInput, DecideResult]
    explain: Stage[ExplainInput, ExplainResult]
    commerce: Stage[CommerceInput, CommerceResult]


def default_search_stages() -> SearchStages:
    return SearchStages(
        research=ResearchStage(),
        extract=ExtractStage(),
        match=MatchStage(),
        verify=VerifyStage(),
        enrich=EnrichStage(),
        decide=DecideStage(),
        explain=ExplainStage(),
        commerce=CommerceStage(),
    )


# --- running one stage --------------------------------------------------------------------------


def run_stage(stage: Stage[In, Out], inp: In, ctx: StageContext, limit_s: float) -> tuple[Out | None, StageReport]:
    """Run one stage with a time limit, falling back on any error or timeout.

    Returns (output, report). The output is None only when the fallback failed too (status FAILED).
    """
    start = monotonic()
    timeout = max(0.0, min(limit_s, ctx.seconds_left))
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"stage-{stage.name}")
    future = pool.submit(stage.run, inp, ctx)
    try:
        done, _ = wait([future], timeout=timeout)
    finally:
        # Python cannot kill a thread. A timed-out run() keeps going in the background until it returns;
        # we just stop waiting for it. Stages should respect ctx.seconds_left themselves.
        pool.shutdown(wait=False, cancel_futures=True)

    error: Exception
    if not done:
        status, reason = StageStatus.TIMEOUT, f"took longer than {timeout:g}s"
        error = TimeoutError(reason)
    else:
        exc = future.exception()
        if exc is None:
            return future.result(), _report(stage.name, StageStatus.OK, start)
        if not isinstance(exc, Exception):
            raise exc  # KeyboardInterrupt and friends are not stage failures
        status, reason, error = StageStatus.FALLBACK, _short(exc), exc

    log.warning("stage %s: %s; using its fallback", stage.name, reason)
    try:
        output = stage.fallback(inp, ctx, error)
    except Exception as fallback_exc:
        log.error("stage %s: fallback failed too: %s", stage.name, _short(fallback_exc))
        return None, _report(stage.name, StageStatus.FAILED, start, f"{reason}; the fallback failed too")
    return output, _report(stage.name, status, start, reason)


def _short(exc: BaseException) -> str:
    return (str(exc) or type(exc).__name__)[:MAX_REASON_CHARS]


def _report(stage: StageName, status: StageStatus, start: float, reason: str | None = None) -> StageReport:
    return StageReport(
        stage=stage,
        status=status,
        duration_ms=int((monotonic() - start) * 1000),
        fallback_reason=reason[:MAX_REASON_CHARS] if reason else None,
    )


def _skipped(stage: StageName, reason: str) -> StageReport:
    return StageReport(stage=stage, status=StageStatus.SKIPPED, duration_ms=0, fallback_reason=reason)


def _require(value: Out | None, what: str) -> Out:
    """The runner stops at the first FAILED stage, so a later stage always has its inputs."""
    if value is None:
        raise RuntimeError(f"{what} is missing; the runner should have stopped earlier")
    return value


# --- the runner ---------------------------------------------------------------------------------


class PipelineRunner:
    """Runs the eight search stages in order, saves the result and always returns a valid SearchResult."""

    def __init__(
        self,
        stages: SearchStages,
        store: ResultStore,
        time_limits: dict[StageName, float] | None = None,
        search_time_limit_s: float = SEARCH_TIME_LIMIT_S,
    ) -> None:
        self.stages = stages
        self.store = store
        self.time_limits = {**STAGE_TIME_LIMITS_S, **(time_limits or {})}
        self.search_time_limit_s = search_time_limit_s
        self.save_time_s: float = getattr(store, "time_limit_s", 0.0)

    def run(self, request: SearchRequest) -> SearchResult:
        # D-21: the limit covers the save too, so the stages stop early enough to leave the store its time. That way
        # a search that runs out of time still gets saved.
        stage_time_s = max(0.0, self.search_time_limit_s - self.save_time_s)
        ctx = StageContext(search_id="srch_" + uuid4().hex[:12], time_limit_s=stage_time_s)
        started_at = datetime.now(UTC)
        search = _Search(self.stages, self.time_limits, request, ctx)

        partial = False
        steps = search.steps()
        for i, (_name, step) in enumerate(steps):
            if search.out_of_time or ctx.seconds_left <= 0:
                search.reports += [_skipped(n, OUT_OF_TIME) for n, _ in steps[i:]]
                partial = True
                break
            report = step()
            search.reports.append(report)
            if report.status is StageStatus.FAILED:
                search.reports += [_skipped(n, EARLIER_FAILURE) for n, _ in steps[i + 1 :]]
                partial = True
                break

        result = search.result(started_at, partial or search.out_of_time)
        return self._save(result, self.search_time_limit_s - (monotonic() - ctx.started_at))

    def _save(self, result: SearchResult, seconds_left: float) -> SearchResult:
        """`result` says saved=True, so the stored row and the response agree. If no row was committed, say so."""
        if self._committed(result, seconds_left):
            return result
        return SearchResult.model_validate({**result.model_dump(), "saved": False})

    def _committed(self, result: SearchResult, seconds_left: float) -> bool:
        """True only when the store reports the row committed. Never raises, and never waits past the search's limit.

        The store has its own, shorter time limit (D-28). This wait is what holds the 150 seconds whatever the store
        does; like a timed-out stage, a save cut off here finishes in the background, and nobody waits for it.
        """
        if seconds_left <= 0:
            log.warning("search %s not saved: no time was left", result.search_id)
            return False
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="result-save")
        future = pool.submit(self.store.save, result)
        try:
            done, _ = wait([future], timeout=seconds_left)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if not done:
            log.warning("search %s not saved: the save ran past the time limit", result.search_id)
            return False
        try:
            return bool(future.result())
        except Exception as exc:  # only the error's type: its text could hold a connection string
            log.error("could not save search %s: %s", result.search_id, type(exc).__name__)
            return False


class _Search:
    """The in-memory state of one search: each stage's output, filled in as the stages run."""

    def __init__(
        self,
        stages: SearchStages,
        time_limits: dict[StageName, float],
        request: SearchRequest,
        ctx: StageContext,
    ) -> None:
        self.stages = stages
        self.limits = time_limits
        self.request = request
        self.rubric = request.rubric
        self.ctx = ctx
        self.out_of_time = False  # set when the search's own deadline cut a stage off
        self.reports: list[StageReport] = []
        self.research_out: ResearchResult | None = None
        self.claims: list[Claim] = []
        self.mentions: list[ProductMention] = []
        self.match_out: MatchResult | None = None
        self.verify_out: VerifyResult | None = None
        self.enrich_out: EnrichResult | None = None
        self.decide_out: DecideResult | None = None
        self.explain_out: ExplainResult | None = None
        self.commerce_out: CommerceResult | None = None

    def steps(self) -> list[tuple[StageName, Callable[[], StageReport]]]:
        return [
            (StageName.RESEARCH, self.research),
            (StageName.EXTRACT, self.extract),
            (StageName.MATCH, self.match),
            (StageName.VERIFY, self.verify),
            (StageName.ENRICH, self.enrich),
            (StageName.DECIDE, self.decide),
            (StageName.EXPLAIN, self.explain),
            (StageName.COMMERCE, self.commerce),
        ]

    def _run(self, stage: Stage[In, Out], inp: In) -> tuple[Out | None, StageReport]:
        limit = self.limits[stage.name]
        budget = self.ctx.seconds_left  # read once, so the decision below matches the wait that ran
        out, report = run_stage(stage, inp, self.ctx, min(limit, budget))
        if report.status is StageStatus.TIMEOUT and budget < limit:
            # The search's deadline, not the stage's own limit, cut this stage off: the search is out of time.
            # Don't rely on seconds_left reading exactly 0 afterwards; a timed wait can wake a moment before
            # its deadline (seen on GitHub's Windows runners), which used to let the next stages start.
            self.out_of_time = True
        return out, report

    @property
    def weight_keys(self) -> list[str]:
        return [w.key for w in self.rubric.weights]

    # Each step runs one stage, keeps its output and returns its report.

    def research(self) -> StageReport:
        self.research_out, report = self._run(self.stages.research, ResearchInput(rubric=self.rubric))
        return report

    def extract(self) -> StageReport:
        sources = _require(self.research_out, "research").sources
        if not sources:
            return _skipped(StageName.EXTRACT, "there were no sources to read")
        reports: list[StageReport] = []
        for source in sources:
            inp = ExtractInput(source=source, criterion_keys=self.weight_keys, category_label=self.rubric.category)
            out, report = self._run(self.stages.extract, inp)
            reports.append(report)
            if out is None:
                break  # the fallback failed; merge() reports FAILED and the runner stops
            self.claims += out.claims
            self.mentions += out.mentions
            if self.out_of_time:
                break  # no time left to read the remaining sources
        return _merge(StageName.EXTRACT, reports)

    def match(self) -> StageReport:
        self.match_out, report = self._run(self.stages.match, MatchInput(mentions=self.mentions[:MAX_MENTIONS]))
        return report

    def verify(self) -> StageReport:
        research = _require(self.research_out, "research")
        rule_keys = [r.attribute for r in self.rubric.hard_rules]
        keys = list(dict.fromkeys(self.weight_keys + rule_keys))[:MAX_VERIFY_KEYS]
        inp = VerifyInput(
            match=_require(self.match_out, "match"),
            claims=self.claims[:MAX_CLAIMS],
            sources=research.sources,
            criterion_keys=keys,
        )
        self.verify_out, report = self._run(self.stages.verify, inp)
        return report

    def enrich(self) -> StageReport:
        # Choosing enrichment targets is US-41; until then nothing is looked up.
        self.enrich_out, report = self._run(self.stages.enrich, EnrichInput(targets=[]))
        return report

    def decide(self) -> StageReport:
        verified = _require(self.verify_out, "verify")
        inp = DecideInput(
            rubric=self.rubric,
            products=_require(self.match_out, "match").products,
            values=verified.values,
            community=verified.community,
        )
        self.decide_out, report = self._run(self.stages.decide, inp)
        return report

    def explain(self) -> StageReport:
        top = sorted(self.rubric.weights, key=lambda w: w.normalized, reverse=True)[:TOP_PRIORITIES]
        inp = ExplainInput(
            ranked=_require(self.decide_out, "decide").ranked[:MAX_EXPLAINED],
            claims=self.claims,
            user_priorities=[w.key for w in top],
        )
        self.explain_out, report = self._run(self.stages.explain, inp)
        return report

    def commerce(self) -> StageReport:
        by_id = {p.id: p for p in _require(self.match_out, "match").products}
        ranked = _require(self.decide_out, "decide").ranked
        products = [by_id[r.product_id] for r in ranked if r.product_id in by_id][:MAX_PRICED]
        if not products:
            return _skipped(StageName.COMMERCE, "no product was ranked, so there was nothing to price")
        inp = CommerceInput(products=products, budget=self.rubric.budget)
        self.commerce_out, report = self._run(self.stages.commerce, inp)
        return report

    # Assembling the result.

    def result(self, started_at: datetime, partial: bool) -> SearchResult:
        values = self.verify_out.values if self.verify_out else []
        community = {c.product_id: c for c in self.verify_out.community} if self.verify_out else {}
        products = [
            ProductSummary(product=p, values=[v for v in values if v.product_id == p.id], community=community.get(p.id))
            for p in (self.match_out.products if self.match_out else [])
        ]
        failures = self.research_out.failures if self.research_out else []
        return SearchResult(
            search_id=self.ctx.search_id,
            request=self.request,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            partial=partial,
            products=products,
            decision=self.decide_out,
            explanations=self.explain_out.explanations if self.explain_out else [],
            commerce=self.commerce_out,
            sources=self.research_out.sources if self.research_out else [],
            stage_reports=self.reports,
            coverage_note=build_coverage_note(self.reports, failures, partial),
            saved=True,  # what the stored row must say; the runner returns a saved=False copy if it is not committed
        )


def _merge(stage: StageName, reports: list[StageReport]) -> StageReport:
    """One report for a stage that ran several times (extract runs once per source)."""
    worst = max(reports, key=lambda r: _SEVERITY[r.status])
    reasons = list(dict.fromkeys(r.fallback_reason for r in reports if r.fallback_reason))
    problems = sum(r.status is not StageStatus.OK for r in reports)
    notes = [f"read {len(reports)} sources"] + ([f"{problems} of them fell back"] if problems else [])
    return StageReport(
        stage=stage,
        status=worst.status,
        duration_ms=sum(r.duration_ms for r in reports),
        fallback_reason="; ".join(reasons)[:MAX_REASON_CHARS] if reasons else None,
        notes=notes,
    )


# --- the coverage note --------------------------------------------------------------------------


def build_coverage_note(reports: Iterable[StageReport], failures: Iterable[SourceFailure], partial: bool) -> str | None:
    """Plain-English summary of everything that did not go to plan. None when everything did."""
    parts: list[str] = []
    skipped: dict[str, list[str]] = {}
    for r in reports:
        label = r.stage.value.capitalize()
        reason = (r.fallback_reason or "no reason given").rstrip(".")
        if r.status is StageStatus.FALLBACK:
            parts.append(f"{label} used a simpler fallback: {reason}.")
        elif r.status is StageStatus.TIMEOUT:
            parts.append(f"{label} {reason}, so a simpler fallback was used.")
        elif r.status is StageStatus.FAILED:
            parts.append(f"{label} failed, so the steps after it did not run.")
        elif r.status is StageStatus.SKIPPED:
            skipped.setdefault(reason, []).append(r.stage.value)
    for f in failures:
        parts.append(f"{SOURCE_LABELS[f.source_type]} {FAILURE_WORDS[f.reason]}.")
    for reason, stages in skipped.items():
        parts.append(f"Skipped {', '.join(stages)} because {reason}.")
    if partial:
        parts.append("These results are partial.")

    note = " ".join(parts)
    if not note:
        return None
    return note if len(note) <= MAX_NOTE_CHARS else note[: MAX_NOTE_CHARS - 3].rstrip() + "..."
