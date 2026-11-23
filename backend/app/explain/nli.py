"""The citation check's model (US-22, P-41): cross-encoder/nli-deberta-v3-xsmall through onnxruntime, on our server.

- The ONNX file and tokenizer.json come from Hugging Face at the exact revision in config/nli.yaml, and only that
  revision. They are kept in backend/.cache/nli/ (git-ignored), so only the first use downloads them. A public model
  needs no key, so no token is ever sent, and no quote or sentence ever leaves this process: Reddit's words included,
  which are read here and never saved (D-53).
- Both files must have the sha256 the card records (ml/MODELS.md card 6), or they are not used.
- Before first use the model reads the reference pairs and compares its entailment probabilities with the full
  model's (config/nli.yaml `reference`). A machine where any differs by more than `reference_tolerance` never uses it.
- A pair is (premise, hypothesis): a cited quote, or a chunk of one, and the sentence. Its entailment probability is
  the softmax of the model's logits at the entailment label. A quote over `premise_tokens` tokens is split into
  overlapping chunks, each cut at token boundaries from the quote's own text.
- Loading runs in a thread of its own, started when the stage is built, so it overlaps the search. The check waits for
  it only within its own time. A failed load is remembered and tried again after RETRY_AFTER_S.
- onnxruntime, tokenizers, huggingface_hub and numpy are imported only here, when a load or a score really starts.
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, Protocol

from .nli_settings import NliSettings, ReferencePair

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "nli"
RETRY_AFTER_S = 600.0
_FEED = {"input_ids": "ids", "attention_mask": "attention_mask", "token_type_ids": "type_ids"}

Pair = tuple[str, str]


class NliUnavailable(Exception):
    """The check can't run. `reason` is plain words for the log line, never a quote's or a sentence's text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PairScorer(Protocol):
    """Pairs in, one entailment probability (0 to 1) per pair out; and a quote split into premises."""

    def entailment(self, pairs: Sequence[Pair]) -> list[float]: ...

    def premises(self, quote: str) -> list[str]: ...


class Session(Protocol):
    """What the scorer needs of an onnxruntime InferenceSession."""

    def get_inputs(self) -> Sequence[Any]: ...

    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> Sequence[Any]: ...


class PairTokenizer(Protocol):
    """What the scorer needs of a tokenizers Tokenizer: pairs encoded together, and a text's token offsets."""

    def encode_batch(self, input: list[Any]) -> list[Any]: ...

    def encode(
        self, sequence: Any, pair: Any = None, is_pretokenized: bool = False, add_special_tokens: bool = True
    ) -> Any: ...


def chunks(text: str, offsets: Sequence[tuple[int, int]], size: int, overlap: int) -> list[str]:
    """The text in pieces of at most `size` tokens, each sharing `overlap` tokens with the one before, cut from the
    text itself at token boundaries. A text of `size` tokens or fewer is one piece: itself."""
    spans = [(a, b) for a, b in offsets if b > a]
    if len(spans) <= size:
        return [text]
    step = max(1, size - overlap)
    out: list[str] = []
    for start in range(0, len(spans), step):
        window = spans[start : start + size]
        out.append(text[window[0][0] : window[-1][1]])
        if start + size >= len(spans):
            break
    return out


def softmax_at(logits: Sequence[float], index: int) -> float:
    top = max(logits)
    exps = [math.exp(x - top) for x in logits]
    return exps[index] / sum(exps)


class OnnxNli:
    """Entailment probabilities from the ONNX cross-encoder, every pair of a call in one batch."""

    def __init__(
        self, session: Session, tokenizer: PairTokenizer, counter: PairTokenizer, settings: NliSettings
    ) -> None:
        self.session = session
        self.tokenizer = tokenizer  # pairs: truncated to max_length, padded to the longest in the batch
        self.counter = counter  # a quote alone, never truncated: how many tokens it really has
        self.settings = settings
        self._inputs = {i.name for i in session.get_inputs()}

    def premises(self, quote: str) -> list[str]:
        encoded = self.counter.encode(quote, add_special_tokens=False)
        return chunks(quote, list(encoded.offsets), self.settings.premise_tokens, self.settings.premise_overlap)

    def entailment(self, pairs: Sequence[Pair]) -> list[float]:
        if not pairs:
            return []
        import numpy as np

        encoded = self.tokenizer.encode_batch([(premise, hypothesis) for premise, hypothesis in pairs])
        feed = {
            name: np.array([getattr(e, field) for e in encoded], dtype=np.int64)
            for name, field in _FEED.items()
            if name in self._inputs
        }
        logits = np.asarray(self.session.run(None, feed)[0], dtype=np.float64)
        if logits.shape != (len(pairs), self.settings.labels):
            raise NliUnavailable(f"the model gave logits of shape {logits.shape}, not {self.settings.labels} per pair")
        return [softmax_at(row.tolist(), self.settings.entailment_index) for row in logits]


