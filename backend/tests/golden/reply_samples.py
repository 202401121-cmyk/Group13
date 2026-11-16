"""US-39, criterion 10: the share of 70 made-up replies (10 per type) the interview classifies correctly.

A reply is read as the interview reads it: code first (buttons, numbers, "skip"), the classifier for the rest. It is
correct when its type is the sample's, and for a correction when it changes the sample's question too. A classifier
failure is never correct: criterion 11 makes it an answer, which would otherwise score on every answer sample.

    python -m tests.golden.reply_samples                    # offline: code alone, no LLM
    python -m tests.golden.reply_samples --live --check 5   # 5 replies live, one at a time; exits 1 if any timed out
    python -m tests.golden.reply_samples --live --spent 5   # all 70 live, once; writes interview/live_record.json

Live runs ask Groq only (private text), one reply at a time, as a shopper would, paced under each Groq model's
per-minute limits as config/models.yaml gives them (pace_from_config). Each reply records the model that answered,
its latency and whether an attempt timed out. Run from backend/; the app reads the API keys from .env itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts import (
    STAGE_TIME_LIMITS_S,
    AskedQuestion,
    DataClass,
    Difficulty,
    InterviewState,
    LLMClient,
    LLMTask,
    LLMUnavailable,
    ReplyClassification,
    ReplyType,
    StageName,
    examples,
)
from app.interview.answers import understand_reply
from app.interview.parsing import RawReply
from app.interview.prompts import CLASSIFY_SYSTEM, classify_message
from app.interview.questions import question_for
from app.interview.replies import CLASSIFY_TOKENS, classify, criterion_of
from app.llm.config import load_llm_config
from app.llm.fitting import estimate_tokens
from app.llm.output import build_system_prompt

HERE = Path(__file__).resolve().parent / "interview"
SAMPLES_PATH = HERE / "replies.json"
RECORD_PATH = HERE / "live_record.json"
TARGET = 0.9  # US-39 criterion 10: never lowered
LIVE_BUDGET = 80  # LLM requests sent (the prompt's budget)
CALL_ROOM = 6  # one call can try the three Groq models, each with its one retry
TURN_S = STAGE_TIME_LIMITS_S[StageName.INTERVIEW]  # 5 s
EARLIER_WEIGHT = 8.0  # what each earlier question was answered (High)

Source = str  # "code": code read it; "llm": the classifier did; "failed": the classifier failed, so it is an answer


def load_samples(path: Path = SAMPLES_PATH) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))["samples"]
    return samples


def samples_digest(path: Path = SAMPLES_PATH) -> str:
    """The set's fingerprint, so a record is known to be of these very replies."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def situation(sample: dict[str, Any]) -> tuple[InterviewState, AskedQuestion]:
    """The interview as the sample has it: its earlier questions answered (High), and its question pending."""
    criteria = examples.criteria()
    by_key = {c.key: c for c in criteria.criteria}
    history = [
        AskedQuestion(
            question=question_for(by_key[key]),
            status="answered",
            reply_text="High",
            classification=ReplyClassification(
                reply_type=ReplyType.ANSWER, weight=EARLIER_WEIGHT, classified_by="fallback"
            ),
        )
        for key in sample["answered"]
    ]
    pending = AskedQuestion(question=question_for(by_key[sample["asked"]]), status="pending")
    state = InterviewState(
        session_id=examples.SESSION_ID,
        intent=examples.intent(),
        criteria=criteria,
        history=[*history, pending],
    )
    return state, pending


def read(sample: dict[str, Any], llm: LLMClient | None) -> tuple[ReplyClassification, Source, float]:
    """How the interview reads this reply, who read it, and how long the reading took (seconds)."""
    state, pending = situation(sample)
    text = sample["reply"]
    started = time.monotonic()
    reply = understand_reply(text, pending.question)
    if reply.reply_type is not ReplyType.UNCLEAR or not text.strip() or llm is None:
        return reply, "code", time.monotonic() - started
    reply = classify(llm, text, state, pending, "srch_replysamples", started)
    return reply, "llm" if reply.classified_by == "llm" else "failed", time.monotonic() - started


def is_correct(sample: dict[str, Any], reply: ReplyClassification, source: Source = "llm") -> bool:
    """Its type, and for a correction its question. A classifier failure is never correct."""
    if source == "failed" or reply.reply_type.value != sample["type"]:
        return False
    if reply.reply_type is ReplyType.CORRECTION:
        return reply.corrects_question_id == f"q_{sample['corrects']}"
    return True


