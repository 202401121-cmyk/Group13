"""US-06's embedder (DECIDED 1): mean pooling under the attention mask, scaled to length 1; the pinned files only; a
machine whose vectors differ from the full model's never uses it; a failed load is held, then tried again."""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from app.memory.embedder import (
    CONFIG_DIR,
    EmbedderHolder,
    MemoryUnavailable,
    OnnxEmbedder,
    check_against,
    clear_shared_embedders,
    cosine,
    load_embedder,
    load_reference,
    sha256_of,
    shared_embedder,
)
from app.memory.settings import MemorySettings, load_memory_settings


class FakeTokenizer:
    """Each text: one token per word, padded with 0s to the longest in the batch."""

    def encode_batch(self, input: list[str]) -> list[Any]:
        words = [text.split() for text in input]
        width = max(len(w) for w in words)
        return [
            SimpleNamespace(
                ids=[1 + i for i in range(len(w))] + [0] * (width - len(w)),
                attention_mask=[1] * len(w) + [0] * (width - len(w)),
                type_ids=[0] * width,
            )
            for w in words
        ]


class FakeSession:
    """Token i's vector is (i, 1, 0, ...): pooled over the real tokens only, the mean depends on the length."""

    def __init__(self, dims: int = 4, inputs: tuple[str, ...] = ("input_ids", "attention_mask")) -> None:
        self.dims = dims
        self.inputs = inputs
        self.fed: list[dict[str, Any]] = []

    def get_inputs(self) -> list[Any]:
        return [SimpleNamespace(name=name) for name in self.inputs]

    def run(self, output_names: Any, input_feed: dict[str, Any]) -> list[Any]:
        self.fed.append(input_feed)
        ids = input_feed["input_ids"]
        out = np.zeros((*ids.shape, self.dims))
        out[:, :, 0] = ids
        out[:, :, 1] = 1.0
        out[ids == 0] = 99.0  # padding: a pooled vector that counted it would be far off
        return [out]


def test_vectors_are_mean_pooled_under_the_mask_and_of_length_1() -> None:
    session = FakeSession()
    vectors = OnnxEmbedder(session, FakeTokenizer(), dimensions=4).embed(["one", "one two three"])
    for vector in vectors:
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0)
    expected_one = np.array([1.0, 1.0, 0, 0]) / math.sqrt(2)  # mean of token 1: (1, 1)
    assert np.allclose(vectors[0], expected_one)
    assert np.allclose(vectors[1], np.array([2.0, 1.0, 0, 0]) / math.sqrt(5))  # mean of tokens 1 to 3: (2, 1)
    assert set(session.fed[0]) == {"input_ids", "attention_mask"}  # only what the model declares


def test_a_model_of_the_wrong_width_is_refused() -> None:
    with pytest.raises(MemoryUnavailable, match="wide"):
        OnnxEmbedder(FakeSession(dims=8), FakeTokenizer(), dimensions=4).embed(["one"])


def test_cosine() -> None:
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([0, 0], [1, 0]) == 0.0


def test_an_unpinned_model_is_never_loaded() -> None:
    with pytest.raises(MemoryUnavailable, match="no revision"):
        load_embedder(MemorySettings(repo="sentence-transformers/all-MiniLM-L6-v2", revision="main"))


def test_a_failed_download_is_a_plain_reason(tmp_path: Path) -> None:
    def broken(**asked: Any) -> str:
        raise OSError("no network")

    with pytest.raises(MemoryUnavailable, match="download failed"):
        load_embedder(load_memory_settings(env={}), tmp_path, download=broken)


def test_the_download_asks_for_the_pinned_revision_from_the_cache_first_and_sends_no_token(tmp_path: Path) -> None:
    settings = load_memory_settings(env={})
    asked: list[dict[str, Any]] = []

    def record(**kwargs: Any) -> str:
        asked.append(kwargs)
        raise OSError("stop here")

    with pytest.raises(MemoryUnavailable):
        load_embedder(settings, tmp_path, download=record)
    assert asked[0]["local_files_only"] is True and asked[1]["local_files_only"] is False
    for call in asked:
        assert call["revision"] == settings.revision and call["token"] is False
        assert set(call) == {"repo_id", "filename", "revision", "cache_dir", "token", "local_files_only"}


