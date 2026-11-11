"""Grouping names into products (US-15, DECIDED 2 to 6, DR-02, DR-06). Code decides; the scorer only rates pairs.

1. The rules (`decide`) settle most pairs. A vague name, two brands with nothing in common, two editions, two model
   codes that differ or a name that is plainly a shorter one plus more words are never one product; agreeing model
   codes are one product. A pair the rules leave open goes to the scorer.
2. Blocking: a name is compared only with clusters that share a word, a number or a code ending with it, and whose
   brands don't conflict with its own.
3. Clustering with a chaining guard: names go in a fixed order, most specific first, and a name joins a cluster only if
   it matches every name already in it, the representative first. So A~B and B~C never pull in a C that fails against
   A, whichever of them came first. A name that matches two clusters is unsure: it stays on its own and is listed.
4. Known aliases from earlier searches (DR-06) keep a product's id from search to search, and settle an unsure name.
   They never pull a product apart: a name with a known id joins a cluster only if none of that id's saved names
   contradicts the cluster, and two names with different known ids are never one product.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from app.contracts import MatchInput, MatchResult, Product, ProductMention, Variant

from .normalise import ParsedName, parse_name, plain_name
from .scorer import PairScorer

log = logging.getLogger(__name__)

MAX_PRODUCTS = 200  # MatchResult.products limit: past it, the products with the fewest mentions are left out
MAX_ALIASES = 50  # Product.aliases limit
MAX_VARIANTS = 20  # Product.variants limit
MAX_LABEL_CHARS = 120  # Variant.label limit
MAX_ID_BODY = 64  # ProductId and VariantId: prefix + at most 64 characters
SUFFIX_CODE_MIN = 3  # a shorter code agrees with a longer one it ends ("xm5", "wh1000xm5") only from 3 characters
# A blocking key shared by more clusters than this (a word in every name, such as a brand's series) is not used when the
# name has a rarer key: it would compare every name with every other and find nothing the rarer keys miss.
MAX_BLOCK = 100
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass
class _Entry:
    """One distinct raw name, with how often it was mentioned."""

    raw: str
    parsed: ParsedName
    count: int
    product_type: bool
    brand: str | None  # as the mention gave it, for the product
    known_id: str | None = None


@dataclass
class _Cluster:
    entries: list[_Entry]
    product_type: bool
    known_id: str | None = None
    unsure: bool = False

    @property
    def rep(self) -> _Entry:
        return self.entries[0]

    @property
    def size(self) -> int:
        return sum(e.count for e in self.entries)


@dataclass
class _Clustering:
    scorer: PairScorer
    known_names: Mapping[str, list[ParsedName]]  # product id -> the names earlier searches saved for it
    clusters: list[_Cluster] = field(default_factory=list)
    index: dict[str, list[int]] = field(default_factory=dict)
    by_known: dict[str, int] = field(default_factory=dict)
    by_plain_type: dict[str, int] = field(default_factory=dict)


# --- the rules ---------------------------------------------------------------------------------------------------


def decide(a: ParsedName, b: ParsedName) -> bool | None:
    """True: one product. False: never one product. None: the rules can't tell, so the scorer decides."""
    if a.is_vague or b.is_vague:
        return False
    if a.brands and b.brands and not (a.brands & b.brands):
        return False  # Sony and Bose; but "Xiaomi Redmi Note 13" and "Redmi Note 13" share a brand
    if a.editions != b.editions:
        return False
    if a.base == b.base:
        return True
    if a.codes or b.codes:
        return _codes_decide(a, b)
    if set(a.numbers) != set(b.numbers):
        return False  # iPhone 15 and iPhone 16; or a number on one side only, which can't be confirmed
    words_a, words_b = set(a.words), set(b.words)
    if words_a != words_b and (words_a < words_b or words_b < words_a):
        return False  # "Redmi 13" and "Redmi Note 13", "Kindle" and "Kindle Paperwhite": more words, another product
    return None


