"""What one evidence call may carry (US-22, US-20, D-91): a product's name and its public quotes, and nothing else.

- Only public quotes: a claim from Reddit or from a video's page (YouTube, its viewers' comments) is never sent to any
  provider (D-53). Explain is given claims, not sources, so it tells them apart by the claim's address. Every other
  source is a maker's, review or store page.
- Only the quotes for the criteria the paragraph is about: the product's top 3 and its trade-off. At most MAX_QUOTES a
  criterion, each quote once.
- Never the shopper's priorities, weights or words, never a score, and never a criterion's name or which matters
  most: the quotes go in groups named t1, t2 ... in a fixed order, by key (prompts.py).
- The product's name is the one its public quotes use most often (ties by claim id): a source's words, never the
  shopper's.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.contracts import Claim
from app.entities.clustering import product_id_for

from .facts import ProductFacts, label

COMMUNITY_HOSTS = ("reddit.com", "redd.it", "youtube.com", "youtu.be")  # as research never fetches them (D-53)
MAX_QUOTES = 3


@dataclass(frozen=True)
class Quoted:
    """One public quote offered to the LLM, under its criterion, with the short ref it cites it by."""

    ref: str
    claim_id: str
    key: str
    quote: str


@dataclass(frozen=True)
class EvidenceRequest:
    product_id: str
    name: str
    labels: Mapping[str, str]  # criterion key -> its words, for the criteria with quotes, in key order: never sent
    quotes: tuple[Quoted, ...]

    def by_ref(self) -> dict[str, Quoted]:
        return {q.ref: q for q in self.quotes}


def is_community(claim: Claim) -> bool:
    """A claim from Reddit or a video's page: community words, never sent to any provider."""
    host = (urlsplit(str(claim.source_url)).hostname or "").lower().rstrip(".")
    return any(host == domain or host.endswith(f".{domain}") for domain in COMMUNITY_HOSTS)


def paragraph_claims(facts: ProductFacts, claims: Sequence[Claim]) -> list[Claim]:
    """This product's claims about the criteria its paragraph is about (its top 3 and its trade-off)."""
    keys = {fact.key for fact in facts.facts()}
    return [
        c
        for c in claims
        if not c.is_product_type and c.criterion_key in keys and product_id_for(c.product_mention) == facts.product_id
    ]


def community_only(facts: ProductFacts, claims: Sequence[Claim]) -> bool:
    """Its paragraph's criteria have evidence, and all of it is community words."""
    mine = paragraph_claims(facts, claims)
    return bool(mine) and all(is_community(c) for c in mine)


def community_quotes(facts: ProductFacts, claims: Sequence[Claim]) -> list[str]:
    """The community quotes about this product: never sent, and never allowed back in a sentence (D-53)."""
    return [c.quote for c in paragraph_claims(facts, claims) if is_community(c)]


def evidence_request(facts: ProductFacts, claims: Sequence[Claim]) -> EvidenceRequest | None:
    """What the evidence call for this product is given, or None when it has no public quote to give."""
    public = sorted((c for c in paragraph_claims(facts, claims) if not is_community(c)), key=lambda c: c.id)
    if not public:
        return None
    quotes: list[Quoted] = []
    for key in sorted({c.criterion_key for c in public if c.criterion_key is not None}):
        seen: set[str] = set()
        for c in (c for c in public if c.criterion_key == key):
            text = c.quote.strip()
            if text.casefold() in seen:
                continue
            seen.add(text.casefold())
            quotes.append(Quoted(f"q{len(quotes) + 1}", c.id, key, text))
            if len(seen) == MAX_QUOTES:
                break
    names = Counter(c.product_mention.strip() for c in public)
    name = max(dict.fromkeys(c.product_mention.strip() for c in public), key=names.__getitem__)  # ties: first by id
    labels = {key: facts.labels.get(key, label(key)) for key in dict.fromkeys(q.key for q in quotes)}
    return EvidenceRequest(facts.product_id, name, labels, tuple(quotes))
