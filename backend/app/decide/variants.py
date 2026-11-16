"""A product's stated versions (US-09, S4-24; DR-02): the RAM or storage its own names state, in words.

A must-have on RAM or storage is checked against every known version of a product, not only the one value verify
resolved: S3-19 left "vivo T5x 8GB RAM" out of "at least 8 GB RAM" because a review page gave its other version's 6 GB.
A value a product's name states counts as stated by the source that named it.

A name states a capacity only in words: "8GB RAM", "512GB SSD", "1 TB HDD", or a phone's "8+128GB" (RAM, then
storage). A bare capacity is a guess, never a statement: in "Core i5/ 16GB/ 512GB SSD/ 2GB Graph" the 16GB is
probably RAM and the 2GB the graphics card's, and "Nova Phone 8GB" may be either. Matching guesses such capacities when
it makes variants (app/entities/normalise.py, the smaller of two is RAM, a lone one storage), so its variant labels are
not read here: the names they come from are, the product's own and its aliases. Plain code, no model.
"""

from __future__ import annotations

import re

from app.contracts import Product

from .units import attribute_unit

# The words that must follow a capacity in a name for it to count, by the rule's attribute word: "ram_gb" takes "8GB
# RAM"; "storage_gb" any storage word; "ssd_gb" only "512GB SSD", so a laptop's "1TB HDD" never counts as its SSD.
_LABELS: dict[str, frozenset[str]] = {
    "ram": frozenset({"ram"}),
    "memory": frozenset({"ram"}),  # a name's "128GB memory" is a phone's storage as often as RAM, so it never counts
    "storage": frozenset({"storage", "ssd", "rom", "hdd", "emmc"}),
    "ssd": frozenset({"ssd"}),
    "hdd": frozenset({"hdd"}),
    "rom": frozenset({"rom"}),
    "emmc": frozenset({"emmc"}),
}
_RAM_WORDS = frozenset({"ram", "memory"})
_PLUS_FORM_WORDS = frozenset({"ram", "memory", "storage", "rom"})  # a phone's "8+128GB": its RAM and its storage

# A capacity and the word after it: "8GB RAM", "8 GB-RAM", "1TB SSD".
_LABELLED = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(gb|tb)\s*-?\s*([a-z]+)")
# RAM plus storage: "8+128GB", "8GB + 256 GB". The storage has 2 to 4 digits, so "8GB+8GB" (virtual RAM) is not one.
_PLUS_FORM = re.compile(r"(?<![\w.])(\d{1,2})\s*(?:gb)?\s*\+\s*(\d{2,4})\s*(gb|tb)(?![a-z])")


def variant_word(attribute: str) -> str | None:
    """The RAM or storage word a rule's attribute names: "ram_gb" -> "ram", "ssd_gb" -> "ssd", "storage" -> "storage".

    None for any other attribute (the weight, a feature, the price): its rules are checked as before.
    """
    parts = [part for part in attribute.casefold().split("_") if part]
    if len(parts) > 1 and attribute_unit(attribute) is not None:
        parts = parts[:-1]
    return parts[0] if len(parts) == 1 and parts[0] in _LABELS else None


def stated_values(product: Product, attribute: str) -> list[str]:
    """The capacities the product's names state in words for this attribute, each once, as "8GB" or "1TB".

    Empty for an attribute that is no RAM or storage word, or when no name states one: the rule is then checked as
    before.
    """
    word = variant_word(attribute)
    if word is None:
        return []
    labels = _LABELS[word]
    found: list[str] = []
    for name in (product.name, *product.aliases):
        text = name.casefold()
        found += [f"{amount}{unit.upper()}" for amount, unit, label in _LABELLED.findall(text) if label in labels]
        if word in _PLUS_FORM_WORDS:
            for ram, storage, unit in _PLUS_FORM.findall(text):
                found.append(f"{ram}GB" if word in _RAM_WORDS else f"{storage}{unit.upper()}")
    return list(dict.fromkeys(found))
