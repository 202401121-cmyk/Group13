"""US-22 with D-91: each evidence sentence read against the quotes it cites, with a fake scorer. Code's own numbers pass
by the number rule alone, so the model reads the sentence without them (A); a long quote is read in chunks and the
best counts; the fallback keeps only sentences that quote 6 words in a row; and the check's one interface (P-49)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from app.explain.check import MIN_WORDS, checked_by_model, checked_by_quotes, hypothesis
from app.explain.codes import values
from app.explain.facts import ProductFacts, facts_for
from app.explain.nli import NliUnavailable, sentence_probabilities
from app.explain.parsing import Draft

from .build import every_claim, nova

BATTERY = "The battery easily lasted a full working day of coding and browsing."
KEYS = "The keys are springy and quiet, and typing for hours never tired my hands."


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


def _facts() -> ProductFacts:
    return facts_for(nova(), every_claim())  # battery 8/10 (24 points), cpu 7.5, keyboard 9; ranks #1 at 73


def _draft(text: str, key: str = "battery_life", *quotes: str) -> Draft:
    return Draft(key, text, ("clm_0000000000000001",), quotes or (BATTERY,))


# --- (A): code's numbers are out before the model reads ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "read"),
    [
        ("Its battery lasts a full working day, with a score of 8 out of 10.", "Its battery lasts a full working day."),
        ("Its battery lasts a full working day, at 8 out of 10.", "Its battery lasts a full working day."),
        ("Its battery adds 24 points and lasts a full working day.", "Its battery and lasts a full working day."),
        ("Its battery lasts a full working day, a score of 73 out of 100.", "Its battery lasts a full working day."),
        ("It ranks #1 and its battery lasts a full working day.", "It and its battery lasts a full working day."),
        # not code's numbers: the model reads them, and the number rule has already checked them against the quotes
        (
            "Its battery reached 10 hours, with a score of 9 out of 10.",
            "Its battery reached 10 hours, with a score of 9 out of 10.",
        ),
        ("Its battery reached 10 hours in a test.", "Its battery reached 10 hours in a test."),
    ],
)
def test_the_model_reads_the_sentence_without_codes_numbers(text: str, read: str) -> None:
    assert hypothesis(text, _facts(), "battery_life") == read


def test_codes_numbers_for_a_criterion_go_by_kind() -> None:
    assert values(_facts(), "battery_life") == {"score": [8.0], "points": [24.0], "total": [73.0], "rank": [1.0]}


def test_a_code_number_of_another_kind_is_left_for_the_model_to_read() -> None:
    text = "Its battery lasts a full working day, with a score of 1 out of 10."  # 1 is the rank, not this score
    assert hypothesis(text, _facts(), "battery_life") == text


def test_a_supported_sentence_passes_and_one_below_the_threshold_fails_in_one_batch() -> None:
    right = _draft("Its battery lasts a full working day of coding, with a score of 8 out of 10.")
    wrong = _draft("Its keys are springy and quiet.", "keyboard_comfort", KEYS)
    scorer = Scorer({(BATTERY, "Its battery lasts a full working day of coding."): 0.97, (KEYS, wrong.text): 0.4})
    assert checked_by_model([right, wrong], _facts(), scorer, 0.9608, 5.0) == [True, False]
    assert len(scorer.batches) == 1


def test_the_threshold_is_inclusive() -> None:
    draft = _draft("Its battery lasts a full working day.")
    scorer = Scorer({(BATTERY, draft.text): 0.9608})
    assert checked_by_model([draft], _facts(), scorer, 0.9608, 5.0) == [True]


def test_a_sentence_of_codes_numbers_alone_repeats_codes_own_and_fails_unread() -> None:
    draft = _draft("It scores 8 out of 10.")
    assert len(hypothesis(draft.text, _facts(), "battery_life").split()) < MIN_WORDS
    scorer = Scorer()
    assert checked_by_model([draft], _facts(), scorer, 0.5, 5.0) == [False]
    assert scorer.batches == []


def test_a_long_quote_is_read_in_chunks_and_the_best_chunk_counts() -> None:
    long_quote = "Made-up filler words here and there. " * 10 + BATTERY
    scorer = Scorer({(BATTERY.split(" ", 4)[-1], "Its battery lasts a full working day."): 0.99}, chunk=8)
    probabilities = sentence_probabilities(scorer, [("Its battery lasts a full working day.", [long_quote])], 5.0)
    (batch,) = scorer.batches
    assert len(batch) > 1  # every chunk of the quote is a premise
    assert probabilities[0] == max(scorer.table.get(pair, 0.1) for pair in batch)


def test_the_interface_gives_a_sentence_with_no_quote_nothing() -> None:
    assert sentence_probabilities(Scorer(), [("A sentence with no quote.", [])], 5.0) == [0.0]


def test_a_tokenizer_failure_is_the_models_failure_so_the_fallback_follows() -> None:
    class _Broken(Scorer):
        def premises(self, quote: str) -> list[str]:
            raise ValueError("a tokenizer error")

    with pytest.raises(NliUnavailable, match="ValueError"):
        checked_by_model([_draft("Its battery lasts a full working day.")], _facts(), _Broken(), 0.5, 5.0)


# --- the fallback -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        (
            "Its battery easily lasted a full working day of use.",
            True,
        ),  # 6 words in a row: "battery easily ... working day"
        ("It easily lasted a working day of coding.", False),  # 5 at most
        ("THE BATTERY EASILY LASTED A FULL day.", True),  # capitals aside
    ],
)
def test_the_fallback_keeps_only_sentences_that_quote_6_words_in_a_row(text: str, kept: bool) -> None:
    assert checked_by_quotes([_draft(text)], 6) == [kept]
