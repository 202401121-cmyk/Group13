"""How a product name is read before it is matched (US-15, DECIDED 1 and 5). Plain code, no model.

A name is cut into parts: its brands, its model codes (letters with digits, such as WH-1000XM5 or A35), its numbers,
its edition words (Pro, Ultra, Plus), the other words that name it, and its variant attributes (RAM, storage, colour,
region; DR-02). Generic words such as "best" or "headphones" go: the brands and generic words are the counting lists
in config/reddit_quality.yaml, so a brand or a generic word is added there, never in code.
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field

from app.research.reddit.quality import load_quality_settings

MAX_ALIAS_CHARS = 80  # an alias kept for later searches is at most this long (DR-06)

# Words that name an edition of a product. "Galaxy S24" and "Galaxy S24 Ultra" are different products, so these are
# never dropped, even when a counting list calls them generic ("ultra" is there, for "ultra thin laptop").
EDITION_WORDS = frozenset(
    {"ultra", "pro", "plus", "max", "mini", "lite", "air", "fe", "se", "neo", "edge", "fold", "flip", "prime"}
)

# Variant attributes (DR-02): versions that differ only in these are variants of one product.
COLOURS = frozenset(
    {
        "black", "white", "silver", "grey", "gray", "blue", "red", "green", "gold", "pink", "purple", "yellow",
        "orange", "midnight", "starlight", "graphite", "titanium", "beige", "cream", "navy", "violet", "lavender",
    }
)  # fmt: skip
REGIONS = {"india": "IN", "indian": "IN", "usa": "US", "us": "US", "uk": "GB", "eu": "EU", "global": "GLOBAL"}
RAM_WORDS = frozenset({"ram", "memory"})
STORAGE_WORDS = frozenset({"ssd", "storage", "rom", "hdd", "emmc"})
SIZE_UNITS = ("mah", "mm")  # a size such as 5000mah or 44mm names a different product, so it counts as a number

_CAPACITY = re.compile(r"^(\d+(?:\.\d+)?)(gb|tb|mb)$")
_RAM_PLUS_STORAGE = re.compile(r"\b(\d{1,2})\s*\+\s*(\d{2,4})\s*(gb|tb)\b")  # "8+128GB": 8 GB RAM, 128 GB storage
_UNITS = re.compile(r"(\d+(?:\.\d+)?)\s*(gb|tb|mb|mah|mm)\b")
# A short prefix joined to a code: "wh-1000xm5" -> "wh1000xm5", "rtx-4060" -> "rtx4060". Not "iphone-16" (a name and
# its number) or "thin-and-light" (no digit).
_JOINED_CODE = re.compile(r"\b([a-z]{1,3})-(?=[a-z]*\d)([a-z0-9]+)\b")
_WORD_AND_NUMBER = re.compile(r"\b([a-z]{4,})(\d{1,3})\b")  # "iphone15" -> "iphone 15", "buds4" -> "buds 4"
_NOT_WORD = re.compile(r"[^a-z0-9.]+")
_LONE_DOT = re.compile(r"(?<!\d)\.|\.(?!\d)")  # a dot is kept only inside a number such as 6.1


@dataclass(frozen=True)
class Vocabulary:
    """The counting lists: brands, and generic words that never name a product on their own."""

    brands: frozenset[str]
    generic_words: frozenset[str]


@functools.cache
def load_vocabulary() -> Vocabulary:
    counting = load_quality_settings().counting
    return Vocabulary(
        brands=frozenset(_plain(word) for word in counting.brands),
        generic_words=frozenset(_plain(word) for word in counting.generic_words),
    )


@dataclass(frozen=True)
class ParsedName:
    """One name, cut into the parts that matching compares."""

    raw: str
    brand: str | None  # the first brand, from the mention or from the brands list
    brands: frozenset[str]  # every brand in the name: "Xiaomi Redmi Note 13" has two
    base: str  # what the scorer compares: the name without generic words and variant attributes
    words: tuple[str, ...]  # plain words other than brands, edition words included
    codes: tuple[str, ...]  # letters with digits: wh1000xm5, a35, g14
    numbers: tuple[str, ...]  # 16, 14, or a size such as 44mm or 10000mah
    editions: frozenset[str]
    attributes: tuple[tuple[str, str], ...] = field(default=())  # sorted (ram|storage|colour|region, value)

    @property
    def key(self) -> str:
        """How the name is kept as an alias (DR-06): its base, at most MAX_ALIAS_CHARS characters."""
        return self.base[:MAX_ALIAS_CHARS].rstrip()

    @property
    def is_vague(self) -> bool:
        """Only a brand or generic words: "Sony", "gaming laptop". It names no one product, so it never merges."""
        return not (self.words or self.codes or self.numbers)


@dataclass
class _Parts:
    brands: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    words: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    capacities: list[tuple[str, str | None]] = field(default_factory=list)  # (capacity, the word after it)
    attributes: dict[str, str] = field(default_factory=dict)


def parse_name(raw: str, brand: str | None = None, vocabulary: Vocabulary | None = None) -> ParsedName:
    known = vocabulary or load_vocabulary()
    given = _plain(brand) if brand else None
    tokens = _tokens(raw)
    if len(tokens) > 1 and tokens[0] == tokens[1] and (tokens[0] in known.brands or tokens[0] == given):
        tokens = tokens[1:]  # "Sony Sony WH-1000XM5": the brand written twice
    parts = _Parts(brands=[given] if given else [])
    for i, token in enumerate(tokens):
        _sort(token, tokens[i + 1] if i + 1 < len(tokens) else None, parts, known, given)
    parts.attributes.update(_capacity_attributes(parts.capacities))
    return ParsedName(
        raw=raw,
        brand=parts.brands[0] if parts.brands else None,
        brands=frozenset(parts.brands),
        base=" ".join(parts.kept),
        words=tuple(parts.words),
        codes=tuple(parts.codes),
        numbers=tuple(parts.numbers),
        editions=frozenset(w for w in parts.words if w in EDITION_WORDS),
        attributes=tuple(sorted(parts.attributes.items())),
    )


def _sort(token: str, after: str | None, parts: _Parts, known: Vocabulary, given: str | None) -> None:
    """Put one token where it belongs: a variant attribute, a brand, a code, a number or a word. Or nowhere."""
    capacity = _CAPACITY.match(token)
    if capacity:
        parts.capacities.append((_capacity_text(capacity), after))
    elif token in RAM_WORDS or token in STORAGE_WORDS:
        return
    elif token in COLOURS:
        parts.attributes.setdefault("colour", "grey" if token == "gray" else token)
    elif token in REGIONS:
        parts.attributes.setdefault("region", REGIONS[token])
    elif token == given or token in known.brands:
        if token not in parts.brands:
            parts.brands.append(token)
        if token not in parts.kept:
            parts.kept.append(token)
    elif token in known.generic_words and token not in EDITION_WORDS:
        return
    elif any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
        size = token[0].isdigit() and token.endswith(SIZE_UNITS)
        (parts.numbers if size else parts.codes).append(token)
        parts.kept.append(token)
    elif token.replace(".", "", 1).isdigit():
        parts.numbers.append(token)
        parts.kept.append(token)
    else:
        parts.words.append(token)
        parts.kept.append(token)


def plain_name(text: str) -> str:
    """Case, spacing and punctuation folded away: how two spellings of one name are compared exactly."""
    return " ".join(_tokens(text))


def _tokens(raw: str) -> list[str]:
    text = unicodedata.normalize("NFKC", raw).casefold()
    text = _RAM_PLUS_STORAGE.sub(r"\1gb ram \2\3", text)
    text = text.replace("+", " plus ")  # "Galaxy S24+" is the S24 Plus, never the S24
    text = _UNITS.sub(r"\1\2", text)  # "128 GB" -> "128gb"
    text = _JOINED_CODE.sub(r"\1\2", text)
    text = _NOT_WORD.sub(" ", text)
    text = _LONE_DOT.sub(" ", text)
    text = _WORD_AND_NUMBER.sub(r"\1 \2", text)
    return text.split()


def _plain(word: str) -> str:
    return "".join(_tokens(word))


def _capacity_text(match: re.Match[str]) -> str:
    amount, unit = match.groups()
    return f"{amount}{unit.upper()}"


def _capacity_attributes(capacities: list[tuple[str, str | None]]) -> dict[str, str]:
    """Which capacity is RAM and which is storage: the word after it says, else the smaller of two is RAM.

    A lone capacity with no word after it is taken as storage. It is still a variant either way; only its label could be
    wrong (a laptop's "16GB" is usually its RAM).
    """
    found: dict[str, str] = {}
    unlabelled: list[str] = []
    for capacity, after in capacities:
        if after in RAM_WORDS:
            found.setdefault("ram", capacity)
        elif after in STORAGE_WORDS:
            found.setdefault("storage", capacity)
        else:
            unlabelled.append(capacity)
    unlabelled.sort(key=_in_gb)
    if len(unlabelled) >= 2 and "ram" not in found:
        found["ram"] = unlabelled.pop(0)
    if unlabelled and "storage" not in found:
        found["storage"] = unlabelled.pop()
    return found


def _in_gb(capacity: str) -> float:
    amount, unit = float(capacity[:-2]), capacity[-2:]
    return amount * {"MB": 1 / 1024, "GB": 1.0, "TB": 1024.0}[unit]
