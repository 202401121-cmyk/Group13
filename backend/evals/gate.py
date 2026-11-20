"""US-49's quality gate (S4-13, task #55): the recorded runs replayed offline, measured, and held to their floors.

Each recording in tests/fixtures/recorded/ with a pipeline.json and an expected_products list in its manifest
(scripts/record_run.py writes the first; the second is written by hand) is replayed with no network:

- research's sources come from pipeline.json, as research found them;
- the real extract stage reads each source, and every LLM call is answered by the recorded reply to the same prompt
  (matched by the prompt, or by its data block if the instructions around it changed). A prompt with no recorded
  reply gets no answer, as when every provider fails;
- the real match (fuzzy), verify and decide stages then run on what extraction found. Nothing is sent anywhere: every
  httpx transport refuses, so a stray call fails the gate instead of reaching the network.

It measures, over every recording:
- `gate.expected_survival`: the share of the expected products extraction still finds (US-49: 90% or more);
- `gate.claims_without_quote`: claims whose quote is not in their source's own text (none may be);
- `gate.shown_without_source`: shown products with no cited source (none may be);
- `gate.claims_kept`: claims extraction kept;
- `gate.label_disagreements`: decide briefs whose hand-labelled winner the scoring code rejects (evals/labels.py).

Each has a floor in evals/thresholds.yaml. With --baseline (main's evals/results/us49-gate.json), a rate that falls more
than 10 points, or a count that falls more than 10%, fails too. The gate prints how long it took.

Run from backend/:  python -m evals.gate [--write] [--baseline FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar
from unittest import mock

import httpx
from pydantic import BaseModel

from app.contracts import (
    DataClass,
    Difficulty,
    ExtractionResult,
    LLMTask,
    LLMUnavailable,
    ResearchInput,
    ResearchResult,
    Rubric,
    SearchRequest,
    SearchResult,
    Source,
    StageContext,
    StageName,
)
from app.extract.recorder import RecordingStage
from app.extract.settings import load_extract_settings
from app.extract.stage import GuardedExtractStage
from app.llm.errors import InvalidOutput
from app.llm.output import parse_output

from .run_baselines import BACKEND, RESULTS_DIR

RECORDED = BACKEND / "tests" / "fixtures" / "recorded"
GATE_OUT = RESULTS_DIR / "us49-gate.json"
RUNNER = "python -m evals.gate (offline)"
SESSION = "sess_gate000000000000000000"
# No cap in the replay: the live run's cap already chose which pages went to the LLM, and a page it never sent has
# no recorded reply, so it gets no answer here. With a cap, threads would race for its places (not deterministic).
EXTRACT_MAX_SOURCES = "100"
LLM_SERVICES = ("groq", "gemini", "mistral", "openrouter", "cerebras")
RATE_DROP = 0.10  # US-49: more than 10 points below main fails
COUNT_DROP = 0.10  # a count: more than 10% below main fails
SURVIVAL_TARGET = 0.9  # US-49: at least 90% of the expected products survive extraction
WORD = re.compile(r"[a-z0-9]+")

M = TypeVar("M", bound=BaseModel)


class NetworkRefused(httpx.TransportError):
    """The gate replays recordings: any real call is a mistake, so it fails at once."""


@contextmanager
def no_network() -> Iterator[None]:
    """Every httpx transport refuses while the gate runs."""

    def refuse(transport: object, request: httpx.Request) -> httpx.Response:
        raise NetworkRefused(f"the gate never calls out ({request.url.host})", request=request)

    with mock.patch.object(httpx.HTTPTransport, "handle_request", refuse):
        yield


# --- the recorded LLM replies --------------------------------------------------------------------------------------


def _user_text(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    users = [m.get("content") for m in body.get("messages", []) if isinstance(m, dict) and m.get("role") == "user"]
    return users[-1] if users and isinstance(users[-1], str) else None


def _reply_text(body: Any) -> str | None:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


def _hashes(user: str) -> dict[str, str]:
    """record_run's fingerprints of a prompt: the whole, and its data block."""
    import hashlib

    return {
        "prompt_sha256": hashlib.sha256(user.encode("utf-8")).hexdigest(),
        "data_sha256": hashlib.sha256(_data_block(user).encode("utf-8")).hexdigest(),
    }


def _data_block(user: str) -> str:
    """The part of a prompt that holds the source: everything after the criteria lines."""
    return user.split("\n\n", 1)[-1]


