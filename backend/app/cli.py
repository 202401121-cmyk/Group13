"""Run one whole search from the terminal (US-52).

    python -m app.cli "best laptop for coding under 70k"

Shows what it understood from the request (US-02) and the criteria it will compare (US-03), asks the follow-up
question instead of searching when the product is unclear (US-01), runs the interview (US-05), prints the rubric,
runs the search pipeline and prints the shortlist. Works with no API keys: intake then uses its keyword rules and
criteria use the product family's fallback set.

By default it answers "Skip" to every interview question, so the rubric uses the default weights. Add
--interactive to answer the questions yourself.

The request and the finished search are each saved once, under a new session ID, when DATABASE_URL is set (US-01,
US-52). A database that is off, down or slow never stops the search; --no-db skips it altogether.

--claims also prints what was read from each source (US-14), each Reddit thread's score, the threads dropped and how
many people name each product (US-13). Verify and decide are real (S3-10b), so the shortlist is ranked from these
claims; this is where the claims themselves can be seen. They are printed and never saved.
"""

from __future__ import annotations

import argparse
import io
import os
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app import db
from app.api.sessions import new_session_id
from app.contracts import (
    Budget,
    Claim,
    CriteriaSet,
    ExtractionResult,
    Intent,
    InterviewState,
    InterviewStep,
    InterviewTurn,
    LLMCallRecord,
    MatchInput,
    MatchResult,
    Money,
    Product,
    RecommenderCount,
    Rubric,
    SearchRequest,
    SearchResult,
    Source,
    SourceTier,
    SourceType,
    Stage,
    StageContext,
    StageName,
)
from app.extract.prompts import public_prompt_keys
from app.extract.recorder import RecordingStage
from app.extract.settings import ExtractSettings, load_extract_settings
from app.extract.tiers import tier_of
from app.intake import EXAMPLE_REQUESTS
from app.interview import QUICK_REPLIES
from app.pipeline import DatabaseResultStore, PipelineRunner, default_search_stages
from app.presearch import default_llm, interview_turn, make_criteria, start_request
from app.research.reddit.quality import ThreadScore
from app.research.stage import ResearchReport

SKIP_REPLY = "Skip"
SOURCES_PER_PRODUCT = 2
MAX_TURNS = 40  # a safety stop: 10 questions, each asked at most twice, plus corrections


class _SeesCounts:
    """Wraps the match stage for --claims, to keep the counts the runner gives it. SearchResult holds none."""

    name = StageName.MATCH

    def __init__(self, inner: Stage[MatchInput, MatchResult]) -> None:
        self.inner = inner
        self.is_placeholder = bool(getattr(inner, "is_placeholder", False))
        self.counts: list[RecommenderCount] | None = None  # None until the runner reaches this stage
        self.names: list[str] = []  # the names before matching (US-15)
        self.matched: MatchResult | None = None  # the products after it

    def run(self, inp: MatchInput, ctx: StageContext) -> MatchResult:
        self.counts = list(inp.recommender_counts)
        self.names = [m.raw_name for m in inp.mentions]
        self.matched = self.inner.run(inp, ctx)
        return self.matched

    def fallback(self, inp: MatchInput, ctx: StageContext, error: Exception) -> MatchResult:
        self.matched = self.inner.fallback(inp, ctx, error)
        return self.matched

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def main(argv: list[str] | None = None) -> int:
    # Windows defaults stdout to cp1252 when output is piped, which can't encode the ₹ sign.
    # Print UTF-8 instead; errors="replace" keeps the guarantee that output never crashes the CLI.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Run one ShopSense search.")
    parser.add_argument("query", help='what you want, e.g. "best laptop for coding under 70k"')
    parser.add_argument(
        "--interactive", action="store_true", help="answer the interview questions yourself (default: skip them all)"
    )
    parser.add_argument(
        "--no-db", action="store_true", help="do not save the request or the search, even if DATABASE_URL is set"
    )
    parser.add_argument(
        "--claims", action="store_true", help="also print what was read from each source: its claims and their quotes"
    )
    args = parser.parse_args(argv)
    query: str = args.query
    store = db.NullRequestStore() if args.no_db else db.default_request_store()
    try:
        return run(query, args.interactive, store, args.claims)
    finally:
        store.close()


