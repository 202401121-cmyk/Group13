"""US-20, US-22 and D-91, the stage: code writes every paragraph's personal part; the top 3 get evidence sentences from
one public call each, given only the product's name and its public quotes; each sentence is checked against its quotes
(the model with NLI=on, else the 6-word rule); a failed one is dropped, and an explanation always appears."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

import pytest
from pydantic import BaseModel

from app.contracts import (
    Claim,
    DataClass,
    Difficulty,
    ExplainInput,
    LLMTask,
    LLMUnavailable,
    StageContext,
    StageName,
)
from app.explain import ExplainStage, TemplateExplainStage
from app.explain.facts import facts_for
from app.explain.nli import NliHolder, NliUnavailable
from app.explain.nli_settings import NliSettings, ReferencePair, load_nli_settings
from app.explain.parsing import RawEvidence, RawEvidenceSentence
from app.explain.templates import COMMUNITY_ONLY, template
from app.llm.data_block import data_blocks
from app.llm.usage import time_already_spent

from .build import KESTREL, NOVA, ORION, claim, fourth, kestrel, nova, orion

T = TypeVar("T", bound=BaseModel)
COMMIT = "a150876415327c80daeff35ca6f68f5ed8cf5c24"
REDDIT = "https://www.reddit.com/r/laptops/comments/abc123/"
POST = "honestly the fan screams like a jet engine whenever gradle starts building"
PRIORITIES = ["battery_life", "cpu_performance", "keyboard_comfort"]
OFF = NliSettings(enabled=False)  # NLI=off: the 6-word rule only


def _quote(name: str, key: str) -> str:
    return f"The {key.replace('_', ' ')} of the {name} held up well in every one of our tests."


def _claims(*, reddit: bool = False) -> list[Claim]:
    keys = ("battery_life", "cpu_performance", "keyboard_comfort", "weight")
    found = [claim(name, key, quote=_quote(name, key)) for name in (NOVA, KESTREL, ORION) for key in keys]
    return [*found, claim(NOVA, "battery_life", 7, quote=POST, url=REDDIT)] if reddit else found


def _input(*products: Any, claims: list[Claim] | None = None) -> ExplainInput:
    shown = list(products) or [nova(), kestrel(), orion(), fourth()]
    return ExplainInput(ranked=shown, claims=_claims() if claims is None else claims, user_priorities=PRIORITIES)


def _ctx() -> StageContext:
    return StageContext(search_id="srch_explain01")


@dataclass
class Call:
    task: LLMTask
    system: str
    user: str
    data_class: DataClass
    difficulty: Difficulty
    spent: float


Answer = RawEvidence | Exception | Callable[[dict[str, Any]], RawEvidence]


class ByProduct:
    """An LLMClient answering each evidence call by the product it names. Records every call; thread-safe."""

    def __init__(self, answers: Mapping[str, Answer] | None = None, default: Answer | None = None) -> None:
        self.answers = dict(answers or {})
        self.default = default if default is not None else quoting
        self.calls: list[Call] = []
        self._lock = threading.Lock()

    def complete(
        self,
        task: LLMTask,
        system: str,
        user: str,
        schema: type[T],
        *,
        data_class: DataClass,
        difficulty: Difficulty,
        search_id: str | None = None,
        max_output_tokens: int = 1024,
    ) -> T:
        with self._lock:
            self.calls.append(Call(task, system, user, data_class, difficulty, time_already_spent()))
        ((_, block),) = data_blocks(user)
        payload = json.loads(block)
        answer = self.answers.get(payload["product"], self.default)
        if isinstance(answer, Exception):
            raise answer
        made = answer(payload) if callable(answer) else answer
        assert isinstance(made, schema)
        return made

    def payloads(self) -> dict[str, dict[str, Any]]:
        return {
            json.loads(data_blocks(c.user)[0][1])["product"]: json.loads(data_blocks(c.user)[0][1]) for c in self.calls
        }


def quoting(payload: dict[str, Any]) -> RawEvidence:
    """One sentence per topic that repeats its first quote word for word: what the 6-word rule keeps."""
    return RawEvidence(
        sentences=[
            RawEvidenceSentence(topic=t["topic"], text=t["quotes"][0]["quote"], cites=[t["quotes"][0]["ref"]])
            for t in payload["topics"]
        ]
    )


def _evidence(out: Any) -> list[str]:
    """The sentences of a paragraph that aren't code's own."""
    product = next(r for r in (nova(), kestrel(), orion(), fourth()) if r.product_id == out.product_id)
    code = {s.text for s in template(facts_for(product, _claims()), PRIORITIES).sentences}
    return [s.text for s in out.sentences if s.text not in code]


# --- with NLI off: the 6-word rule ------------------------------------------------------------------------------------


def test_the_top_3_each_get_one_public_evidence_call_and_the_rest_the_template() -> None:
    llm = ByProduct()
    out = ExplainStage(llm, settings=OFF).run(_input(), _ctx())
    assert len(llm.calls) == 3
    assert {c.task for c in llm.calls} == {LLMTask.WRITE_EVIDENCE}
    assert {c.data_class for c in llm.calls} == {DataClass.PUBLIC}
    assert {c.difficulty for c in llm.calls} == {Difficulty.MEDIUM}
    assert set(llm.payloads()) == {NOVA, KESTREL, ORION}
    assert [e.method for e in out.explanations] == ["llm_unchecked", "llm_unchecked", "llm_unchecked", "template"]
    assert out.explanations[3] == template(facts_for(fourth(), _claims()), PRIORITIES)


def test_a_kept_evidence_sentence_sits_with_codes_own_sentences() -> None:
    out = ExplainStage(ByProduct(), settings=OFF).run(_input(), _ctx())
    nova_out = out.explanations[0]
    assert _evidence(nova_out)[0] == _quote(NOVA, "battery_life")
    code = template(facts_for(nova(), _claims()), PRIORITIES)
    assert [s.text for s in nova_out.sentences if s.text in {c.text for c in code.sentences}] == [
        s.text for s in code.sentences
    ]  # every one of code's sentences, in order
    assert all(s.claim_ids for s in nova_out.sentences)
    assert all(s.supported is None for s in nova_out.sentences)  # nothing was read by the model


def test_with_nli_off_a_sentence_that_doesnt_quote_6_words_is_dropped() -> None:
    def loose(payload: dict[str, Any]) -> RawEvidence:
        first = payload["topics"][0]["quotes"][0]
        return RawEvidence(
            sentences=[RawEvidenceSentence(text="Reviewers liked it a lot overall.", cites=[first["ref"]])]
        )

    out = ExplainStage(ByProduct(default=loose), settings=OFF).run(_input(), _ctx())
    assert [e.method for e in out.explanations] == ["template"] * 4  # every evidence sentence failed: the template


# --- with the model ---------------------------------------------------------------------------------------------------


class Scorer:
    """Every pair gets `value`; each batch can wait first. Records the pairs."""

    def __init__(self, value: float = 0.99, waits: list[float] | None = None) -> None:
        self.value = value
        self.waits = list(waits or [])
        self.pairs: list[tuple[str, str]] = []
        self.batches = 0

    def premises(self, quote: str) -> list[str]:
        return [quote]

    def entailment(self, pairs: Any) -> list[float]:
        self.batches += 1
        if self.waits:
            time.sleep(self.waits.pop(0))
        self.pairs += list(pairs)
        return [self.value] * len(pairs)


def _settings(**changes: Any) -> NliSettings:
    values: dict[str, Any] = {
        "repo": "cross-encoder/nli-deberta-v3-xsmall",
        "revision": COMMIT,
        "model_file": "onnx/model_O4.onnx",
        "model_sha256": "0" * 64,
        "tokenizer_sha256": "0" * 64,
        "threshold": 0.9608,
        "reference": [ReferencePair(premise="p", hypothesis="h", entailment=0.5)],
        "enabled": True,  # NLI=on
    }
    return NliSettings.model_validate({**values, **changes})


def _holder(scorer: Any = None, error: str | None = None) -> NliHolder:
    def load(_: NliSettings) -> Any:
        if error is not None:
            raise NliUnavailable(error)
        return scorer

    return NliHolder(_settings(), load=load)


def test_with_the_model_kept_sentences_are_checked_one_batch_per_product() -> None:
    scorer = Scorer(0.99)
    out = ExplainStage(ByProduct(), settings=_settings(), nli=_holder(scorer)).run(_input(), _ctx())
    assert [e.method for e in out.explanations[:3]] == ["llm_checked"] * 3
    assert scorer.batches == 3
    supported = [s.supported for s in out.explanations[0].sentences]
    # 4 criteria answered, but code's 5 sentences leave room for 3 within the contract's 8; code's carry no verdict
    assert supported.count(True) == 3
    assert supported.count(False) == 0


def test_with_the_model_every_evidence_sentence_failing_gives_the_template() -> None:
    out = ExplainStage(ByProduct(), settings=_settings(), nli=_holder(Scorer(0.2))).run(_input(), _ctx())
    assert [e.method for e in out.explanations] == ["template"] * 4
    assert out.explanations[0] == template(facts_for(nova(), _claims()), PRIORITIES)


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
    with caplog.at_level(logging.INFO, logger="app.explain.stage"):
        out = ExplainStage(ByProduct(), **make()).run(_input(), _ctx())
    assert [e.method for e in out.explanations[:3]] == ["llm_unchecked"] * 3
    assert all(s.supported is None for e in out.explanations for s in e.sentences)
    lines = [r.getMessage() for r in caplog.records if "fell back" in r.getMessage()]
    assert len(lines) == 1 and reason in lines[0]


def test_the_model_starts_loading_when_the_stage_is_built_only_when_on() -> None:
    started: list[int] = []

    class _Watch(NliHolder):
        def start(self) -> None:
            started.append(1)

    ExplainStage(ByProduct(), settings=_settings(), nli=_Watch(_settings()))
    ExplainStage(ByProduct(), settings=_settings(enabled=False), nli=_Watch(_settings()))
    assert started == [1]


# --- the edge cases ---------------------------------------------------------------------------------------------------


def test_a_product_with_no_public_quote_gets_no_call_and_community_evidence_is_said() -> None:
    claims = [c for c in _claims() if c.product_mention != NOVA]
    claims += [claim(NOVA, key, quote=POST, url=REDDIT) for key in ("battery_life", "cpu_performance")]
    llm = ByProduct()
    out = ExplainStage(llm, settings=OFF).run(_input(claims=claims), _ctx())
    assert NOVA not in llm.payloads()
    assert out.explanations[0] == template(facts_for(nova(), claims), PRIORITIES, community_only=True)
    assert out.explanations[0].sentences[-1].text == COMMUNITY_ONLY
    assert all(POST not in c.user for c in llm.calls)


def test_with_the_public_chain_all_down_every_product_gets_codes_sentences(caplog: pytest.LogCaptureFixture) -> None:
    llm = ByProduct(default=LLMUnavailable("every public provider is down"))
    with caplog.at_level(logging.INFO, logger="app.explain.stage"):
        out = ExplainStage(llm, settings=OFF).run(_input(), _ctx())
    assert out == TemplateExplainStage().run(_input(), _ctx())
    assert sum("no public provider answered" in r.getMessage() for r in caplog.records) == 3


def test_one_product_down_leaves_the_others_their_evidence() -> None:
    llm = ByProduct({KESTREL: LLMUnavailable("down")})
    out = ExplainStage(llm, settings=OFF).run(_input(), _ctx())
    assert [e.method for e in out.explanations] == ["llm_unchecked", "template", "llm_unchecked", "template"]


def test_an_evidence_sentence_citing_another_products_claim_is_dropped(caplog: pytest.LogCaptureFixture) -> None:
    kestrel_claim = next(c for c in _claims() if c.product_mention == KESTREL)

    def foreign(payload: dict[str, Any]) -> RawEvidence:
        return RawEvidence(sentences=[RawEvidenceSentence(text=kestrel_claim.quote, cites=[kestrel_claim.id, "q99"])])

    with caplog.at_level(logging.INFO, logger="app.explain.stage"):
        out = ExplainStage(ByProduct({NOVA: foreign}), settings=OFF).run(_input(), _ctx())
    assert out.explanations[0].method == "template"
    assert "it cites no quote, or one it wasn't given" in caplog.text


def test_an_evidence_sentence_with_a_code_number_that_isnt_codes_is_dropped() -> None:
    def wrong_score(payload: dict[str, Any]) -> RawEvidence:
        first = payload["topics"][0]["quotes"][0]
        text = first["quote"].rstrip(".") + ", with a score of 6 out of 10."
        return RawEvidence(sentences=[RawEvidenceSentence(text=text, cites=[first["ref"]])])

    out = ExplainStage(ByProduct({NOVA: wrong_score}), settings=OFF).run(_input(), _ctx())
    assert out.explanations[0].method == "template"  # nova's battery scores 8, not 6


def test_no_shoppers_priority_weight_or_score_is_in_any_evidence_prompt() -> None:
    llm = ByProduct()
    ExplainStage(llm, settings=OFF).run(_input(), _ctx())
    for call in llm.calls:
        text = call.system + call.user
        assert all(key not in text for key in PRIORITIES)
        assert all(word not in text for word in ("priorit", 'weight":', "points", "out of", "score"))
        assert all(str(f.score) not in text for f in facts_for(nova(), _claims()).top if f.score >= 1)


def test_a_reddit_quote_is_never_sent_and_never_shown() -> None:
    llm = ByProduct()
    out = ExplainStage(llm, settings=OFF).run(_input(claims=_claims(reddit=True)), _ctx())
    assert all(POST not in c.user for c in llm.calls)
    assert all(POST not in s.text for e in out.explanations for s in e.sentences)


# --- time ------------------------------------------------------------------------------------------------------------


def test_the_calls_leave_the_check_its_time_and_a_margin() -> None:
    off, on = ByProduct(), ByProduct()
    ExplainStage(off, settings=OFF, clock=lambda: 0.0).run(_input(), _ctx())
    ExplainStage(on, settings=_settings(), nli=_holder(Scorer()), clock=lambda: 0.0).run(_input(), _ctx())
    assert {round(c.spent, 6) for c in off.calls} == {1.0}  # 15 s less the 1 s margin
    assert {round(c.spent, 6) for c in on.calls} == {6.0}  # and the check's 5 s


def test_with_too_little_of_the_search_left_no_call_is_made() -> None:
    llm = ByProduct()
    out = ExplainStage(llm, settings=OFF).run(_input(), StageContext(search_id="srch_explain02", time_limit_s=0.5))
    assert llm.calls == []
    assert out == TemplateExplainStage().run(_input(), _ctx())


def test_a_call_that_runs_past_its_time_leaves_that_product_codes_sentences() -> None:
    def slow(payload: dict[str, Any]) -> RawEvidence:
        time.sleep(2.5)
        return quoting(payload)

    started = time.monotonic()
    out = ExplainStage(ByProduct({ORION: slow}), settings=OFF).run(
        _input(), StageContext(search_id="srch_explain03", time_limit_s=2.0)
    )
    assert time.monotonic() - started < 2.0  # the stage didn't wait for it
    assert [e.method for e in out.explanations] == ["llm_unchecked", "llm_unchecked", "template", "template"]


def test_the_fallback_is_the_template() -> None:
    stage = ExplainStage(ByProduct(), settings=OFF)
    assert stage.fallback(_input(), _ctx(), ValueError("x")) == TemplateExplainStage().run(_input(), _ctx())
    assert stage.name is StageName.EXPLAIN and not stage.is_placeholder


def test_nli_is_read_from_the_environment_on_by_default() -> None:
    assert load_nli_settings(env={}).enabled
    assert not load_nli_settings(env={"NLI": "off"}).enabled
