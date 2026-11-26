"""Fake research and extract stages for reading begun during the interview (US-34, D-94).

Both count what they do, so a test can tell a job taken over from one repeated, and either can wait at a gate, so a
test can confirm while the job is still going. Every wait has a deadline: a broken test fails instead of hanging.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Sequence

from app import contracts as C
from app.contracts import examples as ex
from app.decide.features import feature_key
from app.research import ResearchStage

WAIT_S = 5.0


class Gate:
    """Holds a stage until the test opens it; never longer than WAIT_S."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.opened = threading.Event()

    def hold(self) -> None:
        self.entered.set()
        self.opened.wait(WAIT_S)


class CountingResearch:
    """The stub research, counted. `fail`: run and fallback both raise, so research ends FAILED."""

    name = C.StageName.RESEARCH
    is_placeholder = False

    def __init__(self, gate: Gate | None = None, fail: bool = False) -> None:
        self.gate = gate
        self.fail = fail
        self.runs: list[str] = []  # the search ID of each run
        self._lock = threading.Lock()

    def run(self, inp: C.ResearchInput, ctx: C.StageContext) -> C.ResearchResult:
        with self._lock:
            self.runs.append(ctx.search_id)
        if self.gate is not None:
            self.gate.hold()
        if self.fail:
            raise RuntimeError("research crashed")
        return ResearchStage().run(inp, ctx)

    def fallback(self, inp: C.ResearchInput, ctx: C.StageContext, error: Exception) -> C.ResearchResult:
        if self.fail:
            raise RuntimeError("research's fallback crashed too")
        return ResearchStage().fallback(inp, ctx, error)


class SpecExtract:
    """The example claims of each source, less the keys in `without` unless the search asked for them by
    ask_for_specs: a key the interview added then has no claim until it is asked for again. A ruled-out feature asked
    for gets one made-up claim per source. Records every read, ask, window and kept claim."""

    name = C.StageName.EXTRACT
    is_placeholder = False

    def __init__(self, without: Sequence[str] = ("ram_gb",), gate: Gate | None = None) -> None:
        self.without = set(without)
        self.gate = gate
        self.reads: list[tuple[str, str]] = []  # (search ID, source ID)
        self.asked: list[tuple[str, list[str], bool, list[str]]] = []
        self.windows: list[tuple[str, float]] = []
        self.kept: list[str] = []
        self.inputs: list[C.ExtractInput] = []
        self._asks: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def ask_for_specs(self, search_id: str, keys: Sequence[str], price: bool, features: Sequence[str] = ()) -> None:
        with self._lock:
            self.asked.append((search_id, list(keys), price, list(features)))
            wanted = set(keys) | {key for key in (feature_key(f) for f in features) if key is not None}
            self._asks[search_id] = wanted

    def open_window(self, search_id: str, until: float) -> None:
        with self._lock:
            self.windows.append((search_id, until))

    def keep_claims(self, claims: Sequence[C.Claim]) -> None:
        with self._lock:
            self.kept += [claim.id for claim in claims]

    def coverage_note(self, search_id: str) -> str | None:
        with self._lock:
            read = sum(1 for sid, _ in self.reads if sid == search_id)
        return f"{read} sources read" if read else None

    def read_by(self, search_id: str) -> list[str]:
        with self._lock:
            return [source for sid, source in self.reads if sid == search_id]

    def run(self, inp: C.ExtractInput, ctx: C.StageContext) -> C.ExtractionResult:
        if self.gate is not None:
            self.gate.hold()
        with self._lock:
            self.reads.append((ctx.search_id, inp.source.id))
            self.inputs.append(inp)
            asked = set(self._asks.get(ctx.search_id, set()))
        claims = [
            claim
            for claim in ex.claims()
            if claim.source_id == inp.source.id
            and (claim.criterion_key not in self.without or claim.criterion_key in asked)
        ]
        made_up = [key for key in asked if key.startswith("has_")]
        for key in made_up:
            claims.append(
                C.Claim(
                    id="clm_" + hashlib.sha256(f"{inp.source.id} {key}".encode()).hexdigest()[:16],
                    source_id=inp.source.id,
                    source_url=inp.source.url,
                    product_mention="Nova Book 14",
                    criterion_key=key,
                    score=None,
                    value=0.0,
                    quote="It has no touchscreen at all.",
                    claim_type=C.ClaimType.SPEC,
                    polarity="neutral",
                    retrieved_at=inp.source.fetched_at,
                    extracted_at=inp.source.fetched_at,
                )
            )
        return C.ExtractionResult(source_id=inp.source.id, claims=claims, mentions=ex.mentions())

    def fallback(self, inp: C.ExtractInput, ctx: C.StageContext, error: Exception) -> C.ExtractionResult:
        return C.ExtractionResult(source_id=inp.source.id)
