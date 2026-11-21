"""Which ONNX file of all-MiniLM-L6-v2 preference memory uses (US-06, S5-3 DECIDED 1).

Run from backend/:  python -m evals.memory_model_check
    writes evals/results/memory-model-check-<isa>.json, where <isa> is avx512 or avx2 for the machine it ran on.
    --write-reference also writes config/memory_reference.json, the full model's vectors the app checks against.

Each candidate file in the model's onnx/ folder, at the revision in config/memory.yaml, embeds the same 200 sentences
as onnx/model.onnx, with the app's own embedder (app/memory/embedder.py). The sentences are made by code from our own
criteria (config/criteria_fallback.yaml): each criterion's "label: definition", as recall compares it, and its plain
words as a preference is saved ("Battery life: high importance"). No shopper's text, and no network but the download
of the public model files. The record gives each file's size, sha256, the lowest and mean cosine with the full model,
and the time per sentence on this machine. The rule: the smallest file whose lowest cosine is 0.99 or more, on both an
AVX2 and an AVX-512 machine; run this once on each.
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

import yaml  # type: ignore[import-untyped]  # PyYAML ships no type stubs

from app.memory.embedder import CACHE_DIR, CONFIG_DIR, OnnxEmbedder, cosine, onnx_session, sha256_of, text_tokenizer
from app.memory.settings import load_memory_settings

from .run_baselines import RESULTS_DIR

SENTENCES = 200
FULL = "onnx/model.onnx"
CANDIDATES = (
    "onnx/model_qint8_avx512.onnx",  # the same file as model_qint8_avx512_vnni.onnx and model_qint8_arm64.onnx
    "onnx/model_quint8_avx2.onnx",
    "onnx/model_O4.onnx",
    FULL,
)
LEVEL_WORDS = ("low importance", "good enough will do", "medium importance", "high importance")
REFERENCE_COUNT = 6
MIN_COSINE = 0.99
CRITERIA_FILE = CONFIG_DIR / "criteria_fallback.yaml"


def sentences(count: int = SENTENCES) -> list[str]:
    """`count` distinct sentences from our own criteria: each "label: definition" first, then plain-words weights."""
    raw = yaml.safe_load(CRITERIA_FILE.read_text(encoding="utf-8"))
    sets = [family["criteria"] for family in raw["families"].values()]
    sets += [category["criteria"] for category in (raw.get("categories") or {}).values()]
    seen: dict[str, None] = {}
    criteria = [c for group in sets for c in group]
    for c in criteria:
        seen.setdefault(f"{c['label']}: {c['definition']}", None)
    for i, c in enumerate(criteria):
        seen.setdefault(f"{c['label']}: {LEVEL_WORDS[i % len(LEVEL_WORDS)]}", None)
    found = list(seen)
    if len(found) < count:
        raise ValueError(f"only {len(found)} distinct sentences, not {count}")
    return found[:count]


def reference_json(reference: dict[str, Any]) -> str:
    """The reference file, one sentence and its vector to a block, so a diff stays readable."""
    head = [f"  {json.dumps(key)}: {json.dumps(value)}," for key, value in reference.items() if key != "sentences"]
    blocks = [
        f'    {{"text": {json.dumps(item["text"])},\n     "vector": {json.dumps(item["vector"])}}}'
        for item in reference["sentences"]
    ]
    return "\n".join(["{", *head, '  "sentences": [', ",\n".join(blocks), "  ]", "}"]) + "\n"


def machine() -> dict[str, Any]:
    import numpy
    import onnxruntime
    from numpy._core._multiarray_umath import __cpu_features__ as features

    return {
        "processor": platform.processor() or platform.machine(),
        "system": platform.system(),
        "avx2": bool(features.get("AVX2")),
        "avx512f": bool(features.get("AVX512F")),
        "avx512_vnni": bool(features.get("AVX512VNNI")),
        "onnxruntime": onnxruntime.__version__,
        "numpy": numpy.__version__,
    }


def download(repo: str, revision: str, filename: str) -> Path:
    from huggingface_hub import hf_hub_download

    return Path(
        hf_hub_download(repo_id=repo, filename=filename, revision=revision, cache_dir=str(CACHE_DIR), token=False)
    )


def run(write_reference: bool = False) -> dict[str, Any]:
    settings = load_memory_settings(env={})
    if not settings.repo or len(settings.revision) != 40:
        raise SystemExit("config/memory.yaml needs repo and an exact revision")
    texts = sentences()
    tokenizer_path = download(settings.repo, settings.revision, settings.tokenizer_file)
    tokenizer = text_tokenizer(tokenizer_path, settings.max_length)
    results: dict[str, Any] = {}
    full_vectors: list[list[float]] = []
    for filename in (FULL, *[c for c in CANDIDATES if c != FULL]):
        path = download(settings.repo, settings.revision, filename)
        embedder = OnnxEmbedder(onnx_session(path), tokenizer, settings.dimensions)
        embedder.embed(texts[:8])  # warm up, so the timing is the steady state
        started = time.perf_counter()
        vectors = embedder.embed(texts)
        per_sentence_ms = (time.perf_counter() - started) * 1000 / len(texts)
        if filename == FULL:
            full_vectors = vectors
        cosines = [cosine(a, b) for a, b in zip(vectors, full_vectors, strict=True)]
        results[filename] = {
            "bytes": path.stat().st_size,
            "sha256": sha256_of(path),
            "cosine_min": round(min(cosines), 6),
            "cosine_mean": round(statistics.fmean(cosines), 6),
            "cosine_p1": round(sorted(cosines)[len(cosines) // 100], 6),
            "under_min": sum(c < MIN_COSINE for c in cosines),
            "ms_per_sentence": round(per_sentence_ms, 3),
        }
    passing = [name for name, r in results.items() if r["cosine_min"] >= MIN_COSINE]
    chosen = min(passing, key=lambda name: results[name]["bytes"]) if passing else None
    if write_reference:
        picks = list(range(0, len(texts), len(texts) // REFERENCE_COUNT))[:REFERENCE_COUNT]
        reference = {
            "about": "The full model's vectors (onnx/model.onnx) for a few made-up sentences. Before memory uses its "
            "model on a machine, the model's own vectors for these must match at cosine 0.99 or more "
            "(app/memory/embedder.py). Written by python -m evals.memory_model_check --write-reference.",
            "repo": settings.repo,
            "revision": settings.revision,
            "model_file": FULL,
            "sentences": [{"text": texts[i], "vector": [round(x, 7) for x in full_vectors[i]]} for i in picks],
        }
        (CONFIG_DIR / settings.reference_file).write_text(reference_json(reference), encoding="utf-8", newline="\n")
    tokenizer_sha = sha256_of(tokenizer_path)
    return {
        "generated": datetime.now(UTC).date().isoformat(),
        "runner": "python -m evals.memory_model_check (offline, but for the public model download)",
        "repo": settings.repo,
        "revision": settings.revision,
        "tokenizer_sha256": tokenizer_sha,
        "sentences": len(texts),
        "min_cosine": MIN_COSINE,
        "machine": machine(),
        "files": results,
        "chosen_here": chosen,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evals.memory_model_check", description=(__doc__ or "").split("\n")[0]
    )
    parser.add_argument("--write-reference", action="store_true", help="also write config/memory_reference.json")
    parser.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)
    record = run(args.write_reference)
    isa = "avx512" if record["machine"]["avx512f"] else "avx2"
    out = args.out_dir / f"memory-model-check-{isa}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(record, indent=2) + "\n").encode("utf-8"))
    for name, r in record["files"].items():
        print(
            f"{name:34} {r['bytes']:>11,} bytes  min cos {r['cosine_min']:.6f}  {r['ms_per_sentence']:.2f} ms/sentence"
        )
    print(f"chosen on this machine ({isa}): {record['chosen_here']}")
    print(f"Written: {out.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
