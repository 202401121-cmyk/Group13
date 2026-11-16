"""Is a store listing for this exact model? (US-24, DECIDED 2)

A listing is accepted only when its title names the product's brand and model, read by the match stage's own name
parsing (app/entities/normalise.py) and its model-code rule (app/entities/clustering.py). Never fuzzy: a near name is
another product until shown otherwise, and a refusal only costs a store search link, never a wrong price.

A title carries far more than the name ("16GB RAM 512GB SSD Windows 11 Home"), so the name must appear in it whole:

- the brand, then the model's words and numbers, together and in order: "ASUS Zenbook 14 OLED ..." names the Zenbook
  14, "Asus Zenbook S 14" and "HP Victus 15" (for an "HP 15") don't. Only filler such as "Laptop" is skipped. The
  brand is the name's own first word when it is one ("Redmi Note 13"), else the product's brand ("Apple" before
  "iPhone 15"). A product with no brand is priced only if its name has a model code;
- each of the product's model codes agrees with one of the title's, wherever it is: WH-1000XM5 and WF-1000XM5 never
  do;
- a year in the product's name ("(2025)") needn't be in the title, but a different one there is another model;
- an edition word (Pro, Max, Plus, Ultra...) right after the model is another product unless the product has it too:
  "iPhone 15 Pro" is not the iPhone 15, but "Victus 15 ... Windows 11 Pro" is the Victus 15;
- a listing for something else is refused: an accessory "for" the model ("Case for Apple iPhone 15", "65W Charger
  for ..."), an accessory named outright ("... Silicone Case"), or a second-hand one ("Renewed", "Refurbished").

The variant is set only when the title states every one of its attributes (RAM, storage, colour, region) with the
same values. A title for another configuration is still an offer for the model, with no variant.
"""

from __future__ import annotations

import itertools
from urllib.parse import quote_plus

from app.contracts import Product, Variant
from app.entities.clustering import _codes_agree as codes_agree  # the match stage's own model-code rule
from app.entities.normalise import EDITION_WORDS, load_vocabulary, parse_name, plain_name

STORE_SEARCH = "https://www.google.com/search?tbm=shop&q="
FILLER = frozenset({"laptop", "notebook", "smartphone", "phone", "mobile", "tablet", "pc", "computer"})
SECOND_HAND = frozenset({"renewed", "refurbished", "refurb", "used", "preowned", "secondhand"})
SECOND_HAND_PAIRS = frozenset({("pre", "owned"), ("open", "box"), ("second", "hand")})
ACCESSORIES = frozenset({"case", "cover", "charger", "adapter", "protector", "sleeve", "skin", "pouch", "tempered"})
COMES_WITH = frozenset({"with", "and", "charging", "plus"})  # "with charger", "charging case": part of the product
YEARS = range(2000, 2031)  # 2050 is an RTX 2050, not a year


def query_for(product: Product) -> str:
    """What a store would be asked for: the brand, then the name, without the brand twice."""
    name = " ".join(product.name.split())
    brand = (product.brand or "").strip()
    if not brand or plain_name(name).split()[: len(plain_name(brand).split())] == plain_name(brand).split():
        return name
    return f"{brand} {name}"


def store_search_url(product: Product) -> str:
    """A store search for the product, for a card with no matching listing (US-24, DECIDED 3)."""
    return STORE_SEARCH + quote_plus(query_for(product))


def _tokens(text: str) -> list[str]:
    return [t for t in plain_name(text).split() if t not in FILLER]


def _is_year(token: str) -> bool:
    return len(token) == 4 and token.isdigit() and int(token) in YEARS


def _anchor_and_phrase(product: Product, codes: set[str]) -> tuple[list[str], list[str]]:
    """The brand's tokens, and the model's tokens to find straight after them (codes and years left out)."""
    name = _tokens(product.name)
    brand = plain_name(product.brand).split() if product.brand else []
    if brand and name[: len(brand)] == brand:
        anchor, rest = brand, name[len(brand) :]
    elif name and name[0] in load_vocabulary().brands:
        anchor, rest = name[:1], name[1:]  # a sub-brand that starts the name: "Redmi Note 13" by Xiaomi
    else:
        anchor, rest = brand, name
    return anchor, [t for t in rest if t not in codes and not _is_year(t)]


def accepts(product: Product, title: str) -> bool:
    """True only if the listing's title names this product's brand and model (see the module's rules)."""
    model = parse_name(product.name, product.brand)
    if model.is_vague:
        return False
    tokens = _tokens(title)
    anchor, phrase = _anchor_and_phrase(product, set(model.codes))
    if not anchor and not model.codes:
        return False  # no brand to confirm, and no code to stand for it
    start = _find(tokens, anchor + phrase)
    if start < 0 or "for" in tokens[:start]:
        return False  # not named, or named as what an accessory is "for"
    listing = parse_name(title)
    if not all(any(codes_agree(code, theirs) for theirs in listing.codes) for code in model.codes):
        return False
    own_years = {t for t in _tokens(product.name) if _is_year(t)}
    their_years = {t for t in tokens if _is_year(t)}
    if own_years and their_years and not own_years & their_years:
        return False  # another year's model
    return not (_second_hand(tokens) or _accessory(tokens, product) or _another_edition(model.editions, phrase, tokens))


def _find(tokens: list[str], phrase: list[str]) -> int:
    """Where the phrase starts in the tokens, or -1. An empty phrase is never found."""
    if not phrase:
        return -1
    return next((i for i in range(len(tokens) - len(phrase) + 1) if tokens[i : i + len(phrase)] == phrase), -1)


def _second_hand(tokens: list[str]) -> bool:
    return any(t in SECOND_HAND for t in tokens) or any(
        pair in SECOND_HAND_PAIRS for pair in itertools.pairwise(tokens)
    )


def _accessory(tokens: list[str], product: Product) -> bool:
    """An accessory named in the title, unless it comes with the product ("with charger", "charging case")."""
    own = set(plain_name(product.name).split())
    return any(
        t in ACCESSORIES and t not in own and (i == 0 or tokens[i - 1] not in COMES_WITH) for i, t in enumerate(tokens)
    )


def _another_edition(editions: frozenset[str], phrase: list[str], tokens: list[str]) -> bool:
    """An edition word straight after one of the model's words or numbers, which the product doesn't have."""
    own = set(phrase)
    return any(
        token in EDITION_WORDS and token not in editions and tokens[i - 1] in own
        for i, token in enumerate(tokens)
        if i > 0
    )


def variant_for(product: Product, title: str) -> Variant | None:
    """The product's variant the title states in full (every attribute, same value), or None. The fullest wins."""
    stated = dict(parse_name(title).attributes)
    if not stated:
        return None
    fitting = [
        v
        for v in product.variants
        if v.attributes and all(stated.get(name) == value for name, value in v.attributes.items())
    ]
    return max(fitting, key=lambda v: len(v.attributes), default=None)
