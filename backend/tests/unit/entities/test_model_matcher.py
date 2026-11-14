"""US-15 criteria 2 and 6, and the speed of DECIDED 5 (S3-15): the trained matcher behind the match stage's scorer,
and fuzzy as its fallback. Criterion 5 (precision on held-out pairs) is S3-4's evaluation, ml/results/matcher_v1.json.

No test here downloads or loads the real model: the scorers, sessions, tokenizers and downloads are fakes
(tests/live/test_matcher_live.py loads the real one, on purpose).
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.contracts import MatchInput, MatchResult, ProductMention, StageContext
from app.entities import FuzzyMatchStage, ModelMatchStage
from app.entities import model as model_module
from app.entities.model import (
    ModelHolder,
    ModelUnavailable,
    OnnxPairScorer,
    ShareSpent,
    load_model_scorer,
    shared_model,
)
from app.entities.scorer import PairScorer
from app.entities.settings import MatcherSettings, load_matcher_settings
from app.pipeline import default_search_stages

SEARCH_ID = "srch_aaaaaaaaaaaaaaaaaaaaaaaa"


def _ctx() -> StageContext:
    """A fresh search clock for each run: the model's share is cut to the time the search has left (150 s from the
    context's start), so a context made at import would have no time left by the time a long suite reaches a test."""
    return StageContext(search_id=SEARCH_ID)


REPO_ROOT = Path(__file__).resolve().parents[4]
PINNED = "6a2ce26b0c4caf6fd1655ef88d27e6ee3954b296"
THRESHOLD = 0.9646
CRITERION_1 = ("Sony WH-1000XM5", "XM5", "1000XM5 headphones")
OPEN_PAIR = ("Kindle Paperwhite", "Kindle Paperwite")  # no code, no number: only a scorer can settle it


def _settings(**changes: Any) -> MatcherSettings:
    base = {"repo": "made-up/matcher", "revision": PINNED, "threshold": THRESHOLD, "use": "model"}
    return MatcherSettings.model_validate({**base, **changes})


def _input(*names: str) -> MatchInput:
    return MatchInput(mentions=[ProductMention(raw_name=n, is_product=True) for n in names])


def _groups(result: MatchResult) -> list[set[str]]:
    by_id: dict[str, set[str]] = {}
    for name, product_id in result.alias_map.items():
        by_id.setdefault(product_id, set()).add(name)
    return sorted(by_id.values(), key=lambda names: sorted(names))


class ConstScorer:
    """Every pair gets the same score."""

    threshold = THRESHOLD

    def __init__(self, value: float) -> None:
        self.value = value
        self.pairs: list[tuple[str, str]] = []

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.pairs += pairs
        return [self.value] * len(pairs)


class Loader:
    """A fake model loader: counts its calls, and gives a scorer or raises."""

    def __init__(self, scorer: PairScorer | None = None, error: Exception | None = None) -> None:
        self.scorer, self.error, self.calls = scorer, error, 0

    def __call__(self, settings: MatcherSettings) -> PairScorer:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.scorer is not None
        return self.scorer


def _loaded(scorer: PairScorer, settings: MatcherSettings | None = None) -> ModelHolder:
    holder = ModelHolder(settings or _settings(), load=Loader(scorer))
    holder.get(timeout=5)  # loaded before the run, as it is after research
    return holder


def _stage(scorer: PairScorer, **changes: Any) -> ModelMatchStage:
    settings = _settings(**changes)
    return ModelMatchStage(settings=settings, model=_loaded(scorer, settings))


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.name.startswith("app.entities") and r.levelno >= logging.WARNING
    ]


# --- the model decides open pairs, the rules the rest (criterion 2) ---------------------------------------------------