def measure(
    llm: LLMClient | None,
    samples: Sequence[dict[str, Any]] | None = None,
    pace_s: float = 0.0,
    log: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """The share correct, by type and in all, with each reply's reading, latency and attempts (from `log`, the
    client's call log, when given). `pace_s` is the pause after one reply before the next.

    A reply's attempts are the records written from its start to the next reply's start: a hedged attempt that lost
    finishes in the background during the pause, and still belongs to its reply. A real 429 from Groq (a rate limit,
    perhaps the daily one) stops the run: the result then says so in "stopped"."""
    rows = list(samples if samples is not None else load_samples())
    readings: list[tuple[dict[str, Any], ReplyClassification, Source, float, int]] = []
    stopped: str | None = None
    last_started = time.monotonic()
    for n, sample in enumerate(rows):
        if pace_s and n:
            time.sleep(pace_s)
        if log is not None and _rate_limited(list(log)):
            stopped = f"Groq answered 429 (a rate limit, perhaps the daily one) before {sample['id']}"
            break
        before = len(log) if log is not None else 0
        last_started = time.monotonic()
        reply, source, took = read(sample, llm)
        readings.append((sample, reply, source, took, before))
    if log is not None and readings and not stopped:
        # The last reply's ignored attempt, if any, ends by its deadline: wait out the turn before reading the log.
        time.sleep(max(0.0, last_started + TURN_S + 0.5 - time.monotonic()))
    records = list(log) if log is not None else []
    if _rate_limited(records) and not stopped:
        stopped = "Groq answered 429 (a rate limit, perhaps the daily one) during the last reply"
    results: dict[str, dict[str, Any]] = {}
    for i, (sample, reply, source, took, before) in enumerate(readings):
        after = readings[i + 1][4] if i + 1 < len(readings) else len(records)
        attempts = [
            {"model": f"{r.provider.value}/{r.model}", "outcome": r.outcome, "ms": r.duration_ms}
            for r in records[before:after]
        ]
        results[sample["id"]] = {
            "read_as": reply.reply_type.value,
            "by": source,
            "correct": is_correct(sample, reply, source),
            "latency_ms": round(took * 1000),
            # the winner's record is written first: a losing attempt only ends after the answer is in
            "model": next((a["model"] for a in attempts if a["outcome"] == "ok"), None) if source == "llm" else None,
            "timed_out": any(a["outcome"] == "timeout" for a in attempts),
            "hedged": len(attempts) > 1,
            "attempts": attempts,
        }
    result = summary([s for s, *_ in readings], results)
    if stopped:
        result["stopped"] = stopped
    return result


def _rate_limited(records: Sequence[Any]) -> bool:
    """A real 429: the pool's own holds are "rate_limited" too, but take no time and send nothing."""
    return any(r.outcome == "rate_limited" and r.duration_ms for r in records)


def summary(rows: Sequence[dict[str, Any]], results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[bool]] = {}
    for sample in rows:
        by_type.setdefault(sample["type"], []).append(bool(results[sample["id"]]["correct"]))
    correct = sum(sum(oks) for oks in by_type.values())
    asked = [r["latency_ms"] for r in results.values() if r["by"] != "code" and "latency_ms" in r]
    return {
        "samples": len(rows),
        "correct": correct,
        "share": round(correct / len(rows), 3) if rows else 0.0,
        "by_type": {t: round(sum(oks) / len(oks), 3) for t, oks in by_type.items()},
        "failed": sum(1 for r in results.values() if r["by"] == "failed"),
        "timed_out": sum(1 for r in results.values() if r.get("timed_out")),
        "latency_ms": latency(asked),
        "misses": [{"id": i, "read_as": r["read_as"], "by": r["by"]} for i, r in results.items() if not r["correct"]],
        "results": results,
    }


def latency(values: Sequence[int]) -> dict[str, Any]:
    """Median and 90th percentile of the classifier's turns, against the turn's 5 s."""
    if not values:
        return {"replies": 0}
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, max(0, round(0.9 * len(ordered)) - 1))]
    return {
        "replies": len(ordered),
        "median": statistics.median(ordered),
        "p90": p90,
        "max": ordered[-1],
        "turn_limit": round(TURN_S * 1000),
    }


def sent(records: Sequence[Any]) -> int:
    """Requests really sent. The pool records its own holds as "rate_limited" with no time spent: nothing went out."""
    return sum(
        1 for r in records if r.outcome != "skipped_budget" and not (r.outcome == "rate_limited" and not r.duration_ms)
    )


def budgeted(llm: Any, budget: int = LIVE_BUDGET, already: int = 0) -> Callable[..., Any]:
    """The client's complete(), refusing once the requests sent (with `already` spent elsewhere) leave no room for a
    whole call: the three Groq models, each with its one retry."""
    real = llm.complete

    def complete(*args: Any, **kwargs: Any) -> Any:
        if already + sent(llm.call_log.records) > budget - CALL_ROOM:
            raise LLMUnavailable("the run's LLM budget is spent")
        return real(*args, **kwargs)

    return complete