def run(query: str, interactive: bool, store: db.RequestStore, show_claims: bool = False) -> int:
    query = query.strip()  # trimmed once, as at the API, so blank text is simply ""
    session_id = new_session_id()
    intent = start_request(query)
    if query:  # blank text is not a request, so there is nothing to save
        print(saved_line(store.save_request(session_id, query, intent), session_id))
    if not intent.ready_for_research:
        print(clarification(intent))
        return 1  # no research until the category is known (US-01)
    print(f"Understood: {describe(intent)}")

    criteria = make_criteria(intent)
    print(criteria_block(criteria))
    step = run_interview(InterviewState(session_id=session_id, intent=intent, criteria=criteria), interactive)
    if step.rubric is None:
        print("The interview did not finish, so there is nothing to search yet.")
        return 1
    print(rubric_block(step.rubric, criteria))

    request = SearchRequest(session_id=session_id, raw_query=query, rubric=step.rubric)
    stages = default_search_stages()
    recorder = RecordingStage(stages.extract) if show_claims else None
    sees_counts = _SeesCounts(stages.match) if show_claims else None
    if recorder is not None and sees_counts is not None:
        stages.extract, stages.match = recorder, sees_counts
    result = PipelineRunner(stages, DatabaseResultStore(store)).run(request)
    print(render(result))
    report = getattr(stages.research, "last_report", None)
    if report is not None:  # the live research stage ran: show what it read
        print(research_block(result, report))
    if recorder is not None and sees_counts is not None:
        settings = load_extract_settings()
        print(claims_block(result.sources, recorder.results, settings.domain_tiers))
        print(public_keys_line(step.rubric, settings))
        dropped: tuple[ThreadScore, ...] = getattr(stages.research, "last_dropped_threads", ())
        estimated: frozenset[str] = getattr(stages.research, "last_estimated_threads", frozenset())
        print(reddit_block(result.sources, dropped, sees_counts.counts, estimated, salt_set=salt_is_set()))
        print(match_block(sees_counts.names, sees_counts.matched))
        print(llm_summary(getattr(getattr(default_llm(), "call_log", None), "records", ())))
    return 0


def saved_line(outcome: db.SaveOutcome, session_id: str) -> str:
    """One line on whether the request reached the database. Never the URL or the error details."""
    if outcome == "saved":
        return f"Saved: yes (session {session_id})"
    if outcome == "off":
        return "Saved: no, the database is off (no DATABASE_URL)"
    return "Saved: no, the database could not be reached. The search goes on."


def run_interview(state: InterviewState, interactive: bool) -> InterviewStep:
    """Ask every question in turn. Non-interactive runs answer "Skip", so the rubric keeps the default weights."""
    step = interview_turn(InterviewTurn(state=state))
    for _ in range(MAX_TURNS):
        if step.kind == "finished" or step.question is None:
            break
        print(f"Q: {step.message}")
        if interactive:
            print(f"   ({' / '.join(QUICK_REPLIES)}, or a number from 0 to 10)")
            answer = _read_answer()
        else:
            answer = SKIP_REPLY
            print(f"A: {answer}")
        step = interview_turn(InterviewTurn(state=step.state, reply_text=answer))
    return step


def _read_answer() -> str:
    try:
        answer = input("A: ").strip()[:500]
    except EOFError:  # no terminal input left: treat as a skip rather than stop
        return SKIP_REPLY
    return answer or SKIP_REPLY


def rubric_block(rubric: Rubric, criteria: CriteriaSet) -> str:
    """Each criterion's weight, its share of the score, and where the weight came from."""
    labels = {c.key: c.label for c in criteria.criteria}
    lines = ["Rubric (what matters to you):"]
    lines += [
        f"  - {labels.get(w.key, w.key)}: {w.weight:g}/10, {w.normalized:.0%} of the score ({w.origin})"
        for w in rubric.weights
    ]
    return "\n".join(lines)


