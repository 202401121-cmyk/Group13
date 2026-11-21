"""The citation check's model (US-22, S5-4): pairs scored in one batch at the entailment label, a quote over 400
tokens chunked from its own text, the pinned load with its per-machine check, the background holder and the time
limit. Fakes stand in for onnxruntime and the tokenizer; no test downloads the model."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.explain import nli
from app.explain.nli import NliHolder, NliUnavailable, OnnxNli, chunks, entailment_within, load_nli, softmax_at
from app.explain.nli_settings import NliSettings, ReferencePair

COMMIT = "a150876415327c80daeff35ca6f68f5ed8cf5c24"


@dataclass
class _Encoding:
    ids: list[int]
    attention_mask: list[int]
    type_ids: list[int]
    offsets: list[tuple[int, int]] = field(default_factory=list)


class _Tokenizer:
    """Words are tokens: a pair is [premise words, hypothesis words], padded to the longest in the batch."""

    def __init__(self) -> None:
        self.batches: list[list[Any]] = []

    def encode(
        self, sequence: str, pair: Any = None, is_pretokenized: bool = False, add_special_tokens: bool = True
    ) -> _Encoding:
        offsets, at = [], 0
        for word in sequence.split():
            start = sequence.index(word, at)
            offsets.append((start, start + len(word)))
            at = start + len(word)
        return _Encoding([1] * len(offsets), [1] * len(offsets), [0] * len(offsets), offsets)

    def encode_batch(self, input: list[Any]) -> list[_Encoding]:
        self.batches.append(list(input))
        lengths = [len(p.split()) + len(h.split()) for p, h in input]
        width = max(lengths)
        return [_Encoding([1] * n + [0] * (width - n), [1] * n + [0] * (width - n), [0] * width) for n in lengths]


@dataclass
class _Input:
    name: str


class _Session:
    """Logits per pair: high entailment when the hypothesis's words are all in the premise."""

    def __init__(self, inputs: tuple[str, ...] = ("input_ids", "attention_mask"), shape_ok: bool = True) -> None:
        self.inputs = inputs
        self.feeds: list[dict[str, Any]] = []
        self.shape_ok = shape_ok
        self.pairs: list[tuple[str, str]] = []

    def get_inputs(self) -> list[_Input]:
        return [_Input(name) for name in self.inputs]

    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> list[Any]:
        import numpy as np

        self.feeds.append(input_feed)
        rows = len(input_feed["input_ids"])
        if not self.shape_ok:
            return [np.zeros((rows, 2))]
        return [np.array([[0.0, 4.0 if i % 2 == 0 else -4.0, 0.0] for i in range(rows)])]


def _settings(**changes: Any) -> NliSettings:
    values: dict[str, Any] = {
        "repo": "cross-encoder/nli-deberta-v3-xsmall",
        "revision": COMMIT,
        "model_file": "onnx/model_quint8_avx2.onnx",
        "model_sha256": "0" * 64,
        "tokenizer_sha256": "0" * 64,
        "threshold": 0.5,
        "reference": [ReferencePair(premise="p", hypothesis="h", entailment=0.5)],
    }
    return NliSettings.model_validate({**values, **changes})


def test_the_entailment_probability_is_the_softmax_at_the_entailment_label() -> None:
    assert softmax_at([0.0, 0.0, 0.0], 1) == pytest.approx(1 / 3)
    assert softmax_at([1000.0, 1001.0, 999.0], 1) == pytest.approx(math.e / (1 + math.e + math.exp(-1)))


def test_every_pair_goes_in_one_batch_with_only_the_inputs_the_model_declares() -> None:
    session, tokenizer = _Session(), _Tokenizer()
    scorer = OnnxNli(session, tokenizer, _Tokenizer(), _settings())
    found = scorer.entailment([("a b", "a"), ("c d", "e"), ("f", "f")])
    assert len(session.feeds) == 1 and set(session.feeds[0]) == {"input_ids", "attention_mask"}
    assert tokenizer.batches == [[("a b", "a"), ("c d", "e"), ("f", "f")]]
    assert found[0] == pytest.approx(softmax_at([0.0, 4.0, 0.0], 1))
    assert found[1] == pytest.approx(softmax_at([0.0, -4.0, 0.0], 1))
    assert scorer.entailment([]) == []