def _codes_decide(a: ParsedName, b: ParsedName) -> bool:
    """Model codes decide over words (DECIDED 3): WH-1000XM5 and WF-1000XM5 never merge, XM5 and WH-1000XM5 do."""
    if not (a.codes and b.codes):
        return False  # a code on one side only: "Galaxy" can't be confirmed as "Galaxy A35"
    fewer, more = sorted((a.codes, b.codes), key=len)
    if not all(any(_codes_agree(x, y) for y in more) for x in fewer):
        return False
    if a.numbers and b.numbers and set(a.numbers) != set(b.numbers):
        return False
    only_a, only_b = set(a.words) - set(b.words), set(b.words) - set(a.words)
    return not (only_a and only_b)  # words on one side only are a fuller name; words on both sides disagree


def _codes_agree(x: str, y: str) -> bool:
    if x == y:
        return True
    short, long = sorted((x, y), key=len)
    return len(short) >= SUFFIX_CODE_MIN and long.endswith(short)


def _keys(parsed: ParsedName) -> set[str]:
    """Blocking keys (DECIDED 2): only names sharing one are ever compared."""
    if parsed.is_vague:
        return set()
    keys = {f"w:{w}" for w in parsed.words} | {f"n:{n}" for n in parsed.numbers}
    keys |= {f"c:{c[-SUFFIX_CODE_MIN:]}" for c in parsed.codes}
    return keys


# --- grouping ----------------------------------------------------------------------------------------------------


def group(entries: list[_Entry], scorer: PairScorer, known_names: Mapping[str, list[ParsedName]]) -> list[_Cluster]:
    state = _Clustering(scorer, known_names)
    for entry in sorted(entries, key=_specific_first):
        if entry.product_type:
            _place_product_type(state, entry)
        else:
            _place(state, entry)
    return state.clusters


def _specific_first(entry: _Entry) -> tuple[int, int, int, str]:
    longest_code = max((len(c) for c in entry.parsed.codes), default=0)
    return (-longest_code, -len(entry.parsed.base), -entry.count, entry.raw)


def _place_product_type(state: _Clustering, entry: _Entry) -> None:
    """A kind of product (DECIDED 6) is never merged into a product: only the same words, spelled another way."""
    plain = plain_name(entry.raw)
    if plain in state.by_plain_type:
        state.clusters[state.by_plain_type[plain]].entries.append(entry)
        return
    state.by_plain_type[plain] = len(state.clusters)
    state.clusters.append(_Cluster([entry], product_type=True))


def _place(state: _Clustering, entry: _Entry) -> None:
    known = entry.known_id
    if known is not None and known in state.by_known:
        state.clusters[state.by_known[known]].entries.append(entry)  # an earlier search said it is this product
        return
    matched = [i for i in _matching_clusters(state, entry) if _fits_known(state, state.clusters[i], known)]
    if len(matched) == 1:
        cluster = state.clusters[matched[0]]
        cluster.entries.append(entry)
        if known is not None and cluster.known_id is None:
            cluster.known_id = known
            state.by_known[known] = matched[0]
        return
    _start(state, entry, unsure=len(matched) > 1)


def _fits_known(state: _Clustering, cluster: _Cluster, known: str | None) -> bool:
    """May a name carry its known id into this cluster? Only if nothing an earlier search saved for that id says no."""
    if cluster.known_id is not None:
        return known is None or cluster.known_id == known
    if known is None:
        return True
    saved = state.known_names.get(known, [])
    return all(decide(member.parsed, name) is not False for member in cluster.entries for name in saved)


def _matching_clusters(state: _Clustering, entry: _Entry) -> list[int]:
    """The clusters this name matches: every name in them, by the rules or by the scorer."""
    blocks = [state.index.get(key, []) for key in _keys(entry.parsed)]
    rare = [block for block in blocks if len(block) <= MAX_BLOCK]
    candidates = sorted({i for block in (rare or blocks) for i in block})
    pending: dict[int, list[int]] = {}  # cluster -> its members the scorer must still pass
    to_score: list[tuple[str, str]] = []
    for i in candidates:
        open_members = _open_members(entry, state.clusters[i])
        if open_members is not None:
            pending[i] = []
            for member in open_members:
                pending[i].append(len(to_score))
                to_score.append((entry.parsed.base, member.parsed.base))
    scores = state.scorer.score(to_score) if to_score else []
    passed = [score >= state.scorer.threshold for score in scores]
    return sorted(i for i, positions in pending.items() if all(passed[p] for p in positions))


