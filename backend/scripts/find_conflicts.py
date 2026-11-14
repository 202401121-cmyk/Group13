"""Find candidate conflicts for the seeded-conflicts file (US-16, S3-16, D-61). Calls real APIs: never run it in CI or
pytest.

Run from backend/:  python -m scripts.find_conflicts [--categories laptop earbuds] [--out FILE] [--budget-llm 60 ...]

For each category of ml/labelling/listings.csv it runs one search, with the database off and Reddit off, keeps every
claim the extract stage makes, and groups the claims by product and criterion. A product and criterion is a candidate
when two pages on different sites give values in the same unit that differ by more than the criterion's tolerance
(config/trust.yaml). Reddit is never a side, and one site repeated is never a conflict.

For each candidate it looks for the maker's own page: a maker page the search already read, else one search on the
brand's own site (config/research.yaml maker_domains), read through the page readers (US-38). It asks the extract stage
what that page says, and proposes the figure only when the page states it in words: the quote is the page's own words
(the extract stage checks that) and writes the number with its unit (checked here, so the 14 of "Nova Book 14" never
counts). The link and the exact words go in how_established. A page that could not be read says so.

It writes ml/data/interim/conflict_candidates/<date>.csv (git-ignored; <date>-2.csv and on for a later run that day) in
conflicts.csv's columns, with how_established starting "PROPOSED:" and added_by empty, and never writes into
ml/labelling/. A person checks every candidate against the maker's page before anything goes into conflicts.csv
(ml/DATA.md, section 9.5). Every call to a paid or rate-limited service is counted against a budget and refused past
it; the totals are printed, keys never. Each search prints where its claims dropped out, so a run with no candidates
says why.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import statistics
import sys
import threading
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Protocol, TypeVar
from urllib.parse import urlsplit

import httpx

from app.api.evidence import ClaimRecorder
from app.api.sessions import new_session_id
from app.contracts import (
    Claim,
    ExtractInput,
    InterviewState,
    MatchResult,
    SearchRequest,
    Source,
    SourceTier,
    SourceType,
    StageContext,
)
from app.decide.units import unit_key
from app.interview.rubric_builder import default_rubric
from app.llm.health import ProviderHealth
from app.pipeline import InMemoryResultStore, PipelineRunner, default_search_stages, new_search_id
from app.verify.combine import measure
from app.verify.trust import TrustTable, default_trust_table, main_domain, source_tier

REPO = Path(__file__).resolve().parents[2]
LABELLING = REPO / "ml" / "labelling"
LISTINGS = LABELLING / "listings.csv"
CONFLICTS = LABELLING / "conflicts.csv"
OUT_DIR = REPO / "ml" / "data" / "interim" / "conflict_candidates"

# conflicts.csv's columns (ml/DATA.md 9.3), in its order; a test keeps the two the same.
HEADER: tuple[str, ...] = (
    "conflict_id",
    "category",
    "product",
    "criterion",
    "unit",
    "known_answer",
    "tolerance",
    "how_established",
    "added_by",
    "claim_url",
    "claim_site",
    "claim_tier",
    "claim_value",
    "claim_retrieved_at",
    "claim_quote",
)
MAX_HOW_ESTABLISHED = 300  # ml/DATA.md 9.3
MAX_QUOTE = 200  # one sentence, at most this long
MIN_WORDS = 40  # a proposal needs room for this many characters of the page's words beside its link
_T = TypeVar("_T")

# One made-up request per category of listings.csv. Each has a budget, and most a spec, so the search asks for figures.
QUERIES: dict[str, str] = {
    "smartphone": "phone with a good camera under ₹30,000 with at least 8 GB RAM",
    "air_fryer": "air fryer for a family of four under ₹8,000",
    "laptop": "laptop for coding under ₹70,000 with at least 16 GB RAM",
    "smartwatch": "smartwatch with a long battery life under ₹5,000",
    "earbuds": "earbuds for the gym under ₹3,000",
    "headphones": "noise cancelling headphones under ₹10,000",
}

REDDIT_HOSTS = ("reddit.com", "redd.it")
# A number as a page writes it: never the 990 after a comma or the 5 after "4." (a name's digits are refused below).
_FIGURE = re.compile(r"(?<![\d,])(?<!\d\.)\d[\d,]*(?:\.\d+)?(?!\d)")
_CURRENCY_BEFORE = re.compile(r"(?<![A-Za-z])(?:₹|rs\.?|inr|\$|usd)\s*$", re.IGNORECASE)
_UNIT_AFTER = re.compile(r"[\s-]*([A-Za-z]+|\"|″|”)")
# check_conflicts.py's SECOND_SENTENCE: a stop, any closing quotes or brackets, a space, then a capital letter.
_SENTENCE_BREAK = re.compile(r"[.!?][\"'”’)]*(\s+)[\"'“‘(]*[A-Z]")
_HIDDEN = frozenset({"Cc", "Cf", "Zl", "Zp"})  # check_conflicts.py's hidden characters


# --- what a search found, and the candidates in it --------------------------------------------------------------------


@dataclass(frozen=True)
class Found:
    """One search: every claim the extract stage made, the sources they came from, and the matched products."""

    category: str
    matched: MatchResult
    claims: list[Claim]
    sources: dict[str, Source]


@dataclass(frozen=True)
class Side:
    """One page's value for a product and criterion."""

    url: str
    site: str
    tier: SourceTier
    value: float
    unit: str
    unit_key: str
    retrieved_at: datetime
    quote: str


