"""The trained matcher (US-15 criteria 2 and 6, S3-15): the ONNX cross-encoder from S3-4, scored on CPU (D-64, P-10).

- The model and its tokenizer come from Hugging Face at the exact revision in config/matcher.yaml, and only that
  revision. They are kept in backend/.cache/matcher/ (git-ignored), so only the first use downloads them. A public
  model needs no key, so no token is ever sent.
- model.onnx must have the sha256 the card records (ml/MODELS.md), or it is not used.
- Loading runs in a thread of its own, started when the match stage is built, so it overlaps research. The stage waits
  for it only within its time share; a model that isn't ready gives fuzzy matching for that run.
- onnxruntime, tokenizers, huggingface_hub and numpy are imported only here, when a load or a score really starts:
  with MATCHER=fuzzy none of them is imported.

The score of a pair is the sigmoid of the model's one logit, as S3-4 measured it (ml/training/export_onnx.py).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from .scorer import PairScorer
from .settings import MatcherSettings

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "matcher"
MODEL_FILE = "model.onnx"
TOKENIZER_FILE = "tokenizer.json"
RETRY_AFTER_S = 600.0  # a failed load is tried again after this long, never on every search
# The model's inputs, and where each comes from in a tokenizers Encoding. A model that declares fewer gets fewer.
_FEED = {"input_ids": "ids", "attention_mask": "attention_mask", "token_type_ids": "type_ids"}


class ModelUnavailable(Exception):
    """The model can't score this run. `reason` is plain words for the log line, never a file's or a key's content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ShareSpent(Exception):
    """The model would score past the run's share of the match stage's time."""


class Session(Protocol):
    """What the scorer needs of an onnxruntime InferenceSession."""

    def get_inputs(self) -> Sequence[Any]: ...

    def run(self, output_names: list[str], input_feed: dict[str, Any]) -> list[Any]: ...


class PairTokenizer(Protocol):
    """What the scorer needs of a tokenizers Tokenizer: pairs in, encodings with ids, a mask and type ids out."""

    def encode_batch(self, input: list[tuple[str, str]]) -> list[Any]: ...


class OnnxPairScorer:
    """Scores pairs with the ONNX model, `batch_size` pairs at a time. One batch runs at a time in the process."""

    def __init__(self, session: Session, tokenizer: PairTokenizer, threshold: float, batch_size: int = 32) -> None:
        self.session = session
        self.tokenizer = tokenizer
        self.threshold = threshold
        self.batch_size = batch_size
        self._inputs = {i.name for i in session.get_inputs()}
        self._lock = threading.Lock()

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        return self.score_until(pairs, None)

    def score_until(self, pairs: Sequence[tuple[str, str]], seconds: float | None) -> list[float]:
        """As score(), but a batch waits for the model at most `seconds` while another search's batch runs: past
        that, ShareSpent, so a run never waits on others beyond its share (build rule 9)."""
        import numpy as np

        scores: list[float] = []
        for start in range(0, len(pairs), self.batch_size):
            chunk = [(left, right) for left, right in pairs[start : start + self.batch_size]]
            if not self._lock.acquire(timeout=-1 if seconds is None else max(0.0, seconds)):
                raise ShareSpent
            try:
                encoded = self.tokenizer.encode_batch(chunk)
                feed = {
                    name: np.array([getattr(e, field) for e in encoded], dtype=np.int64)
                    for name, field in _FEED.items()
                    if name in self._inputs
                }
                logits = np.asarray(self.session.run(["logits"], feed)[0], dtype=np.float64)
            finally:
                self._lock.release()
            first = logits.reshape(len(chunk), -1)[:, 0]
            scores += (1.0 / (1.0 + np.exp(-np.clip(first, -60.0, 60.0)))).tolist()
        return scores


# --- loading ----------------------------------------------------------------------------------------------------------

Download = Callable[..., str]
OpenSession = Callable[[Path], Session]
OpenTokenizer = Callable[[Path, int], PairTokenizer]


