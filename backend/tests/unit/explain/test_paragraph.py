"""D-91: a paragraph is code's sentences, always, with the evidence sentences that were kept, each after the sentence
about its criterion. Every sentence of a written paragraph cites claims, and only evidence sentences carry a verdict."""

from __future__ import annotations

import hashlib

from app.explain.facts import ProductFacts, facts_for
from app.explain.paragraph import MAX_SENTENCES, paragraph
from app.explain.parsing import Draft
from app.explain.templates import template

from .build import every_claim, kestrel, nova

PRIORITIES = ["battery_life", "cpu_performance", "keyboard_comfort"]


def _facts() -> ProductFacts:
    return facts_for(nova(), every_claim())


def _id(key: str) -> str:
    return "clm_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def _draft(key: str, text: str) -> Draft:
    return Draft(key, text, (_id(key),), ("A made-up quote.",))


def test_no_evidence_kept_is_the_template_word_for_word() -> None:
    assert paragraph(_facts(), PRIORITIES, [], "llm_checked") == template(_facts(), PRIORITIES)


def test_each_evidence_sentence_follows_its_criterions_sentence_and_the_trade_off_comes_last() -> None:
    kept = [
        _draft("battery_life", "Reviewers say its battery lasts a full working day."),
        _draft("keyboard_comfort", "Its keys are quiet and springy."),
        _draft("weight", "It is heavy to carry all day."),  # its trade-off
    ]
    out = paragraph(_facts(), PRIORITIES, kept, "llm_checked")
    code = [s.text for s in template(_facts(), PRIORITIES).sentences]
    assert [s.text for s in out.sentences] == [
        code[0],
        code[1],
        "Reviewers say its battery lasts a full working day.",
        code[2],
        code[3],
        "Its keys are quiet and springy.",
        "It is heavy to carry all day.",
        code[4],
    ]
    assert out.method == "llm_checked"
    assert out.trade_off == template(_facts(), PRIORITIES).trade_off


def test_every_sentence_cites_claims_and_only_evidence_carries_a_verdict() -> None:
    kept = [_draft("battery_life", "Reviewers say its battery lasts a full working day.")]
    checked = paragraph(_facts(), PRIORITIES, kept, "llm_checked")
    assert all(s.claim_ids for s in checked.sentences)
    assert [s.supported for s in checked.sentences] == [None, None, True, None, None, None]
    battery = next(f for f in _facts().top if f.key == "battery_life")
    assert checked.sentences[1].claim_ids == list(battery.claim_ids)  # a strength cites its criterion's claims
    assert checked.sentences[0].claim_ids == [_id("battery_life")]  # code's other sentences cite the evidence's
    unchecked = paragraph(_facts(), PRIORITIES, kept, "llm_unchecked")
    assert unchecked.method == "llm_unchecked"
    assert all(s.supported is None for s in unchecked.sentences)


def test_a_paragraph_never_passes_the_contracts_8_sentences() -> None:
    keys = ("battery_life", "cpu_performance", "keyboard_comfort", "weight")
    kept = [_draft(key, f"A made-up line about its {key}.") for key in keys]
    assert len(template(_facts(), PRIORITIES).sentences) + len(kept) > MAX_SENTENCES  # 5 of code's, 4 evidence
    out = paragraph(_facts(), PRIORITIES, kept, "llm_checked")
    assert len(out.sentences) == MAX_SENTENCES
    assert "A made-up line about its weight." not in [s.text for s in out.sentences]  # the last criteria first


def test_a_tied_products_evidence_follows_its_strength_not_the_tie() -> None:
    tied = facts_for(kestrel().model_copy(update={"tied_with": [nova().product_id]}), every_claim())
    kept = [_draft(tied.top[0].key, "A made-up line about its best point.")]
    texts = [s.text for s in paragraph(tied, PRIORITIES, kept, "llm_checked").sentences]
    assert texts.index("A made-up line about its best point.") == 3  # rank, tie, strength, then it