def _open_members(entry: _Entry, cluster: _Cluster) -> list[_Entry] | None:
    """None when the rules rule this cluster out; else its members only the scorer can settle (maybe none)."""
    if cluster.unsure or cluster.product_type:
        return None  # an unsure name is never something others join
    open_members: list[_Entry] = []
    for member in cluster.entries:  # the representative first: most clusters are ruled out by it alone
        verdict = decide(entry.parsed, member.parsed)
        if verdict is False:
            return None
        if verdict is None:
            open_members.append(member)
    return open_members


def _start(state: _Clustering, entry: _Entry, *, unsure: bool) -> None:
    position = len(state.clusters)
    known = entry.known_id if entry.known_id not in state.by_known else None
    state.clusters.append(_Cluster([entry], product_type=False, known_id=known, unsure=unsure))
    if known is not None:
        state.by_known[known] = position
    if not unsure:
        for key in _keys(entry.parsed):
            state.index.setdefault(key, []).append(position)


# --- products ----------------------------------------------------------------------------------------------------


def match_names(inp: MatchInput, scorer: PairScorer) -> MatchResult:
    entries = _entries(inp.mentions, inp.known_aliases)
    needed = {e.known_id for e in entries if e.known_id is not None}
    known_names: dict[str, list[ParsedName]] = {}
    for key, product_id in inp.known_aliases.items():
        if product_id in needed:
            known_names.setdefault(product_id, []).append(parse_name(key))
    clusters = group(entries, scorer, known_names)
    unmatched = sorted(e.raw for c in clusters if c.unsure for e in c.entries)
    return _result(clusters, inp, unmatched)


def unmerged(inp: MatchInput) -> MatchResult:
    """The fallback (DECIDED 8): every distinct name is its own product. Nothing is merged, nothing is guessed."""
    clusters = [_Cluster([e], product_type=e.product_type) for e in _entries(inp.mentions, {})]
    return _result(clusters, inp, [])


def _entries(mentions: Iterable[ProductMention], known: Mapping[str, str]) -> list[_Entry]:
    counts: Counter[str] = Counter()
    types: dict[str, bool] = {}
    brands: dict[str, Counter[str]] = {}
    for mention in mentions:
        counts[mention.raw_name] += 1
        # A name marked a kind of product anywhere is kept as one: it is never merged into a product (DECIDED 6).
        types[mention.raw_name] = types.get(mention.raw_name, False) or not mention.is_product
        if mention.brand:
            brands.setdefault(mention.raw_name, Counter())[mention.brand] += 1
    entries = []
    for raw in sorted(counts):
        brand = _most_common(brands.get(raw))
        parsed = parse_name(raw, brand)
        known_id = None if types[raw] else known.get(parsed.key)
        entries.append(_Entry(raw, parsed, counts[raw], types[raw], brand, known_id))
    return entries


def _result(clusters: list[_Cluster], inp: MatchInput, unmatched: list[str]) -> MatchResult:
    kept = sorted(clusters, key=lambda c: (-c.size, c.rep.raw))[:MAX_PRODUCTS]
    if len(kept) < len(clusters):
        log.info("match: kept the %d products with the most mentions of %d", len(kept), len(clusters))
    counts: dict[str, int] = {}
    for row in inp.recommender_counts:  # two spellings that fold to one name keep the larger count, never the sum
        name = plain_name(row.product_mention)
        counts[name] = max(counts.get(name, 0), row.distinct_recommenders)
    products: list[Product] = []
    alias_map: dict[str, str] = {}
    used: set[str] = set()
    for cluster in sorted(kept, key=lambda c: (c.known_id is None, _canonical(c).raw)):
        product = _product(cluster, counts, used)
        used.add(product.id)
        products.append(product)
        alias_map.update({e.raw: product.id for e in cluster.entries})
    kept_names = set(alias_map)
    return MatchResult(
        products=sorted(products, key=lambda p: p.id),
        alias_map=alias_map,
        unmatched=[name for name in unmatched if name in kept_names],
        method="fuzzy",
    )


def _canonical(cluster: _Cluster) -> _Entry:
    """The name shown for the product: the fullest base, then the most mentioned, then the longest, then by text."""
    return max(cluster.entries, key=lambda e: (len(e.parsed.base), e.count, len(e.raw), e.raw))


