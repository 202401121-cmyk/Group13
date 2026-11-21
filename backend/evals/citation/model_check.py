"""Which ONNX file of nli-deberta-v3-xsmall the citation check uses (US-22, S5-4 DECIDED 1).

Run from backend/:  python -m evals.citation.model_check  [--write-config]
    writes evals/results/citation-model-check-<isa>.json, where <isa> is avx512 or avx2 for the machine it ran on.
    --write-config also pins the chosen file, both sha256 and 6 reference pairs in config/nli.yaml.

Each candidate file in the model's onnx/ folder, at the revision in config/nli.yaml, reads the 40 tuning pairs with
the app's own scorer (app/explain/nli.py): the premise is the quote (in chunks if it is long), the hypothesis the
sentence, and the highest entailment probability counts, as in the check. Each file is compared with onnx/model.onnx.
The rule: the smallest file whose entailment probabilities agree with the full model's within 0.02 on every tuning
pair, on both an AVX2 and an AVX-512 machine; run this once on each. The record gives each file's size, sha256, the
largest and mean difference, and the time per pair on this machine. Only the public model files are downloaded; the
pairs never leave this machine.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.explain.nli import CACHE_DIR, OnnxNli, PairScorer, onnx_session, pair_tokenizer, sha256_of
from app.explain.nli_settings import load_nli_settings

from ..memory_model_check import machine
from ..run_baselines import RESULTS_DIR
from .config_file import write_config
from .pairs import LabelledPair, load_pairs, load_split

FULL = "onnx/model.onnx"
CANDIDATES = (
    "onnx/model_qint8_avx512.onnx",  # byte for byte the same file as model_qint8_avx512_vnni and model_qint8_arm64
    "onnx/model_quint8_avx2.onnx",
    "onnx/model_O4.onnx",
    FULL,
)
TOLERANCE = 0.02
REFERENCE_COUNT = 6
BATCH = 8  # pairs per call: the same work as the check's one batch per product, at a product's size


def best_entailment(scorer: PairScorer, pairs: list[tuple[str, str]], batch: int = BATCH) -> list[float]:
    """For each (quote, sentence), the highest entailment probability over the quote's chunks."""
    out: list[float] = []
    for start in range(0, len(pairs), batch):
        chunked: list[tuple[str, str]] = []
        spans: list[tuple[int, int]] = []
        for quote, sentence in pairs[start : start + batch]:
            first = len(chunked)
            chunked += [(premise, sentence) for premise in scorer.premises(quote)]
            spans.append((first, len(chunked)))
        found = scorer.entailment(chunked)
        out += [max(found[a:b]) for a, b in spans]
    return out


def compare(probabilities: dict[str, list[float]], sizes: dict[str, int]) -> tuple[dict[str, Any], str | None]:
    """Each file's largest and mean difference from the full model's, and the smallest file within TOLERANCE."""
    full = probabilities[FULL]
    rows: dict[str, Any] = {}
    for name, found in probabilities.items():
        differences = [abs(a - b) for a, b in zip(found, full, strict=True)]
        rows[name] = {
            "difference_max": round(max(differences), 6),
            "difference_mean": round(statistics.fmean(differences), 6),
            "over_tolerance": sum(d > TOLERANCE for d in differences),
        }
    passing = [name for name, row in rows.items() if row["difference_max"] <= TOLERANCE]
    return rows, min(passing, key=lambda name: sizes[name]) if passing else None


def download(repo: str, revision: str, filename: str) -> Path:
    from huggingface_hub import hf_hub_download

    return Path(
        hf_hub_download(repo_id=repo, filename=filename, revision=revision, cache_dir=str(CACHE_DIR), token=False)
    )


def run(write: bool = False) -> dict[str, Any]:
    settings = load_nli_settings(env={})
    if not settings.repo or len(settings.revision) != 40:
        raise SystemExit("config/nli.yaml needs repo and an exact revision")
    tune_ids, _ = load_split()
    tuning: list[LabelledPair] = [p for p in load_pairs() if p.id in set(tune_ids)]
    pairs = [(p.quote, p.sentence) for p in tuning]
    tokenizer_path = download(settings.repo, settings.revision, settings.tokenizer_file)
    probabilities: dict[str, list[float]] = {}
    sizes: dict[str, int] = {}
    files: dict[str, Any] = {}
    for filename in (FULL, *[c for c in CANDIDATES if c != FULL]):
        path = download(settings.repo, settings.revision, filename)
        scorer = OnnxNli(
            onnx_session(path),
            pair_tokenizer(tokenizer_path, settings.max_length),
            pair_tokenizer(tokenizer_path, None),
            settings,
        )
        best_entailment(scorer, pairs[:BATCH])  # warm up, so the timing is the steady state
        started = time.perf_counter()
        probabilities[filename] = best_entailment(scorer, pairs)
        sizes[filename] = path.stat().st_size
        files[filename] = {
            "bytes": sizes[filename],
            "sha256": sha256_of(path),
            "ms_per_pair": round((time.perf_counter() - started) * 1000 / len(pairs), 2),
        }
    rows, chosen = compare(probabilities, sizes)
    for name, row in rows.items():
        files[name].update(row)
    tokenizer_sha = sha256_of(tokenizer_path)
    if write and chosen is not None:
        picks = list(range(0, len(tuning), max(1, len(tuning) // REFERENCE_COUNT)))[:REFERENCE_COUNT]
        reference = [
            {"premise": pairs[i][0], "hypothesis": pairs[i][1], "entailment": round(probabilities[FULL][i], 6)}
            for i in picks
        ]
        write_config(
            {
                "model_file": chosen,
                "model_sha256": files[chosen]["sha256"],
                "tokenizer_sha256": tokenizer_sha,
                "reference": reference,
            }
        )
    return {
        "generated": datetime.now(UTC).date().isoformat(),
        "runner": "python -m evals.citation.model_check (offline, but for the public model download)",
        "repo": settings.repo,
        "revision": settings.revision,
        "tokenizer_sha256": tokenizer_sha,
        "pairs": len(pairs),
        "tolerance": TOLERANCE,
        "machine": machine(),
        "files": files,
        "chosen_here": chosen,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.citation.model_check", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--write-config", action="store_true", help="pin the chosen file in config/nli.yaml")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    record = run(args.write_config)
    isa = "avx512" if record["machine"]["avx512f"] else "avx2"
    out = args.out_dir / f"citation-model-check-{isa}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(record, indent=2) + "\n").encode("utf-8"))
    for name, r in record["files"].items():
        print(
            f"{name:34} {r['bytes']:>12,} bytes  largest difference {r['difference_max']:.4f}  "
            f"{r['ms_per_pair']:.1f} ms/pair"
        )
    print(f"chosen on this machine ({isa}): {record['chosen_here']}")
    print(f"Written: {out.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