class ReplayLLM:
    """An LLMClient that answers with the recorded reply to the same prompt, parsed as the app parses a live one."""

    def __init__(self, rows: Sequence[Mapping[str, Any]], accepted: Sequence[Mapping[str, Any]] = ()) -> None:
        # What the live client accepted for each prompt (record_run's pipeline.json), by the prompt's hashes: the
        # surest answer, whichever provider or retry gave it. The raw replies below are for prompts it lacks.
        self.accepted = {a["prompt_sha256"]: json.dumps(a["reply"]) for a in accepted}
        self.accepted_data = {a["data_sha256"]: json.dumps(a["reply"]) for a in accepted}
        self.replies: list[tuple[str, str]] = []
        for row in rows:
            user, reply = _user_text(row.get("request_body")), _reply_text(row.get("response_body"))
            if row.get("status") == 200 and user is not None and reply is not None:
                self.replies.append((user, reply))
        self.answered = 0
        self.unanswered = 0
        self._used: set[int] = set()
        self._lock = threading.Lock()  # extraction reads sources on several threads

    @property
    def has_accepted(self) -> bool:
        return bool(self.accepted)

    def accepted_for(self, user: str) -> str | None:
        hashes = _hashes(user)
        return self.accepted.get(hashes["prompt_sha256"]) or self.accepted_data.get(hashes["data_sha256"])

    def reply_for(self, user: str, *, fresh_only: bool = False) -> str | None:
        """The recorded replies to this prompt, in the order they came, one per call: a retry gets the next one, as
        it did live. Past the last, the last again. The data block alone matches if no whole prompt does."""
        with self._lock:
            matches = [i for i, (asked, _) in enumerate(self.replies) if asked == user]
            if not matches:
                block = _data_block(user)
                matches = [i for i, (asked, _) in enumerate(self.replies) if _data_block(asked) == block]
            if not matches:
                return None
            fresh = [i for i in matches if i not in self._used]
            if not fresh and fresh_only:
                return None
            chosen = fresh[0] if fresh else matches[-1]
            self._used.add(chosen)
            return self.replies[chosen][1]

    def complete(
        self,
        task: LLMTask,
        system: str,
        user: str,
        schema: type[M],
        *,
        data_class: DataClass,
        difficulty: Difficulty,
        search_id: str | None = None,
        max_output_tokens: int = 1024,
    ) -> M:
        # As the live client did: each recorded reply to this prompt in turn (its retries and the route's next
        # providers), the first one that parses. If none does, the last error, as when every attempt failed.
        accepted = self.accepted_for(user)
        if accepted is not None:
            self.answered += 1
            return parse_output(accepted, schema)
        if self.has_accepted:  # the recording says which replies the run used: a prompt without one got none
            self.unanswered += 1
            raise LLMUnavailable("replay: the live run accepted no reply to this prompt")
        error: Exception | None = None
        while (reply := self.reply_for(user, fresh_only=error is not None)) is not None:
            try:
                parsed = parse_output(reply, schema)
            except InvalidOutput as exc:
                error = exc
                continue
            self.answered += 1
            return parsed
        self.unanswered += 1
        # As the live client ends when every attempt failed: unavailable, whatever the last attempt's trouble was.
        why = "no recorded reply parses" if error is not None else "no recorded reply to this prompt"
        raise LLMUnavailable(f"replay: {why}")


# --- one recording -------------------------------------------------------------------------------------------------


class _RecordedResearch:
    """Research as the recording found it: its sources, with no call."""

    name = StageName.RESEARCH
    is_placeholder = False

    def __init__(self, sources: list[Source]) -> None:
        self.sources = sources

    def run(self, inp: ResearchInput, ctx: StageContext) -> ResearchResult:
        return ResearchResult(queries=["recorded"], sources=self.sources)

    def fallback(self, inp: ResearchInput, ctx: StageContext, error: Exception) -> ResearchResult:
        return self.run(inp, ctx)


class _NoPrices:
    """Commerce with no call: a store search for each product, as its own honest fallback gives."""

    name = StageName.COMMERCE
    is_placeholder = False

    def run(self, inp: Any, ctx: StageContext) -> Any:
        from app.commerce import CommerceStage

        return CommerceStage().fallback(inp, ctx, RuntimeError("the gate looks up no prices"))

    def fallback(self, inp: Any, ctx: StageContext, error: Exception) -> Any:
        return self.run(inp, ctx)


def recordings(root: Path = RECORDED) -> list[Path]:
    """The recordings the gate replays: every one with a pipeline record (US-49's)."""
    return sorted(
        p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".") and (p / "pipeline.json").is_file()
    )


def without_expected(root: Path = RECORDED) -> list[str]:
    """Gate recordings with no hand-written expected list: each one fails the gate, so none is skipped unseen."""
    out = []
    for folder in recordings(root):
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if not manifest.get("expected_products"):
            out.append(folder.name)
    return out