@dataclass(frozen=True)
class Candidate:
    category: str
    product: str
    product_id: str
    brand: str | None
    aliases: tuple[str, ...]
    criterion: str
    unit: str  # as most of the pages write it
    unit_key: str
    sides: tuple[Side, ...]


@dataclass(frozen=True)
class Proposal:
    """What the maker's page says: a figure with its link and exact words, or why there is none."""

    answer: float | None = None
    url: str | None = None
    words: str | None = None
    why: str | None = None  # with a page and no answer: what happened, as the end of a sentence about the page


NOT_READ = "could not be read for this figure; check it by hand"
LINK_TOO_LONG = "has a link too long to quote its words here; check it by hand"


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _is_reddit(source: Source) -> bool:
    host = _host(str(source.url))
    return source.type is SourceType.REDDIT or any(host == h or host.endswith("." + h) for h in REDDIT_HOSTS)


def _resolver(matched: MatchResult) -> dict[str, str]:
    """Every name a product goes by, folded, as verify reads them (app/verify/resolve.py)."""
    names = {raw.casefold(): pid for raw, pid in matched.alias_map.items()}
    for product in matched.products:
        for name in (product.name, *product.aliases):
            names.setdefault(name.casefold(), product.id)
    return names


def _differ(a: float, b: float, table: TrustTable, key: str) -> bool:
    """Beyond the criterion's tolerance both ways round: Tolerance.allows measures from one side only."""
    tolerance = table.tolerance(key)
    return not (tolerance.allows(a, b) or tolerance.allows(b, a))


@dataclass
class Funnel:
    """Where a search's claims drop out on the way to a candidate, so a run that finds none says why."""

    claims: int = 0
    with_figure: int = 0  # a criterion, a number and a unit, and not from Reddit
    on_a_product: int = 0  # ... and naming a matched product
    on_two_sites: int = 0  # products and criteria with pages on two sites or more, in one unit
    already_known: int = 0  # products and criteria conflicts.csv already holds
    candidates: int = 0

    def __str__(self) -> str:
        return (
            f"{self.claims} claims, {self.with_figure} with a figure, {self.on_a_product} on a matched product; "
            f"{self.on_two_sites} product-criterion pairs on two sites or more, {self.already_known} already in "
            f"conflicts.csv, {self.candidates} candidates"
        )


