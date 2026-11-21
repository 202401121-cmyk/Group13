"""Each evidence sentence against the quotes it cites (US-22, D-91). Code decides; the model only reads.

- With the model: a phrase that states one of code's own numbers by its kind (a criterion's score or points, the rank,
  the 0-100 score; codes.py) passes by the number rule alone (parsing.py), so it is taken out before the model reads
  the sentence (DECIDED 3, A). Each cited quote is a premise for what is left, through the check's one interface
  (nli.sentence_probabilities), one batch per product. A sentence passes at or above the threshold.
- A sentence with fewer than MIN_WORDS words left once code's numbers are out only repeats code's own sentence: it
  fails.
- The fallback (the model missing, failing or out of time, or NLI off): a sentence passes only when it holds
  `quoted_words` words of a cited quote in a row, word for word.

A sentence that fails is dropped; the paragraph's code sentences always stay (paragraph.py).
"""

from __future__ import annotations

from collections.abc import Sequence

from .codes import without_codes
from .facts import ProductFacts
from .nli import PairScorer, sentence_probabilities
from .parsing import Draft
from .quoted import quotes_words, words

MIN_WORDS = 3


def hypothesis(text: str, facts: ProductFacts, key: str) -> str:
    """The sentence as the model reads it: every phrase that states its kind's own code value taken out (codes.py)."""
    return without_codes(text, facts, key)


def checked_by_model(
    drafts: Sequence[Draft], facts: ProductFacts, scorer: PairScorer, threshold: float, timeout_s: float
) -> list[bool]:
    """Each draft's verdict from the model (one batch), or NliUnavailable."""
    hypotheses = [hypothesis(d.text, facts, d.key) for d in drafts]
    readable = [i for i, h in enumerate(hypotheses) if len(words(h)) >= MIN_WORDS]
    found = sentence_probabilities(scorer, [(hypotheses[i], drafts[i].quotes) for i in readable], timeout_s)
    best = dict(zip(readable, found, strict=True))
    return [i in best and best[i] >= threshold for i in range(len(drafts))]


def checked_by_quotes(drafts: Sequence[Draft], n: int) -> list[bool]:
    """The fallback: each draft passes only when it quotes a cited quote, `n` words in a row."""
    return [quotes_words(d.text, d.quotes, n) for d in drafts]