def load_model_scorer(
    settings: MatcherSettings,
    cache_dir: Path = CACHE_DIR,
    *,
    download: Download | None = None,
    open_session: OpenSession | None = None,
    open_tokenizer: OpenTokenizer | None = None,
) -> OnnxPairScorer:
    """The model scorer, from the local cache or, the first time, from Hugging Face. Raises ModelUnavailable."""
    if not settings.pinned:
        raise ModelUnavailable("no revision is set in config/matcher.yaml")
    fetch = download if download is not None else _hf_download
    try:
        model_path = _fetch(fetch, settings, MODEL_FILE, cache_dir)
        tokenizer_path = _fetch(fetch, settings, TOKENIZER_FILE, cache_dir)
    except Exception as exc:
        raise ModelUnavailable(f"the download failed ({type(exc).__name__})") from exc
    if settings.model_sha256 and _sha256(model_path) != settings.model_sha256.lower():
        raise ModelUnavailable("the downloaded model is not the pinned one (its sha256 differs)")
    try:
        session = (open_session or _onnx_session)(model_path)
        tokenizer = (open_tokenizer or _tokenizer)(tokenizer_path, settings.max_length)
    except Exception as exc:
        raise ModelUnavailable(f"the model could not be loaded ({type(exc).__name__})") from exc
    return OnnxPairScorer(session, tokenizer, settings.threshold, settings.batch_size)


def _fetch(fetch: Download, settings: MatcherSettings, filename: str, cache_dir: Path) -> Path:
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _onnx_session(path: Path) -> Session:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3  # errors only: its warnings name graph nodes, which the log doesn't need
    session: Session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
    return session


def _tokenizer(path: Path, max_length: int) -> PairTokenizer:
    """As S3-4 tokenised: pairs cut to `max_length` tokens, longest first, and padded to the longest in the batch."""
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.enable_truncation(max_length=max_length)
    pad = tokenizer.token_to_id("[PAD]")
    tokenizer.enable_padding(pad_id=pad if pad is not None else 0, pad_token="[PAD]")
    return tokenizer


# --- one model per process, loaded in the background ------------------------------------------------------------------


class ModelHolder:
    """Loads the model once, in its own thread, and hands it over when it is ready.

    A failed load is remembered: every run until RETRY_AFTER_S has passed is told why, at once, and fuzzy matching runs.
    """

    def __init__(
        self,
        settings: MatcherSettings,
        load: Callable[[MatcherSettings], PairScorer] | None = None,
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
        """Begin loading, unless the model is loaded, loading, or failed less than RETRY_AFTER_S ago."""
        with self._lock:
            if self._scorer is not None or self._loading:
                return
            if self._failed_at is not None and self._clock() - self._failed_at < self._retry_after_s:
                return
            self._loading = True
            self._done.clear()
        try:
            threading.Thread(target=self._load, name="matcher-load", daemon=True).start()
        except Exception as exc:  # no thread to be had: a failed load, never a stuck "loading" or a failed search
            with self._lock:
                self._loading = False
                self._reason = f"the model could not be loaded ({type(exc).__name__})"
                self._failed_at = self._clock()
            self._done.set()

    def get(self, timeout: float) -> PairScorer:
        """The model, waiting at most `timeout` seconds for it. Raises ModelUnavailable with the reason."""
        self.start()
        self._done.wait(max(0.0, timeout))
        with self._lock:
            if self._scorer is not None:
                return self._scorer
            if self._loading:
                raise ModelUnavailable(f"the model was still loading after {timeout:.2g} s")
            raise ModelUnavailable(self._reason or "the model could not be loaded")

    def _load(self) -> None:
        started = time.monotonic()
        load = self._load_with if self._load_with is not None else load_model_scorer  # looked up now: tests swap it
        scorer: PairScorer | None = None
        try:
            scorer = load(self.settings)
            reason = None
        except ModelUnavailable as exc:
            reason = exc.reason
        except Exception as exc:  # a bug in a library must not stop the search (build rule 9)
            reason = f"the model could not be loaded ({type(exc).__name__})"
        with self._lock:
            self._scorer, self._reason, self._loading = scorer, reason, False
            self._failed_at = None if scorer is not None else self._clock()
        self._done.set()
        if scorer is not None:
            log.info("match: the matcher model is ready (%.1f s)", time.monotonic() - started)
        else:
            log.info("match: the matcher model is not available: %s", reason)


_holders: dict[tuple[str, str, str, float, int, int], ModelHolder] = {}
_holders_lock = threading.Lock()


def shared_model(settings: MatcherSettings) -> ModelHolder:
    """The process's one holder for this model: every search's stage shares it, so the model loads once."""
    key = (
        settings.repo,
        settings.revision,
        settings.model_sha256,
        settings.threshold,
        settings.batch_size,
        settings.max_length,
    )
    with _holders_lock:
        if key not in _holders:
            _holders[key] = ModelHolder(settings)
        return _holders[key]


def shared_models() -> dict[tuple[str, str, str, float, int, int], ModelHolder]:
    with _holders_lock:
        return dict(_holders)


def clear_shared_models() -> None:
    """For tests: the holders last as long as the process."""
    with _holders_lock:
        _holders.clear()