# --- loading ----------------------------------------------------------------------------------------------------------

Download = Callable[..., str]
OpenSession = Callable[[Path], Session]
OpenTokenizer = Callable[[Path, int | None], PairTokenizer]


def load_nli(
    settings: NliSettings,
    cache_dir: Path = CACHE_DIR,
    *,
    download: Download | None = None,
    open_session: OpenSession | None = None,
    open_tokenizer: OpenTokenizer | None = None,
) -> OnnxNli:
    """The scorer, from the local cache or, the first time, from Hugging Face. Raises NliUnavailable."""
    if not settings.pinned:
        raise NliUnavailable("no revision, sha256 and threshold are set in config/nli.yaml")
    model_path, tokenizer_path = pinned_files(settings, cache_dir, download=download)
    if sha256_of(model_path) != settings.model_sha256.lower():
        raise NliUnavailable("the downloaded model is not the pinned one (its sha256 differs)")
    if sha256_of(tokenizer_path) != settings.tokenizer_sha256.lower():
        raise NliUnavailable("the downloaded tokenizer is not the pinned one (its sha256 differs)")
    opener = open_tokenizer or pair_tokenizer
    try:
        scorer = OnnxNli(
            (open_session or onnx_session)(model_path),
            opener(tokenizer_path, settings.max_length),
            opener(tokenizer_path, None),
            settings,
        )
    except Exception as exc:
        raise NliUnavailable(f"the model could not be loaded ({type(exc).__name__})") from exc
    check_against(scorer, settings.reference, settings.reference_tolerance)
    return scorer


def pinned_files(
    settings: NliSettings, cache_dir: Path = CACHE_DIR, *, download: Download | None = None
) -> tuple[Path, Path]:
    """The pinned model file and tokenizer.json, from the cache or downloaded once. Raises NliUnavailable. Their
    sha256 is load_nli's to check."""
    fetch = download if download is not None else _hf_download
    try:
        return (
            _fetch(fetch, settings, settings.model_file, cache_dir),
            _fetch(fetch, settings, settings.tokenizer_file, cache_dir),
        )
    except Exception as exc:
        raise NliUnavailable(f"the download failed ({type(exc).__name__})") from exc


def check_against(scorer: PairScorer, reference: Sequence[ReferencePair], tolerance: float) -> float:
    """The largest difference between this machine's entailment probabilities and the full model's on the reference
    pairs. Raises NliUnavailable when it is over `tolerance`, or when there is nothing to check against."""
    if not reference:
        raise NliUnavailable("there are no reference pairs to check the model against")
    try:
        found = scorer.entailment([(r.premise, r.hypothesis) for r in reference])
        worst = max(abs(mine - r.entailment) for mine, r in zip(found, reference, strict=True))
    except NliUnavailable:
        raise
    except Exception as exc:
        raise NliUnavailable(f"the model could not read the reference pairs ({type(exc).__name__})") from exc
    if not worst <= tolerance:
        raise NliUnavailable(f"on this machine the model's probabilities differ from the full model's (by {worst:.4f})")
    return worst


def _fetch(fetch: Download, settings: NliSettings, filename: str, cache_dir: Path) -> Path:
    """The file from the cache if it is there (no network at all), else downloaded once at the pinned revision."""
    asked = {
        "repo_id": settings.repo,
        "filename": filename,
        "revision": settings.revision,
        "cache_dir": str(cache_dir),
        "token": False,  # a public model: the developer's HF_TOKEN is never sent
    }
    try:
        return Path(fetch(**asked, local_files_only=True))
    except Exception:  # not in the cache yet
        return Path(fetch(**asked, local_files_only=False))


def _hf_download(**kwargs: Any) -> str:
    from huggingface_hub import hf_hub_download

    return str(hf_hub_download(**kwargs))


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def onnx_session(path: Path) -> Session:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3  # errors only
    session: Session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    return session


def pair_tokenizer(path: Path, max_length: int | None) -> PairTokenizer:
    """With `max_length`, pairs cut to it and padded to the longest in the batch; with None, a text's full length."""
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(path))
    if max_length is None:
        tokenizer.no_truncation()
        tokenizer.no_padding()
        return tokenizer
    tokenizer.enable_truncation(max_length=max_length)
    pad = tokenizer.token_to_id("[PAD]")
    tokenizer.enable_padding(pad_id=pad if pad is not None else 0, pad_token="[PAD]")
    return tokenizer


# --- scoring within a time limit --------------------------------------------------------------------------------------

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nli")