def test_logits_of_the_wrong_shape_make_the_model_unavailable() -> None:
    scorer = OnnxNli(_Session(shape_ok=False), _Tokenizer(), _Tokenizer(), _settings())
    with pytest.raises(NliUnavailable, match="shape"):
        scorer.entailment([("a", "b")])


def test_a_quote_of_400_tokens_or_fewer_is_one_premise_itself() -> None:
    quote = " ".join(f"w{i}" for i in range(400))
    scorer = OnnxNli(_Session(), _Tokenizer(), _Tokenizer(), _settings())
    assert scorer.premises(quote) == [quote]


def test_a_quote_over_400_tokens_is_split_into_overlapping_chunks_of_its_own_text() -> None:
    quote = " ".join(f"w{i}" for i in range(900))
    scorer = OnnxNli(_Session(), _Tokenizer(), _Tokenizer(), _settings())
    pieces = scorer.premises(quote)
    assert len(pieces) == 3
    assert all(piece in quote and len(piece.split()) <= 400 for piece in pieces)
    assert pieces[0].split()[0] == "w0" and pieces[-1].split()[-1] == "w899"
    assert pieces[0].split()[-50:] == pieces[1].split()[:50]  # each shares 50 tokens with the one before
    assert pieces[1].split()[-50:] == pieces[2].split()[:50]


def test_chunks_cut_at_token_boundaries_and_cover_the_end() -> None:
    text = "aa bb cc dd ee"
    offsets = [(0, 2), (3, 5), (6, 8), (9, 11), (12, 14)]
    assert chunks(text, offsets, 2, 1) == ["aa bb", "bb cc", "cc dd", "dd ee"]
    assert chunks(text, offsets, 3, 0) == ["aa bb cc", "dd ee"]
    assert chunks(text, offsets, 5, 2) == [text]


# --- loading ----------------------------------------------------------------------------------------------------------


def _files(tmp_path: Path) -> tuple[Path, Path]:
    model, tokenizer = tmp_path / "model.onnx", tmp_path / "tokenizer.json"
    model.write_bytes(b"made-up model bytes")
    tokenizer.write_bytes(b"{}")
    return model, tokenizer


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pinned(model: Path, tokenizer: Path, **changes: Any) -> NliSettings:
    return _settings(model_sha256=_sha(model), tokenizer_sha256=_sha(tokenizer), **changes)


def _download(model: Path, tokenizer: Path, asked: list[dict[str, Any]]) -> Callable[..., str]:
    def fetch(**kwargs: Any) -> str:
        asked.append(kwargs)
        return str(model if kwargs["filename"].endswith(".onnx") else tokenizer)

    return fetch


def test_an_unpinned_or_untuned_config_never_loads_anything(tmp_path: Path) -> None:
    for settings in (NliSettings(), _settings(revision="main"), _settings(threshold=None), _settings(model_file="")):
        with pytest.raises(NliUnavailable, match=r"config/nli\.yaml"):
            load_nli(settings, tmp_path, download=lambda **_: pytest.fail("never downloads"))


def test_the_pinned_files_load_from_the_cache_first_with_no_token(tmp_path: Path) -> None:
    model, tokenizer = _files(tmp_path)
    asked: list[dict[str, Any]] = []
    near = [ReferencePair(premise="a b", hypothesis="a", entailment=softmax_at([0.0, 4.0, 0.0], 1))]
    settings = _pinned(model, tokenizer, reference=near)
    scorer = load_nli(
        settings,
        tmp_path,
        download=_download(model, tokenizer, asked),
        open_session=lambda _: _Session(),
        open_tokenizer=lambda _path, _length: _Tokenizer(),
    )
    assert isinstance(scorer, OnnxNli)
    assert all(a["revision"] == COMMIT and a["token"] is False and a["local_files_only"] for a in asked)


def test_a_file_whose_sha256_differs_is_never_used(tmp_path: Path) -> None:
    model, tokenizer = _files(tmp_path)
    for settings, what in (
        (_settings(model_sha256="1" * 64, tokenizer_sha256=_sha(tokenizer)), "model"),
        (_settings(model_sha256=_sha(model), tokenizer_sha256="1" * 64), "tokenizer"),
    ):
        with pytest.raises(NliUnavailable, match=f"downloaded {what} is not the pinned one"):
            load_nli(
                settings,
                tmp_path,
                download=_download(model, tokenizer, []),
                open_session=lambda _: _Session(),
                open_tokenizer=lambda _path, _length: _Tokenizer(),
            )


