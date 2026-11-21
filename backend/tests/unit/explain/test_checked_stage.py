"""US-22's stage (S5-4): ExplainStage's paragraphs, each written sentence checked against its quotes by a local model.
Checked paragraphs are llm_checked; with the model missing, failing, switched off, untuned or out of time, only the
sentences that quote their evidence stay (llm_unchecked) and one log line says why; the check never runs past
explain's 15 s; and a Reddit quote is read on our server only, never sent or saved."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from app.contracts import Claim, ExplainInput, ExplainResult, StageContext
from app.explain import CheckedExplainStage, ExplainStage
from app.explain.check import quoted_only
from app.explain.facts import facts_for
from app.explain.nli import NliHolder, NliUnavailable
from app.explain.nli_settings import NliSettings, ReferencePair, load_nli_settings

from .build import explain_input
from .test_stage import Scripted, _answer, _good

COMMIT = "a150876415327c80daeff35ca6f68f5ed8cf5c24"


def _ctx() -> StageContext:
    """A search that has just started: the check stops at the time the search has left, so never a shared one."""
    return StageContext(search_id="srch_explain01")


class Scorer:
    """Every pair gets `value`; each batch can wait first. Records the pairs."""

    def __init__(self, value: float = 0.9, waits: Sequence[float] = ()) -> None:
        self.value = value
        self.waits = list(waits)
        self.pairs: list[tuple[str, str]] = []
        self.batches = 0

    def premises(self, quote: str) -> list[str]:
        return [quote]

    def entailment(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.batches += 1
        if self.waits:
            time.sleep(self.waits.pop(0))
        self.pairs += list(pairs)
        return [self.value] * len(pairs)


def _settings(**changes: Any) -> NliSettings:
    values: dict[str, Any] = {
        "repo": "cross-encoder/nli-deberta-v3-xsmall",
        "revision": COMMIT,
        "model_file": "onnx/model_quint8_avx2.onnx",
        "model_sha256": "0" * 64,
        "tokenizer_sha256": "0" * 64,
        "threshold": 0.5,
        "reference": [ReferencePair(premise="p", hypothesis="h", entailment=0.5)],
        "enabled": True,  # NLI=on: the check is off by default (P-48)
    }
    return NliSettings.model_validate({**values, **changes})


def _holder(scorer: Any = None, error: str | None = None) -> NliHolder:
    def load(_: NliSettings) -> Any:
        if error is not None:
            raise NliUnavailable(error)
        return scorer

    return NliHolder(_settings(), load=load)


def _written(inp: ExplainInput) -> Scripted:
    good = _good(inp)
    return Scripted([_answer(good["nova"], good["kestrel"], good["orion"])])


def _stage(inp: ExplainInput, **kwargs: Any) -> CheckedExplainStage:
    return CheckedExplainStage(_written(inp), **kwargs)


def _unchecked(inp: ExplainInput) -> ExplainResult:
    return ExplainStage(_written(inp)).run(inp, _ctx())


def _fallback(inp: ExplainInput) -> list[Any]:
    """What DECIDED 4 gives: each written paragraph keeps only its sentences that quote their evidence."""
    claims = {c.id: c for c in inp.claims}
    out = []
    for e in _unchecked(inp).explanations:
        facts = next(facts_for(r, inp.claims) for r in inp.ranked if r.product_id == e.product_id)
        out.append(quoted_only(e, facts, claims, 6) if e.method == "llm_unchecked" else e)
    return out


def test_with_the_model_every_written_paragraph_is_checked() -> None:
    inp = explain_input()
    scorer = Scorer(0.9)
    out = _stage(inp, settings=_settings(), nli=_holder(scorer)).run(inp, _ctx())
    assert [e.method for e in out.explanations] == ["llm_checked"] * 3
    written = _unchecked(inp)
    for checked, before in zip(out.explanations, written.explanations, strict=True):
        assert [s.text for s in checked.sentences] == [s.text for s in before.sentences]
        own = {s.text for s in before.sentences if s.text.startswith("We could not check")}
        assert all(s.supported is (None if s.text in own else True) for s in checked.sentences)
    assert scorer.batches == 3  # one batch per product
    assert ExplainResult.model_validate_json(out.model_dump_json()) == out


def test_templates_are_codes_own_words_and_are_never_checked() -> None:
    inp = explain_input()
    scorer = Scorer(0.9)
    out = CheckedExplainStage(Scripted([_answer()]), settings=_settings(), nli=_holder(scorer)).run(inp, _ctx())
    assert [e.method for e in out.explanations] == ["template"] * 3
    assert scorer.batches == 0


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (
            lambda: {"settings": _settings(), "nli": _holder(error="the download failed (ConnectionError)")},
            "the download failed (ConnectionError)",
        ),
        (
            lambda: {"settings": _settings(threshold=None), "nli": _holder(Scorer())},
            "no revision, sha256 and threshold",
        ),
        (
            lambda: {"settings": _settings(time_limit_s=0.2), "nli": _holder(Scorer(waits=[0.6]))},
            "the check took longer than",
        ),
        (lambda: {"settings": _settings(revision="main")}, "no revision, sha256 and threshold"),
    ],
    ids=["model failing", "not tuned", "past its time", "not pinned"],
)
def test_without_the_model_only_sentences_that_quote_their_evidence_stay_with_one_log_line(
    make: Callable[[], dict[str, Any]], reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    inp = explain_input()
    kwargs = make()
    with caplog.at_level(logging.INFO, logger="app.explain.checked"):
        out = _stage(inp, **kwargs).run(inp, _ctx())
    assert out.explanations == _fallback(inp)
    assert all(e.method in ("llm_unchecked", "template") for e in out.explanations)
    assert all(s.supported is None for e in out.explanations for s in e.sentences)
    lines = [r.getMessage() for r in caplog.records if r.name == "app.explain.checked"]
    assert len(lines) == 1 and reason in lines[0]


def test_off_by_default_explain_is_todays_unchanged_and_no_model_is_loaded(caplog: pytest.LogCaptureFixture) -> None:
    """P-48: with NLI unset or off, a search explains exactly as before US-22: nothing checked, nothing removed."""
    inp = explain_input()
    settings = load_nli_settings(env={})
    assert not settings.enabled
    scorer = Scorer(0.0)
    with caplog.at_level(logging.INFO, logger="app.explain.checked"):
        out = _stage(inp, settings=settings, nli=_holder(scorer)).run(inp, _ctx())
    assert out == _unchecked(inp)
    assert any(e.method == "llm_unchecked" for e in out.explanations)  # the written paragraphs, as written
    assert scorer.batches == 0
    assert not [r for r in caplog.records if r.name == "app.explain.checked"]


def test_nli_on_runs_the_check_with_the_repository_config() -> None:
    inp = explain_input()
    scorer = Scorer(0.99)
    settings = load_nli_settings(env={"NLI": "on"})
    out = _stage(inp, settings=settings, nli=_holder(scorer)).run(inp, _ctx())
    assert scorer.batches == len([e for e in _unchecked(inp).explanations if e.method == "llm_unchecked"])
    assert all(e.method in ("llm_checked", "template") for e in out.explanations)


def test_the_model_starts_loading_when_the_stage_is_built_only_when_on() -> None:
    started: list[int] = []

    class _Watch(NliHolder):
        def start(self) -> None:
            started.append(1)

    CheckedExplainStage(Scripted([]), settings=_settings(), nli=_Watch(_settings()))
    CheckedExplainStage(Scripted([]), settings=_settings(enabled=False), nli=_Watch(_settings()))
    assert started == [1]


def test_a_product_checked_before_the_time_ran_out_keeps_its_check() -> None:
    inp = explain_input()
    scorer = Scorer(0.9, waits=[0.0, 2.0])
    out = _stage(inp, settings=_settings(time_limit_s=1.0), nli=_holder(scorer)).run(inp, _ctx())
    assert out.explanations[0].method == "llm_checked"
    assert out.explanations[1:] == _fallback(inp)[1:]


def test_the_check_never_runs_past_explains_own_15_seconds() -> None:
    """The LLM took 14.5 of explain's 15 s: no time is left for the check, so the fallback is used at once."""
    inp = explain_input()
    now = [0.0]
    good = _good(inp)

    class _Slow(Scripted):
        def complete(self, *args: Any, **kwargs: Any) -> Any:
            now[0] = 14.5
            return super().complete(*args, **kwargs)

    scorer = Scorer(0.9)
    stage = CheckedExplainStage(
        _Slow([_answer(good["nova"], good["kestrel"], good["orion"])]),
        settings=_settings(),
        nli=_holder(scorer),
        clock=lambda: now[0],
    )
    assert stage.run(inp, _ctx()).explanations == _fallback(inp)
    assert scorer.batches == 0


