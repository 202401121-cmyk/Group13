"""Sub-brands are brands a shopper can rule out (US-10, S4-25, P-26): "no POCO" and "no iQOO" are understood.

The brands the exclusions understand are the counting brands (config/reddit_quality.yaml) and every parent and sub-brand
in config/brand_families.yaml. The counting lists themselves don't change. Every request here is made up.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.decide import rules
from app.decide.rules import BrandFamily, brand_families
from app.intake import exclusions
from app.intake.exclusions import parse_exclusions
from app.research.reddit.quality import load_quality_settings


def found(text: str, **options: bool) -> list[tuple[str, str]]:
    return [(e.kind, e.value) for e in parse_exclusions(text, **options).exclusions]


@pytest.fixture
def fresh_brands() -> Iterator[None]:
    """The exclusions' brand list read anew, before and after: it is kept once read."""
    exclusions._brands.cache_clear()
    yield
    exclusions._brands.cache_clear()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("no POCO", [("brand", "POCO")], id="poco"),
        pytest.param("no iQOO", [("brand", "iQOO")], id="iqoo"),
        pytest.param("no poco or redmi", [("brand", "poco"), ("brand", "redmi")], id="two-sub-brands"),
        pytest.param("avoid Mi phones", [("brand", "Mi")], id="mi"),
        pytest.param("NO IQOO", [("brand", "IQOO")], id="capitals"),
        pytest.param("no Xiaomi", [("brand", "Xiaomi")], id="the-parent-still"),
        pytest.param("no vivo", [("brand", "vivo")], id="vivo-still"),
    ],
)
def test_a_sub_brand_is_a_brand(text: str, expected: list[tuple[str, str]]) -> None:
    assert found(text) == expected


def test_a_sub_brand_is_understood_in_a_request_too() -> None:
    assert found("a phone under 20k, no POCO", require_trigger=True) == [("brand", "POCO")]


def test_a_brand_in_neither_list_is_still_not_understood() -> None:
    result = parse_exclusions("no Lava")
    assert result.exclusions == []
    assert result.not_understood == ["Lava"]


@pytest.mark.usefixtures("fresh_brands")
def test_the_brand_list_is_the_counting_brands_and_the_families() -> None:
    """Sub-brands join the exclusions' list only: Reddit counting and matching read the counting lists as before."""
    counting = {brand.casefold() for brand in load_quality_settings().counting.brands}
    families = {name.casefold() for family in brand_families() for name in (family.parent, *family.sub_brands)}
    assert exclusions._brands() == frozenset(counting | families)
    assert {"poco", "iqoo", "mi"} <= exclusions._brands()


@pytest.mark.usefixtures("fresh_brands")
def test_a_broken_family_table_never_stops_intake(monkeypatch: pytest.MonkeyPatch) -> None:
    """Build rule 9: with config/brand_families.yaml unusable, the counting brands are still understood."""

    def broken() -> tuple[BrandFamily, ...]:
        raise OSError("made-up failure")

    monkeypatch.setattr(rules, "brand_families", broken)
    assert found("no Xiaomi") == [("brand", "Xiaomi")]
    assert parse_exclusions("no POCO").not_understood == ["POCO"]
