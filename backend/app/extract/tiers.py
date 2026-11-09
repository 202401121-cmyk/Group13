"""What kind of site a source is, and so what type its claims are (US-14, DR-05). US-16 replaces this in Sprint 3.

Code decides this from the source, never the model: a page could otherwise talk its way into counting as a spec.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.contracts import ClaimType, Source, SourceTier, SourceType

CLAIM_TYPES: dict[SourceTier, ClaimType] = {
    SourceTier.MANUFACTURER: ClaimType.SPEC,
    SourceTier.LAB: ClaimType.SPEC,
    SourceTier.EDITORIAL: ClaimType.EXPERIENCE,
    SourceTier.COMMUNITY: ClaimType.EXPERIENCE,
    SourceTier.RETAILER: ClaimType.EXPERIENCE,  # a shop's listing is unchecked, so it must not count as a spec
}


# A subdomain or a folder with one of these names is a forum, even on a maker's or a lab's own domain: an owner's post
# is not the maker's spec. Calling a real spec page a forum is the safer mistake, as it only counts for less.
_FORUM_NAMES = frozenset({"answers", "communities", "community", "discuss", "discussions", "forum", "forums"})


def _names_a_forum(source: Source, labels: list[str]) -> bool:
    subdomain_words = [word for label in labels[:-2] for word in label.split("-")]
    folders = (source.url.path or "").lower().split("/")
    return not _FORUM_NAMES.isdisjoint([*subdomain_words, *folders])


def tier_of(source: Source, domains: Mapping[str, SourceTier]) -> SourceTier:
    """Reddit and forums are community. Otherwise the longest domain that matches, else research's tier."""
    if source.type is SourceType.REDDIT:
        return SourceTier.COMMUNITY
    labels = (source.url.host or "").lower().rstrip(".").split(".")
    if _names_a_forum(source, labels):
        return SourceTier.COMMUNITY
    for start in range(len(labels)):
        tier = domains.get(".".join(labels[start:]))
        if tier is not None:
            return tier
    return source.tier


def claim_type_for(source: Source, domains: Mapping[str, SourceTier]) -> ClaimType:
    return CLAIM_TYPES[tier_of(source, domains)]
