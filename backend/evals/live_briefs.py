"""Sprint 3 baselines, live (S3-18, DECIDED 2): the full flow on 5 briefs, with no database. Calls real APIs: run it
by hand, never in CI or pytest.

Run from backend/:  python -m evals.live_briefs        (writes evals/results/sprint-3-live.json)
Options (US-14): --out FILE, --categories in the order to run, --max-sources N, --budget-<service> N. A run with --out
leaves the committed record alone.

One made-up request for each of the five categories with the most data in ml/labelling/listings.csv. Each runs the
CLI's flow: intake, criteria, an interview answered "Skip", then the pipeline, with results kept in memory only. The
record holds numbers and fixed labels only (stage names, statuses, methods, source types). No request, quote, product
name or link is written, so no Reddit text reaches the repository (D-53).

Every call to a paid or rate-limited service is counted and refused past its share (scripts/find_conflicts.py's
guard, with Reddit's archives allowed up to theirs): LLM 60, Serper 30, Reddit archive 30, Jina 30 pages, Firecrawl 10
credits. Extraction reads at most EXTRACT_MAX_SOURCES=8 sources a search, so a brief stays near 12 LLM calls.

Each brief also gets a funnel (US-14): where its sources stopped turning into scored products, as counts with a reason
for each. It holds counts and fixed labels only, like the rest of the record.

S5-6a (US-14, US-46): each brief also says
- its funnel in eight steps (`steps`): sources found, read, extracted, claims kept, products kept, with a price, with
  any score, ranked;
- every model attempt minute by minute (`attempts_by_minute`), per provider: sent, answered, no room (our own pacing
  or a provider's daily budget held it: nothing was sent), 429 (sent, and the provider refused it), timed out, and any
  other failure. A call this run's own LLM share refuses reaches the client as a failed request, so it counts as sent
  and other;
- each answered attempt's size and time per provider and outcome (`answer_sizes`: [output tokens, seconds]), so an
  answer cut off at its room can be told from one that ran out of time;
- whether it ran on an empty research cache (`--cold`). The cache's folders are moved aside before the brief and back
  after it, never deleted, so the cache's own expiry still deletes what they hold in time (D-53: some hold Reddit
  titles, links and threads). Its usage counts stay, so the daily caps still hold, and Reddit's own cache
  (`.cache/reddit`) is never touched.
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
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any

import httpx

from app.contracts import (
    DataClass,
    ExtractionResult,
    InterviewState,
    LLMCallRecord,
    LLMTask,
    Provider,
    Rubric,
    SearchRequest,
    SearchResult,
    SourceType,
    StageName,
    StageStatus,
    ValueState,
)
from app.extract.settings import ExtractSettings
from app.llm.client import CUT_OFF_ROOM
from app.research import usage
from app.research.readers import search_credits
from scripts.find_conflicts import QUERIES, Budget, OverBudget, service_of

from . import checks
from .run_baselines import BACKEND, RESULTS_DIR, measure

LIVE_OUT = RESULTS_DIR / "sprint-3-live.json"
CATEGORIES = ("smartphone", "air_fryer", "laptop", "smartwatch", "earbuds")
BRIEF_CATEGORIES = (
    "ereader",
    "headphones",
    "monitor",
    "phone",
)  # US-41: the hand-written decide briefs'  # most rows in listings.csv, in order
# Firecrawl in credits: a fact search that reads its results costs 5 (D-101), so a brief needs about 15 of them.
BUDGET = {"llm": 60, "serper": 30, "reddit": 30, "jina": 30, "firecrawl": 75}
# The run budget's services, and what the app counts for each call (D-78): a refused call gives those units back.
APP_COUNTED: dict[str, tuple[str, ...]] = {
    "serper": ("serper",),
    "jina": ("jina",),
    "firecrawl": ("firecrawl", "firecrawl_pages"),  # a page's credit or a search's 2, and the page itself
    "reddit": ("reddit_archive",),
}
MAX_SOURCES = 8
NOT_FINISHED = ("needs a clarification", "the interview did not finish")
SOURCE = "backend/evals/live_briefs.py, 5 live briefs, --no-db"
SEARCH_SNIPPET = "search_snippet"  # Source.via for a search result whose page was never fetched (app/extract/stage.py)
RESEARCH_CACHE = BACKEND / ".cache" / "research"
COLD_ASIDE = BACKEND / ".cache" / "research-aside"
KEPT_IN_CACHE = {"usage.json"}  # the daily and total counts: the caps hold whatever the cache holds (D-78)
PRICE_ATTRIBUTES = {checks.PRICE_KEY, "price"}  # what decide names a budget hold-back by
CUT_OFF_SHARE = 0.95  # an unusable answer this close to its output reserve was most likely cut off
KEY_KINDS = ("rubric", "spec", "neither")
PRICE_LINE = f"\n- {checks.PRICE_KEY}: "  # how a prompt asks for the price (app/extract/prompts.py)
# A title that names a list: "best ... under", "top 10", "vs". Matched by code; only the label is kept.
_LIST_TITLE = re.compile(r"\b(best|top|under|below|vs|versus|compared|comparison)\b", re.IGNORECASE)
_DISCOUNT = re.compile(r"\b(off|discount|save|saving|savings|cashback|emi)\b", re.IGNORECASE)
_SCALED = re.compile(r"\d\s*(k|lakh|lakhs|lac|crore|cr)\b", re.IGNORECASE)
PAGE_KINDS = {"reddit": "thread", "web_search": "snippet", "retailer": "store", "manufacturer": "maker"}


def page_kind(source_type: str, title: str, products: int) -> str:
    """What kind of page a source is, by code: a store's or a maker's page, a Reddit thread, a search snippet, a list
    of products (its title says so, or it names 3 or more) or a review of one or two. Only the label is ever kept."""
    if source_type in PAGE_KINDS:
        return PAGE_KINDS[source_type]
    return "list" if _LIST_TITLE.search(title) or products >= 3 else "review"


def price_problem(value: object, unit: str | None) -> str | None:
    """Why decide could not use this kept price, read the way verify passes it on (the value, then its unit), or None.
    A discount is not a price; "25k" and "1.2 lakh" are read as 25 and 1.2 (app/decide/units.py)."""
    from app.decide.units import quantity

    text = str(value) if unit is None else f"{value} {unit}"
    if isinstance(value, bool):
        return "unreadable"
    if _DISCOUNT.search(text):
        return "discount"
    if _SCALED.search(text):
        return "k_or_lakh"
    amount = quantity(text)
    if amount is None:
        return "unreadable"
    return None if amount.unit in (None, "inr") else "other_currency"


__all__ = ["BUDGET", "CATEGORIES", "LIVE_OUT", "LiveBudget", "OverBudget", "funnel", "main", "options", "text_in"]


def firecrawl_cost(request: httpx.Request, cost: int) -> int:
    """A Firecrawl search that reads its results costs a credit for each result as well (D-101): 2 for each 10 results,
    and 1 a result read, as Firecrawl charges (app/research/readers.py, search_credits)."""
    if request.url.host != "api.firecrawl.dev" or "search" not in request.url.path:
        return cost
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        return cost
    limit = body.get("limit") if isinstance(body, dict) else None
    if not isinstance(limit, int) or limit < 1:
        return cost
    return search_credits(limit, read="scrapeOptions" in body)  # any scrape options: priced as reading them


class LiveBudget(Budget):
    """find_conflicts' guard, with Reddit's archives counted against their share instead of always refused.

    A call it refuses never leaves the machine, so it counts nothing against the app's own caps either (D-78): the unit
    the app took for it goes back (app/research/usage.py, APP_COUNTED). A Firecrawl search is priced by its body."""

    def admit(self, request: httpx.Request) -> None:
        service, cost = service_of(request.url)
        cost = firecrawl_cost(request, cost) if service == "firecrawl" else cost
        if service is None:
            return
        with self._lock:
            spent = self.used.get(service, 0) + cost
            cap = min(self.limits.get(service, 0), self._caps.get(service, sys.maxsize))
            if spent > cap:
                if service == "llm":  # so the funnel can tell the run's own share from a provider with no room
                    _CURRENT.refused = getattr(_CURRENT, "refused", 0) + 1
                for provider in APP_COUNTED.get(service, ()):
                    usage.not_sent(provider)
                raise OverBudget(f"the {service} budget is spent", request=request)
            self.used[service] = spent
        for provider in APP_COUNTED.get(service, ()):
            usage.sent(provider)


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


_CURRENT = threading.local()  # in a thread reading one source: that source's id, and the run's own refusals


class _KeepExtract(_Keep):
    """Extract's wrapper also keeps the stage's own counts (US-14). The pipeline asks for the coverage note once, and
    the note clears them, so they are read just before it.

    Each source gets one row: its type, page kind, what its call asked and returned, how the call ended, and the
    parser's drops for it. A source the runner gave up on gets a row saying so, and its run, which may still finish
    later, is left out of every count: the search used the fallback's empty result, not the late one."""

    def __init__(self, inner: Any, returned: _Returned | None = None) -> None:
        super().__init__(inner)
        self.counts: dict[str, int] | None = None
        self.fallbacks: Counter[str] = Counter()
        self.returned = returned
        self.sources: dict[str, dict[str, Any]] = {}  # per source id: its row (labels and counts only)
        self.started: set[str] = set()
        self.drops: dict[str, Counter[str]] = {}  # per source id: the parser's drops, by reason
        self.price_drops: dict[str, Counter[str]] = {}

    def run(self, inp: Any, ctx: Any) -> Any:
        source = inp.source
        with self._lock:
            self.started.add(source.id)
        _CURRENT.source, _CURRENT.refused = source.id, 0
        if self.returned is not None:
            self.returned.start()
        try:
            out = self.inner.run(inp, ctx)
        finally:
            call = self.returned.last() if self.returned is not None else None
            _CURRENT.source = None
        products = sum(1 for m in getattr(out, "mentions", []) if m.is_product)
        row = _row(source, products, call)
        with self._lock:
            self.outputs.append(out)
            self.sources.setdefault(source.id, row)  # a row from the fallback stays: that run was given up on
        return out

    def fallback(self, inp: Any, ctx: Any, error: Exception) -> Any:
        why = "timeout" if isinstance(error, TimeoutError) else "error"
        with self._lock:
            self.fallbacks[why] += 1
            self.sources[inp.source.id] = _row(inp.source, 0, None) | {"answer": "gave_up", "gave_up": True}
        return super().fallback(inp, ctx, error)

    def note_drops(self, counts: Mapping[str, int], price: bool) -> None:
        """The parser's drops for the source this thread is reading, if it is one of this brief's."""
        source_id = getattr(_CURRENT, "source", None)
        with self._lock:
            if source_id is None or source_id not in self.started:
                return  # a late run from an earlier brief: not this brief's
            target = self.price_drops if price else self.drops
            target.setdefault(source_id, Counter()).update(counts)

    def snapshot(self) -> tuple[dict[str, dict[str, Any]], list[Any]]:
        """The rows, each with its parser drops, and the outputs as they stand, copied under the lock: a run given up
        on may still be adding to them."""
        with self._lock:
            rows = {
                key: {
                    **row,
                    "dropped": dict(self.drops.get(key, {})),
                    "price_dropped": dict(self.price_drops.get(key, {})),
                }
                for key, row in self.sources.items()
            }
            return rows, list(self.outputs)

    def coverage_note(self, search_id: str) -> str | None:
        read = getattr(self.inner, "counts_for", None)
        if read is not None:
            self.counts = read(search_id)
        note = getattr(self.inner, "coverage_note", None)
        return note(search_id) if note is not None else None