def entailment_within(scorer: PairScorer, pairs: Sequence[Pair], timeout_s: float) -> list[float]:
    """The scorer's answer, or NliUnavailable once `timeout_s` has passed (the scoring is left to finish alone)."""
    if timeout_s <= 0:
        raise NliUnavailable("there was no time left for the check")
    future: Future[list[float]] = _pool.submit(scorer.entailment, list(pairs))
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeout:
        raise NliUnavailable(f"the check took longer than {timeout_s:.1f} s") from None
    except NliUnavailable:
        raise
    except Exception as exc:  # a bug in a library must not stop the search (build rule 9)
        raise NliUnavailable(f"the model failed ({type(exc).__name__})") from exc


def sentence_probabilities(
    scorer: PairScorer, checks: Sequence[tuple[str, Sequence[str]]], timeout_s: float
) -> list[float]:
    """The check's one interface (P-49): each sentence with its quotes in, its best entailment probability out.

    Every quote (in chunks when long) is a premise for its sentence, all in one batch, and the highest probability
    counts; a sentence with no quote gets 0.0. Explain calls the model only through here, so the model can move to a
    service of its own without touching explain's logic. NliUnavailable for any failure, the tokenizer's included.
    """
    pairs: list[Pair] = []
    spans: list[tuple[int, int]] = []
    try:
        for sentence, quotes in checks:
            first = len(pairs)
            pairs += [(premise, sentence) for quote in quotes for premise in scorer.premises(quote)]
            spans.append((first, len(pairs)))
    except Exception as exc:  # the tokenizer is part of the model: its failure gives the fallback too (DECIDED 5)
        raise NliUnavailable(f"the model could not read a quote ({type(exc).__name__})") from exc
    found = entailment_within(scorer, pairs, timeout_s) if pairs else []
    return [max(found[a:b], default=0.0) for a, b in spans]


# --- one model per process, loaded in the background ------------------------------------------------------------------


class NliHolder:
    """Loads the scorer once, in its own thread, and hands it over when it is ready. A failed load is remembered:
    until RETRY_AFTER_S has passed, every caller is told why at once."""

    def __init__(
        self,
        settings: NliSettings,
        load: Callable[[NliSettings], PairScorer] | None = None,
        *,
        retry_after_s: float = RETRY_AFTER_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._load_with = load
        self._retry_after_s = retry_after_s
        self._clock = clock
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._scorer: PairScorer | None = None
        self._loading = False
        self._reason: str | None = None
        self._failed_at: float | None = None

    def start(self) -> None:
        """Begin loading, unless it is loaded, loading, or failed less than RETRY_AFTER_S ago."""
        with self._lock:
            if self._scorer is not None or self._loading:
                return
            if self._failed_at is not None and self._clock() - self._failed_at < self._retry_after_s:
                return
            self._loading = True
            self._done.clear()
        try:
            threading.Thread(target=self._load, name="nli-load", daemon=True).start()
        except Exception as exc:  # no thread to be had: a failed load, never a stuck "loading"
            with self._lock:
                self._loading = False
                self._reason = f"the model could not be loaded ({type(exc).__name__})"
                self._failed_at = self._clock()
            self._done.set()

    def get(self, timeout: float) -> PairScorer:
        """The scorer, waiting at most `timeout` seconds for it. Raises NliUnavailable with the reason."""
        self.start()
        self._done.wait(max(0.0, timeout))
        with self._lock:
            if self._scorer is not None:
                return self._scorer
            if self._loading:
                raise NliUnavailable(f"the model was still loading after {timeout:.2g} s")
            raise NliUnavailable(self._reason or "the model could not be loaded")

    def _load(self) -> None:
        started = time.monotonic()
        load = self._load_with if self._load_with is not None else load_nli
        scorer: PairScorer | None = None
        try:
            scorer = load(self.settings)
            reason = None
        except NliUnavailable as exc:
            reason = exc.reason
        except Exception as exc:  # a bug in a library must not stop the search (build rule 9)
            reason = f"the model could not be loaded ({type(exc).__name__})"
        with self._lock:
            self._scorer, self._reason, self._loading = scorer, reason, False
            self._failed_at = None if scorer is not None else self._clock()
        self._done.set()
        if scorer is not None:
            log.info("explain: the citation check's model is ready (%.1f s)", time.monotonic() - started)
        else:
            log.info("explain: the citation check's model is not available: %s", reason)


_holders: dict[tuple[str, str, str, str, str], NliHolder] = {}
_holders_lock = threading.Lock()


def shared_nli(settings: NliSettings) -> NliHolder:
    """The process's one holder for this model: every search shares it, so the model loads once."""
    key = (settings.repo, settings.revision, settings.model_file, settings.model_sha256, settings.tokenizer_sha256)
    with _holders_lock:
        if key not in _holders:
            _holders[key] = NliHolder(settings)
        return _holders[key]


def clear_shared_nli() -> None:
    """For tests: the holders last as long as the process."""
    with _holders_lock:
        _holders.clear()