class Replay:
    """What one recording's replay gave."""

    def __init__(
        self, name: str, expected: list[str], sources: list[Source], results: list[ExtractionResult], result: Any
    ) -> None:
        self.name, self.expected, self.sources, self.results, self.result = name, expected, sources, results, result


def replay(folder: Path) -> Replay:
    """Replay one recording offline: its sources, the real extract stage on its recorded replies, then the rest."""
    from app.decide import WeightedDecideStage
    from app.enrich import EnrichStage
    from app.entities import FuzzyMatchStage
    from app.explain import TemplateExplainStage
    from app.pipeline import InMemoryResultStore, PipelineRunner, SearchStages
    from app.verify import TrustVerifyStage

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    run = json.loads((folder / "pipeline.json").read_text(encoding="utf-8"))[0]
    sources = [Source.model_validate(s) for s in run["sources"]]
    rows = [
        row
        for service in LLM_SERVICES
        if (folder / f"{service}.json").is_file()
        for row in json.loads((folder / f"{service}.json").read_text(encoding="utf-8"))
    ]
    settings = load_extract_settings(env={"EXTRACT_MAX_SOURCES": EXTRACT_MAX_SOURCES})
    extract = RecordingStage(GuardedExtractStage(ReplayLLM(rows, run.get("llm_replies", [])), settings))
    stages = SearchStages(
        research=_RecordedResearch(sources),
        extract=extract,
        match=FuzzyMatchStage(),
        verify=TrustVerifyStage(),
        enrich=EnrichStage(),
        decide=WeightedDecideStage(),
        explain=TemplateExplainStage(),
        commerce=_NoPrices(),
    )
    rubric = Rubric.model_validate(run["rubric"])
    request = SearchRequest(session_id=SESSION, raw_query=manifest["request"], rubric=rubric)
    result = PipelineRunner(stages, InMemoryResultStore()).run(request)
    expected = list(manifest.get("expected_products", []))
    return Replay(folder.name, expected, sources, list(extract.results.values()), result)


# --- the measures --------------------------------------------------------------------------------------------------


def _words(name: str) -> set[str]:
    return set(WORD.findall(name.casefold()))


def survives(expected: str, names: Sequence[str]) -> bool:
    """Extraction still finds this product: a name it gave holds every word of the expected one ("Dell XPS 14" in
    "Dell XPS 14 (2026)"). A shorter name never counts: "Dell XPS" is no proof it found the 14."""
    want = _words(expected)
    return bool(want) and any(want <= got for got in map(_words, names))


def _plain(text: str) -> str:
    return " ".join(text.split()).casefold()


def quoted(claim_quote: str, source_text: str) -> bool:
    """The quote is the source's own words, whitespace and case aside."""
    return bool(claim_quote.strip()) and _plain(claim_quote) in _plain(source_text)


def measure_one(rp: Replay) -> dict[str, Any]:
    by_id = {s.id: s for s in rp.sources}
    claims = [c for r in rp.results for c in r.claims]
    names = [m.raw_name for r in rp.results for m in r.mentions if m.is_product] + [c.product_mention for c in claims]
    result: SearchResult | Any = rp.result
    names += [p.product.name for p in getattr(result, "products", [])] + [
        alias for p in getattr(result, "products", []) for alias in p.product.aliases
    ]
    survived = [name for name in rp.expected if survives(name, names)]
    unquoted = [c for c in claims if c.source_id not in by_id or not quoted(c.quote, by_id[c.source_id].text)]
    return {
        "expected": len(rp.expected),
        "survived": len(survived),
        "missed": sorted(set(rp.expected) - set(survived)),
        "claims": len(claims),
        "claims_without_quote": len(unquoted),
        "shown": len(result.decision.ranked) if result.decision is not None else 0,
        "shown_without_source": _shown_without_source(result),
    }


def _shown_without_source(result: SearchResult) -> int:
    """Shown products with no source behind them, by build rule 5's own reading (evals/checks.py, as the live record
    counts them): neither named in a source nor backed by a cited claim from one."""
    from .checks import unsourced

    if result.decision is None:
        return 0
    shown_ids = {r.product_id for r in result.decision.ranked}
    shown = [p.product for p in result.products if p.product.id in shown_ids]
    values = [v for p in result.products for v in p.values]
    return unsourced(shown, values, result.sources, result.citations)


def _measure(measure_id: str, value: float, unit: str, detail: str) -> dict[str, Any]:
    source = "backend/tests/fixtures/recorded (the recordings with a pipeline.json), app.extract, app.decide"
    return {"id": measure_id, "value": value, "unit": unit, "status": "measured", "source": source, "detail": detail}