class _Returned:
    """Wraps extract's LLM client to count the claims each answer holds before the parser drops any: all of them, and
    the price claims. Counts only: the answer itself is passed on untouched and never kept."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.counts: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._local = threading.local()  # the last call this thread made: a source runs in one thread

    def start(self) -> None:
        self._local.call = None

    def last(self) -> dict[str, Any] | None:
        """This thread's call since start(): whether it asked for the price, what came back, and how it ended."""
        call = getattr(self._local, "call", None)
        return dict(call) if call is not None else None

    def watch(self) -> Callable[[], None]:
        """Note each attempt's outcome for the thread that made it, as the client logs it. Returns the undo."""
        log = getattr(self.inner, "call_log", None)
        if log is None or not callable(getattr(log, "record", None)):
            return lambda: None
        own = "record" in vars(log)
        original = log.record

        def record(entry: Any) -> None:
            outcomes = getattr(self._local, "outcomes", None)
            if outcomes is not None:
                outcomes.append(str(getattr(entry, "outcome", "")))
            original(entry)

        log.record = record

        def undo() -> None:
            if own:
                log.record = original
            else:
                del log.record

        return undo

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        user = args[2] if len(args) > 2 else kwargs.get("user", "")
        call: dict[str, Any] = {
            "price_asked": PRICE_LINE in str(user),
            "price_returned": 0,
            "claims_returned": 0,
            "answer": "ok",
        }
        self._local.call = call
        self._local.outcomes = []
        try:
            out = self.inner.complete(*args, **kwargs)
        except Exception:
            call["answer"] = _why_no_answer(self._local.outcomes, getattr(_CURRENT, "refused", 0))
            raise
        finally:
            self._local.outcomes = None
        claims = getattr(out, "claims", None)
        if isinstance(claims, list):
            price = sum(1 for c in claims if getattr(c, "criterion_key", None) == checks.PRICE_KEY)
            call["price_returned"], call["claims_returned"] = price, len(claims)
            with self._lock:
                self.counts["claims"] += len(claims)
                self.counts[checks.PRICE_KEY] += price
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


