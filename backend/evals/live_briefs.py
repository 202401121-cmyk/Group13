"""Sprint 3 baselines, live (S3-18, DECIDED 2): the full flow on 5 briefs, with no database. Calls real APIs: run it
by hand, never in CI or pytest.

Run from backend/:  python -m evals.live_briefs        (writes evals/results/sprint-3-live.json)

One made-up request for each of the five categories with the most data in ml/labelling/listings.csv. Each runs the
CLI's flow: intake, criteria, an interview answered "Skip", then the pipeline, with results kept in memory only. The
record holds numbers and fixed labels only (stage names, statuses, methods, source types). No request, quote, product
name or link is written, so no Reddit text reaches the repository (D-53).

Every call to a paid or rate-limited service is counted and refused past its share (scripts/find_conflicts.py's
guard, with Reddit's archives allowed up to theirs): LLM 60, Serper 30, Reddit archive 30, Jina 30 pages, Firecrawl 10
credits. Extraction reads at most EXTRACT_MAX_SOURCES=8 sources a search, so a brief stays near 12 LLM calls.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import statistics
import sys
import threading
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from time import monotonic
from typing import Any

import httpx

from app.contracts import InterviewState, SearchRequest, SourceType, StageName, StageStatus
from scripts.find_conflicts import QUERIES, Budget, OverBudget, service_of

from . import checks
from .run_baselines import BACKEND, RESULTS_DIR, measure

LIVE_OUT = RESULTS_DIR / "sprint-3-live.json"
CATEGORIES = ("smartphone", "air_fryer", "laptop", "smartwatch", "earbuds")  # most rows in listings.csv, in order
BUDGET = {"llm": 60, "serper": 30, "reddit": 30, "jina": 30, "firecrawl": 10}
MAX_SOURCES = 8
NOT_FINISHED = ("needs a clarification", "the interview did not finish")
SOURCE = "backend/evals/live_briefs.py, 5 live briefs, --no-db"
__all__ = ["BUDGET", "CATEGORIES", "LIVE_OUT", "LiveBudget", "OverBudget", "main", "text_in"]


class LiveBudget(Budget):
    """find_conflicts' guard, with Reddit's archives counted against their share instead of always refused."""

    def admit(self, request: httpx.Request) -> None:
        service, cost = service_of(request.url)
        if service is None:
            return
        with self._lock:
            spent = self.used.get(service, 0) + cost
            cap = min(self.limits.get(service, 0), self._caps.get(service, sys.maxsize))
            if spent > cap:
                raise OverBudget(f"the {service} budget is spent", request=request)
            self.used[service] = spent


class _Keep:
    """Wraps a stage to keep what it returned. Extract runs per source, in threads, so every output is kept."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name
        self.is_placeholder = bool(getattr(inner, "is_placeholder", False))
        self.outputs: list[Any] = []
        self._lock = threading.Lock()

    def run(self, inp: Any, ctx: Any) -> Any:
        out = self.inner.run(inp, ctx)
        with self._lock:
            self.outputs.append(out)
        return out

    def fallback(self, inp: Any, ctx: Any, error: Exception) -> Any:
        out = self.inner.fallback(inp, ctx, error)
        with self._lock:
            self.outputs.append(out)
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class _Dropped(logging.Handler):
    """Counts the claims the extract parser drops, by reason, from its own log line (counts only, never text)."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.counts: Counter[str] = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        args = record.args if isinstance(record.args, tuple) else ()
        if record.getMessage().startswith("extract: claims dropped") and len(args) == 2 and isinstance(args[1], dict):
            self.counts.update({str(k): int(v) for k, v in args[1].items()})


def run_brief(category: str, query: str) -> dict[str, Any]:
    """One brief through the CLI's flow. Numbers and fixed labels only."""
    from app.api.sessions import new_session_id
    from app.cli import run_interview
    from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
    from app.presearch import make_criteria, start_request

    timings: dict[str, float] = {}
    started = monotonic()
    intent = start_request(query)
    timings["intake"] = round(monotonic() - started, 2)
    if not intent.ready_for_research:
        return {"category": category, "finished": False, "why": NOT_FINISHED[0], "presearch_seconds": timings}
    mark = monotonic()
    criteria = make_criteria(intent)
    timings["criteria"] = round(monotonic() - mark, 2)
    mark = monotonic()
    session_id = new_session_id()
    step = run_interview(InterviewState(session_id=session_id, intent=intent, criteria=criteria), interactive=False)
    timings["interview"] = round(monotonic() - mark, 2)
    if step.rubric is None:
        return {"category": category, "finished": False, "why": NOT_FINISHED[1], "presearch_seconds": timings}

    stages = default_search_stages()
    research, extract, match, verify = (
        _Keep(s) for s in (stages.research, stages.extract, stages.match, stages.verify)
    )
    stages.research, stages.extract, stages.match, stages.verify = research, extract, match, verify
    dropped = _Dropped()
    parser_log = logging.getLogger("app.extract.parsing")
    parser_log.addHandler(dropped)
    level = parser_log.level
    parser_log.setLevel(logging.INFO)
    try:
        request = SearchRequest(session_id=session_id, raw_query=query, rubric=step.rubric)
        result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    finally:
        parser_log.removeHandler(dropped)
        parser_log.setLevel(level)
    record = _numbers(category, result, research, extract, match, verify, dropped.counts)
    record["presearch_seconds"] = timings
    return record


