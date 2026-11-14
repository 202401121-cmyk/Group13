"""Exclusions typed in words (US-10): "no Xiaomi", "not touchscreen", "no Galaxy A35". Code only: no LLM is asked.

A phrase starts at "no", "not", "without", "except", "avoid", "anything but" or "don't want" and runs to the end of the
sentence or the next of those words; a list in it ("no Xiaomi or Realme") gives one exclusion each. Each item is:
- a model, if it has a name with a digit in it ("Galaxy A35", "Pixel 8"). A measure ("1.5 kg", "70k", "16gb") is not;
- a brand, if it is one of the counting brands in config/reddit_quality.yaml;
- a feature, if config/exclusions.yaml lists it, in any of its forms;
- otherwise not understood, as typed. Nothing is guessed.

"always" (save to memory) is never set here: the shopper sets it in the editor, and a guest's "always" is never used in
another search until login exists (D-36, US-30). The route reads a bare list ("Xiaomi, Realme") as exclusions; intake's
keyword fallback asks for a trigger word, so an ordinary request never turns into exclusions, and there a word it can't
place ends the phrase ("not too heavy, Dell or Lenovo" rules nothing out).
"""

from __future__ import annotations

import re
from functools import cache

from app.contracts import Exclusion, ExclusionParseResponse
from app.decide.features import FEATURES_PATH, feature_key, feature_name, known_feature, load_feature_words
from app.research.reddit.quality import load_quality_settings

# The feature table is app/decide/features.py's, shared with the search and decide (S3-9).
__all__ = ["FEATURES_PATH", "feature_key", "known_feature", "load_feature_words", "parse_exclusions"]

MAX_EXCLUSIONS = 30  # ExclusionParseResponse.exclusions
MAX_NOT_UNDERSTOOD = 20  # ExclusionParseResponse.not_understood
MAX_ITEM_CHARS = 200  # each not-understood item, as the contract allows
# Words that make a phrase an amount or a comparison, never a model's name: "spend more than 50000", "above 50000".
_NOT_A_NAME = frozenset(
    [
        "more",
        "less",
        "than",
        "over",
        "under",
        "above",
        "below",
        "upto",
        "up",
        "to",
        "spend",
        "spending",
        "exceeding",
        "exceed",
        "budget",
        "price",
        "cost",
        "costing",
        "rs",
        "rupees",
        "inr",
        "lakh",
        "lakhs",
        "around",
        "about",
        "max",
        "maximum",
        "min",
        "minimum",
        "limit",
    ]
)

_TRIGGER = re.compile(
    r"\b(?:anything\s+but|(?:don['’]?t|do\s+not)\s+want|without|except|avoid|not|no)\b", re.IGNORECASE
)
_SENTENCE_END = re.compile(r"[;!?\n]|\.(?=\s|$)")
_LIST = re.compile(r"\s*(?:,|/|&|\bor\b|\band\b|\bnor\b)\s*", re.IGNORECASE)
_LEAD_INS = re.compile(
    r"^(?:(?:show\s+me|me|anything|stuff|products|items|phones|laptops|any|a|an|the|from|by|made\s+by|made|of)\s+)+",
    re.IGNORECASE,
)
_TAIL = re.compile(
    r"(?:\s+(?:phones?|laptops?|products?|brands?|stuff|devices?|ones|please|at\s+all|either|anymore|items?))+$",
    re.IGNORECASE,
)
_EDGES = " \t\"'“”‘’()[]{}:;,.!?-"
# A comparison or a measure is a must-have's business, not an exclusion: "no more than 1.5 kg", "not over 70k".
_COMPARISON = re.compile(r"^(?:more|less|over|under|above|below|bigger|smaller|heavier|lighter|cheaper|too)\b", re.I)
_MEASURE = re.compile(
    r"^[₹$]?\d[\d,]*(?:\.\d+)?\s*(?:k|gb|tb|mb|kg|g|mah|hz|w|mp|inch|inches|in|cm|mm|rs|lakh)?$", re.I
)
_LETTERS_AND_DIGITS = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9+-]+$")


@cache
def _brands() -> frozenset[str]:
    return frozenset(brand.casefold() for brand in load_quality_settings().counting.brands)


def _plain(text: str) -> str:
    return " ".join(text.casefold().split())


def _phrases(text: str, require_trigger: bool) -> list[str]:
    """Each piece of text a trigger word rules out. With no trigger, the whole text, unless one is required."""
    triggers = list(_TRIGGER.finditer(text))
    if not triggers:
        return [] if require_trigger else [text]
    phrases: list[str] = []
    for i, trigger in enumerate(triggers):
        end = triggers[i + 1].start() if i + 1 < len(triggers) else len(text)
        piece = text[trigger.end() : end]
        stop = _SENTENCE_END.search(piece)
        phrases.append(piece[: stop.start()] if stop else piece)
    return phrases


def _clean(item: str) -> str:
    item = item.strip(_EDGES)
    previous = None
    while previous != item:
        previous = item
        item = _TAIL.sub("", _LEAD_INS.sub("", item)).strip(_EDGES)
    return item


def _is_model(item: str) -> bool:
    words = item.split()
    if any(w.casefold() in _NOT_A_NAME for w in words):  # an amount or a comparison, however it is written
        return False
    if any(_LETTERS_AND_DIGITS.match(w) and not _MEASURE.match(w) for w in words):
        return True
    return 2 <= len(words) <= 3 and words[-1].isdigit() and words[0].isalpha()  # "Pixel 8", "iPhone 15"


def _kind_of(item: str) -> Exclusion | None:
    if _COMPARISON.match(item) or _MEASURE.match(item):
        return None
    if _is_model(item):
        return Exclusion(kind="model", value=item[:80])
    if _plain(item) in _brands():
        return Exclusion(kind="brand", value=item)
    feature = feature_name(item)
    if feature is not None:
        return Exclusion(kind="feature", value=feature)
    return None


def parse_exclusions(text: str, *, require_trigger: bool = False) -> ExclusionParseResponse:
    """The exclusions in the shopper's words, and the words that could not be read as one (as typed)."""
    exclusions: list[Exclusion] = []
    seen: set[tuple[str, str]] = set()
    not_understood: list[str] = []
    for phrase in _phrases(text, require_trigger):
        for raw in _LIST.split(phrase):
            item = _clean(raw)[:MAX_ITEM_CHARS]
            if not item:
                continue
            exclusion = _kind_of(item)
            if exclusion is None:
                if len(not_understood) < MAX_NOT_UNDERSTOOD and item not in not_understood:
                    not_understood.append(item)
                if require_trigger:
                    # In a request, a word we can't place ends the phrase: in "not too heavy, Dell or Lenovo" the brands
                    # after it are what the shopper wants, not what they rule out. The editor's box keeps reading on.
                    break
                continue
            key = (exclusion.kind, exclusion.value.casefold())
            if key not in seen and len(exclusions) < MAX_EXCLUSIONS:
                seen.add(key)
                exclusions.append(exclusion)
    return ExclusionParseResponse(exclusions=exclusions, not_understood=not_understood)