def describe(intent: Intent) -> str:
    """The detected intent in one line, so the user can see what was understood (US-02)."""
    parts = [intent.category_label or "Unknown product"]
    if intent.use_case:
        parts.append(f"use {intent.use_case}")
    parts.append(budget_text(intent.budget))
    parts += [f"{len(intent.hard_rules)} must-haves"] if intent.hard_rules else []
    parts += [f"excluding {', '.join(e.value for e in intent.exclusions)}"] if intent.exclusions else []
    parts += [f"region {intent.region}", f"detected by {intent.detected_by}"]
    return " · ".join(parts)


def criteria_block(criteria: CriteriaSet) -> str:
    """The criteria the search will compare, each with its plain-English definition (US-03)."""
    lines = [f"Criteria ({criteria.source}):"]
    lines += [f"  - {c.label}: {c.definition}" for c in criteria.criteria]
    return "\n".join(lines)


def clarification(intent: Intent) -> str:
    """The follow-up question with its options, or example requests after a second unclear reply (US-01)."""
    if intent.show_example_requests:
        lines = ["I still can't tell what you're looking for. Try a request like:"]
        return "\n".join(lines + [f"  - {example}" for example in EXAMPLE_REQUESTS])
    lines = [intent.clarification_question or "What kind of product are you looking for?"]
    lines += [f"  {i}. {option}" for i, option in enumerate(intent.clarification_options, start=1)]
    if intent.budget:
        lines.append(f"(Noted: {budget_text(intent.budget)}.)")
    return "\n".join(lines)


def budget_text(budget: Budget | None) -> str:
    if budget is None:
        return "no budget given"
    if budget.min and budget.max:
        return f"budget {money(budget.min)} to {money(budget.max)}"
    if budget.max:
        return f"budget up to {money(budget.max)}"
    return f"budget from {money(budget.min)}" if budget.min else "no budget given"


def money(value: Money) -> str:
    """₹1,50,000 in Indian digit grouping; other currencies as $800."""
    whole = int(value.amount)
    amount = _indian_grouping(whole) if value.currency == "INR" else f"{whole:,}"
    if value.amount != whole:
        amount += f"{value.amount - whole:.2f}"[1:]
    return f"{_SYMBOLS.get(value.currency, value.currency + ' ')}{amount}"


_SYMBOLS = {"INR": "₹", "USD": "$"}


def _indian_grouping(number: int) -> str:
    digits = str(number)
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *groups, tail]) if head else ",".join([*groups, tail])


def research_block(result: SearchResult, report: ResearchReport) -> str:
    """What research read: each source's type, fetch time and URL, the time per source, and provider use."""
    lines = [f"\nSources ({len(result.sources)}):"]
    lines += [f"  {s.type.value:<12} {s.fetched_at:%Y-%m-%d %H:%M:%S}Z  {s.url}" for s in result.sources]
    times = ", ".join(f"{kind.value} {seconds:.1f} s" for kind, seconds in report.seconds.items())
    lines.append(f"\nTime per source: {times}; total {report.total_seconds:.1f} s")
    use = ", ".join(
        f"{name} {report.used.get(name, 0)} / {report.today.get(name, 0)} / {cap}" for name, cap in report.caps.items()
    )
    lines.append(f"Provider use (this search / today / daily cap): {use}")
    lines.append(f"Comparison query: {report.comparison or 'none (fewer than two candidates)'}")
    return "\n".join(lines)


def claims_block(
    sources: Sequence[Source], results: Mapping[str, ExtractionResult], domains: Mapping[str, SourceTier]
) -> str:
    """Per source: its type, tier, how it was read (US-38), fetch time and URL, then each claim: criterion, value or
    score, type, quote."""
    lines = ["\nClaims per source:"]
    for source in sources:
        tier = tier_of(source, domains).name.lower()
        lines.append(
            f"  {source.type.value} ({tier}) via {source.via}  {source.fetched_at:%Y-%m-%d %H:%M:%S}Z  {source.url}"
        )
        found = results.get(source.id)
        lines += [claim_line(claim) for claim in found.claims] if found and found.claims else ["    No claims."]
    return "\n".join(lines)