def pace_from_config(samples: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The gap between replies, from Groq's per-minute limits in config/models.yaml (`pool.members`).

    One reply at a time goes to the route's first model (gpt-oss-120b) and, if that is slow, to its hedge model
    (gpt-oss-20b): up to two requests a reply, but never two on one model, and Groq's limits are each model's own
    (its rate-limit headers, docs/api-setup.md). So each model must allow one request, and the largest classification's
    booked tokens (its prompt plus CLASSIFY_TOKENS), per gap: 60 / requests_per_minute seconds, and 60 x booked /
    tokens_per_minute. The slowest of those over both models, with 10% to spare.
    """
    config = load_llm_config()
    first = next(m for p in config.chain(DataClass.PRIVATE, Difficulty.SHORT) for m in config.models if m.provider is p)
    hedge = config.hedge.get(LLMTask.CLASSIFY_REPLY)
    names = [first.model, *([hedge.model] if hedge else [])]
    members = [next(m for m in config.pool.members if m.model == name) for name in names]
    system = build_system_prompt(CLASSIFY_SYSTEM, RawReply)
    booked = 0
    for sample in samples if samples is not None else load_samples():
        state, pending = situation(sample)
        earlier = [e for e in state.history if e.status in ("answered", "skipped")]
        user = classify_message(pending, criterion_of(state, pending), earlier, sample["reply"])
        booked = max(booked, estimate_tokens(system) + estimate_tokens(user) + CLASSIFY_TOKENS)
    by_requests = max(60 / m.requests_per_minute for m in members)
    by_tokens = max(60 * booked / m.tokens_per_minute for m in members)
    return {
        "source": "config/models.yaml pool.members (Groq's rate-limits page, 7 Nov 2026), each model's own limits",
        "models": names,
        "requests_per_reply_at_most": len(names),
        "requests_per_model_per_reply_at_most": 1,
        "requests_per_minute": [m.requests_per_minute for m in members],
        "tokens_per_minute": [m.tokens_per_minute for m in members],
        "tokens_booked_per_request": booked,
        "seconds_by_requests": round(by_requests, 2),
        "seconds_by_tokens": round(by_tokens, 2),
        "pace_s": round(max(by_requests, by_tokens) * 1.1, 1),
    }


def spread(samples: Sequence[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """n replies spread over the set (and so over the types)."""
    step = max(1, len(samples) // n)
    return list(samples[::step][:n])


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.golden.reply_samples")
    parser.add_argument("--live", action="store_true", help="the live classifier (Groq), one reply at a time")
    parser.add_argument("--check", type=int, default=0, help="only this many replies, spread over the types")
    parser.add_argument("--spent", type=int, default=0, help="requests already spent from this budget elsewhere")
    parser.add_argument("--budget", type=int, default=LIVE_BUDGET, help="LLM requests this run may send in all")
    args = parser.parse_args(list(argv))
    if not args.live:
        result = measure(None)
        print(f"code alone: {result['correct']} of {result['samples']} ({result['share']:.0%})")
        return 0
    from app.llm import build_llm_client  # only a live run builds a real client

    samples = load_samples()
    pace = pace_from_config(samples)
    print(f"pace: {pace}")
    client = build_llm_client()
    client.complete = budgeted(client, budget=args.budget, already=args.spent)  # type: ignore[method-assign]
    rows = spread(samples, args.check) if args.check else samples
    result = measure(client, rows, pace_s=pace["pace_s"], log=client.call_log.records)
    attempts = client.call_log.records
    if result.get("stopped"):
        print(f"stopped: {result['stopped']}. Groq's daily limits reset at 00:00 UTC (05:30 IST). No record written.")
        print(f"requests sent: {sent(attempts)} (+{args.spent} before)")
        return 2
    for sample_id, r in result["results"].items():
        tried = ", ".join(f"{a['model'].split('/', 1)[1]} {a['outcome']} {a['ms']} ms" for a in r["attempts"])
        print(f"  {sample_id}: {r['read_as']} by {r['by']}, {r['latency_ms']} ms [{tried}]")
    print(f"requests sent: {sent(attempts)} (+{args.spent} before); latency {result['latency_ms']}")
    if args.check:
        print(f"check: {result['correct']} of {len(rows)}; timed out: {result['timed_out']}")
        return 1 if result["timed_out"] or result["failed"] else 0
    record = {
        "story": "US-39 criterion 10: 70 made-up replies, 10 per type, each through the live classifier once",
        "runner": "python -m tests.golden.reply_samples --live",
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "samples_sha256": samples_digest(),
        "target": TARGET,
        **result,
        "meets_target": result["share"] >= TARGET,
        "pace": pace,
        "budget": args.budget,
        "llm_requests_sent": sent(attempts) + args.spent,
        "requests_in_this_run": sent(attempts),
        "pool_holds": len(attempts) - sent(attempts),
        "outcomes": {o: sum(1 for r in attempts if r.outcome == o) for o in sorted({r.outcome for r in attempts})},
        "providers": sorted({f"{r.provider.value}/{r.model}" for r in attempts}),
    }
    RECORD_PATH.write_bytes((json.dumps(record, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"live: {result['correct']} of {result['samples']} ({result['share']:.1%}); by type {result['by_type']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