def _first_per_source(outputs: list[Any]) -> list[Any]:
    """One extraction per source: the first that came back. A run the runner gave up on can still finish after its
    fallback did; the fallback's output is what the search used."""
    seen: set[str] = set()
    kept: list[Any] = []
    for out in outputs:
        if out.source_id not in seen:
            seen.add(out.source_id)
            kept.append(out)
    return kept


def _numbers(
    category: str,
    result: Any,
    research: _Keep,
    extract: _Keep,
    match: _Keep,
    verify: _Keep,
    dropped: Mapping[str, int],
) -> dict[str, Any]:
    sources = list(result.sources)  # what the search kept, whatever a late run returned after its fallback
    type_of = {s.id: s.type.value for s in sources}
    ranked = result.decision.ranked if result.decision is not None else []
    rubric = result.request.rubric
    products = {summary.product.id: summary for summary in result.products}
    shown = [products[r.product_id].product for r in ranked if r.product_id in products]
    values = [v for summary in result.products for v in summary.values]
    broken, unknown = checks.shown_violations(rubric, shown, values)
    indexed = checks.by_product(values)
    fits = {r.product_id: checks.fit(rubric, indexed.get(r.product_id, {})) for r in ranked}
    mentions = {pid: summary.product.recommender_count for pid, summary in products.items()}
    wins = checks.most_mentioned_wins_fitting_worst([(r.product_id, r.rank) for r in ranked], fits, mentions)
    extractions = _first_per_source(extract.outputs)
    kept_claims = [c for out in extractions for c in out.claims]
    reached_quote = len(kept_claims) + dropped.get("contract", 0)
    quote_checked = reached_quote + dropped.get("quote", 0)
    given = Counter(type_of.get(out.source_id, "unknown") for out in extractions)
    filtered = result.decision.filtered if result.decision is not None else []
    return {
        "category": category,
        "finished": True,
        "search_seconds": round((result.finished_at - result.started_at).total_seconds(), 2),
        "stages": {
            r.stage.value: {"status": r.status.value, "seconds": round(r.duration_ms / 1000, 2)}
            for r in result.stage_reports
        },
        "methods": {
            "match": match.outputs[0].method if match.outputs else None,
            "trust": verify.outputs[0].trust_method if verify.outputs else None,
            "decide": result.decision.method if result.decision is not None else None,
        },
        "products": {
            "ranked": len(ranked),
            "with_a_score": sum(1 for r in ranked if r.score is not None),
            "not_ranked": sum(1 for r in ranked if r.score is None),
            "filtered_out": sum(1 for f in filtered if f.group == "filtered_out"),
            "cant_tell": sum(1 for f in filtered if f.group == "cant_tell"),
            "coverage": [round(r.coverage, 4) for r in ranked],
        },
        "checks": {
            "rule_check": "independent",
            "hard_rule_violations_shown": broken,
            "shown_with_unknown_rule": unknown,
            "unsourced_recheck": checks.unsourced(shown, values, sources, result.citations),
            "dropped_unsourced": len({pid for out in verify.outputs[:1] for pid in out.dropped_unsourced}),
            "popularity_by_fit": wins,
        },
        "research": {
            "sources": len(sources),
            "sources_by_type": dict(sorted(Counter(s.type.value for s in sources).items())),
            "pages_by_via": dict(sorted(Counter(s.via for s in sources).items())),
        },
        "extraction": {
            "sources_given": len(extractions),
            "sources_given_by_type": dict(sorted(given.items())),
            "claims_kept": len(kept_claims),
            "claims_by_source_type": dict(
                sorted(Counter(type_of.get(c.source_id, "unknown") for c in kept_claims).items())
            ),
            "dropped_by_reason": dict(sorted(dropped.items())),
            "quote_found_share": round(reached_quote / quote_checked, 4) if quote_checked else None,
        },
    }


# --- the record -------------------------------------------------------------------------------------------------------

READ_TYPES = ("reddit", "review_site", "manufacturer", "lab", "retailer", "youtube")  # web_search: snippets, never read