def test_the_model_merges_criterion_1s_names_and_keeps_wh_and_wf_apart() -> None:
    sure = ConstScorer(1.0)
    one = _stage(sure).run(_input(*CRITERION_1), _ctx())
    assert _groups(one) == [set(CRITERION_1)]
    assert one.method == "model"
    apart = _stage(sure).run(_input("Sony WH-1000XM5", "Sony WF-1000XM5"), _ctx())
    assert _groups(apart) == [{"Sony WF-1000XM5"}, {"Sony WH-1000XM5"}]  # different codes: the rules say no first
    assert apart.method == "model"
    # The rules settle all of these (codes that agree, codes that differ) before the model is asked, so no score of
    # the model can merge the WH and WF or split criterion 1's names. Pairs the model does decide: the next test.
    assert sure.pairs == []


def test_a_pair_at_the_threshold_merges_and_one_below_stays_separate() -> None:
    at = _stage(ConstScorer(THRESHOLD)).run(_input(*OPEN_PAIR), _ctx())
    assert _groups(at) == [set(OPEN_PAIR)]
    below_scorer = ConstScorer(0.9645)
    below = _stage(below_scorer).run(_input(*OPEN_PAIR), _ctx())
    assert _groups(below) == [{OPEN_PAIR[0]}, {OPEN_PAIR[1]}]  # uncertain: kept apart, never guessed
    assert below.method == "model"
    assert [frozenset(p) for p in below_scorer.pairs] == [{"kindle paperwhite", "kindle paperwite"}]  # normalised names


def test_the_same_inputs_give_the_same_output() -> None:
    names = ["Sony WH-1000XM5", "XM5", "Sony WF-1000XM5", "Kindle Paperwhite", "Kindle Paperwite", "Galaxy A35"]
    first = _stage(ConstScorer(0.99)).run(_input(*names), _ctx())
    for seed in range(5):
        shuffled = list(names)
        random.Random(seed).shuffle(shuffled)
        assert _stage(ConstScorer(0.99)).run(_input(*shuffled), _ctx()) == first


# --- falling back to fuzzy (criterion 6) ------------------------------------------------------------------------------


@dataclass
class Case:
    reason: str
    stage: Callable[[Path], ModelMatchStage]


class _Slow:
    """Each call moves the stage's clock on 5 seconds: past a 3-second share after the first batch."""

    threshold = THRESHOLD

    def __init__(self, clock: list[float]) -> None:
        self.clock = clock

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.clock[0] += 5.0
        return [1.0] * len(pairs)


class _Broken:
    threshold = THRESHOLD

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        raise RuntimeError("made-up scoring error")


def _files(folder: Path, model_bytes: bytes = b"made-up onnx bytes") -> Callable[..., str]:
    """A fake hf_hub_download: writes the asked-for file into `folder` and gives its path."""

    def download(**kwargs: Any) -> str:
        path = folder / kwargs["filename"]
        path.write_bytes(model_bytes if kwargs["filename"].endswith(".onnx") else b"{}")
        return str(path)

    return download


def _sha(data: bytes = b"made-up onnx bytes") -> str:
    return hashlib.sha256(data).hexdigest()


def _with_loader(settings: MatcherSettings, load: Callable[[MatcherSettings], PairScorer]) -> ModelMatchStage:
    return ModelMatchStage(settings=settings, model=ModelHolder(settings, load=load))


def _fails(error: Exception) -> Callable[..., Any]:
    def raise_it(*args: Any, **kwargs: Any) -> Any:
        raise error

    return raise_it


_still_loading = threading.Event()


def _blocks(settings: MatcherSettings) -> PairScorer:
    _still_loading.wait(10)
    return ConstScorer(1.0)


def _slow_stage(_: Path) -> ModelMatchStage:
    clock = [0.0]
    settings = _settings(batch_size=1)
    return ModelMatchStage(settings=settings, model=_loaded(_Slow(clock), settings), clock=lambda: clock[0])


