"""Never recommend an unsourced product (US-40, FR-25, build rule 5). Plain Python, with no model.

A product is kept if a source names it, or a claim about it quotes the source it came from:
- a source names it when one of its names (its name, its aliases, or a raw name match mapped to it) appears as whole
  words, in any case, in a source's title, text or one of its comments. Every name counts, even inside a longer one:
  a page about the "Galaxy S24 Ultra" names the "Galaxy S24" too;
- a claim about it is one whose product_mention is one of those names, and it counts only if its quote is found in its
  own source's text or comments (the check extraction makes, app/extract/parsing.py).

Anything else was named only by the model, so it is dropped before any value is combined. This check logs nothing: the
verify stage logs each drop once per search, with its reason and its names (names_of), never a source's text. A
product type ("memory foam") follows the same rule: it is shown as a result too, so it needs a source as much as a
product does.

A variant's label is not one of its names: it describes a configuration ("16 GB / 512 GB / India") that many products
share, so finding it says nothing about which product a page is about.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from app.contracts import Product, Source, VerifyInput
from app.extract.parsing import found_quote
from app.research.reddit.recommenders import MIN_NAME_CHARS, ProductMatcher

UNSOURCED_REASON = "no source names it, and no claim about it quotes a source"


def _names(product: Product, raw_names: Mapping[str, list[str]]) -> list[str]:
    """Every name a product goes by: its own, its aliases, and the raw names the match mapped to it."""
    names = [product.name, *product.aliases, *raw_names.get(product.id, [])]
    return [name for name in dict.fromkeys(names) if len(name.strip()) >= MIN_NAME_CHARS]


def names_of(inp: VerifyInput) -> dict[str, list[str]]:
    """Each product id, with the names this check looks for."""
    raw: dict[str, list[str]] = {}
    for spelling, product_id in sorted(inp.match.alias_map.items()):
        raw.setdefault(product_id, []).append(spelling)
    return {product.id: _names(product, raw) for product in inp.match.products}


def _texts(source: Source) -> Iterator[str]:
    yield source.title
    yield source.text
    for comment in source.comments:
        yield comment.text


def _named_in_sources(matcher: ProductMatcher, inp: VerifyInput) -> set[str]:
    named: set[str] = set()
    for source in inp.sources:
        for text in _texts(source):
            named |= matcher.find_all(text)  # every name counts, even inside a longer one (find() keeps one)
    return named


def _quoted_in_claims(matcher: ProductMatcher, inp: VerifyInput, wanted: set[str]) -> set[str]:
    """The products among `wanted` that a claim quoting its own source is about."""
    exact = dict(inp.match.alias_map)
    folded = {raw.casefold(): pid for raw, pid in sorted(exact.items())}
    sources = {source.id: source for source in inp.sources}
    quoted: set[str] = set()
    for claim in inp.claims:
        if not wanted - quoted:
            break
        mention = claim.product_mention
        about = matcher.find_all(mention)
        direct = exact.get(mention) or folded.get(mention.casefold())
        if direct is not None:
            about.add(direct)
        about &= wanted - quoted
        source = sources.get(claim.source_id)  # a claim always has its source_url (the contract asks for it)
        if not about or source is None:
            continue
        parts = [source.text, *(comment.text for comment in source.comments)]
        if found_quote(claim.quote, parts) is not None:
            quoted |= about
    return quoted


def unsourced(inp: VerifyInput) -> list[tuple[str, str]]:
    """The products no source names and no claim about them quotes a source, each with its reason, in id order."""
    names = names_of(inp)
    matcher = ProductMatcher({name: product_id for product_id, spelled in names.items() for name in spelled})
    kept = _named_in_sources(matcher, inp)
    missing = {product.id for product in inp.match.products} - kept
    if missing:
        missing -= _quoted_in_claims(matcher, inp, missing)
    return [(product_id, UNSOURCED_REASON) for product_id in sorted(missing)]
