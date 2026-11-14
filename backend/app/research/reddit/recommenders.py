"""Counts how many different people name each product in Reddit threads (US-13). Plain Python, with no model.

ProductMatcher is a small Aho-Corasick matcher: one pass over the text finds every name at once, however many names
there are, and a word-boundary rule and a leftmost-longest rule then pick the real matches. It needs no library, so it
can't fail to install on any machine.

A count is distinct author_hash values, so one person posting ten times counts once. An author we can't identify counts
for nothing. Inputs are sorted and ties go by name, so the same threads always give the same counts. Everything stays in
memory (D-53).
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence

from app.contracts import RecommenderCount, Source, SourceType

from ..dedupe import MERGED_LINE
from .quality import CountingWords, load_quality_settings

MAX_COUNTS = 200  # ResearchResult.recommender_counts and MatchInput.recommender_counts limit
MIN_NAME_CHARS = 2
LATIN_ENDS_AT = 0x250  # past this, a letter is in another script, so it can't be part of a Latin name's word
_HYPHEN = "-"
_LONG_DASH = chr(0x2013)  # in a sentence it is punctuation, though inside a name it can stand for a hyphen
_HYPHENS = str.maketrans(dict.fromkeys(map(chr, (0x2010, 0x2011, 0x2012)), _HYPHEN))
_LONG_DASHES = str.maketrans(dict.fromkeys(map(chr, (0x2014, 0x2015, 0x2212)), _LONG_DASH))
_POSSESSIVE = re.compile("['\u2019]s\\b")


def _plain(text: str) -> str:
    """The form texts are compared in: one case, single spaces, and one kind each of hyphen and long dash."""
    return " ".join(text.translate(_HYPHENS).translate(_LONG_DASHES).casefold().split())


def _plain_name(name: str) -> str:
    """The same for a name, where any dash is a hyphen."""
    return _plain(name).replace(_LONG_DASH, _HYPHEN)


def _is_word_char(char: str) -> bool:
    """A letter, a digit or a mark that builds a letter, such as a Hindi vowel sign."""
    return char.isalnum() or unicodedata.category(char).startswith("M")


def _same_word(neighbour: str, edge: str) -> bool:
    """Would this neighbour run on from the name's edge? A Hindi letter touching a Latin name doesn't."""
    return _is_word_char(neighbour) and (ord(neighbour) < LATIN_ENDS_AT) == (ord(edge) < LATIN_ENDS_AT)


def _bounded(text: str, start: int, end: int) -> bool:
    """Does the match stand as its own word? In "WF-1000XM5", "1000XM5" doesn't: it is the end of another name."""
    if start > 0:
        before = text[start - 1]
        if _same_word(before, text[start]):
            return False
        if before == _HYPHEN and start >= 2 and _same_word(text[start - 2], text[start]):
            return False
    return end == len(text) or not _same_word(text[end], text[end - 1])