def _product(cluster: _Cluster, counts: Mapping[str, int], used: set[str]) -> Product:
    canonical = _canonical(cluster)
    product_id = cluster.known_id if cluster.known_id and cluster.known_id not in used else None
    if product_id is None:
        product_id = _unique(product_id_for(canonical.raw), canonical.raw, cluster.product_type, used)
    names = sorted({e.raw for e in cluster.entries} - {canonical.raw})
    brand = _most_common(Counter(e.brand for e in cluster.entries if e.brand))
    return Product(
        id=product_id,
        name=canonical.raw,
        brand=brand[:60] if brand else None,
        kind="product_type" if cluster.product_type else "product",
        aliases=names[:MAX_ALIASES],
        variants=_variants(cluster, product_id),
        recommender_count=max((counts.get(plain_name(e.raw), 0) for e in cluster.entries), default=0),
    )


def _variants(cluster: _Cluster, product_id: str) -> list[Variant]:
    """DR-02: names that differ only in RAM, storage, colour or region are variants of the one product."""
    found = sorted({e.parsed.attributes for e in cluster.entries if e.parsed.attributes})
    if cluster.product_type or not found:
        return []
    variants: list[Variant] = []
    used: set[str] = set()
    for attributes in found[:MAX_VARIANTS]:
        body = _slug(f"{product_id.removeprefix('prd_')}_{'_'.join(v for _, v in attributes)}")
        variant_id = "var_" + _unique_body(body, repr(attributes), used)
        used.add(variant_id)
        label = _label(attributes)[:MAX_LABEL_CHARS]
        variants.append(Variant(id=variant_id, label=label, attributes=dict(attributes)))
    return variants


def _label(attributes: tuple[tuple[str, str], ...]) -> str:
    parts = dict(attributes)
    words = []
    if "ram" in parts:
        words.append(f"{_spaced(parts['ram'])} RAM")
    if "storage" in parts:
        words.append(_spaced(parts["storage"]))
    if "colour" in parts:
        words.append(parts["colour"].capitalize())
    if "region" in parts:
        words.append(parts["region"])
    return " / ".join(words)


def _spaced(capacity: str) -> str:
    return f"{capacity[:-2]} {capacity[-2:]}"  # "128GB" -> "128 GB"


# --- ids ---------------------------------------------------------------------------------------------------------


def product_id_for(name: str) -> str:
    """prd_ and the name as a slug: "Nova Book 14" -> prd_nova_book_14. The same name always gives the same id."""
    return "prd_" + _slug(name)[:MAX_ID_BODY]


def _unique(product_id: str, name: str, product_type: bool, used: set[str]) -> str:
    if product_id not in used:
        return product_id
    body = product_id.removeprefix("prd_")
    return "prd_" + _unique_body(body, f"{name}|{product_type}", used, prefix="prd_")


def _unique_body(body: str, seed: str, used: set[str], prefix: str = "var_") -> str:
    if prefix + body not in used:
        return body
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return f"{body[: MAX_ID_BODY - 9]}_{digest}"


def _slug(text: str) -> str:
    slug = _NOT_SLUG.sub("_", plain_name(text)).strip("_")[:MAX_ID_BODY].strip("_")
    return slug if len(slug) >= 3 else (slug + "_product")[:MAX_ID_BODY]


def _most_common(counter: Counter[str] | None) -> str | None:
    if not counter:
        return None
    return min(counter.items(), key=lambda item: (-item[1], item[0]))[0]


# --- aliases to keep (DR-06) -------------------------------------------------------------------------------------


def alias_keys(result: MatchResult) -> dict[str, str]:
    """The names of each product that merged more than one name, as alias key -> product id, for later searches.

    Only names that carry the product's full model code are kept. A short form such as "XM5" joined because its code
    ends the full one; it could as well be the WF-1000XM5, so saving it would decide later searches on a guess. A
    product type, or a product with a single name, adds nothing a later search couldn't work out itself.
    """
    keys: dict[str, str] = {}
    for product in result.products:
        if product.kind != "product" or not product.aliases:
            continue
        full = set(parse_name(product.name, product.brand).codes)
        for name in (product.name, *product.aliases):
            parsed = parse_name(name, product.brand)
            if parsed.key and set(parsed.codes) <= full:
                keys.setdefault(parsed.key, product.id)
    return keys
