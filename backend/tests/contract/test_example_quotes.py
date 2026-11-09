"""The example claims keep to the rule real claims keep to: each quote is its source's own words (build rule 4)."""

from __future__ import annotations

import pytest

from app.contracts import Claim, examples


def _claims() -> list[Claim]:
    return [*examples.claims(), examples.product_type_claim()]


def _text_of(source_id: str) -> str:
    sources = {s.id: s for s in [*examples.sources(), examples.product_guide()]}
    return sources[source_id].text


@pytest.mark.parametrize("claim", _claims(), ids=lambda c: c.id)
def test_every_example_quote_is_word_for_word_in_its_source(claim: Claim) -> None:
    assert claim.quote in _text_of(claim.source_id)


def test_every_example_claim_url_matches_its_source() -> None:
    sources = {s.id: s for s in [*examples.sources(), examples.product_guide()]}
    for claim in _claims():
        assert claim.source_url == sources[claim.source_id].url


def test_the_example_text_hash_still_matches_its_text() -> None:
    import hashlib

    for source in examples.sources():
        assert source.text_hash == hashlib.sha256(source.text.encode()).hexdigest()