def test_the_check_never_runs_past_the_searchs_own_time_left(caplog: pytest.LogCaptureFixture) -> None:
    """Explain started with half a second of the search's 150 s left: the runner stops waiting then, so no check."""
    inp = explain_input()
    scorer = Scorer(0.9)
    short = StageContext(search_id="srch_explain05", time_limit_s=0.5)
    with caplog.at_level(logging.INFO, logger="app.explain.checked"):
        out = _stage(inp, settings=_settings(), nli=_holder(scorer)).run(inp, short)
    assert out.explanations == _fallback(inp)
    assert scorer.batches == 0
    assert "the citation check fell back" in caplog.text


def test_a_reddit_quote_is_read_on_our_server_only_never_sent_or_saved(caplog: pytest.LogCaptureFixture) -> None:
    secret = "Secret thread words about how long its battery really lasts."
    reddit_url = "https://www.reddit.com/r/laptops/comments/abc123/made_up_thread/"
    base = explain_input()
    claims = [Claim.model_validate({**c.model_dump(), "source_url": reddit_url, "quote": secret}) for c in base.claims]
    inp = explain_input(*base.ranked, claims=claims)
    llm = _written(inp)
    scorer = Scorer(0.9)
    with caplog.at_level(logging.DEBUG):
        out = CheckedExplainStage(llm, settings=_settings(), nli=_holder(scorer)).run(inp, _ctx())
    assert any(premise == secret for premise, _ in scorer.pairs)  # read here, by the local model
    assert llm.calls and all(secret not in call.system + call.user for call in llm.calls)  # never sent
    assert secret not in json.dumps(out.model_dump(mode="json"))  # never in what is shown or saved
    assert secret not in caplog.text


def test_the_fallback_is_explain_stages_the_template() -> None:
    inp = explain_input()
    stage = _stage(inp, settings=_settings(), nli=_holder(Scorer()))
    assert stage.fallback(inp, StageContext(search_id="srch_explain03"), ValueError("x")) == ExplainStage(
        _written(inp)
    ).fallback(inp, _ctx(), ValueError("x"))