NO_SALT = "REDDIT_USER_SALT is not set"


def public_keys_line(rubric: Rubric, settings: ExtractSettings) -> str:
    """How many criteria and spec keys reached public prompts (US-14): fixed keys and our own definitions only. Spec
    keys include each ruled-out feature config/exclusions.yaml knows (S3-9)."""
    keys = [w.key for w in rubric.weights]
    rules = [r.attribute for r in rubric.hard_rules]
    features = [e.value for e in rubric.exclusions if e.kind == "feature"]
    public = public_prompt_keys(rubric.category, keys, rules, rubric.budget is not None, settings, features)
    return public.line(len(keys))


def llm_summary(records: Iterable[LLMCallRecord]) -> str:
    """Per provider: the calls that really went out, their median and slowest time, timeouts and 429s. Numbers only.

    A call our own limits refused has no input tokens, as it never went out: it isn't a call, or a provider's 429.
    """
    sent: dict[str, list[LLMCallRecord]] = {}
    for record in records:
        if record.input_tokens > 0:
            sent.setdefault(record.provider.value, []).append(record)
    lines = ["\nLLM calls per provider:"]
    if not sent:
        return "\n".join([*lines, "  No LLM calls went out."])
    lines.append(f"  {'provider':<12} {'calls':>5} {'median':>8} {'slowest':>8} {'timeouts':>8} {'429s':>5}")
    for provider, rows in sorted(sent.items()):
        times = [row.duration_ms / 1000 for row in rows]
        timeouts = sum(row.outcome == "timeout" for row in rows)
        limited = sum(row.outcome == "rate_limited" for row in rows)
        median = f"{statistics.median(times):.1f} s"
        lines.append(
            f"  {provider:<12} {len(rows):>5} {median:>8} {f'{max(times):.1f} s':>8} {timeouts:>8} {limited:>5}"
        )
    return "\n".join(lines)


def salt_is_set() -> bool:
    return bool(os.environ.get("REDDIT_USER_SALT", "").strip())


def match_block(names: Sequence[str], matched: MatchResult | None) -> str:
    """US-15: the names extraction found, then the products match made of them, with their variants."""
    distinct = Counter(names)
    lines = [f"\nNames before matching ({len(names)} mentions, {len(distinct)} different):"]
    lines += [f"  {name}" + (f"  (x{count})" if count > 1 else "") for name, count in sorted(distinct.items())]
    if not distinct:
        lines.append("  No names.")
    if matched is None:
        lines.append("\nProducts after matching:\n  The search stopped before match.")
        return "\n".join(lines)
    lines.append(f"\nProducts after matching ({len(matched.products)}, method {matched.method}):")
    for product in matched.products:
        lines += _product_lines(product)
    if matched.unmatched:
        lines.append(f"Kept apart, unsure: {', '.join(matched.unmatched)}")
    return "\n".join(lines)


def _product_lines(product: Product) -> list[str]:
    kind = "  (kind of product)" if product.kind == "product_type" else ""
    lines = [f"  {product.id}  {product.name}{kind}"]
    if product.aliases:
        lines.append(f"    also: {', '.join(product.aliases)}")
    return lines + [f"    variant: {variant.label}" for variant in product.variants]