class ProductMatcher:
    """Finds product names in text: any case, whole words only, and the longest name where names overlap."""

    def __init__(self, names: Iterable[str] | Mapping[str, str]) -> None:
        """`names` is a list of names, or a mapping from each spelling to the product it means."""
        pairs = names.items() if isinstance(names, Mapping) else ((name, name) for name in names)
        self._products: dict[str, str] = {}
        for spelling, product in sorted(pairs):  # sorted, so the same names always give the same spelling
            plain = _plain_name(spelling)
            if plain:
                self._products.setdefault(plain, product)
        self._next: list[dict[str, int]] = [{}]
        self._back: list[int] = [0]
        self._ends: list[list[str]] = [[]]
        for plain in sorted(self._products):
            self._add(plain)
        self._link()

    def _add(self, plain: str) -> None:
        state = 0
        for char in plain:
            following = self._next[state].get(char)
            if following is None:
                following = len(self._next)
                self._next[state][char] = following
                self._next.append({})
                self._back.append(0)
                self._ends.append([])
            state = following
        self._ends[state].append(plain)

    def _link(self) -> None:
        """Breadth first: each state's fallback is the longest suffix that is also a prefix of some name."""
        queue = deque(self._next[0].values())
        while queue:
            state = queue.popleft()
            for char, following in self._next[state].items():
                queue.append(following)
                back = self._back[state]
                while back and char not in self._next[back]:
                    back = self._back[back]
                self._back[following] = self._next[back].get(char, 0)
                self._ends[following] = [*self._ends[following], *self._ends[self._back[following]]]

    def _walk(self, text: str) -> Iterator[tuple[int, int, str]]:
        state = 0
        for index, raw in enumerate(text):
            char = _HYPHEN if raw == _LONG_DASH else raw
            while state and char not in self._next[state]:
                state = self._back[state]
            state = self._next[state].get(char, 0)
            for plain in self._ends[state]:
                yield index + 1 - len(plain), index + 1, plain

    def find(self, text: str) -> list[str]:
        """The products named, in the order they appear. Overlaps go to the longest name, then the leftmost."""
        plain_text = _plain(text)
        hits = [hit for hit in self._walk(plain_text) if _bounded(plain_text, hit[0], hit[1])]
        hits.sort(key=lambda hit: (hit[0] - hit[1], hit[0], hit[2]))
        taken = [False] * len(plain_text)
        found: list[tuple[int, str]] = []
        for start, end, plain in hits:
            if not any(taken[start:end]):
                taken[start:end] = [True] * (end - start)
                found.append((start, plain))
        return [self._products[plain] for _, plain in sorted(found)]

    def find_all(self, text: str) -> set[str]:
        """Every product any of whose names stands as whole words in the text, in any case, overlaps included: in
        "Galaxy S24 Ultra", both "Galaxy S24 Ultra" and "Galaxy S24". For sourcing (US-40), where any naming counts;
        find() keeps one name per stretch of text, for counting people."""
        plain_text = _plain(text)
        return {
            self._products[plain] for start, end, plain in self._walk(plain_text) if _bounded(plain_text, start, end)
        }


def _what_each_wrote(thread: Source) -> Iterator[tuple[str | None, str]]:
    """Who wrote what. A "Same topic" line is ours, added when near-duplicates merge, so it isn't the author's words."""
    own = "\n".join(line for line in thread.text.splitlines() if not MERGED_LINE.match(line))
    yield thread.author_hash, f"{thread.title}\n{own}"
    for comment in sorted(thread.comments, key=lambda c: c.id):
        yield comment.author_hash, comment.text


def _can_be_a_name(name: str) -> bool:
    """A model gives these names, and one like "A" or "14" would match almost every post."""
    plain = _plain_name(name)
    return len(plain) >= MIN_NAME_CHARS and any(c.isalpha() for c in plain)


def _names_a_product(name: str, skip: frozenset[str]) -> bool:
    """False for a name made only of brand and generic words: "dell", "gaming laptop", "dell laptop", not "MacBook"."""
    words = re.findall(r"[^\W_]+", _POSSESSIVE.sub("", _plain_name(name)))
    return any(word not in skip for word in words)


def count_recommenders(
    threads: Sequence[Source],
    names: Iterable[str],
    aliases: Mapping[str, str] | None = None,
    words: CountingWords | None = None,
) -> list[RecommenderCount]:
    """Distinct people per product, most first. `aliases` maps another spelling to the product's own name.

    Extraction marks what is a product at all, and its caller passes only those. `words` is the config's brand and
    generic word lists, for the names that aren't a product on their own all the same.
    """
    lists = words if words is not None else load_quality_settings().counting
    skip = frozenset(_plain_name(word) for word in (*lists.brands, *lists.generic_words))
    spellings = {name: name for name in sorted(names) if _names_a_product(name, skip)}
    spellings.update({spelling: name for spelling, name in (aliases or {}).items() if _names_a_product(spelling, skip)})
    matcher = ProductMatcher({spelling: product for spelling, product in spellings.items() if _can_be_a_name(spelling)})
    people: dict[str, set[str]] = {}
    places: dict[str, set[str]] = {}
    for thread in sorted((t for t in threads if t.type is SourceType.REDDIT), key=lambda t: t.id):
        for author, text in _what_each_wrote(thread):
            if author is None:
                continue
            for product in matcher.find(text):
                people.setdefault(product, set()).add(author)
                places.setdefault(product, set()).add(thread.id)
    rows = [
        RecommenderCount(product_mention=product, distinct_recommenders=len(authors), threads=len(places[product]))
        for product, authors in people.items()
    ]
    rows.sort(key=lambda row: (-row.distinct_recommenders, row.product_mention.casefold(), row.product_mention))
    return rows[:MAX_COUNTS]