NO_ROOM = {"skipped_budget", "rate_limited"}


def _why_no_answer(outcomes: list[str] | None, refused: int) -> str:
    """Why a call gave no answer, from its own attempts: the run's own LLM share refused it, every provider had no
    room, it ran out of time, or it failed (an unusable answer or an error)."""
    seen = set(outcomes or ())
    if refused:
        return "over_run_budget"
    if seen and seen <= NO_ROOM:
        return "no_room"
    if "timeout" in seen and seen <= NO_ROOM | {"timeout"}:
        return "timed_out"
    return "failed"


def _row(source: Any, products: int, call: Mapping[str, Any] | None) -> dict[str, Any]:
    """One source's row: labels and counts only."""
    kind = source.type.value
    return {
        "type": kind,
        "kind": page_kind(kind, source.title, products),
        "sent": call is not None,
        "price_asked": bool(call and call["price_asked"]),
        "price_returned": int(call["price_returned"]) if call else 0,
        "claims_returned": int(call["claims_returned"]) if call else 0,
        "answer": str(call["answer"]) if call else "not_sent",
        "gave_up": False,
    }


class _Dropped(logging.Handler):
    """Counts the claims the extract parser drops, by reason, from its own log lines (counts only, never text), for
    the source the logging thread is reading: so a late run from an earlier brief is never this brief's."""

    def __init__(self, extract: _KeepExtract) -> None:
        super().__init__(logging.INFO)
        self.extract = extract

    def emit(self, record: logging.LogRecord) -> None:
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) != 2 or not isinstance(args[1], dict):
            return
        message = record.getMessage()
        if message.startswith(("extract: claims dropped", "extract: price claims dropped")):
            counts = {str(k): int(v) for k, v in args[1].items()}
            self.extract.note_drops(counts, price=message.startswith("extract: price claims dropped"))


def tally(rows: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, int], Counter[str], Counter[str]]:
    """What the search used, from its sources' rows: the claims and price claims the model returned, and the parser's
    drops. A source the runner gave up on is left out: its late run's claims never reached the search."""
    used = [row for row in rows.values() if not row.get("gave_up")]
    returned = {
        "claims": sum(int(row.get("claims_returned", 0)) for row in used),
        checks.PRICE_KEY: sum(int(row.get("price_returned", 0)) for row in used),
    }
    dropped: Counter[str] = Counter()
    price_dropped: Counter[str] = Counter()
    for row in used:
        dropped.update(row.get("dropped", {}))
        price_dropped.update(row.get("price_dropped", {}))
    return returned, dropped, price_dropped


def steps(record: Mapping[str, Any], result: Any) -> dict[str, int]:
    """The brief's funnel in eight steps (S5-6a): where sources stopped turning into ranked products.

    read: sent to a model; extracted: a valid answer with at least one claim kept; products kept: after match and
    verify; with a price: a price claim attached; with any score: a criterion score from any source; ranked: shown with
    a score."""
    funnel_ = record.get("funnel", {})
    scored = sum(1 for summary in result.products if any(v.score is not None for v in summary.values))
    return {
        "sources_found": int(funnel_.get("sources", {}).get("found", 0)),
        "read": int(funnel_.get("extract", {}).get("sent", 0)),
        "extracted": int(record.get("extraction", {}).get("sources_with_claims", 0)),
        "claims_kept": int(funnel_.get("claims", {}).get("kept", 0)),
        "products_kept": len(result.products),
        "with_a_price": int(funnel_.get("price", {}).get("products_with_a_price", 0)),
        "with_any_score": scored,
        "ranked": int(record.get("products", {}).get("with_a_score", 0)),
    }


ATTEMPT_KINDS = ("sent", "answered", "no_room", "refused_429", "timeout", "other")  # keys: a letter first


def attempt_kind(call: LLMCallRecord) -> str:
    """An attempt as S5-6a counts it. Nothing sent (our pacing held it, or the budget refused it) is no room."""
    if call.outcome == "skipped_budget" or (call.outcome == "rate_limited" and call.input_tokens == 0):
        return "no_room"
    if call.outcome == "rate_limited":
        return "refused_429"
    if call.outcome == "ok":
        return "answered"
    if call.outcome == "timeout":
        return "timeout"
    return "other"


