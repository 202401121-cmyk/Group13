"""Sentence embeddings on our own server (US-06, P-40): all-MiniLM-L6-v2 through onnxruntime, never a provider.

- The ONNX file and tokenizer.json come from Hugging Face at the exact revision in config/memory.yaml, and only that
  revision. They are kept in backend/.cache/memory/ (git-ignored), so only the first use downloads them. A public model
  needs no key, so no token is ever sent, and nothing a shopper typed ever leaves this process.
- Both files must have the sha256 the card records (ml/MODELS.md card 5), or they are not used.
- Before first use the model embeds a few fixed sentences and compares them with the full model's vectors
  (config/memory_reference.json). A machine where they differ (cosine under 0.99) never uses it: memory is off there.
- A vector is the mean of the token vectors under the attention mask, scaled to length 1 (as sentence-transformers
  does for this model), so a dot product is the cosine.
- Loading runs in a thread of its own; a caller waits for it only within its time share. A failed load is remembered
  and tried again after RETRY_AFTER_S, never on every interview.
- onnxruntime, tokenizers, huggingface_hub and numpy are imported only here, when a load or an embedding really starts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from .settings import MemorySettings

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache" / "memory"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
RETRY_AFTER_S = 600.0
_FEED = {"input_ids": "ids", "attention_mask": "attention_mask", "token_type_ids": "type_ids"}

Vector = list[float]


class MemoryUnavailable(Exception):
    """Memory can't be used for this interview. `reason` is plain words for the log line, never a shopper's text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Embedder(Protocol):
    """Texts in, one unit-length vector per text out."""

    def embed(self, texts: Sequence[str]) -> list[Vector]: ...


class Session(Protocol):
    """What the embedder needs of an onnxruntime InferenceSession."""

    def get_inputs(self) -> Sequence[Any]: ...

    def run(self, output_names: list[str] | None, input_feed: dict[str, Any]) -> Sequence[Any]: ...


class TextTokenizer(Protocol):
    """What the embedder needs of a tokenizers Tokenizer."""

    def encode_batch(self, input: list[str]) -> list[Any]: ...


