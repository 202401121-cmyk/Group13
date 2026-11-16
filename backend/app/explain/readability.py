"""How hard a text is to read (US-20, DECIDED 3): the Flesch-Kincaid grade, worked out here with no new package.

grade = 0.39 x words per sentence + 11.8 x syllables per word - 15.59. Words are runs of letters, so numbers and
symbols ("8", "#1", "%") don't count. Syllables are vowel groups, less a silent final "e" or "-es"/"-ed" that adds none.
It is an estimate, as every syllable count is: it only has to tell plain text from dense text.
"""

from __future__ import annotations

import re

GRADE_LIMIT = 9.0  # US-20: "about school Grade 8"; over 9 is rewritten once, then the template is used

_SENTENCE_END = re.compile(r"[.!?]+(?=\s|$)")
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
_ES_SYLLABLE = re.compile(r"(?:[cszgx]es|ches|shes)$")  # "sources", "boxes": the -es is a syllable of its own
_SILENT_END = re.compile(r"(?:[^laeiouy]es|[^laeiouydt]ed|[^laeiouy]e)$")  # "fixes" no, "worked", "life"
_VOWELS = re.compile(r"[aeiouy]{1,2}")


def syllables(word: str) -> int:
    letters = re.sub(r"[^a-z]", "", word.lower())
    if not letters:
        return 0
    if len(letters) <= 3:
        return 1
    if not _ES_SYLLABLE.search(letters):
        letters = _SILENT_END.sub("", letters)
    letters = letters.removeprefix("y")
    return max(1, len(_VOWELS.findall(letters)))


def grade(text: str) -> float:
    """The Flesch-Kincaid grade of the text, or 0.0 when it has no words."""
    words = _WORD.findall(text)
    if not words:
        return 0.0
    sentences = max(1, sum(1 for part in _SENTENCE_END.split(text) if _WORD.search(part)))
    count = sum(syllables(word) for word in words)
    return 0.39 * len(words) / sentences + 11.8 * count / len(words) - 15.59