CASES = [
    Case(
        "no revision",
        lambda tmp: _with_loader(
            _settings(revision="upload pending"),
            lambda s: load_model_scorer(s, tmp, download=_fails(AssertionError("never asked"))),
        ),
    ),
    Case(
        "the download failed (ConnectionError)",
        lambda tmp: _with_loader(_settings(), lambda s: load_model_scorer(s, tmp, download=_fails(ConnectionError()))),
    ),
    Case(
        "sha256",
        lambda tmp: _with_loader(
            _settings(model_sha256=_sha(b"the pinned bytes")), lambda s: load_model_scorer(s, tmp, download=_files(tmp))
        ),
    ),
    Case(
        "could not be loaded (ValueError)",
        lambda tmp: _with_loader(
            _settings(model_sha256=_sha()),
            lambda s: load_model_scorer(s, tmp, download=_files(tmp), open_session=_fails(ValueError("bad graph"))),
        ),
    ),
    Case("scoring failed (RuntimeError)", lambda tmp: _stage(_Broken())),
    Case("longer than its 3 s share", _slow_stage),
    Case("still loading", lambda tmp: _with_loader(_settings(time_share_s=0.05), _blocks)),
]


@pytest.mark.parametrize("case", CASES, ids=[c.reason for c in CASES])
def test_each_fallback_reason_gives_method_fuzzy_and_one_log_line(
    case: Case, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    names = (*OPEN_PAIR, "Kindle Paperwhyte", *CRITERION_1, "Sony WF-1000XM5")
    _still_loading.clear()
    try:
        with caplog.at_level(logging.INFO):
            result = case.stage(tmp_path).run(_input(*names), _ctx())
    finally:
        _still_loading.set()
    assert result.method == "fuzzy"
    assert result == FuzzyMatchStage().run(_input(*names), _ctx())  # the whole run is fuzzy, never a mix
    (line,) = _warnings(caplog)
    assert line.startswith("match: fuzzy matching ran instead of the model: ")
    assert case.reason in line


def test_matcher_fuzzy_never_loads_the_model() -> None:
    loader = Loader(ConstScorer(1.0))
    settings = load_matcher_settings(env={"MATCHER": "fuzzy"})
    stage = ModelMatchStage(settings=settings, model=ModelHolder(settings, load=loader))
    result = stage.run(_input(*OPEN_PAIR, *CRITERION_1), _ctx())
    assert loader.calls == 0
    assert result.method == "fuzzy"
    ModelMatchStage(settings=settings).run(_input(*OPEN_PAIR), _ctx())
    assert model_module.shared_models() == {}  # not even a holder for it


def test_matcher_force_fail_falls_back_to_fuzzy(caplog: pytest.LogCaptureFixture) -> None:
    loader = Loader(ConstScorer(1.0))
    settings = load_matcher_settings(env={"MATCHER": "model", "MATCHER_FORCE_FAIL": "1"})
    stage = ModelMatchStage(settings=settings, model=ModelHolder(settings, load=loader))
    with caplog.at_level(logging.WARNING):
        result = stage.run(_input(*OPEN_PAIR), _ctx())
    assert result.method == "fuzzy"
    assert result == FuzzyMatchStage().run(_input(*OPEN_PAIR), _ctx())
    assert _warnings(caplog) == [
        "match: fuzzy matching ran instead of the model: MATCHER_FORCE_FAIL=1 made it fail on purpose"
    ]
    assert loader.calls == 0  # failing on purpose needs no model


def test_the_stage_fallback_runs_fuzzy_matching() -> None:
    stage = _stage(ConstScorer(1.0))
    out = stage.fallback(_input(*CRITERION_1), _ctx(), RuntimeError("made up"))
    assert out == FuzzyMatchStage().run(_input(*CRITERION_1), _ctx())
    assert stage.baseline.__class__ is FuzzyMatchStage


# --- settings: MATCHER and the default (DECIDED 4) ------------------------------------------------------------------


def test_the_default_is_fuzzy_and_an_unknown_matcher_value_uses_it(caplog: pytest.LogCaptureFixture) -> None:
    settings = load_matcher_settings(env={})
    assert (settings.default, settings.use, settings.force_fail) == ("fuzzy", "fuzzy", False)
    assert load_matcher_settings(env={"MATCHER": " Model "}).use == "model"
    with caplog.at_level(logging.WARNING):
        assert load_matcher_settings(env={"MATCHER": "onnx"}).use == "fuzzy"
    assert any("MATCHER" in line and "fuzzy" in line for line in _warnings(caplog))


def test_the_config_holds_s3_4s_model_revision_threshold_and_checksum() -> None:
    settings = load_matcher_settings(env={})
    onnx = json.loads((REPO_ROOT / "ml" / "results" / "matcher_v1_onnx.json").read_text(encoding="utf-8"))
    results = json.loads((REPO_ROOT / "ml" / "results" / "matcher_v1.json").read_text(encoding="utf-8"))
    assert settings.revision == onnx["huggingface_revision"] == PINNED
    assert settings.threshold == results["threshold"]["value"] == THRESHOLD
    assert settings.repo == "Om-Pandya/shopsense-matcher"
    assert settings.model_sha256 in (REPO_ROOT / "ml" / "MODELS.md").read_text(encoding="utf-8")
    assert settings.pinned


def test_a_config_that_cannot_be_read_never_uses_the_model(tmp_path: Path) -> None:
    broken = tmp_path / "matcher.yaml"
    broken.write_text("revision: [not, a, string\n", encoding="utf-8")
    settings = load_matcher_settings(broken, env={"MATCHER": "model"})
    assert not settings.pinned
    assert ModelMatchStage(settings=settings).run(_input(*OPEN_PAIR), _ctx()).method == "fuzzy"


# --- loading the model ------------------------------------------------------------------------------------------------


@dataclass
class _Input:
    name: str


@dataclass
class _Encoding:
    ids: list[int]
    attention_mask: list[int]
    type_ids: list[int]


class FakeTokenizer:
    def encode_batch(self, pairs: list[tuple[str, str]]) -> list[_Encoding]:
        return [_Encoding([len(a), len(b)], [1, 1], [0, 1]) for a, b in pairs]


class FakeSession:
    """Declares two of the three inputs, as some exports do; answers a logit of (first id - 10)."""

    def __init__(self) -> None:
        self.feeds: list[dict[str, Any]] = []

    def get_inputs(self) -> list[_Input]:
        return [_Input("input_ids"), _Input("attention_mask")]

    def run(self, outputs: list[str], feed: dict[str, Any]) -> list[Any]:
        import numpy as np

        assert outputs == ["logits"]
        self.feeds.append(feed)
        return [np.array([[float(row[0]) - 10.0] for row in feed["input_ids"]], dtype=np.float32)]


def test_the_onnx_scorer_batches_and_returns_sigmoid_probabilities() -> None:
    session = FakeSession()
    scorer = OnnxPairScorer(session, FakeTokenizer(), threshold=THRESHOLD, batch_size=2)
    pairs = [("x" * n, "y") for n in (10, 12, 8, 30, 10)]
    scores = scorer.score(pairs)
    assert [len(f["input_ids"]) for f in session.feeds] == [2, 2, 1]
    assert all(set(f) == {"input_ids", "attention_mask"} for f in session.feeds)  # only what the model declares
    assert all(f["input_ids"].dtype.name == "int64" for f in session.feeds)
    expected = [1 / (1 + 2.718281828459045 ** -(n - 10)) for n in (10, 12, 8, 30, 10)]
    assert scores == pytest.approx(expected, abs=1e-6)
    assert scores[0] == pytest.approx(0.5) and all(0.0 <= s <= 1.0 for s in scores)
    assert scorer.score([]) == []


def test_a_cached_model_loads_without_the_network_at_the_pinned_revision_and_no_token(tmp_path: Path) -> None:
    asked: list[dict[str, Any]] = []
    files = _files(tmp_path)

    def cached(**kwargs: Any) -> str:
        asked.append(kwargs)
        return files(**kwargs)

    opened: list[str] = []
    settings = _settings(model_sha256=_sha())
    scorer = load_model_scorer(
        settings,
        tmp_path,
        download=cached,
        open_session=lambda path: opened.append(Path(path).name) or FakeSession(),
        open_tokenizer=lambda path, max_length: opened.append(Path(path).name) or FakeTokenizer(),
    )
    assert isinstance(scorer, OnnxPairScorer) and scorer.threshold == THRESHOLD
    assert [a["filename"] for a in asked] == ["model.onnx", "tokenizer.json"]
    assert all(a["local_files_only"] is True for a in asked)  # found in the cache: never the network
    assert all(a["revision"] == PINNED and a["token"] is False for a in asked)
    assert all(a["repo_id"] == "made-up/matcher" and a["cache_dir"] == str(tmp_path) for a in asked)
    assert opened == ["model.onnx", "tokenizer.json"]


def test_a_model_not_in_the_cache_is_downloaded_once_at_the_pinned_revision(tmp_path: Path) -> None:
    asked: list[bool] = []
    files = _files(tmp_path)

    def download(**kwargs: Any) -> str:
        asked.append(kwargs["local_files_only"])
        if kwargs["local_files_only"]:
            raise FileNotFoundError("not cached")
        assert kwargs["revision"] == PINNED and kwargs["token"] is False
        return files(**kwargs)

    load_model_scorer(
        _settings(model_sha256=_sha()),
        tmp_path,
        download=download,
        open_session=lambda path: FakeSession(),
        open_tokenizer=lambda path, max_length: FakeTokenizer(),
    )
    assert asked == [True, False, True, False]  # each file: the cache, then the download


def test_the_model_loads_once_per_process_and_in_the_background(monkeypatch: pytest.MonkeyPatch) -> None:
    release = threading.Event()
    calls: list[str] = []

    def slow_load(settings: MatcherSettings) -> PairScorer:
        calls.append(threading.current_thread().name)
        release.wait(5)
        return ConstScorer(1.0)

    monkeypatch.setattr(model_module, "load_model_scorer", slow_load)
    settings = _settings()
    started = time.monotonic()
    first, second = ModelMatchStage(settings=settings), ModelMatchStage(settings=settings)
    assert time.monotonic() - started < 1.0  # building the stage never waits for the model
    assert first.model is second.model is shared_model(settings)
    release.set()
    assert first.run(_input(*OPEN_PAIR), _ctx()).method == "model"
    assert second.run(_input(*OPEN_PAIR), _ctx()).method == "model"
    assert calls == ["matcher-load"]  # one load, in its own thread


def test_a_failed_load_is_tried_again_only_after_its_pause() -> None:
    now = [0.0]
    loader = Loader(error=ModelUnavailable("the download failed (ConnectionError)"))
    holder = ModelHolder(_settings(), load=loader, retry_after_s=600, clock=lambda: now[0])
    for _ in range(3):
        with pytest.raises(ModelUnavailable, match="download failed"):
            holder.get(timeout=5)
    assert loader.calls == 1
    now[0] = 601.0
    loader.error, loader.scorer = None, ConstScorer(1.0)
    assert holder.get(timeout=5) is loader.scorer
    assert loader.calls == 2


def test_a_thread_that_cannot_start_is_a_failed_load_not_a_failed_search(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_thread(self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    loader = Loader(ConstScorer(1.0))
    settings = _settings()
    holder = ModelHolder(settings, load=loader)
    monkeypatch.setattr(threading.Thread, "start", no_thread)
    stage = ModelMatchStage(settings=settings, model=holder)  # building the stage still works
    started = time.monotonic()
    with pytest.raises(ModelUnavailable, match=r"could not be loaded \(RuntimeError\)"):
        holder.get(timeout=5)
    assert time.monotonic() - started < 1.0  # told at once, never left "loading"
    assert stage.run(_input(*OPEN_PAIR), _ctx()).method == "fuzzy"
    assert loader.calls == 0


def test_a_run_waits_for_another_searchs_batch_only_within_its_share(caplog: pytest.LogCaptureFixture) -> None:
    scorer = OnnxPairScorer(FakeSession(), FakeTokenizer(), threshold=THRESHOLD)
    settings = _settings(time_share_s=0.2)
    stage = ModelMatchStage(settings=settings, model=_loaded(scorer, settings))
    with pytest.raises(ShareSpent), scorer._lock:  # another search's batch is running
        scorer.score_until([("a", "b")], 0.05)
    scorer._lock.acquire()
    try:
        started = time.monotonic()
        with caplog.at_level(logging.WARNING):
            result = stage.run(_input(*OPEN_PAIR), _ctx())
        waited = time.monotonic() - started
    finally:
        scorer._lock.release()
    assert result.method == "fuzzy"
    assert waited < 1.0
    (line,) = _warnings(caplog)
    assert "share" in line


def test_the_share_always_leaves_match_2_seconds_for_fuzzy(caplog: pytest.LogCaptureFixture) -> None:
    clock = [0.0]
    settings = _settings(batch_size=1, time_share_s=5.0)  # a share as long as the stage itself
    stage = ModelMatchStage(settings=settings, model=_loaded(_Slow(clock), settings), clock=lambda: clock[0])
    with caplog.at_level(logging.WARNING):
        assert stage.run(_input(*OPEN_PAIR, "Kindle Paperwhyte"), _ctx()).method == "fuzzy"
    (line,) = _warnings(caplog)
    assert "longer than its 3 s share" in line


# --- speed (criterion 5's DECIDED 5) ----------------------------------------------------------------------------------


@pytest.fixture
def worst_case_names() -> Iterator[list[str]]:
    """S3-3's worst case for blocking: 1,000 names sharing one word, none with a code: every pair needs the scorer."""
    rng = random.Random(11)
    syllables = ["ka", "lo", "mi", "ra", "su", "te", "vo", "ze", "ni", "pa"]
    yield sorted({"Nova " + "".join(rng.choice(syllables) for _ in range(4)) for _ in range(1400)})[:1000]


class _AsSlowAsTheModel:
    """About what S3-4 measured on CPU: 1.7 seconds for 1,000 pairs."""

    threshold = THRESHOLD

    def score(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        time.sleep(0.0017 * len(pairs))
        return [0.0] * len(pairs)


@pytest.mark.slow
def test_the_time_share_keeps_1000_mentions_under_5_seconds(
    worst_case_names: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    stage = _stage(_AsSlowAsTheModel(), time_share_s=load_matcher_settings(env={}).time_share_s)
    started = time.perf_counter()
    with caplog.at_level(logging.WARNING):
        result = stage.run(_input(*worst_case_names), _ctx())
    seconds = time.perf_counter() - started
    assert seconds < 5.0
    assert result.method == "fuzzy"
    assert result == FuzzyMatchStage().run(_input(*worst_case_names), _ctx())
    (line,) = _warnings(caplog)
    assert "share" in line


def test_the_share_never_takes_the_time_the_search_has_left_for_fuzzy() -> None:
    clock = [0.0]
    settings = _settings(batch_size=1)
    stage = ModelMatchStage(settings=settings, model=_loaded(_Slow(clock), settings), clock=lambda: clock[0])
    nearly_out = StageContext(search_id=SEARCH_ID, time_limit_s=1.0)  # 1 s left: less than fuzzy's 2 s room
    assert stage.run(_input(*OPEN_PAIR, "Kindle Paperwhyte"), nearly_out).method == "fuzzy"


# --- the pipeline ----------------------------------------------------------------------------------------------------


def test_the_pipeline_matches_with_the_model_stage() -> None:
    stage = default_search_stages().match
    assert isinstance(stage, ModelMatchStage)
    assert isinstance(stage.baseline, FuzzyMatchStage)
    assert stage.settings.use == "fuzzy"  # the default (DECIDED 4): no model is loaded in a plain run
    assert model_module.shared_models() == {}