def test_a_download_that_fails_names_only_its_error_type(tmp_path: Path) -> None:
    def broken(**_: Any) -> str:
        raise ConnectionError("https://secret.example.test/with-a-token")

    with pytest.raises(NliUnavailable) as caught:
        load_nli(_settings(), tmp_path, download=broken)
    assert caught.value.reason == "the download failed (ConnectionError)"


def test_a_machine_where_the_probabilities_differ_from_the_full_model_never_uses_it(tmp_path: Path) -> None:
    model, tokenizer = _files(tmp_path)
    far = [ReferencePair(premise="a b", hypothesis="a", entailment=0.5)]  # the fake gives about 0.96 here
    near = [ReferencePair(premise="a b", hypothesis="a", entailment=softmax_at([0.0, 4.0, 0.0], 1) - 0.01)]
    options: dict[str, Any] = {
        "download": _download(model, tokenizer, []),
        "open_session": lambda _: _Session(),
        "open_tokenizer": lambda _path, _length: _Tokenizer(),
    }
    with pytest.raises(NliUnavailable, match="differ from the full model's"):
        load_nli(_pinned(model, tokenizer, reference=far), tmp_path, **options)
    with pytest.raises(NliUnavailable, match="no reference pairs"):
        load_nli(_pinned(model, tokenizer, reference=[]), tmp_path, **options)
    assert isinstance(load_nli(_pinned(model, tokenizer, reference=near), tmp_path, **options), OnnxNli)


# --- the holder and the time limit ------------------------------------------------------------------------------------


class _Fixed:
    def __init__(self, value: float = 0.9, wait_s: float = 0.0, error: Exception | None = None) -> None:
        self.value, self.wait_s, self.error = value, wait_s, error

    def entailment(self, pairs: Any) -> list[float]:
        time.sleep(self.wait_s)
        if self.error is not None:
            raise self.error
        return [self.value] * len(pairs)

    def premises(self, quote: str) -> list[str]:
        return [quote]


def test_the_holder_loads_once_in_the_background_and_hands_the_scorer_over() -> None:
    loads: list[int] = []

    def load(_: NliSettings) -> _Fixed:
        loads.append(1)
        return _Fixed()

    holder = NliHolder(_settings(), load=load)
    holder.start()
    assert holder.get(5.0).entailment([("p", "h")]) == [0.9]
    holder.get(5.0)
    assert loads == [1]


def test_a_failed_load_is_remembered_and_said_at_once_until_it_may_be_tried_again() -> None:
    now = [0.0]
    loads: list[int] = []

    def fail(_: NliSettings) -> Any:
        loads.append(1)
        raise NliUnavailable("the download failed (ConnectionError)")

    holder = NliHolder(_settings(), load=fail, retry_after_s=600, clock=lambda: now[0])
    with pytest.raises(NliUnavailable, match="download failed"):
        holder.get(5.0)
    with pytest.raises(NliUnavailable, match="download failed"):
        holder.get(5.0)
    assert loads == [1]
    now[0] = 601.0
    with pytest.raises(NliUnavailable):
        holder.get(5.0)
    assert loads == [1, 1]


def test_a_model_still_loading_says_so_within_the_wait() -> None:
    release = threading.Event()

    def load(_: NliSettings) -> _Fixed:
        release.wait(5.0)
        return _Fixed()

    holder = NliHolder(_settings(), load=load)
    with pytest.raises(NliUnavailable, match="still loading"):
        holder.get(0.05)
    release.set()
    assert holder.get(5.0) is not None


def test_one_holder_per_model_for_the_whole_process() -> None:
    assert nli.shared_nli(_settings()) is nli.shared_nli(_settings())
    assert nli.shared_nli(_settings()) is not nli.shared_nli(_settings(model_file="onnx/model.onnx"))


def test_scoring_past_its_time_or_failing_makes_the_model_unavailable() -> None:
    assert entailment_within(_Fixed(), [("p", "h")], 1.0) == [0.9]
    with pytest.raises(NliUnavailable, match=r"longer than 0\.1 s"):
        entailment_within(_Fixed(wait_s=1.0), [("p", "h")], 0.1)
    with pytest.raises(NliUnavailable, match="no time left"):
        entailment_within(_Fixed(), [("p", "h")], 0.0)
    with pytest.raises(NliUnavailable, match=r"failed \(RuntimeError\)"):
        entailment_within(_Fixed(error=RuntimeError("secret")), [("p", "h")], 1.0)