def attempts_by_minute(calls: Sequence[LLMCallRecord], since: datetime) -> list[dict[str, Any]]:
    """Every attempt from `since` on, minute by minute (0 is the brief's first minute), per provider: sent and each
    outcome. A minute with no attempt is left out."""
    rows: dict[int, dict[str, Counter[str]]] = {}
    for call in calls:
        if call.at < since:
            continue
        minute = int((call.at - since).total_seconds() // 60)
        counted = rows.setdefault(minute, {}).setdefault(call.provider.value, Counter())
        kind = attempt_kind(call)
        counted[kind] += 1
        if kind != "no_room":
            counted["sent"] += 1
    return [
        {
            "minute": minute,
            "providers": {
                provider: {k: counted[k] for k in ATTEMPT_KINDS if counted[k]}
                for provider, counted in sorted(by_provider.items())
            },
        }
        for minute, by_provider in sorted(rows.items())
    ]


def answer_sizes(calls: Sequence[LLMCallRecord], since: datetime) -> dict[str, dict[str, list[list[float]]]]:
    """Each answered attempt from `since` on, per provider and outcome, as [output tokens, seconds] (S5-6a): how big an
    answer was and how long it took, so a cut-off answer's room and time can be sized. Numbers only."""
    sizes: dict[str, dict[str, list[list[float]]]] = {}
    for call in calls:
        if call.at < since or call.outcome not in {"ok", "invalid_output", "timeout"}:
            continue
        sizes.setdefault(call.provider.value, {}).setdefault(call.outcome, []).append(
            [call.output_tokens, round(call.duration_ms / 1000, 2)]
        )
    return {provider: dict(sorted(kinds.items())) for provider, kinds in sorted(sizes.items())}


def fact_calls(calls: Sequence[LLMCallRecord], since: datetime) -> list[dict[str, Any]]:
    """Enrich's fact-reading call from `since` on (D-101): which provider it went to, how it ended, its input and
    output tokens and its seconds, so its size and time can be reported before and after. Numbers and labels only."""
    return [
        {
            "provider": call.provider.value,
            "outcome": call.outcome,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "seconds": round(call.duration_ms / 1000, 2),
        }
        for call in calls
        if call.at >= since and call.task is LLMTask.ENRICH_EXTRACT
    ]


def set_aside_research_cache(cache: Path = RESEARCH_CACHE, aside: Path = COLD_ASIDE) -> int:
    """Moves the research cache's folders aside, so the next brief starts cold (S5-6a DECIDED 1). Nothing is deleted,
    the usage counts stay where they are, and Reddit's own cache (a folder of its own) is never touched. Returns how
    many folders moved."""
    if not cache.is_dir():
        return 0
    target = aside / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    moved = 0
    for entry in sorted(cache.iterdir()):
        if entry.name in KEPT_IN_CACHE:
            continue
        target.mkdir(parents=True, exist_ok=True)
        entry.rename(target / entry.name)
        moved += 1
    return moved


def restore_research_cache(cache: Path = RESEARCH_CACHE, aside: Path = COLD_ASIDE) -> int:
    """Puts every set-aside file back in its own folder of the research cache, where the cache's own expiry deletes it
    in time (D-53; app/research/cache.py sweeps each folder's files by age). A file whose key the cache holds again goes
    back beside it as `<key>.aside-<stamp>.json`: the cache never serves it, and its sweep still deletes it when its
    time is up. Nothing is deleted here; only the emptied set-aside folders go. Returns how many files went back."""
    if not aside.is_dir():
        return 0
    back = 0
    for stamp in sorted(p for p in aside.iterdir() if p.is_dir()):
        for path in sorted(p for p in stamp.rglob("*") if p.is_file()):
            target = cache / path.relative_to(stamp)
            if target.exists():
                target = target.with_name(f"{path.stem}.aside-{stamp.name}{path.suffix}")
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
            back += 1
        for folder in sorted((p for p in stamp.rglob("*") if p.is_dir()), reverse=True):
            folder.rmdir()
        stamp.rmdir()
    return back


def run_brief(category: str, query: str) -> dict[str, Any]:
    """One brief through the CLI's flow. Numbers and fixed labels only."""
    from app.api.sessions import new_session_id
    from app.cli import run_interview
    from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages
    from app.presearch import make_criteria, start_request

    timings: dict[str, float] = {}
    began = datetime.now(UTC)
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

    from app.llm import load_llm_config
    from app.presearch import default_llm

    from .enrich_coverage import Recorder, top5_evidence

    stages = default_search_stages()
    stages.decide, stages.enrich = Recorder(stages.decide), Recorder(stages.enrich)  # US-41: before and after
    reader: Any = stages.extract
    returned = _Returned(reader.llm)
    reader.llm = returned
    extract = _KeepExtract(reader, returned)
    research, match, verify = (_Keep(s) for s in (stages.research, stages.match, stages.verify))
    stages.research, stages.extract, stages.match, stages.verify = research, extract, match, verify
    dropped = _Dropped(extract)
    parser_log = logging.getLogger("app.extract.parsing")
    parser_log.addHandler(dropped)
    level = parser_log.level
    parser_log.setLevel(logging.INFO)
    unwatch = returned.watch()
    try:
        request = SearchRequest(session_id=session_id, raw_query=query, rubric=step.rubric)
        result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    finally:
        parser_log.removeHandler(dropped)
        parser_log.setLevel(level)
        unwatch()
    rows, outputs = extract.snapshot()
    used, drops, price_drops = tally(rows)
    with extract._lock:
        fallbacks = Counter(extract.fallbacks)
    record = _numbers(category, result, research, outputs, match, verify, drops)
    call_log = getattr(default_llm(), "call_log", None)
    pool = load_llm_config().pool
    record["funnel"] = funnel(
        result,
        _first_per_source(outputs),
        stage_counts=extract.counts,
        fallbacks=fallbacks,
        dropped=drops,
        price_dropped=price_drops,
        per_source=rows,
        returned=used,
        alias_map=match.outputs[0].alias_map if match.outputs else {},
        calls=list(getattr(call_log, "records", [])),
        reserve={data_class: reader.settings.output_tokens(data_class) for data_class in DataClass},
        last_resort={m.provider.value: pool.public_output_tokens for m in pool.members if pool.public_output_tokens},
        spec_keys=set(reader.settings.public_spec_keys),
        settings=reader.settings,
    )
    record["presearch_seconds"] = timings
    record["steps"] = steps(record, result)
    record["top5_evidence"] = top5_evidence(stages.decide, stages.enrich)  # type: ignore[arg-type]
    record["attempts_by_minute"] = attempts_by_minute(list(getattr(call_log, "records", [])), began)
    record["answer_sizes"] = answer_sizes(list(getattr(call_log, "records", [])), began)
    record["fact_call"] = fact_calls(list(getattr(call_log, "records", [])), began)
    record["began_utc"] = {"hour": began.hour, "minute": began.minute}  # numbers: the record holds no free text
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
    outputs: list[Any],
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
    extractions = _first_per_source(outputs)  # a copy taken under the extract wrapper's lock
    kept_claims = [c for out in extractions for c in out.claims]
    # A claim dropped for "room" (S4-1) had its quote found too: it passed the quote check, then the room was full.
    reached_quote = len(kept_claims) + dropped.get("contract", 0) + dropped.get("room", 0)
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
            "sources_with_claims": sum(1 for out in extractions if out.claims),
            "sources_given_by_type": dict(sorted(given.items())),
            "claims_kept": len(kept_claims),
            "claims_by_source_type": dict(
                sorted(Counter(type_of.get(c.source_id, "unknown") for c in kept_claims).items())
            ),
            "dropped_by_reason": dict(sorted(dropped.items())),
            "quote_found_share": round(reached_quote / quote_checked, 4) if quote_checked else None,
        },
    }


