"""A stand-in for the embedding model: deterministic, offline and fast. The same words give the same vector, so the same
criterion's "label: definition" has cosine 1.0 with itself, and unrelated texts share few words."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence

from app.memory.embedder import Embedder

SIZE = 128


class WordEmbedder:
    """A bag of words, each word hashed to one of SIZE places, scaled to length 1."""

    def __init__(self, size: int = SIZE) -> None:
        self.size = size
        self.calls: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [vector_of(text, self.size) for text in texts]


def vector_of(text: str, size: int = SIZE) -> list[float]:
    values = [0.0] * size
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        values[int(hashlib.sha256(word.encode()).hexdigest(), 16) % size] += 1.0
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm else values


class ReadyHolder:
    """An EmbedderHolder whose model is already loaded."""

    def __init__(self, embedder: Embedder) -> None:
        self.embedder = embedder
        self.started = 0

    def start(self) -> None:
        self.started += 1

    def get(self, timeout: float) -> Embedder:
        return self.embedder


def ready_holder(embedder: Embedder) -> ReadyHolder:
    return ReadyHolder(embedder)