class OnnxEmbedder:
    """Mean-pooled, length-1 vectors from the ONNX model's last hidden state."""

    def __init__(self, session: Session, tokenizer: TextTokenizer, dimensions: int = 384, batch_size: int = 32) -> None:
        self.session = session
        self.tokenizer = tokenizer
        self.dimensions = dimensions
        self.batch_size = batch_size
        self._inputs = {i.name for i in session.get_inputs()}

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        import numpy as np

        vectors: list[Vector] = []
        for start in range(0, len(texts), self.batch_size):
            chunk = list(texts[start : start + self.batch_size])
            encoded = self.tokenizer.encode_batch(chunk)
            feed = {
                name: np.array([getattr(e, field) for e in encoded], dtype=np.int64)
                for name, field in _FEED.items()
                if name in self._inputs
            }
            hidden = np.asarray(self.session.run(None, feed)[0], dtype=np.float64)  # batch x tokens x dimensions
            if hidden.ndim != 3 or hidden.shape[-1] != self.dimensions:
                raise MemoryUnavailable(f"the model gave vectors of shape {hidden.shape}, not {self.dimensions} wide")
            mask = np.array([e.attention_mask for e in encoded], dtype=np.float64)[:, :, None]
            pooled = (hidden * mask).sum(axis=1) / np.clip(mask.sum(axis=1), 1e-9, None)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            vectors += (pooled / np.clip(norms, 1e-12, None)).tolist()
        return vectors


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """The cosine of two vectors (for unit vectors, their dot product). 0 for a zero vector."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / size if size else 0.0


# --- loading ----------------------------------------------------------------------------------------------------------

Download = Callable[..., str]
OpenSession = Callable[[Path], Session]
OpenTokenizer = Callable[[Path, int], TextTokenizer]


def load_embedder(
    settings: MemorySettings,
    cache_dir: Path = CACHE_DIR,
    *,
    download: Download | None = None,
    open_session: OpenSession | None = None,
    open_tokenizer: OpenTokenizer | None = None,
    reference: Sequence[tuple[str, Vector]] | None = None,
) -> OnnxEmbedder:
    """The embedder, from the local cache or, the first time, from Hugging Face. Raises MemoryUnavailable."""
    if not settings.pinned:
        raise MemoryUnavailable("no revision and sha256 are set in config/memory.yaml")
    fetch = download if download is not None else _hf_download
    try:
        model_path = _fetch(fetch, settings, settings.model_file, cache_dir)
        tokenizer_path = _fetch(fetch, settings, settings.tokenizer_file, cache_dir)
    except Exception as exc:
        raise MemoryUnavailable(f"the download failed ({type(exc).__name__})") from exc
    if sha256_of(model_path) != settings.model_sha256.lower():
        raise MemoryUnavailable("the downloaded model is not the pinned one (its sha256 differs)")
    if sha256_of(tokenizer_path) != settings.tokenizer_sha256.lower():
        raise MemoryUnavailable("the downloaded tokenizer is not the pinned one (its sha256 differs)")
    try:
        session = (open_session or onnx_session)(model_path)
        tokenizer = (open_tokenizer or text_tokenizer)(tokenizer_path, settings.max_length)
        embedder = OnnxEmbedder(session, tokenizer, settings.dimensions)
    except Exception as exc:
        raise MemoryUnavailable(f"the model could not be loaded ({type(exc).__name__})") from exc
    check_against(embedder, reference if reference is not None else load_reference(settings), settings)
    return embedder


def check_against(embedder: Embedder, reference: Sequence[tuple[str, Vector]], settings: MemorySettings) -> float:
    """The lowest cosine between this machine's vectors and the full model's for the reference sentences. Raises
    MemoryUnavailable when it is under `check_min_cosine`, or when there is nothing to check against."""
    if not reference:
        raise MemoryUnavailable("there are no reference vectors to check the model against")
    try:
        found = embedder.embed([text for text, _ in reference])
        lowest = min(cosine(mine, theirs) for mine, (_, theirs) in zip(found, reference, strict=True))
    except MemoryUnavailable:
        raise
    except Exception as exc:
        raise MemoryUnavailable(f"the model could not embed the reference sentences ({type(exc).__name__})") from exc
    if not lowest >= settings.check_min_cosine:
        raise MemoryUnavailable(
            f"on this machine the model's vectors differ from the full model's (cosine {lowest:.4f})"
        )
    return lowest


def load_reference(settings: MemorySettings, config_dir: Path = CONFIG_DIR) -> list[tuple[str, Vector]]:
    """The reference sentences and the full model's vectors for them, from config/. Empty if it can't be read."""
    try:
        raw = json.loads((config_dir / settings.reference_file).read_text(encoding="utf-8"))
        return [(str(item["text"]), [float(x) for x in item["vector"]]) for item in raw["sentences"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _fetch(fetch: Download, settings: MemorySettings, filename: str, cache_dir: Path) -> Path:
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


def text_tokenizer(path: Path, max_length: int) -> TextTokenizer:
    """Texts cut to `max_length` tokens and padded to the longest in the batch, as sentence-transformers does."""
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(path))
    tokenizer.enable_truncation(max_length=max_length)
    pad = tokenizer.token_to_id("[PAD]")
    tokenizer.enable_padding(pad_id=pad if pad is not None else 0, pad_token="[PAD]")
    return tokenizer


# --- one model per process, loaded in the background ------------------------------------------------------------------


class EmbedderHolder:
    """Loads the embedder once, in its own thread, and hands it over when it is ready. A failed load is remembered:
    until RETRY_AFTER_S has passed, every caller is told why at once."""

    def __init__(
        self,
        settings: MemorySettings,
        load: Callable[[MemorySettings], Embedder] | None = None,
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
        self._embedder: Embedder | None = None
        self._loading = False
        self._reason: str | None = None
        self._failed_at: float | None = None

    def start(self) -> None:
        """Begin loading, unless it is loaded, loading, or failed less than RETRY_AFTER_S ago."""
        with self._lock:
            if self._embedder is not None or self._loading:
                return
            if self._failed_at is not None and self._clock() - self._failed_at < self._retry_after_s:
                return
            self._loading = True
            self._done.clear()
        try:
            threading.Thread(target=self._load, name="memory-load", daemon=True).start()
        except Exception as exc:  # no thread to be had: a failed load, never a stuck "loading"
            with self._lock:
                self._loading = False
                self._reason = f"the model could not be loaded ({type(exc).__name__})"
                self._failed_at = self._clock()
            self._done.set()

    def get(self, timeout: float) -> Embedder:
        """The embedder, waiting at most `timeout` seconds for it. Raises MemoryUnavailable with the reason."""
        self.start()
        self._done.wait(max(0.0, timeout))
        with self._lock:
            if self._embedder is not None:
                return self._embedder
            if self._loading:
                raise MemoryUnavailable(f"the model was still loading after {timeout:.2g} s")
            raise MemoryUnavailable(self._reason or "the model could not be loaded")

    def _load(self) -> None:
        started = time.monotonic()
        load = self._load_with if self._load_with is not None else load_embedder
        embedder: Embedder | None = None
        try:
            embedder = load(self.settings)
            reason = None
        except MemoryUnavailable as exc:
            reason = exc.reason
        except Exception as exc:  # a bug in a library must not stop the interview (build rule 9)
            reason = f"the model could not be loaded ({type(exc).__name__})"
        with self._lock:
            self._embedder, self._reason, self._loading = embedder, reason, False
            self._failed_at = None if embedder is not None else self._clock()
        self._done.set()
        if embedder is not None:
            log.info("memory: the embedding model is ready (%.1f s)", time.monotonic() - started)
        else:
            log.info("memory: the embedding model is not available: %s", reason)


_holders: dict[tuple[str, str, str, str, str], EmbedderHolder] = {}
_holders_lock = threading.Lock()


def shared_embedder(settings: MemorySettings) -> EmbedderHolder:
    """The process's one holder for this model: every interview shares it, so the model loads once."""
    key = (settings.repo, settings.revision, settings.model_file, settings.model_sha256, settings.tokenizer_sha256)
    with _holders_lock:
        if key not in _holders:
            _holders[key] = EmbedderHolder(settings)
        return _holders[key]


def clear_shared_embedders() -> None:
    """For tests: the holders last as long as the process."""
    with _holders_lock:
        _holders.clear()