# --- the funnel (US-14) ----------------------------------------------------------------------------------------------


def _spacing(name: str) -> str:
    return " ".join(name.split()).casefold()


def _sorted(counts: Mapping[str, int]) -> dict[str, int]:
    return dict(sorted((k, v) for k, v in counts.items() if v))


def _outcome_kind(call: LLMCallRecord) -> str:
    """An attempt's outcome, with our own pacing ("held": nothing sent) told apart from a provider's real 429."""
    if call.outcome == "rate_limited" and call.input_tokens == 0:
        return "held"
    return call.outcome


def funnel(
    result: SearchResult,
    extractions: Sequence[ExtractionResult],
    *,
    stage_counts: Mapping[str, int] | None,
    fallbacks: Mapping[str, int],
    dropped: Mapping[str, int],
    returned: Mapping[str, int],
    alias_map: Mapping[str, str],
    price_dropped: Mapping[str, int] | None = None,
    per_source: Mapping[str, Mapping[str, Any]] | None = None,
    calls: Sequence[LLMCallRecord],
    reserve: Mapping[DataClass, int],
    spec_keys: Collection[str],
    settings: ExtractSettings | None = None,
    last_resort: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Where one search's sources stopped turning into scored products: counts per step, each with its reason.

    `extractions` is one per source that reached extract; `stage_counts` the extract stage's own counts; `fallbacks`
    the runs the runner gave up on, by why; `dropped` the parser's drop log; `returned` the claims the model gave;
    `alias_map` match's names; `price_dropped` the parser's drops of price claims; `per_source` each source's type,
    page kind, price counts and how its call ended; `reserve` each data class's answer size, and `last_resort` the
    smaller one a pool member answers a public page with. A claim's product is found as verify finds it: its exact
    name, then its case only. With `settings`, it also counts what every public prompt carried."""
    rubric = result.request.rubric
    sources = list(result.sources)
    type_of = {s.id: s.type.value for s in sources}
    reached = {out.source_id for out in extractions}
    counts = stage_counts or {}
    weights = [w.key for w in rubric.weights]
    specs = {*spec_keys, checks.PRICE_KEY, *(rule.attribute for rule in rubric.hard_rules)}
    names = dict(alias_map)
    folded = {raw.casefold(): pid for raw, pid in sorted(names.items())}
    kept = [claim for out in extractions for claim in out.claims]

    def product_of(mention: str) -> str | None:
        return names.get(mention) or folded.get(mention.casefold())

    def key_kind(key: str | None) -> str:
        if key in weights:
            return "rubric"
        return "spec" if key is not None and (key in specs or key.startswith("has_")) else "neither"

    matched = [claim for claim in kept if product_of(claim.product_mention) is not None]
    spaced = {_spacing(raw) for raw in names}
    lost = Counter[str](
        "spacing" if _spacing(c.product_mention) in spaced else "no_product"
        for c in kept
        if product_of(c.product_mention) is None
    )
    own = [c for c in calls if c.search_id == result.search_id]
    outcomes = Counter[str](c.outcome for c in own)
    by_provider: dict[str, Counter[str]] = {}
    by_provider_outcome: dict[str, Counter[str]] = {}
    for call in own:
        by_provider.setdefault(call.provider.value, Counter())[call.data_class.value] += 1
        by_provider_outcome.setdefault(call.provider.value, Counter())[_outcome_kind(call)] += 1
    cut_off = sum(1 for c in own if _cut_off(c, reserve, last_resort or {}))

    ranked = result.decision.ranked if result.decision is not None else []
    filtered = result.decision.filtered if result.decision is not None else []
    summaries = {s.product.id: s for s in result.products}
    states = Counter[str]({state.value: 0 for state in ValueState})
    per_product: list[dict[str, Any]] = []
    by_criterion = [Counter[str]({state.value: 0 for state in ValueState}) for _ in weights]  # in rubric order
    for item in ranked:
        values = {v.criterion_key: v for v in summaries[item.product_id].values} if item.product_id in summaries else {}
        each = [values[key].state.value if key in values else ValueState.UNKNOWN.value for key in weights]
        mine = Counter[str](each)
        states.update(mine)
        for position, state in enumerate(each):
            by_criterion[position][state] += 1
        per_product.append(
            {"rank": item.rank, "scored": item.score is not None}
            | {state.value: mine.get(state.value, 0) for state in ValueState}
        )
    priced = {
        pid
        for pid, summary in summaries.items()
        for v in summary.values
        if v.criterion_key == checks.PRICE_KEY and v.state in (ValueState.KNOWN, ValueState.DISPUTED)
    }
    price_kept = [claim for claim in kept if claim.criterion_key == checks.PRICE_KEY]
    unusable = Counter[str](p for c in price_kept if (p := price_problem(c.value, c.unit)) is not None)
    by_source: dict[str, Counter[str]] = {}
    for row in (per_source or {}).values():
        counted = by_source.setdefault(f"{row['type']}.{row['kind']}", Counter())
        counted.update(
            reached=1,
            sent=int(row["sent"]),
            gave_up=int(bool(row.get("gave_up"))),
            price_asked=int(row["price_asked"]),
            price_returned=int(row["price_returned"]),
        )
    unanswered = Counter[str](
        str(row.get("answer")) for row in (per_source or {}).values() if row.get("answer") not in ("ok", "not_sent")
    )
    for_price = [f for f in filtered if f.rule_attribute in PRICE_ATTRIBUTES]
    return {
        "sources": {
            "found": len(sources),
            "by_type": _sorted(Counter(s.type.value for s in sources)),
            "by_via": _sorted(Counter(s.via for s in sources)),
        },
        "extract": {
            "reached_the_stage": len(reached),
            "not_started_time_limit": sum(1 for s in sources if s.id not in reached),
            # never sent: the search had no time left to send it and still give the later stages theirs (US-46)
            "not_reached": counts.get("not_reached", 0),
            "search_snippet": sum(1 for s in sources if s.via == SEARCH_SNIPPET and s.id in reached),
            "over_source_cap": counts.get("over_source_cap", 0),
            "over_reddit_budget": counts.get("over_reddit_budget", 0),
            "over_reddit_share": counts.get("over_reddit_share", 0),
            "sent": counts.get("sent", 0),
            "sent_with_no_criteria": counts.get("no_criteria", 0),
            "no_valid_answer": counts.get("unreadable", 0),
            # per source, how its call ended when no answer came: the run's own LLM share refused it, every provider
            # had no room, it ran out of time, it failed, or the runner gave up on the source
            "no_answer_by_reason": _sorted(unanswered),
            "timed_out": fallbacks.get("timeout", 0),
            "failed": fallbacks.get("error", 0),
        },
        "llm": {
            "calls": len(own),
            "by_provider": {p: _sorted(c) for p, c in sorted(by_provider.items())},
            "by_outcome": _sorted(outcomes),
            # per provider, how each attempt ended: counts only (US-46, S4-29). "held" is our own pacing, with nothing
            # sent; "rate_limited" is a provider's real 429.
            "by_provider_outcome": {p: _sorted(c) for p, c in sorted(by_provider_outcome.items())},
            "failed": len(own) - outcomes.get("ok", 0),
            "cut_off": cut_off,
            "no_room": outcomes.get("skipped_budget", 0) + outcomes.get("rate_limited", 0),
        },
        "claims": {
            "returned": returned.get("claims", 0),
            "dropped_by_reason": _sorted(dropped),
            "kept": len(kept),
            "kept_by_kind": _sorted(Counter("score" if c.score is not None else "value" for c in kept)),
            "kept_by_key": {kind: sum(1 for c in kept if key_kind(c.criterion_key) == kind) for kind in KEY_KINDS},
            "kept_by_source_type": _sorted(Counter(type_of.get(c.source_id, "unknown") for c in kept)),
            "scorable": sum(1 for c in matched if c.score is not None and c.criterion_key in weights),
        },
        "match": {
            "claims_matching_a_product": len(matched),
            "claims_matching_nothing": len(kept) - len(matched),
            "nothing_by_reason": _sorted(lost),  # spacing: the same name, spaced otherwise; no_product: no such name
        },
        "shown": {
            "products": len(ranked),
            "with_a_score": sum(1 for item in ranked if item.score is not None),
            "states": dict(sorted(states.items())),
            "per_product": per_product,
            "by_criterion": [dict(sorted(c.items())) for c in by_criterion],  # by the rubric's order, never its keys
        },
        "held_back": {
            "filtered_out": sum(1 for f in filtered if f.group == "filtered_out"),
            "cant_tell": sum(1 for f in filtered if f.group == "cant_tell"),
            "cant_tell_for_price": sum(1 for f in for_price if f.group == "cant_tell"),
        },
        "price": {
            "asked": rubric.budget is not None,
            "returned": returned.get(checks.PRICE_KEY, 0),
            "dropped_by_reason": _sorted(price_dropped or {}),
            "kept": len(price_kept),
            "kept_unusable_by_reason": _sorted(unusable),
            "attached": sum(1 for c in price_kept if product_of(c.product_mention) is not None),
            "products_with_a_price": len(priced),
            "shown_with_a_price": sum(1 for item in ranked if item.product_id in priced),
            "cant_tell_for_price": sum(1 for f in for_price if f.group == "cant_tell"),
            "filtered_for_price": sum(1 for f in for_price if f.group == "filtered_out"),
            "by_source": {
                kind: {k: counted[k] for k in ("reached", "sent", "gave_up", "price_asked", "price_returned")}
                for kind, counted in sorted(by_source.items())
            },
        },
    } | ({"public_prompt": _public_prompt(rubric, settings)} if settings is not None else {})


def _cut_off(call: LLMCallRecord, reserve: Mapping[DataClass, int], last_resort: Mapping[str, int]) -> bool:
    """Was this unusable answer most likely cut off at its size limit? The limit is the stage's for the data class,
    or the smaller one a pool member answers a public page with. A chunked call adds up its chunks' output, so it is
    left out rather than guessed at."""
    if call.outcome != "invalid_output" or call.fitted == "chunked":
        return False
    limit = reserve.get(call.data_class, sys.maxsize)
    if call.data_class is DataClass.PUBLIC and call.provider.value in last_resort:
        limit = min(limit, last_resort[call.provider.value])
    # The one retry of a cut-off public answer asks for CUT_OFF_ROOM times the room (S5-6a): an answer between the two
    # rooms came from that retry and was not cut off.
    if call.data_class is DataClass.PUBLIC and limit < call.output_tokens < CUT_OFF_SHARE * limit * CUT_OFF_ROOM:
        return False
    return call.output_tokens >= CUT_OFF_SHARE * limit


def _public_prompt(rubric: Rubric, settings: ExtractSettings) -> dict[str, Any]:
    """How many of the search's criteria and rules every public prompt carried, and whether it asked for the price:
    the keys the extract stage picks, by its own function, from the keys the pipeline gives it."""
    from app.decide.features import known_feature
    from app.extract.prompts import public_prompt_keys
    from app.pipeline import MAX_CATEGORY_LABEL

    weights = [w.key for w in rubric.weights]
    rules = list(dict.fromkeys(rule.attribute for rule in rubric.hard_rules))
    named = (known_feature(e.value) for e in rubric.exclusions if e.kind == "feature")
    features = list(dict.fromkeys(name for name in named if name is not None))
    category = rubric.category[:MAX_CATEGORY_LABEL]
    keys = public_prompt_keys(category, weights, rules, rubric.budget is not None, settings, features)
    return {
        "criteria": len(keys.criteria),
        "of_criteria": len(weights),
        "spec_keys": len(keys.specs),
        "price_asked": checks.PRICE_KEY in keys.specs,
        "rules_asked": sum(1 for rule in rules if rule in keys.specs),
        "rules": len(rules),
    }


# --- the record -------------------------------------------------------------------------------------------------------

READ_TYPES = ("reddit", "review_site", "manufacturer", "lab", "retailer", "youtube")  # web_search: snippets, never read


def _given(brief: Mapping[str, Any]) -> tuple[int, Mapping[str, int]]:
    """Sources given to extract, overall and by type. A record from before the fixes of 14 Nov named them
    sources_read and kept no types; its research counts by type are the same sources."""
    extraction = brief["extraction"]
    total = extraction.get("sources_given", extraction.get("sources_read", 0))
    return total, extraction.get("sources_given_by_type", brief["research"]["sources_by_type"])


def measures(briefs: list[dict[str, Any]], source: str, max_sources: int = MAX_SOURCES) -> list[dict[str, Any]]:
    done = [b for b in briefs if b.get("finished")]
    coverage = [c for b in done for c in b["products"]["coverage"]]
    given = sum(_given(b)[0] for b in done)
    kept = sum(b["extraction"]["claims_kept"] for b in done)
    dropped_quote = sum(b["extraction"]["dropped_by_reason"].get("quote", 0) for b in done)
    dropped_contract = sum(b["extraction"]["dropped_by_reason"].get("contract", 0) for b in done)
    dropped_room = sum(b["extraction"]["dropped_by_reason"].get("room", 0) for b in done)  # quote found (S4-1)
    checked = kept + dropped_contract + dropped_room + dropped_quote
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
            f"{max_sources}-source cap count too: extract passes them over without a model call",
        ),
        measure(
            "live.quote_found_share",
            (kept + dropped_contract + dropped_room) / checked if checked else None,
            "rate",
            source,
            f"claims whose quote is in their source, of {checked} the parser checked ({dropped_quote} dropped). "
            "Kept claims are counted after duplicates go; a claim dropped because the answer passed its room counts as "
            "found; a quote found but refused as an instruction counts as not found",
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
    {
        *CATEGORIES,
        *BRIEF_CATEGORIES,
        *NOT_FINISHED,
        "model",
        "fuzzy",
        "learned",
        "tier_order",
        "monte_carlo",
        "weighted",
        "independent",
    }
    | {s.value for s in StageName}
    | {s.value for s in StageStatus}
    | {t.value for t in SourceType}
    | {"api", "direct", "jina", "search_snippet", "firecrawl", "unknown", "live", "offline"}
    | {"cold", "as_found"}  # S5-6a: the research cache a brief started on
    | {p.value for p in Provider}  # D-101: the provider the fact call went to, and how it ended
    | {"ok", "invalid_output", "rate_limited", "timeout", "error", "skipped_budget"}
)
META_KEYS = {"generated", "commit", "runner", "id", "unit", "status", "source", "detail", "budget_note"}
_KEY = re.compile(r"[a-z][a-z0-9_.]*")  # a key is a field or label name: never a product name, quote or link


def text_paths(record: Any, path: str = "") -> list[str]:
    """Where text_in finds something, as field paths only ("briefs.0.cache"): what a refused record can say about
    itself. A key that failed is never shown, as it could be the text; its parent's path is."""
    if isinstance(record, dict):
        here = [path or "(top)" for k in record if not _KEY.fullmatch(str(k))]
        return here + [
            found
            for k, v in record.items()
            if _KEY.fullmatch(str(k))
            for found in text_paths(v, f"{path}.{k}" if path else str(k))
        ]
    if isinstance(record, list):
        return [found for i, item in enumerate(record) for found in text_paths(item, f"{path}.{i}")]
    if isinstance(record, str) and path.rsplit(".", 1)[-1] not in META_KEYS and record not in ALLOWED_TEXT:
        return [path]
    return []


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


@dataclass(frozen=True)
class Options:
    out: Path
    categories: tuple[str, ...]
    max_sources: int
    budget: dict[str, int]
    rebuild: bool
    cold: bool = False  # S5-6a: each brief starts on an empty research cache
    briefs: tuple[tuple[str, str], ...] = ()  # US-41: (category, request) pairs from a file, in place of QUERIES


def _cap(text: str) -> int:
    """A source cap from 1 to config/extract.yaml's own: a run may read fewer sources, never more (and the stage would
    quietly ignore anything outside 1 to 100)."""
    from app.extract.settings import load_extract_settings

    highest = load_extract_settings(env={}).max_sources_per_search
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if not 1 <= value <= highest:
        raise argparse.ArgumentTypeError(f"the cap must be 1 to {highest}")
    return value


def _recorded_cap(record: Mapping[str, Any], default: int) -> int:
    """The source cap a record was made with, from its budget note."""
    found = re.search(r"EXTRACT_MAX_SOURCES=(\d+)", str(record.get("budget_note", "")))
    return int(found[1]) if found else default


def options(argv: list[str] | None = None) -> Options:
    parser = argparse.ArgumentParser(prog="python -m evals.live_briefs", description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--rebuild", action="store_true", help="recompute the measures from the briefs already recorded (no calls)"
    )
    parser.add_argument("--out", type=Path, default=LIVE_OUT, help="the record to write (default: the committed one)")
    parser.add_argument(
        "--categories", nargs="+", choices=CATEGORIES, default=list(CATEGORIES), help="the briefs, in the order to run"
    )
    parser.add_argument(
        "--max-sources", type=_cap, default=MAX_SOURCES, help="EXTRACT_MAX_SOURCES for every brief, 1 to config's cap"
    )
    parser.add_argument(
        "--cold",
        action="store_true",
        help="before each brief, move the research cache's folders aside (never deleted; its usage counts stay)",
    )
    parser.add_argument(
        "--briefs",
        type=Path,
        default=None,
        help='a JSON list of {"category": ..., "request": ...}, run in place of the fixed queries; never written out',
    )
    for service, limit in BUDGET.items():
        parser.add_argument(f"--budget-{service}", type=int, default=limit, help=f"calls to {service} in all")
    args = parser.parse_args(argv)
    budget = {service: int(getattr(args, f"budget_{service}")) for service in BUDGET}
    return Options(
        args.out, tuple(args.categories), args.max_sources, budget, args.rebuild, args.cold, _briefs(args.briefs)
    )


def _briefs(path: Path | None) -> tuple[tuple[str, str], ...]:
    """People-written briefs (US-41, DECIDED 7): each a known category and its request. The request is sent to the
    app as the shopper's words, and never reaches the record."""
    if path is None:
        return ()
    rows = json.loads(path.read_text(encoding="utf-8"))
    briefs = tuple((str(row["category"]), str(row["request"])) for row in rows)
    unknown = sorted({category for category, _ in briefs if category not in (*CATEGORIES, *BRIEF_CATEGORIES)})
    if unknown:
        raise SystemExit(f"--briefs: {len(unknown)} categories are not among {CATEGORIES}")
    return briefs


def run_all(opts: Options, budget: LiveBudget, run: Callable[[str, str], dict[str, Any]]) -> dict[str, Any]:
    """Every brief in order, each held to an even share of what is left, and the record they make."""
    from scripts.eval_trust import current_commit

    briefs: list[dict[str, Any]] = []
    if opts.cold:
        restore_research_cache()  # anything an earlier cold run left aside goes back under the cache's expiry
    runs = list(opts.briefs) or [(category, QUERIES[category]) for category in opts.categories]
    for index, (category, query) in enumerate(runs):
        left = len(runs) - index
        share = {service: budget.left(service) // left for service in opts.budget}
        before = dict(budget.used)
        moved = set_aside_research_cache() if opts.cold else 0
        print(f"\n== {category} (share {share}){f', research cache set aside ({moved} folders)' if opts.cold else ''}")
        with budget.share(**share):
            try:
                brief = run(category, query)
            except Exception as exc:  # one brief failing must not lose the others
                print(f"   failed: {type(exc).__name__}")
                brief = {"category": category, "finished": False, "why": NOT_FINISHED[1]}
            finally:
                if opts.cold:
                    restore_research_cache()
        brief["calls"] = {service: budget.used.get(service, 0) - before.get(service, 0) for service in opts.budget}
        brief["cache"] = "cold" if opts.cold else "as_found"
        briefs.append(brief)
        print(f"   {json.dumps({k: v for k, v in brief.items() if k in ('products', 'methods', 'calls')})}")
    return {
        "generated": datetime.now(UTC).date().isoformat(),
        "commit": current_commit(),
        "runner": "python -m evals.live_briefs (live)",
        "budget_note": f"limits {opts.budget}, EXTRACT_MAX_SOURCES={opts.max_sources}, MATCHER=fuzzy",
        "calls_used": dict(budget.used),
        "measures": measures(briefs, SOURCE, opts.max_sources),
        "briefs": briefs,
    }


def _write(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def _shown(path: Path) -> str:
    try:
        return path.resolve().relative_to(BACKEND.parent).as_posix()
    except ValueError:
        return str(path)


def main(argv: list[str] | None = None) -> int:
    opts = options(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if opts.rebuild:
        recorded = json.loads(opts.out.read_text(encoding="utf-8"))
        recorded["measures"] = measures(recorded["briefs"], SOURCE, _recorded_cap(recorded, opts.max_sources))
        _write(opts.out, recorded)
        print(f"Rebuilt {len(recorded['measures'])} measures from the recorded briefs; no call was made")
        return 0
    from dotenv import load_dotenv

    load_dotenv()  # the keys stay in the environment; nothing here prints them
    os.environ["EXTRACT_MAX_SOURCES"] = str(opts.max_sources)
    os.environ["MATCHER"] = "fuzzy"  # the default (S3-15), stated so a developer's .env can't change it
    budget = LiveBudget(opts.budget)
    budget.install()  # kept until the process ends: a stage thread still running after a brief counts too
    record = run_all(opts, budget, run_brief)
    leaked = text_in(record)
    if leaked:  # never write text that could be a quote, a name or a link
        print(f"refused to write: {len(leaked)} strings are not fixed labels, at {sorted(set(text_paths(record)))}")
        return 1
    _write(opts.out, record)
    print(f"\ncalls used: {budget.used} of {opts.budget}")
    print(f"Written: {_shown(opts.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
