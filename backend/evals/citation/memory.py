"""Could the citation check run on a 512 MB host of its own? (S5-4b DECIDED 9, P-10, P-49)

Run from backend/:  python -m evals.citation.memory     (writes evals/results/citation-memory.json)

1. In this process: the largest batch the recorded runs give the check (run.evidence_batches), as the model reads it:
   each sentence without code's numbers, against every chunk of every quote it cites.
2. In a fresh process each (memory_child.py, which imports nothing of the app): only the pinned model file and its
   tokenizer, scoring that batch. Once with onnxruntime's defaults, once with its memory arena and memory pattern off.
3. Each process's peak memory, beside LIMIT_MB, and whether the two gave the same probabilities.

No smaller or quantised model is tried: quality must not drop (P-49). The model must already be in backend/.cache/nli/
(python -m evals.citation.model_check downloads it). Nothing leaves this machine.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.explain.check import hypothesis
from app.explain.nli import chunks, pair_tokenizer, pinned_files, sha256_of
from app.explain.nli_settings import load_nli_settings

from ..run_baselines import RESULTS_DIR
from .run import evidence_batches

MEMORY_OUT = RESULTS_DIR / "citation-memory.json"
CHILD = Path(__file__).with_name("memory_child.py")
LIMIT_MB = 450.0
SAME = 1e-6


def largest_batch() -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """The recorded runs' biggest batch for the check: which product it is, and its (premise, hypothesis) pairs."""
    settings = load_nli_settings(env={})
    _, tokenizer_path = pinned_files(settings)
    counter = pair_tokenizer(tokenizer_path, None)

    def premises(quote: str) -> list[str]:
        offsets = list(counter.encode(quote, add_special_tokens=False).offsets)
        return chunks(quote, offsets, settings.premise_tokens, settings.premise_overlap)

    best: tuple[dict[str, Any], list[tuple[str, str]]] = ({}, [])
    for run, facts, drafts in evidence_batches():
        pairs = [
            (premise, hypothesis(d.text, facts, d.key))
            for d in drafts
            for quote in d.quotes
            for premise in premises(quote)
        ]
        if len(pairs) > len(best[1]):
            best = ({"run": run, "product_id": facts.product_id, "sentences": len(drafts), "pairs": len(pairs)}, pairs)
    if not best[1]:
        raise SystemExit("the recorded runs gave no batch to check")
    return best


def measure(pairs: list[tuple[str, str]], lean: bool) -> dict[str, Any]:
    settings = load_nli_settings(env={})
    model_path, tokenizer_path = pinned_files(settings)
    if sha256_of(model_path) != settings.model_sha256:
        raise SystemExit("the cached model is not the pinned one")
    job = {
        "model": str(model_path),
        "tokenizer": str(tokenizer_path),
        "max_length": settings.max_length,
        "entailment_index": settings.entailment_index,
        "pairs": pairs,
        "lean": lean,
    }
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "job.json"
        path.write_text(json.dumps(job), encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(CHILD), str(path)], capture_output=True, text=True, check=True, timeout=600
        )
    out: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    return out


def run() -> dict[str, Any]:
    batch, pairs = largest_batch()
    default, lean = measure(pairs, lean=False), measure(pairs, lean=True)
    same = all(abs(a - b) <= SAME for a, b in zip(default["probabilities"], lean["probabilities"], strict=True))
    settings = load_nli_settings(env={})
    peak = {
        name: max(v for k, v in got["memory"].items() if k.endswith("_peak_mb"))
        for name, got in (("default", default), ("lean", lean))
    }
    import onnxruntime

    return {
        "generated": datetime.now(UTC).date().isoformat(),
        "runner": "python -m evals.citation.memory (offline; the model from backend/.cache/nli/)",
        "model": {"repo": settings.repo, "revision": settings.revision, "file": settings.model_file},
        "machine": {"processor": platform.processor() or platform.machine(), "onnxruntime": onnxruntime.__version__},
        "batch": batch,
        "default": default["memory"],
        "lean": lean["memory"],
        "peak_mb": peak,
        "limit_mb": LIMIT_MB,
        "lean_fits": peak["lean"] <= LIMIT_MB,
        "same_probabilities": same,
    }


def main() -> int:
    record = run()
    MEMORY_OUT.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_OUT.write_bytes((json.dumps(record, indent=2) + "\n").encode("utf-8"))
    print(
        json.dumps(
            {k: record[k] for k in ("batch", "peak_mb", "limit_mb", "lean_fits", "same_probabilities")}, indent=2
        )
    )
    print(f"Written: {MEMORY_OUT.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