def find_candidates(
    found: Found, table: TrustTable, known: set[tuple[str, str]], funnel: Funnel | None = None
) -> list[Candidate]:
    """Each product and criterion where two pages on different sites disagree beyond the tolerance, in one unit.

    A page counts once (its first claim). Reddit is never a side. A claim in another unit than most pages use is left
    out: the file's values are in the conflict's unit, and no tool converts them (ml/DATA.md 9.3). A product and
    criterion already in conflicts.csv (`known`, folded product names) is skipped: each is entered once. `funnel`, if
    given, counts where the claims dropped out.
    """
    counts = funnel if funnel is not None else Funnel()
    counts.claims += len(found.claims)
    names = _resolver(found.matched)
    products = {p.id: p for p in found.matched.products}
    groups: dict[tuple[str, str], dict[str, Side]] = {}
    for claim in found.claims:
        source = found.sources.get(claim.source_id)
        if claim.criterion_key is None or source is None or _is_reddit(source):
            continue
        measured = measure(claim.value, claim.unit, claim.criterion_key)
        if measured.raw is None or not measured.unit:
            continue
        counts.with_figure += 1
        product_id = names.get(claim.product_mention.casefold())
        if product_id is None:
            continue
        counts.on_a_product += 1
        url = str(source.url)
        side = Side(
            url=url,
            site=main_domain(_host(url)),
            tier=source_tier(source, table),  # as verify ranks it (config/trust.yaml), and eval_trust reads it
            value=measured.raw,
            unit=(claim.unit or measured.unit).strip(),
            unit_key=measured.unit,
            retrieved_at=claim.retrieved_at,
            quote=claim.quote,
        )
        groups.setdefault((product_id, claim.criterion_key), {}).setdefault(url, side)

    found_now: list[Candidate] = []
    for (product_id, key), by_page in groups.items():
        product = products.get(product_id)
        if product is None:
            continue
        unit_key = Counter(s.unit_key for s in by_page.values()).most_common(1)[0][0]
        sides = tuple(s for s in by_page.values() if s.unit_key == unit_key)
        if len({s.site for s in sides}) < 2:
            continue
        counts.on_two_sites += 1
        if (product.name.casefold(), key) in known:
            counts.already_known += 1
            continue
        disagree = any(a.site != b.site and _differ(a.value, b.value, table, key) for a, b in combinations(sides, 2))
        if not disagree:
            continue
        counts.candidates += 1
        found_now.append(
            Candidate(
                category=found.category,
                product=product.name,
                product_id=product_id,
                brand=product.brand,
                aliases=tuple(product.aliases),
                criterion=key,
                unit=Counter(s.unit for s in sides).most_common(1)[0][0],
                unit_key=unit_key,
                sides=tuple(sorted(sides, key=lambda s: (s.site, s.url))),
            )
        )
    return sorted(found_now, key=lambda c: (c.product.casefold(), c.criterion))


# --- the maker's page -------------------------------------------------------------------------------------------------


def _plain(text: str) -> str:
    """The page's words on one line: line breaks and runs of spaces become one space, and hidden characters go, so the
    words fit one CSV line and check_conflicts.py reads them (it reads a file line by line)."""
    return " ".join("".join(c for c in text if unicodedata.category(c) not in _HIDDEN or c.isspace()).split())


def _stated_at(text: str, number: float, unit: str) -> tuple[int, int] | None:
    """Where `text` writes `number` with its unit: the unit after it ("11 hours", "16GB", "11-hour") or its currency
    before it ("₹49,990", "Rs. 49,990"). A number inside a name ("Nova Book 14 specs", "i7", "DDR5") never counts."""
    for found in _FIGURE.finditer(text):
        try:
            value = float(found.group().replace(",", ""))
        except ValueError:
            continue
        if abs(value - number) > 1e-9:
            continue
        currency = _CURRENCY_BEFORE.search(text[max(0, found.start() - 6) : found.start()])
        if currency is not None:
            if unit_key(currency.group()) == unit:
                return found.start() - len(currency.group()), found.end()
            continue
        after = _UNIT_AFTER.match(text, found.end())
        if text[found.start() - 1 : found.start()].isalpha() or after is None:
            continue
        if unit_key(after.group(1)) == unit:
            return found.start(), after.end()
    return None