def reddit_block(
    sources: Sequence[Source],
    dropped: Sequence[ThreadScore],
    counts: Sequence[RecommenderCount] | None,
    estimated: frozenset[str] = frozenset(),
    *,
    salt_set: bool = False,
) -> str:
    """Each Reddit thread's score, the threads dropped, and how many different people name each product."""
    threads = [s for s in sources if s.type is SourceType.REDDIT]
    lines = [f"\nReddit threads ({len(threads)} kept, {len(dropped)} dropped):"]
    for source in threads:
        mark = "  (estimated)" if str(source.url) in estimated else ""
        lines.append(f"  kept     {source.quality:.3f}  {source.community}  {source.url}{mark}")
    for row in dropped:
        mark = "  (estimated)" if row.estimated else ""
        lines.append(f"  dropped  {row.score:.3f}  {row.community}  {row.url}{mark}")
    if not threads and not dropped:
        lines.append("  No Reddit threads.")
    lines.append("\nRecommenders per product:")
    if counts is None:
        lines.append("  The search stopped before the counts were used.")
        return "\n".join(lines)
    lines += [
        f"  {row.product_mention}: {_people(row.distinct_recommenders)} in {_threads(row.threads)}" for row in counts
    ]
    if not counts:
        lines.append("  No products were counted.")
        if threads and not any(t.author_hash or any(c.author_hash for c in t.comments) for t in threads):
            why = "these threads carry no author hashes (they were cached before the salt was set)"
            lines.append(f"  No Reddit author is known, so nobody can be counted: {why if salt_set else NO_SALT}.")
    return "\n".join(lines)


def _people(count: int) -> str:
    return f"{count} person" if count == 1 else f"{count} people"


def _threads(count: int) -> str:
    return f"{count} thread" if count == 1 else f"{count} threads"


def claim_line(claim: Claim) -> str:
    name = f"{claim.product_mention} (kind of product)" if claim.is_product_type else claim.product_mention
    if claim.score is not None:
        said = f"score {claim.score:g}/10"
    else:
        value = claim.value
        said = str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
        if claim.unit and claim.unit.casefold() not in said.casefold():  # a text value often holds its unit already
            said = f"{said} {claim.unit}"
    quote = " ".join(claim.quote.split())
    return f'    - {name} | {claim.criterion_key} | {said} | {claim.claim_type.value} | "{quote}"'


def render(result: SearchResult) -> str:
    """The shortlist as plain text."""
    names = {p.product.id: p.product.name for p in result.products}
    lines = [f"\nResults for: {result.request.raw_query}"]

    lines += _ranked_lines(result, names)

    filtered = result.decision.filtered if result.decision else []
    for group, title in (("filtered_out", "Filtered out"), ("cant_tell", "Can't tell")):
        rows = [f for f in filtered if f.group == group]
        if rows:
            lines.append(f"\n{title}:")
            lines += [f"  - {names.get(f.product_id, f.product_id)}: {'; '.join(f.reasons)}" for f in rows]
    hint = result.decision.relaxation_hint if result.decision else None
    if hint:
        lines.append(f"\nHint: {hint}")

    if result.coverage_note:
        lines.append(f"\nCoverage note: {result.coverage_note}")
    if not result.saved:
        lines.append("\nThis search could not be saved.")
    return "\n".join(lines)


def _ranked_lines(result: SearchResult, names: Mapping[str, str]) -> list[str]:
    """Each ranked product with its score, or "not ranked" when decide had nothing to score (never a 0, DR-09)."""
    ranked = result.decision.ranked if result.decision else []
    lines = ["\nRanked:" if ranked else "\nNo product passed your rules."]
    for r in ranked:
        name = names.get(r.product_id, r.product_id)
        chance = f"{r.chance_first:.0%}" if r.chance_first is not None else "n/a"
        score = "not ranked" if r.score is None else f"score {r.score:.1f}"
        lines.append(f"  {r.rank}. {name}: {score}, coverage {r.coverage:.0%}, chance of being first {chance}")
        lines += [f"       source: {url}" for url in top_sources(result, r.product_id)]
    return lines


def top_sources(result: SearchResult, product_id: str) -> list[str]:
    """URLs of the sources that name the product, most authoritative first."""
    summary = next((p for p in result.products if p.product.id == product_id), None)
    if summary is None:
        return []
    names = [n.lower() for n in [summary.product.name, *summary.product.aliases]]

    def names_it(source: Source) -> bool:
        text = f"{source.title} {source.text}".lower()
        return any(n in text for n in names)

    matching = sorted((s for s in result.sources if names_it(s)), key=lambda s: s.tier)
    return [str(s.url) for s in matching[:SOURCES_PER_PRODUCT]]


if __name__ == "__main__":
    sys.exit(main())