def test_a_tokenizer_whose_sha256_is_wrong_is_never_used(tmp_path: Path) -> None:
    settings = load_memory_settings(env={})
    model, tokenizer = tmp_path / "model.onnx", tmp_path / "tokenizer.json"
    model.write_bytes(b"pretend model")
    tokenizer.write_bytes(b"pretend tokenizer")
    pinned = MemorySettings.model_validate({**settings.model_dump(), "model_sha256": sha256_of(model)})
    with pytest.raises(MemoryUnavailable, match="tokenizer"):
        load_embedder(
            pinned, tmp_path, download=lambda **a: str(model if a["filename"] == pinned.model_file else tokenizer)
        )


def test_a_machine_whose_vectors_differ_never_uses_the_model() -> None:
    class Off:
        def embed(self, texts: Any) -> list[list[float]]:
            return [[0.0, 1.0] for _ in texts]

    reference = [("a", [1.0, 0.0]), ("b", [0.0, 1.0])]
    with pytest.raises(MemoryUnavailable, match="differ"):
        check_against(Off(), reference, MemorySettings())
    with pytest.raises(MemoryUnavailable, match="no reference"):
        check_against(Off(), [], MemorySettings())


def test_the_reference_file_is_the_pinned_models() -> None:
    settings = load_memory_settings(env={})
    raw = json.loads((CONFIG_DIR / settings.reference_file).read_text(encoding="utf-8"))
    assert (raw["repo"], raw["revision"], raw["model_file"]) == (settings.repo, settings.revision, "onnx/model.onnx")
    found = load_reference(settings)
    assert len(found) >= 5
    for text, vector in found:
        assert text and len(vector) == settings.dimensions
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, abs_tol=1e-5)


def test_the_pinned_model_is_the_one_the_check_chose() -> None:
    settings = load_memory_settings(env={})
    record = json.loads(
        (Path(__file__).resolve().parents[3] / "evals" / "results" / "memory-model-check-avx2.json").read_text(
            encoding="utf-8"
        )
    )
    chosen = record["files"][settings.model_file]
    assert record["chosen_here"] == settings.model_file and record["revision"] == settings.revision
    assert chosen["sha256"] == settings.model_sha256 and record["tokenizer_sha256"] == settings.tokenizer_sha256
    assert chosen["cosine_min"] >= settings.check_min_cosine
    smaller = [name for name, r in record["files"].items() if r["bytes"] < chosen["bytes"]]
    assert all(record["files"][name]["cosine_min"] < settings.check_min_cosine for name in smaller)
    assert settings.pinned and settings.dimensions == 384


def test_a_failed_load_is_held_then_tried_again() -> None:
    clock = {"now": 0.0}
    calls: list[int] = []

    def broken(settings: MemorySettings) -> Any:
        calls.append(1)
        raise MemoryUnavailable("the model could not be loaded (RuntimeError)")

    holder = EmbedderHolder(MemorySettings(), broken, retry_after_s=600, clock=lambda: clock["now"])
    with pytest.raises(MemoryUnavailable, match="could not be loaded"):
        holder.get(5)
    with pytest.raises(MemoryUnavailable, match="could not be loaded"):
        holder.get(5)
    assert len(calls) == 1  # held: not tried on every interview
    clock["now"] = 601.0
    with pytest.raises(MemoryUnavailable):
        holder.get(5)
    assert len(calls) == 2


def test_a_slow_load_answers_within_the_wait_and_is_ready_later() -> None:
    def slow(settings: MemorySettings) -> Any:
        time.sleep(0.3)
        return "ready"

    holder = EmbedderHolder(MemorySettings(), slow)
    with pytest.raises(MemoryUnavailable, match="still loading"):
        holder.get(0.01)
    assert holder.get(5) == "ready"


def test_a_bug_while_loading_is_a_plain_reason() -> None:
    def buggy(settings: MemorySettings) -> Any:
        raise ZeroDivisionError

    with pytest.raises(MemoryUnavailable, match="ZeroDivisionError"):
        EmbedderHolder(MemorySettings(), buggy).get(5)


def test_one_holder_per_model_in_the_process() -> None:
    clear_shared_embedders()
    settings = load_memory_settings(env={})
    assert shared_embedder(settings) is shared_embedder(settings)
    clear_shared_embedders()