def _sentence_at(text: str, at: int) -> tuple[int, int]:
    """The sentence holding position `at`, split where check_conflicts.py sees a second sentence."""
    start = 0
    for found in _SENTENCE_BREAK.finditer(text):
        if found.start(1) > at:
            return start, found.start(1)
        start = found.end(1)
    return start, len(text)


def _words_around(text: str, number: float, unit: str, limit: int) -> str | None:
    """The page's own words that state `number` in `unit`: their sentence, cut to `limit` characters around the figure
    if longer. Always one unbroken run of the text (with its spacing made plain), never words joined from two places.
    None when the text doesn't state the figure in that unit."""
    flat = _plain(text)
    span = _stated_at(flat, number, unit)
    if span is None or span[1] - span[0] > limit:
        return None
    start, end = _sentence_at(flat, span[0])
    sentence = flat[start:end].strip()
    offset = start + (len(flat[start:end]) - len(flat[start:end].lstrip()))
    if len(sentence) <= limit:
        return sentence
    first, last = span[0] - offset, span[1] - offset
    left = max(0, first - (limit - (last - first)) // 2)
    right = min(len(sentence), left + limit)
    left = max(0, right - limit)
    if left > 0 and (space := sentence.find(" ", left, first)) != -1:
        left = space + 1  # no half word at the start
    if right < len(sentence) and (space := sentence.rfind(" ", last, right)) != -1:
        right = space  # nor at the end
    return sentence[left:right].strip()


def propose(candidate: Candidate, page: Source | None, extracted: Sequence[Claim]) -> Proposal:
    """The figure the maker's page states in words for this product and criterion, in the conflict's unit, or none."""
    if page is None:
        return Proposal()
    url = str(page.url)
    room = MAX_HOW_ESTABLISHED - len(f"PROPOSED: the maker page {url} says ''.")
    if room < MIN_WORDS:
        return Proposal(url=url, why=LINK_TOO_LONG)
    names = {candidate.product.casefold(), *(a.casefold() for a in candidate.aliases)}
    for claim in extracted:
        if claim.criterion_key != candidate.criterion or claim.product_mention.casefold() not in names:
            continue
        measured = measure(claim.value, claim.unit, candidate.criterion)
        if measured.raw is None or measured.unit != candidate.unit_key:
            continue
        words = _words_around(claim.quote, measured.raw, candidate.unit_key, room)
        if words is not None:
            return Proposal(answer=measured.raw, url=url, words=words)
    return Proposal(url=url, why=f"doesn't state it in {candidate.unit}")


def how_established(proposal: Proposal) -> str:
    """The note for a person, at most 300 characters. With a proposed answer it always fits (propose keeps room)."""
    if proposal.answer is not None:
        return f"PROPOSED: the maker page {proposal.url} says '{proposal.words}'."
    if proposal.url is None:
        return "PROPOSED: no answer: no maker page found."
    said = f"PROPOSED: no answer: the maker page {proposal.url} {proposal.why or 'was found'}."
    if len(said) <= MAX_HOW_ESTABLISHED:
        return said
    return "PROPOSED: no answer: the maker page's link is too long for this column; find the page again by hand."


# --- the file ---------------------------------------------------------------------------------------------------------


def _number_text(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def candidate_rows(
    candidate: Candidate, proposal: Proposal, conflict_id: str, table: TrustTable
) -> list[dict[str, str]]:
    """One row per page, in conflicts.csv's columns. The tolerance is the criterion's share of the answer (or, with no
    answer, of the pages' middle value): a starting point for the person to check."""
    base = proposal.answer if proposal.answer is not None else statistics.median(s.value for s in candidate.sides)
    tolerance = round(table.tolerance(candidate.criterion).relative * abs(base), 2)
    shared = {
        "conflict_id": conflict_id,
        "category": candidate.category,
        "product": candidate.product,
        "criterion": candidate.criterion,
        "unit": candidate.unit,
        "known_answer": "" if proposal.answer is None else f"{_number_text(proposal.answer)} {candidate.unit}",
        "tolerance": _number_text(tolerance),
        "how_established": how_established(proposal),
        "added_by": "",
    }
    return [
        {
            **shared,
            "claim_url": side.url,
            "claim_site": side.site,
            "claim_tier": side.tier.name.lower(),
            "claim_value": f"{_number_text(side.value)} {candidate.unit}",
            "claim_retrieved_at": side.retrieved_at.astimezone(UTC).date().isoformat(),
            "claim_quote": _words_around(side.quote, side.value, side.unit_key, MAX_QUOTE) or "",
        }
        for side in candidate.sides
    ]


def refuse_labelling(path: Path) -> None:
    """Never anywhere in ml/labelling/: conflicts.csv is a person's to fill (ml/DATA.md 9.2)."""
    target = path.resolve()
    if target == CONFLICTS.resolve() or LABELLING.resolve() in target.parents:
        raise ValueError("find_conflicts never writes into ml/labelling: a person copies the checked rows there")


def write_candidates(path: Path, rows: Sequence[Mapping[str, str]]) -> None:
    """The candidates file, new: an earlier run's file is never overwritten."""
    refuse_labelling(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def default_out(today: date | None = None, folder: Path = OUT_DIR) -> Path:
    """<date>.csv, or <date>-2.csv and on when an earlier run today already wrote one."""
    day = (today or datetime.now(UTC).date()).isoformat()
    path, n = folder / f"{day}.csv", 1
    while path.exists():
        n += 1
        path = folder / f"{day}-{n}.csv"
    return path


def _rows(path: Path) -> list[dict[str, str]]:
    """A CSV file's rows. utf-8-sig: a file saved from a spreadsheet as "CSV UTF-8" starts with a byte-order mark."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def categories(listings: Path) -> list[str]:
    """listings.csv's categories, in the order they first appear. The file is read; ml code is never imported."""
    return list(dict.fromkeys(row["category"] for row in _rows(listings) if row.get("category")))


def known_pairs(conflicts: Path) -> set[tuple[str, str]]:
    """The products (folded) and criteria conflicts.csv already holds."""
    return {(row["product"].casefold(), row["criterion"]) for row in _rows(conflicts)}


def next_conflict_ids(*files: Path) -> Iterator[str]:
    """c-numbers after the last one these files use (conflicts.csv and earlier candidate files), so checked rows can be
    copied as they are, even from two runs' files."""
    numbers = (re.fullmatch(r"c(\d{3})", row.get("conflict_id") or "") for path in files for row in _rows(path))
    n = max((int(found.group(1)) for found in numbers if found), default=0) + 1
    while True:
        yield f"c{n:03d}"
        n += 1


class MakerPages(Protocol):
    def find(self, candidate: Candidate, found: Found) -> Source | None: ...


class Extractor(Protocol):
    def claims(self, page: Source, candidate: Candidate) -> Sequence[Claim]: ...


def write_found(
    founds: Iterable[Found],
    maker: MakerPages,
    extractor: Extractor,
    out: Path,
    table: TrustTable,
    known: set[tuple[str, str]],
    ids: Iterator[str],
) -> int:
    """Candidates from every search, each with what its maker's page says, written to `out`. Returns how many."""
    rows: list[dict[str, str]] = []
    count = 0
    for found in founds:
        funnel = Funnel()
        found_here = find_candidates(found, table, known, funnel)
        print(f"  {found.category}: {funnel}")
        for candidate in found_here:
            known.add((candidate.product.casefold(), candidate.criterion))  # once, even if another search finds it
            rows += candidate_rows(candidate, _ask_maker(candidate, found, maker, extractor), next(ids), table)
            count += 1
    write_candidates(out, rows)
    return count


def _ask_maker(candidate: Candidate, found: Found, maker: MakerPages, extractor: Extractor) -> Proposal:
    """What the maker's page says. A failure never drops the candidate: it stays, with no proposed answer, and a page
    that could not be read says so, rather than that it doesn't state the figure."""
    page = _safely(lambda: maker.find(candidate, found))
    if page is None:
        return Proposal()
    extracted = _safely(lambda: list(extractor.claims(page, candidate)))
    if extracted is None:
        return Proposal(url=str(page.url), why=NOT_READ)
    return propose(candidate, page, extracted)


def _safely(call: Callable[[], _T]) -> _T | None:
    try:
        return call()
    except Exception as exc:
        print(f"  (maker page: {type(exc).__name__}; no answer proposed)")
        return None


class PageNotRead(RuntimeError):
    """The extract stage could not read a maker page (no model answered, or a limit held it back)."""


# --- the budget -------------------------------------------------------------------------------------------------------


class OverBudget(httpx.TransportError):
    """Raised for a call past its budget. The app takes it as a network failure, so the search carries on without it."""


LLM_HOSTS = (
    "api.groq.com",
    "generativelanguage.googleapis.com",
    "openrouter.ai",
    "api.cerebras.ai",
    "api.mistral.ai",
    "api.deepseek.com",
)
REDDIT_CALL_HOSTS = ("reddit.com", "redd.it", "photon-reddit.com", "pullpush.io", "pushshift.io")


def service_of(url: httpx.URL) -> tuple[str | None, int]:
    """Which budget a call counts against, and its cost. A plain page read counts against none."""
    host = (url.host or "").lower()
    if any(host == h or host.endswith("." + h) for h in REDDIT_CALL_HOSTS):
        return "reddit", 1
    if host in LLM_HOSTS:
        return "llm", 1
    if host == "google.serper.dev":
        return "serper", 1
    if host == "jina.ai" or host.endswith(".jina.ai"):
        return "jina", 1
    if host == "api.firecrawl.dev":
        return "firecrawl", 2 if "search" in url.path else 1  # a search costs 2 credits, a scrape 1
    return None, 0


class Budget:
    """Calls per service, refused past the total or past the share a phase was given. Reddit is always refused."""

    def __init__(self, limits: Mapping[str, int]) -> None:
        self.limits = dict(limits)
        self.used = dict.fromkeys(self.limits, 0)
        self._caps: dict[str, int] = {}
        self._lock = threading.Lock()

    def left(self, service: str) -> int:
        return self.limits.get(service, 0) - self.used.get(service, 0)

    def admit(self, request: httpx.Request) -> None:
        service, cost = service_of(request.url)
        if service is None:
            return
        if service == "reddit":
            raise OverBudget("Reddit is off for this script (D-53)", request=request)
        with self._lock:
            spent = self.used.get(service, 0) + cost
            cap = min(self.limits.get(service, 0), self._caps.get(service, sys.maxsize))
            if spent > cap:
                raise OverBudget(f"the {service} budget is spent", request=request)
            self.used[service] = spent

    @contextmanager
    def share(self, **parts: int) -> Iterator[None]:
        """Within this block a service may spend at most `parts[service]` more."""
        with self._lock:
            before = dict(self._caps)
            self._caps |= {service: self.used.get(service, 0) + n for service, n in parts.items()}
        try:
            yield
        finally:
            with self._lock:
                self._caps = before

    def install(self) -> Callable[[], None]:
        """Every httpx call in the process goes through `admit` first. Returns what undoes it.

        A refused LLM call reaches the LLM client as a failed request (ProviderError). Counted as the provider's
        failure, three in a minute would pause it for 90 seconds, into the next search and the maker pages, with calls
        still left. So a failure the budget caused says nothing about the provider's health."""
        send = httpx.HTTPTransport.handle_request
        record = ProviderHealth.record_failure
        budget = self

        def handle(transport: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
            budget.admit(request)
            return send(transport, request)

        def record_failure(health: ProviderHealth, provider: Any, error: Exception, model: str | None = None) -> bool:
            if refused_by_budget(error):
                health.release(provider, model)
                return False
            return record(health, provider, error, model)

        httpx.HTTPTransport.handle_request = handle  # type: ignore[method-assign,assignment]
        ProviderHealth.record_failure = record_failure  # type: ignore[method-assign,assignment]

        def undo() -> None:
            httpx.HTTPTransport.handle_request = send  # type: ignore[method-assign]
            ProviderHealth.record_failure = record  # type: ignore[method-assign]

        return undo

    @contextmanager
    def guarding(self) -> Iterator[None]:
        undo = self.install()
        try:
            yield
        finally:
            undo()


def refused_by_budget(error: BaseException) -> bool:
    """The error, or one it was raised from (the provider adapters raise `from None`, which keeps the context)."""
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, OverBudget):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


# --- the live run -----------------------------------------------------------------------------------------------------

DEFAULT_BUDGET = {"llm": 60, "serper": 15, "jina": 20, "firecrawl": 15}  # S3-16's
MAKER_RESERVE = {"llm": 12, "serper": 3, "jina": 4, "firecrawl": 3}  # kept for the maker pages after the searches
SERVICES = tuple(DEFAULT_BUDGET)


class _SeesMatch:
    """Wraps the match stage to keep the products and their names; SearchResult keeps no alias map."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = inner.name
        self.is_placeholder = bool(getattr(inner, "is_placeholder", False))
        self.matched: MatchResult | None = None

    def run(self, inp: Any, ctx: StageContext) -> MatchResult:
        matched: MatchResult = self.inner.run(inp, ctx)
        self.matched = matched
        return matched

    def fallback(self, inp: Any, ctx: StageContext, error: Exception) -> MatchResult:
        matched: MatchResult = self.inner.fallback(inp, ctx, error)
        self.matched = matched
        return matched

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def run_search(category: str, query: str) -> Found | None:
    """One search as app.cli runs it, with no interview (the default weights), and every claim kept."""
    from app.presearch import make_criteria, start_request  # here: it reads .env and builds the LLM client

    intent = start_request(query)
    if not intent.ready_for_research:
        print(f"  {category}: the request needs a clarification; skipped")
        return None
    criteria = make_criteria(intent)  # the criteria file cache, backend/.cache/criteria/ (build rule 7): no database
    state = InterviewState(session_id=new_session_id(), intent=intent, criteria=criteria)
    stages = default_search_stages()
    recorder = ClaimRecorder(stages.extract)
    seer = _SeesMatch(stages.match)
    stages.extract = recorder
    stages.match = seer
    request = SearchRequest(session_id=state.session_id, raw_query=query, rubric=default_rubric(state))
    result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    if seer.matched is None:
        print(f"  {category}: no products were matched")
        return None
    claims = list(recorder.claims().values())
    print(f"  {category}: {len(result.sources)} sources, {len(claims)} claims, {len(seer.matched.products)} products")
    return Found(category, seer.matched, claims, {s.id: s for s in result.sources})


class LiveMakerPages:
    """A maker page the search read, else one search on the brand's own site, read through the page readers."""

    def __init__(self) -> None:
        from app.research.cache import ResearchCache
        from app.research.client import build_research_client
        from app.research.pages import PageReaders, ReviewPages
        from app.research.readers import FirecrawlClient, JinaReader
        from app.research.serper import SerperSource
        from app.research.settings import load_research_settings
        from app.research.usage import UsageBook

        self.settings = load_research_settings()
        http = build_research_client()
        cache = ResearchCache(enabled=self.settings.cache_on)
        firecrawl_key = os.environ.get("FIRECRAWL_API_KEY", "").strip() or None
        readers = PageReaders(
            jina=JinaReader(http, os.environ.get("JINA_API_KEY", "").strip() or None),
            firecrawl=FirecrawlClient(http, firecrawl_key, cache=cache) if firecrawl_key else None,
        )
        self.pages = ReviewPages(http, self.settings, cache=cache, readers=readers)
        serper_key = os.environ.get("SERPER_API_KEY", "").strip()
        self.serper = SerperSource(http, serper_key, cache=cache) if serper_key else None
        self.usage = UsageBook(self.settings.caps, path=None).search()

    def find(self, candidate: Candidate, found: Found) -> Source | None:
        names = {candidate.product.casefold(), *(a.casefold() for a in candidate.aliases)}
        for source in found.sources.values():
            words = f"{source.title or ''} {source.text}".casefold()
            if source.type is SourceType.MANUFACTURER and any(name in words for name in names):
                return source
        brand = (candidate.brand or candidate.product.split()[0]).casefold()
        domains = self.settings.maker_domains.get(brand)
        if not domains or self.serper is None:
            return None
        hits = self.serper.search(candidate.product, "IN", self.usage, sites=domains)
        for hit in hits.sources[:2]:
            read = self.pages.fetch(str(hit.url), self.usage)
            if read.source is not None:
                return read.source
        return None


class LiveExtractor:
    """The extract stage on one maker page, asked only about the candidate's criterion."""

    def __init__(self) -> None:
        from app.extract import default_extract_stage
        from app.research.settings import load_research_settings

        self.stage = default_extract_stage()
        self.max_tokens = load_research_settings().max_source_tokens

    def claims(self, page: Source, candidate: Candidate) -> Sequence[Claim]:
        from app.research.capping import cap_source

        search_id = new_search_id()
        self.stage.ask_for_specs(search_id, [candidate.criterion], has_budget=candidate.criterion == "price_inr")
        given = ExtractInput(
            source=cap_source(page, self.max_tokens),
            criterion_keys=[candidate.criterion],
            category_label=candidate.category[:60],
        )
        claims = self.stage.run(given, StageContext(search_id=search_id)).claims
        skipped = self.stage.coverage_note(search_id)  # also frees what the stage kept for this search
        if skipped:  # no model answered, or a limit held the page back: not read, which is not "doesn't state it"
            raise PageNotRead(skipped)
        return claims


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.find_conflicts",
        description="Propose candidate conflicts for a person to check (ml/DATA.md 9.5). Calls real APIs.",
    )
    parser.add_argument("--categories", nargs="+", help="some of listings.csv's categories (default: all)")
    parser.add_argument("--out", type=Path, help=f"the candidates file (default: {OUT_DIR}/<today>.csv)")
    for service, limit in DEFAULT_BUDGET.items():
        parser.add_argument(
            f"--budget-{service}", type=int, default=limit, help=f"most {service} calls (default {limit})"
        )
    parser.add_argument("--max-sources", type=int, default=5, help="pages the extract stage reads per search")
    args = parser.parse_args(argv)
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    wanted = args.categories or categories(LISTINGS)
    unknown = [c for c in wanted if c not in QUERIES]
    if unknown:
        parser.error(f"no request for {', '.join(unknown)}: add one to QUERIES")
    out = args.out or default_out()
    try:  # before any call is spent, not after
        refuse_labelling(out)
    except ValueError as exc:
        parser.error(str(exc))
    if out.exists():
        parser.error(f"{out} already exists; an earlier run's candidates are never overwritten")

    from dotenv import load_dotenv

    load_dotenv()  # the keys stay in the environment; nothing here prints them
    os.environ["REDDIT_SOURCE"] = "off"
    os.environ["EXTRACT_MAX_SOURCES"] = str(args.max_sources)
    budget = Budget({service: getattr(args, f"budget_{service}") for service in SERVICES})
    reserve = {service: min(MAKER_RESERVE[service], budget.limits[service]) for service in SERVICES}
    # Never undone: a stage thread the runner gave up on may still call out after the last search, and it counts too.
    budget.install()

    founds: list[Found] = []
    for index, category in enumerate(wanted):
        searches_left = len(wanted) - index
        share = {s: max(0, (budget.left(s) - reserve[s]) // searches_left) for s in SERVICES}
        print(f"{category}: {QUERIES[category]!r}  (share {share})")
        with budget.share(**share):
            try:
                found = run_search(category, QUERIES[category])
            except Exception as exc:
                print(f"  {category}: the search failed ({type(exc).__name__})")
                found = None
        if found is not None:
            founds.append(found)
    print("maker pages:")
    count = write_found(
        founds,
        LiveMakerPages(),
        LiveExtractor(),
        out,
        default_trust_table(),
        known_pairs(CONFLICTS),
        next_conflict_ids(CONFLICTS, *sorted(OUT_DIR.glob("*.csv"))),
    )
    print(f"\n{count} candidates in {out}")
    print("calls used: " + ", ".join(f"{s} {budget.used[s]} of {budget.limits[s]}" for s in SERVICES) + ", reddit 0")
    print("Check every candidate against the maker's page before it goes into conflicts.csv (ml/DATA.md 9.5).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
