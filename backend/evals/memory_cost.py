"""What preference memory costs on CPU (US-06, P-10): peak memory with the matcher and memory's model both loaded, and
the time to embed 10 criteria.

Run from backend/:  python -m evals.memory_cost     (writes evals/results/memory-cost.json)

It loads the trained matcher (config/matcher.yaml, ml/MODELS.md card 1) and memory's embedding model
(config/memory.yaml, card 5) in one process, as the backend does when both are on, and reads the process's peak
memory after each step. Then it embeds the same 10 made-up criteria ("label: definition", as recall does) 30 times and
gives the median and the slowest. Both models must already be in backend/.cache/ (their first use downloads them).
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.contracts import CriteriaInput
from app.criteria import fallback_set
from app.entities.model import load_model_scorer
from app.entities.settings import load_matcher_settings
from app.memory.embedder import load_embedder
from app.memory.settings import load_memory_settings

from .memory_probe import MB, memory_bytes
from .run_baselines import RESULTS_DIR

COST_OUT = RESULTS_DIR / "memory-cost.json"
RUNS = 30


def peak_bytes() -> int:
    """The process's peak memory so far (peak working set on Windows, max resident set elsewhere)."""
    return memory_bytes()[0]


def ten_criteria() -> list[str]:
    laptop = fallback_set(CriteriaInput(category="laptop", category_label="Laptop")).criteria
    phone = fallback_set(CriteriaInput(category="smartphone", category_label="Smartphone")).criteria
    return [f"{c.label}: {c.definition}" for c in [*laptop, *phone]][:10]


def _mb(steps: dict[str, float], name: str) -> None:
    peak, now = memory_bytes()
    steps[f"{name}_peak_mb"] = round(peak / MB, 1)
    steps[f"{name}_now_mb"] = round(now / MB, 1)


def run() -> dict[str, Any]:
    steps: dict[str, float] = {}
    _mb(steps, "start")
    started = time.perf_counter()
    load_model_scorer(load_matcher_settings(env={}))
    steps["matcher_load_s"] = round(time.perf_counter() - started, 3)
    _mb(steps, "with_matcher")
    settings = load_memory_settings(env={})
    started = time.perf_counter()
    embedder = load_embedder(settings)
    steps["memory_load_s"] = round(time.perf_counter() - started, 3)
    _mb(steps, "with_both")
    texts = ten_criteria()
    embedder.embed(texts)  # warm up
    times = []
    for _ in range(RUNS):
        started = time.perf_counter()
        embedder.embed(texts)
        times.append((time.perf_counter() - started) * 1000)
    _mb(steps, "after_embedding")
    import onnxruntime

    return {
        "generated": datetime.now(UTC).date().isoformat(),
        "runner": "python -m evals.memory_cost (offline, both models from backend/.cache/)",
        "machine": {
            "processor": platform.processor() or platform.machine(),
            "system": platform.system(),
            "onnxruntime": onnxruntime.__version__,
        },
        "matcher": load_matcher_settings(env={}).revision,
        "memory_model": f"{settings.repo}@{settings.revision} {settings.model_file}",
        "peak_memory": steps,
        "embed_10_criteria_ms": {
            "runs": RUNS,
            "median": round(statistics.median(times), 2),
            "slowest": round(max(times), 2),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.memory_cost", description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    record = run()
    out = args.out_dir / COST_OUT.name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(record, indent=2) + "\n").encode("utf-8"))
    print(json.dumps({k: record[k] for k in ("peak_memory", "embed_10_criteria_ms")}, indent=2))
    print(f"Written: {out.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