def measure(root: Path = RECORDED) -> dict[str, Any]:
    """The gate's record: each recording's numbers, and the measures thresholds.yaml holds floors for."""
    from .labels import disagreements

    missing = set(without_expected(root))
    folders = [f for f in recordings(root) if f.name not in missing]
    with no_network(), mock.patch.dict(os.environ, {"EXTRACT_MAX_SOURCES": EXTRACT_MAX_SOURCES}):
        per = {folder.name: measure_one(replay(folder)) for folder in folders}
    expected = sum(r["expected"] for r in per.values())
    survived = sum(r["survived"] for r in per.values())
    survival = round(survived / expected, 4) if expected else 0.0
    labels = disagreements()
    measures = [
        _measure("gate.expected_survival", survival, "rate", "Expected products extraction still finds, of all."),
        _measure(
            "gate.claims_without_quote",
            sum(r["claims_without_quote"] for r in per.values()),
            "claims",
            "Claims whose quote is not their source's own words.",
        ),
        _measure(
            "gate.shown_without_source",
            sum(r["shown_without_source"] for r in per.values()),
            "products",
            "Shown products with no cited source.",
        ),
        _measure("gate.claims_kept", sum(r["claims"] for r in per.values()), "claims", "Claims extraction kept."),
        _measure("gate.label_disagreements", len(labels), "briefs", "Hand-labelled winners the scoring code rejects."),
    ]
    return {
        "story": "US-49 (S4-13): the recorded runs replayed offline",
        "runner": RUNNER,
        "recordings": per,
        "survival_target": SURVIVAL_TARGET,
        "meets_target": survival >= SURVIVAL_TARGET,
        "label_problems": labels,
        "without_expected": sorted(missing),
        "measures": measures,
    }


# --- against main and the floors -----------------------------------------------------------------------------------


def against_main(now: Sequence[Mapping[str, Any]], main: Sequence[Mapping[str, Any]]) -> list[str]:
    """What fell too far from main's record: a rate by more than 10 points, a count by more than 10%."""
    from .thresholds import SAFETY

    before = {m["id"]: m for m in main}
    problems = []
    for m in now:
        b = before.get(m["id"])
        if b is None or m["id"] in SAFETY or b.get("value") is None:  # a safety measure is held exactly by its floor
            continue
        drop = float(b["value"]) - float(m["value"])
        if m["unit"] == "rate" and drop > RATE_DROP + 1e-9:
            problems.append(f"{m['id']} fell {drop * 100:.1f} points from main: {b['value']} to {m['value']}")
        elif m["unit"] != "rate" and float(b["value"]) > 0 and drop / float(b["value"]) > COUNT_DROP + 1e-9:
            problems.append(f"{m['id']} fell {drop / float(b['value']):.0%} from main: {b['value']} to {m['value']}")
    return problems


def floor_problems(measures: Sequence[Mapping[str, Any]]) -> list[str]:
    """The gate's measures against their floors in thresholds.yaml."""
    from .thresholds import THRESHOLDS, check, load

    loaded = load(THRESHOLDS)
    mine = {m["id"] for m in measures}
    return [p for p in check(loaded, measures) if p.split(" ", 1)[0] in mine]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.gate", description="US-49's replay gate.")
    parser.add_argument("--write", action="store_true", help=f"write {GATE_OUT.name} (main's record)")
    parser.add_argument("--baseline", type=Path, help="main's us49-gate.json, to fail a fall of more than 10 points")
    args = parser.parse_args(argv)
    started = time.monotonic()
    record = measure()
    problems = floor_problems(record["measures"]) + [f"label: {p}" for p in record["label_problems"]]
    problems += [f"recording {name} has no expected_products in its manifest" for name in record["without_expected"]]
    if not record["meets_target"]:  # the target is reported, never lowered; the floor is what fails (Om, 19 Nov 2026)
        survival = next(m["value"] for m in record["measures"] if m["id"] == "gate.expected_survival")
        print(f"US-49's target of {SURVIVAL_TARGET} is not met: expected survival is {survival}.")
    if args.baseline is not None and args.baseline.is_file():
        main_record = json.loads(args.baseline.read_text(encoding="utf-8"))
        problems += against_main(record["measures"], main_record["measures"])
    elif args.baseline is not None:
        print(f"No baseline at {args.baseline}: only the floors are checked.")
    if args.write:
        from scripts.eval_trust import current_commit

        when = {"generated": _today(), "commit": current_commit()}
        GATE_OUT.write_bytes((json.dumps({**when, **record}, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    for m in record["measures"]:
        print(f"{m['id']}: {m['value']} {m['unit']}")
    for problem in problems:
        print(f"FAIL {problem}")
    print(f"The gate took {time.monotonic() - started:.1f} s ({len(record['recordings'])} recordings).")
    return 1 if problems else 0


def _today() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