def _given(brief: Mapping[str, Any]) -> tuple[int, Mapping[str, int]]:
    """Sources given to extract, overall and by type. A record from before the fixes of 14 Nov named them
    sources_read and kept no types; its research counts by type are the same sources."""
    extraction = brief["extraction"]
    total = extraction.get("sources_given", extraction.get("sources_read", 0))
    return total, extraction.get("sources_given_by_type", brief["research"]["sources_by_type"])


def measures(briefs: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    done = [b for b in briefs if b.get("finished")]
    coverage = [c for b in done for c in b["products"]["coverage"]]
    given = sum(_given(b)[0] for b in done)
    kept = sum(b["extraction"]["claims_kept"] for b in done)
    dropped_quote = sum(b["extraction"]["dropped_by_reason"].get("quote", 0) for b in done)
    dropped_contract = sum(b["extraction"]["dropped_by_reason"].get("contract", 0) for b in done)
    checked = kept + dropped_contract + dropped_quote
    via = Counter[str]()
    given_by_type = Counter[str]()
    claims_by_type = Counter[str]()
    for b in done:
        via.update(b["research"]["pages_by_via"])
        given_by_type.update(_given(b)[1])
        claims_by_type.update(b["extraction"]["claims_by_source_type"])
    independent = bool(done) and all(b["checks"].get("rule_check") == "independent" for b in done)
    rechecked = bool(done) and all("unsourced_recheck" in b["checks"] for b in done)
    by_fit = bool(done) and all("popularity_by_fit" in b["checks"] for b in done)
    old = "this record predates the independent check (14 Nov 2026); the next live run measures it"
    rule_detail = (
        "shown products breaking a rule, by evals/checks.py's own reading of the rubric"
        if independent
        else "shown products breaking a rule, by decide's own filter: this record predates the independent check"
    )
    fit_detail = (
        "briefs where the most-mentioned product ranks first while it fits worst (fit: weights times each "
        "value's own score)"
    )
    out = [
        measure("live.briefs_finished", len(done), "briefs", source, f"of {len(briefs)}"),
        measure(
            "live.hard_rule_violations_shown",
            sum(b["checks"]["hard_rule_violations_shown"] for b in done),
            "products",
            source,
            rule_detail,
        ),
        measure(
            "live.unsourced_shown",
            sum(b["checks"]["unsourced_recheck"] for b in done) if rechecked else None,
            "products",
            source,
            "shown products named in no source and citing no claim from one" if rechecked else old,
            "measured" if rechecked else "not_recorded",
        ),
        measure(
            "live.most_mentioned_worst_fit_wins",
            sum(int(b["checks"]["popularity_by_fit"]) for b in done) if by_fit else None,
            "briefs",
            source,
            fit_detail if by_fit else old,
            "measured" if by_fit else "not_recorded",
        ),
        measure("live.ranked_products", sum(b["products"]["ranked"] for b in done), "products", source, "shown"),
        measure(
            "live.briefs_with_a_score",
            sum(1 for b in done if b["products"]["with_a_score"]),
            "briefs",
            source,
            "briefs where at least one shown product has a score (S3-10b's 'not ranked' risk)",
        ),
        measure(
            "live.coverage_mean",
            statistics.fmean(coverage) if coverage else None,
            "rate",
            source,
            f"mean coverage over {len(coverage)} ranked products",
        ),
        measure(
            "live.sources_per_brief",
            statistics.fmean(b["research"]["sources"] for b in done) if done else None,
            "sources",
            source,
            "sources the search kept, mean per brief",
        ),
        measure("live.pages_direct", via.get("direct", 0), "pages", source, "pages read by a plain fetch"),
        measure("live.pages_jina", via.get("jina", 0), "pages", source, "pages read through Jina Reader"),
        measure("live.pages_firecrawl", via.get("firecrawl", 0), "pages", source, "pages read through Firecrawl"),
        measure(
            "live.claims_per_source_given",
            kept / given if given else None,
            "claims",
            source,
            f"{kept} claims kept from {given} sources given to extract. Search snippets and sources past the "
            f"{MAX_SOURCES}-source cap count too: extract passes them over without a model call",
        ),
        measure(
            "live.quote_found_share",
            (kept + dropped_contract) / checked if checked else None,
            "rate",
            source,
            f"claims whose quote is in their source, of {checked} the parser checked ({dropped_quote} dropped). "
            "Kept claims are counted after duplicates go; a quote found but refused as an instruction counts as "
            "not found",
        ),
        measure(
            "live.search_seconds_max",
            max((b["search_seconds"] for b in done), default=None),
            "seconds",
            source,
            "the longest search",
        ),
    ]
    for kind in READ_TYPES:
        if given_by_type.get(kind):
            out.append(
                measure(
                    f"live.claims_per_source.{kind}",
                    claims_by_type.get(kind, 0) / given_by_type[kind],
                    "claims",
                    source,
                    f"{claims_by_type.get(kind, 0)} claims from {given_by_type[kind]} {kind} sources given to extract",
                )
            )
    stages = sorted({stage for b in done for stage in b["stages"]})
    for stage in stages:
        seconds = [b["stages"][stage]["seconds"] for b in done if stage in b["stages"]]
        out.append(
            measure(f"live.stage_seconds_max.{stage}", max(seconds), "seconds", source, f"the longest {stage} stage")
        )
    return sorted(out, key=lambda m: m["id"])


ALLOWED_TEXT = (
    {*CATEGORIES, *NOT_FINISHED, "model", "fuzzy", "learned", "tier_order", "monte_carlo", "weighted", "independent"}
    | {s.value for s in StageName}
    | {s.value for s in StageStatus}
    | {t.value for t in SourceType}
    | {"api", "direct", "jina", "search_snippet", "firecrawl", "unknown", "live", "offline"}
)
META_KEYS = {"generated", "commit", "runner", "id", "unit", "status", "source", "detail", "budget_note"}
_KEY = re.compile(r"[a-z][a-z0-9_.]*")  # a key is a field or label name: never a product name, quote or link


def text_in(record: Any, key: str = "") -> list[str]:
    """Every string in the record, key or value, that is not a fixed label or field name: what must never be in it
    (a quote, a product name, a link)."""
    if isinstance(record, dict):
        bad_keys = [str(k) for k in record if not _KEY.fullmatch(str(k))]
        return bad_keys + [found for k, v in record.items() for found in text_in(v, str(k))]
    if isinstance(record, list):
        return [found for item in record for found in text_in(item, key)]
    if isinstance(record, str) and key not in META_KEYS and record not in ALLOWED_TEXT:
        return [record]
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.live_briefs", description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--rebuild", action="store_true", help="recompute the measures from the briefs already recorded (no calls)"
    )
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.rebuild:
        recorded = json.loads(LIVE_OUT.read_text(encoding="utf-8"))
        recorded["measures"] = measures(recorded["briefs"], SOURCE)
        LIVE_OUT.write_bytes((json.dumps(recorded, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        print(f"Rebuilt {len(recorded['measures'])} measures from the recorded briefs; no call was made")
        return 0
    from dotenv import load_dotenv

    from scripts.eval_trust import current_commit

    load_dotenv()  # the keys stay in the environment; nothing here prints them
    os.environ["EXTRACT_MAX_SOURCES"] = str(MAX_SOURCES)
    os.environ["MATCHER"] = "fuzzy"  # the default (S3-15), stated so a developer's .env can't change it
    budget = LiveBudget(BUDGET)
    budget.install()  # kept until the process ends: a stage thread still running after a brief counts too
    briefs: list[dict[str, Any]] = []
    calls: dict[str, dict[str, int]] = {}
    for index, category in enumerate(CATEGORIES):
        left = len(CATEGORIES) - index
        share = {service: budget.left(service) // left for service in BUDGET}
        before = dict(budget.used)
        print(f"\n== {category} (share {share})")
        with budget.share(**share):
            try:
                brief = run_brief(category, QUERIES[category])
            except Exception as exc:  # one brief failing must not lose the others
                print(f"   failed: {type(exc).__name__}")
                brief = {"category": category, "finished": False, "why": NOT_FINISHED[1]}
        calls[category] = {service: budget.used[service] - before.get(service, 0) for service in BUDGET}
        brief["calls"] = calls[category]
        briefs.append(brief)
        print(f"   {json.dumps({k: v for k, v in brief.items() if k in ('products', 'methods', 'calls')})}")
    record = {
        "generated": datetime.now(UTC).date().isoformat(),
        "commit": current_commit(),
        "runner": "python -m evals.live_briefs (live)",
        "budget_note": f"limits {BUDGET}, EXTRACT_MAX_SOURCES={MAX_SOURCES}, MATCHER=fuzzy",
        "calls_used": dict(budget.used),
        "measures": measures(briefs, SOURCE),
        "briefs": briefs,
    }
    leaked = text_in(record)
    if leaked:  # never write text that could be a quote, a name or a link
        print(f"refused to write: {len(leaked)} strings are not fixed labels")
        return 1
    LIVE_OUT.parent.mkdir(parents=True, exist_ok=True)
    LIVE_OUT.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"\ncalls used: {budget.used} of {BUDGET}")
    print(f"Written: {LIVE_OUT.relative_to(BACKEND.parent).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
