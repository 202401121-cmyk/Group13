"""Shared by train.py, evaluate.py and export_onnx.py (S3-4). Needs torch, so CI's ml job never imports it."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

ML_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_DIR / "scripts"))  # the scripts are plain files, not a package

import torch  # noqa: E402

BASE_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"  # 22.7M parameters, Apache-2.0
SEED = 20261111
MAX_LENGTH = 128
MODEL_DIR = ML_DIR / "models" / "matcher_v1"  # git-ignored (CI fails if ml/models is tracked)


def load(path: str | Path) -> tuple[torch.nn.Module, object]:
    """The base model (or a saved checkpoint) as a sentence-transformers CrossEncoder; we train its model ourselves."""
    from sentence_transformers import CrossEncoder

    cross = CrossEncoder(str(path), num_labels=1, max_length=MAX_LENGTH, device="cpu")
    return cross.model, cross.tokenizer


@torch.no_grad()
def score(
    model: torch.nn.Module, tokenizer: object, pairs: Sequence[tuple[str, str]], batch_size: int = 64
) -> list[float]:
    """Probability that each (left, right) pair is the same product, from 0 to 1."""
    model.eval()
    out: list[float] = []
    for start in range(0, len(pairs), batch_size):
        chunk = pairs[start : start + batch_size]
        batch = tokenizer(  # type: ignore[operator]
            [left for left, _ in chunk],
            [right for _, right in chunk],
            truncation=True,
            max_length=MAX_LENGTH,
            padding=True,
            return_tensors="pt",
        )
        logits = model(**batch).logits[:, 0]
        out += torch.sigmoid(logits).tolist()
    return out
