"""US-22's check of each written sentence against its quotes (S5-4 DECIDED 2 and 4), with a fake scorer: a supported
sentence passes, one naming a number no quote or score holds fails, a long quote is read in chunks and the best counts,
a failed sentence becomes its point's template sentence or goes, a paragraph left with nothing becomes the template,
the fallback keeps only sentences that quote 6 words in a row, and the validator never sees an unsupported sentence
under llm_checked."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.contracts import Claim, Explanation, ExplanationSentence
from app.explain.check import TRADE_OFF, check_explanation, items, quoted_only, quotes_words, rebuild
from app.explain.facts import ProductFacts, facts_for
from app.explain.nli import NliUnavailable
from app.explain.templates import strength_sentence, template, trade_off_text, unverified_sentences

from .build import NOVA, claim, explain_input, nova

BATTERY = "The battery easily lasted a full working day of coding and browsing."
KEYS = "The keys are springy and quiet, and typing for hours never tired my hands."
SPEED = "It compiles a large project in under a minute, quicker than most laptops here."
WEIGHT = "At almost two kilos it is a heavy one to carry around all day."


class Scorer:
    """Entailment by a table of (premise, hypothesis) pairs; 0.1 for any other. Records every batch it is given."""

    def __init__(self, table: Mapping[tuple[str, str], float] | None = None, chunk: int = 0) -> None:
        self.table = dict(table or {})
        self.batches: list[list[tuple[str, str]]] = []
        self.chunk = chunk

    def premises(self, quote: str) -> list[str]:
        if not self.chunk or len(quote.split()) <= self.chunk:
            return [quote]
        words = quote.split()
        return [" ".join(words[i : i + self.chunk]) for i in range(0, len(words), self.chunk)]

    def entailment(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.batches.append(list(pairs))
        return [self.table.get(pair, 0.1) for pair in pairs]


def _setup() -> tuple[ProductFacts, dict[str, Claim]]:
    claims = [
        claim(NOVA, "battery_life", quote=BATTERY),
        claim(NOVA, "keyboard_comfort", quote=KEYS),
        claim(NOVA, "cpu_performance", quote=SPEED),
        claim(NOVA, "weight", quote=WEIGHT),
    ]
    inp = explain_input(nova(), claims=claims)
    facts = facts_for(inp.ranked[0], inp.claims)
    return facts, {c.id: c for c in claims}


def _ids(facts: ProductFacts, key: str) -> list[str]:
    return list(next(f for f in facts.facts() if f.key == key).claim_ids)


def _written(facts: ProductFacts, sentences: list[tuple[str, list[str]]], trade: str | None = None) -> Explanation:
    return Explanation(
        product_id=facts.product_id,
        sentences=[ExplanationSentence(text=t, claim_ids=c) for t, c in sentences],
        trade_off=trade,
        unverified=list(facts.unverified),
        method="llm_unchecked",
    )


S_BATTERY = "Its battery lasts a full working day, at 8 out of 10."
S_KEYS = "Its keyboard is comfortable for long typing, at 9 out of 10."
S_SPEED = "It is fast for coding work, at 7.5 out of 10."


def test_a_supported_sentence_passes_and_is_marked_supported() -> None:
    facts, claims = _setup()
    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life")), (S_KEYS, _ids(facts, "keyboard_comfort"))])
    scorer = Scorer({(BATTERY, S_BATTERY): 0.92, (KEYS, S_KEYS): 0.88})
    out = check_explanation(para, facts, claims, scorer, 0.5, 5.0)
    assert out.method == "llm_checked"
    assert [(s.text, s.supported) for s in out.sentences] == [(S_BATTERY, True), (S_KEYS, True)]
    assert len(scorer.batches) == 1  # every pair of the product in one batch


def test_a_sentence_below_the_threshold_becomes_its_points_template_sentence() -> None:
    facts, claims = _setup()
    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life")), (S_KEYS, _ids(facts, "keyboard_comfort"))])
    out = check_explanation(para, facts, claims, Scorer({(BATTERY, S_BATTERY): 0.92, (KEYS, S_KEYS): 0.2}), 0.5, 5.0)
    keyboard = next(f for f in facts.top if f.key == "keyboard_comfort")
    assert [s.text for s in out.sentences] == [S_BATTERY, strength_sentence(keyboard)]
    assert out.sentences[1].supported is None and out.sentences[1].claim_ids == list(keyboard.claim_ids)
    assert out.method == "llm_checked"


def test_a_sentence_with_a_number_no_quote_or_score_holds_fails_however_well_it_reads() -> None:
    facts, claims = _setup()
    invented = "Its battery lasts 12 hours, a full working day."
    para = _written(facts, [(S_KEYS, _ids(facts, "keyboard_comfort")), (invented, _ids(facts, "battery_life"))])
    out = check_explanation(para, facts, claims, Scorer({(KEYS, S_KEYS): 0.9, (BATTERY, invented): 0.99}), 0.5, 5.0)
    assert invented not in [s.text for s in out.sentences]


def test_a_number_its_quote_holds_is_allowed_and_the_same_number_without_it_is_not() -> None:
    facts, claims = _setup()
    with_number = "It built a large project in 40 seconds in one review."
    speed_id = _ids(facts, "cpu_performance")[0]
    para = _written(facts, [(with_number, [speed_id]), (S_KEYS, _ids(facts, "keyboard_comfort"))])
    in_quote = "It compiles a large project in 40 seconds, quicker than most laptops here."
    quoting = {**claims, speed_id: Claim.model_validate({**claims[speed_id].model_dump(), "quote": in_quote})}
    table = {(in_quote, with_number): 0.9, (SPEED, with_number): 0.9, (KEYS, S_KEYS): 0.9}
    out = check_explanation(para, facts, quoting, Scorer(table), 0.5, 5.0)
    assert [s.text for s in out.sentences] == [with_number, S_KEYS]
    out = check_explanation(para, facts, claims, Scorer(table), 0.5, 5.0)  # SPEED holds no 40, nor do the scores
    assert with_number not in [s.text for s in out.sentences]


def test_a_long_quote_is_read_in_chunks_and_the_best_chunk_counts() -> None:
    facts, claims = _setup()
    long_quote = " ".join(["Filler words about the box and the charger."] * 11) + " " + BATTERY
    battery_id = _ids(facts, "battery_life")[0]
    claims = {
        **claims,
        battery_id: Claim.model_validate({**claims[battery_id].model_dump(), "quote": long_quote}),
    }
    scorer = Scorer(chunk=20)
    pieces = scorer.premises(claims[battery_id].quote)
    assert len(pieces) > 1
    scorer.table = {(pieces[-1], S_BATTERY): 0.95}
    para = _written(facts, [(S_BATTERY, [battery_id])])
    out = check_explanation(para, facts, claims, scorer, 0.5, 5.0)
    assert [p for p, _ in scorer.batches[0]] == pieces  # every chunk read, in one batch
    assert out.sentences[0].supported is True


def test_every_sentence_failing_gives_the_full_template() -> None:
    facts, claims = _setup()
    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life")), (S_KEYS, _ids(facts, "keyboard_comfort"))])
    out = check_explanation(para, facts, claims, Scorer(), 0.5, 5.0)
    assert out == template(facts)


def test_codes_own_sentence_is_never_read_and_carries_no_verdict() -> None:
    facts, claims = _setup()
    own = unverified_sentences([facts.labels[k] for k in facts.unverified])[0]
    everything = list(facts.claim_ids())
    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life")), (own, everything)])
    scorer = Scorer({(BATTERY, S_BATTERY): 0.9})
    out = check_explanation(para, facts, claims, scorer, 0.5, 5.0)
    assert [(s.text, s.supported) for s in out.sentences] == [(S_BATTERY, True), (own, None)]
    assert all(hypothesis != own for _, hypothesis in scorer.batches[0])


def test_a_failed_sentence_naming_two_strengths_gets_each_points_template_sentence_back() -> None:
    """The LLM may join strengths into one sentence (prompts.py): a failed one must not lose a top-3 point."""
    facts, claims = _setup()
    both = "Its battery and keyboard score well, at 8 and 9 out of 10."
    cited = _ids(facts, "battery_life") + _ids(facts, "keyboard_comfort")
    para = _written(facts, [(both, cited), (S_SPEED, _ids(facts, "cpu_performance"))])
    out = check_explanation(para, facts, claims, Scorer({(SPEED, S_SPEED): 0.9}), 0.5, 5.0)
    joined = [f for f in facts.top if f.key in {"battery_life", "keyboard_comfort"}]
    assert [s.text for s in out.sentences] == [*(strength_sentence(f) for f in joined), S_SPEED]
    assert out.method == "llm_checked"


def test_replacements_never_take_a_paragraph_past_the_contracts_8_sentences() -> None:
    facts, _ = _setup()
    kept = [(f"Its weight is noted here, point {i}.", _ids(facts, "weight")) for i in range(7)]
    every = [i for f in facts.top for i in f.claim_ids]
    para = _written(facts, [*kept, ("Its battery, keyboard and speed are all good.", every)])
    out = rebuild(para, facts, {**{i: True for i in range(7)}, 7: False}, method="llm_checked", supported=True)
    assert len(out.sentences) == 8
    assert out.sentences[7].text == strength_sentence(facts.top[0])  # the biggest point first


def test_a_tokenizer_failure_is_the_models_failure_so_the_fallback_follows() -> None:
    facts, claims = _setup()

    class _Broken(Scorer):
        def premises(self, quote: str) -> list[str]:
            raise ValueError("a tokenizer error")

    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life"))])
    with pytest.raises(NliUnavailable, match="ValueError"):
        check_explanation(para, facts, claims, _Broken(), 0.5, 5.0)


def test_a_failed_sentence_whose_point_is_already_said_is_removed() -> None:
    facts, claims = _setup()
    again = "Its battery is one of its best points."
    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life")), (again, _ids(facts, "battery_life"))])
    out = check_explanation(para, facts, claims, Scorer({(BATTERY, S_BATTERY): 0.9}), 0.5, 5.0)
    assert [s.text for s in out.sentences] == [S_BATTERY]


def test_a_trade_off_its_quote_doesnt_support_becomes_the_templates() -> None:
    facts, claims = _setup()
    assert facts.trade_off is not None and facts.trade_off.fact.key == "weight"
    written = "It is light enough to carry anywhere."
    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life"))], written)
    out = check_explanation(para, facts, claims, Scorer({(BATTERY, S_BATTERY): 0.9}), 0.5, 5.0)
    assert out.trade_off == trade_off_text(facts.trade_off)
    kept = "It is heavy, at almost two kilos."
    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life"))], kept)
    scorer = Scorer({(BATTERY, S_BATTERY): 0.9, (WEIGHT, kept): 0.9})
    assert check_explanation(para, facts, claims, scorer, 0.5, 5.0).trade_off == kept


def test_the_threshold_is_inclusive() -> None:
    facts, claims = _setup()
    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life"))])
    assert check_explanation(para, facts, claims, Scorer({(BATTERY, S_BATTERY): 0.5}), 0.5, 5.0).sentences[0].supported


# --- the fallback: 6 words of a cited quote in a row ------------------------------------------------------------------


def test_six_words_of_a_cited_quote_in_a_row_word_for_word() -> None:
    assert quotes_words("Reviewers say it lasted a full working day of coding.", [BATTERY], 6)
    assert not quotes_words("It lasted a full working day.", [BATTERY], 6)  # 5 words only
    assert quotes_words("THE BATTERY EASILY LASTED A FULL day", [BATTERY], 6)  # capitals aside
    assert not quotes_words("The battery lasted easily a full working day.", [BATTERY], 6)  # not in a row
    assert not quotes_words("Anything at all", [], 6)


def test_the_fallback_keeps_quoting_sentences_and_templates_unchecked() -> None:
    facts, claims = _setup()
    quoting = "One review says the battery easily lasted a full working day."
    para = _written(facts, [(quoting, _ids(facts, "battery_life")), (S_KEYS, _ids(facts, "keyboard_comfort"))])
    out = quoted_only(para, facts, claims, 6)
    keyboard = next(f for f in facts.top if f.key == "keyboard_comfort")
    assert out.method == "llm_unchecked"
    assert [(s.text, s.supported) for s in out.sentences] == [(quoting, None), (strength_sentence(keyboard), None)]


def test_the_fallback_with_nothing_quoted_gives_the_full_template() -> None:
    facts, claims = _setup()
    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life"))])
    assert quoted_only(para, facts, claims, 6) == template(facts)


# --- the contract -----------------------------------------------------------------------------------------------------


def test_the_validator_never_sees_an_unsupported_sentence_under_llm_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, list[Any]]] = []
    real = Explanation.model_validate

    def spy(data: Any, *args: Any, **kwargs: Any) -> Explanation:
        seen.append((data["method"], [s.get("supported") for s in data["sentences"]]))
        return real(data, *args, **kwargs)

    monkeypatch.setattr(Explanation, "model_validate", spy)  # the class check.py builds with
    facts, claims = _setup()
    para = _written(
        facts,
        [
            (S_BATTERY, _ids(facts, "battery_life")),
            (S_KEYS, _ids(facts, "keyboard_comfort")),
            (S_SPEED, _ids(facts, "cpu_performance")),
        ],
    )
    check_explanation(para, facts, claims, Scorer({(BATTERY, S_BATTERY): 0.9}), 0.5, 5.0)
    assert seen and all(not (method == "llm_checked" and False in supported) for method, supported in seen)
    with pytest.raises(ValueError, match="unsupported sentences must be removed"):
        rebuild(para, facts, {0: True}, method="llm_checked", supported=False)


def test_items_are_the_written_sentences_and_the_written_trade_off_only() -> None:
    facts, _ = _setup()
    own = unverified_sentences([facts.labels[k] for k in facts.unverified])[0]
    assert facts.trade_off is not None
    para = _written(facts, [(S_BATTERY, _ids(facts, "battery_life")), (own, list(facts.claim_ids()))], "Heavy.")
    assert [i.index for i in items(para, facts)] == [0, TRADE_OFF]
    templated = _written(facts, [(S_BATTERY, _ids(facts, "battery_life"))], trade_off_text(facts.trade_off))
    assert [i.index for i in items(templated, facts)] == [0]
