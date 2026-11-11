"""Export the matcher to ONNX and check it (US-15, S3-4, DECIDED 7 and 8). D-64.

    python ml/training/export_onnx.py [--model ml/models/matcher_v1/epoch-003]

1. Exports model.onnx next to the tokenizer files, in ml/models/matcher_v1/onnx/
   (git-ignored: weights are never committed).
2. Scores 200 dev pairs with PyTorch and with onnxruntime: they must agree within 1e-4.
3. Times 1,000 pairs on CPU with onnxruntime and records peak memory.
4. If HF_TOKEN and HF_MODEL_REPO are set (Om makes the repository by hand), uploads the folder and records the commit
   revision. Otherwise it says "upload pending". The token is read from the environment and never printed or saved.
Writes ml/results/matcher_v1_onnx.json.
"""

from __future__ import annotations

import sys
from pathlib import Path as _Path

sys.path.insert(0, str(_Path(__file__).resolve().parents[1] / "scripts"))  # ml/scripts holds the shared code

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from checkpoints import latest_checkpoint
from common import MAX_LENGTH, MODEL_DIR, load, score
from matcher_data import LABELLING_DIR, build_all
from pairsets import DEFAULT_RAW_DIR

OUT = Path(__file__).resolve().parents[1] / "results" / "matcher_v1_onnx.json"
TOLERANCE = 1e-4


def export(model: torch.nn.Module, tokenizer: object, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    model.eval()
    # Eager attention traces to plain ops whose shapes follow the batch. The fused kind bakes in the sample's shape.
    model.config._attn_implementation = "eager"  # type: ignore[union-attr]
    sample = tokenizer(  # type: ignore[operator]
        ["Nova Book 14 16GB 512GB silver", "Kettle 1.7L"],
        ["Nova Book 14 Laptop", "Kettle Pro 1.7 L Steel Grey with a long name here"],
        truncation=True,
        max_length=MAX_LENGTH,
        padding=True,  # different lengths, so some positions really are padding
        return_tensors="pt",
    )
    names = ["input_ids", "attention_mask", "token_type_ids"]  # the model's own argument order, by name

    class Wrapper(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.inner = model

        def forward(self, input_ids, attention_mask, token_type_ids):  # type: ignore[no-untyped-def]
            return self.inner(input_ids=input_ids, attention_mask=attention_mask, token_type_ids=token_type_ids).logits

    path = folder / "model.onnx"
    torch.onnx.export(
        Wrapper(),
        tuple(sample[name] for name in names),
        str(path),
        input_names=names,
        output_names=["logits"],
        dynamic_axes={**{name: {0: "batch", 1: "sequence"} for name in names}, "logits": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    tokenizer.save_pretrained(folder)  # type: ignore[attr-defined]
    return path


def onnx_scores(
    session: ort.InferenceSession, tokenizer: object, pairs: list[tuple[str, str]], batch_size: int = 32
) -> list[float]:
    wanted = {i.name for i in session.get_inputs()}
    out: list[float] = []
    for start in range(0, len(pairs), batch_size):
        chunk = pairs[start : start + batch_size]
        batch = tokenizer(  # type: ignore[operator]
            [a for a, _ in chunk],
            [b for _, b in chunk],
            truncation=True,
            max_length=MAX_LENGTH,
            padding=True,
            return_tensors="np",
        )
        logits = session.run(["logits"], {k: v.astype(np.int64) for k, v in batch.items() if k in wanted})[0][:, 0]
        out += (1 / (1 + np.exp(-logits))).tolist()
    return out


def upload(folder: Path) -> str | None:
    token, repo = os.environ.get("HF_TOKEN"), os.environ.get("HF_MODEL_REPO")
    if not token or not repo:
        return None
    from huggingface_hub import HfApi

    info = HfApi(token=token).upload_folder(
        folder_path=str(folder), repo_id=repo, repo_type="model", commit_message="Matcher v1 (ONNX)"
    )
    return str(info.oid)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the matcher to ONNX and check it.")
    parser.add_argument("--model", type=Path, default=None)
    args = parser.parse_args()
    found = args.model or (latest_checkpoint(MODEL_DIR) or (0, None))[1]
    if found is None:
        raise SystemExit(f"no finished checkpoint in {MODEL_DIR}: run train.py first")
    model, tokenizer = load(found)
    folder = MODEL_DIR / "onnx"
    path = export(model, tokenizer, folder)
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])

    dev = build_all(DEFAULT_RAW_DIR, LABELLING_DIR, 20261111).dev
    every = max(1, len(dev) // 200)  # spread over every source: Abt-Buy, Amazon-Google and our own pairs
    pairs = [(p.left, p.right) for p in dev[::every]][:200]
    torch_scores = score(model, tokenizer, pairs)
    onnx_out = onnx_scores(session, tokenizer, pairs)
    worst = max(abs(a - b) for a, b in zip(torch_scores, onnx_out, strict=True))
    print(f"200 pairs: largest difference between PyTorch and ONNX scores {worst:.2e} (limit {TOLERANCE:g})")

    measured = json.loads(
        subprocess.run(
            [sys.executable, str(Path(__file__).with_name("measure_onnx.py")), str(folder)],
            capture_output=True,
            text=True,
            check=True,
        )
        .stdout.strip()
        .splitlines()[-1]
    )  # a separate process, so PyTorch's memory is not counted
    seconds, peak = measured["seconds_for_1000_pairs"], measured["peak_memory_mb"]
    print(f"1,000 pairs on CPU with onnxruntime alone: {seconds} s, peak memory {peak} MB")

    result = {
        "date": datetime.now(UTC).date().isoformat(),
        "model": found.name,
        "pairs_checked": len(pairs),
        "checked_on": "sigmoid probabilities of dev pairs from every source",
        "largest_score_difference": float(f"{worst:.3e}"),
        "tolerance": TOLERANCE,
        "check_passed": worst <= TOLERANCE,
        "pairs_timed": 1000,
        "timed_on": "2 made-up short titles repeated 500 times, batches of 32 (ml/training/measure_onnx.py)",
        "seconds_for_1000_pairs": seconds,
        "peak_memory_mb": peak,
        "onnxruntime": ort.__version__,
        "cpu_threads": os.cpu_count(),
        "huggingface_revision": None,
        "upload": "pending",
    }
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")  # before any upload can fail
    print(f"wrote {OUT}")
    revision = upload(folder) if worst <= TOLERANCE else None
    if revision:
        result |= {"huggingface_revision": revision, "upload": "done"}
        OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"Hugging Face revision: {revision}" if revision else "upload pending (HF_TOKEN and HF_MODEL_REPO are not set)"
    )
    return 0 if worst <= TOLERANCE else 1


if __name__ == "__main__":
    raise SystemExit(main())
