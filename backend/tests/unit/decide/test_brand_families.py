"""Ruling out a brand rules out the sub-brands sold under it (US-10, S4-25, P-26; S3-19: "no Xiaomi" kept POCO).

config/brand_families.yaml names each parent brand and its sub-brands. Ruling out a parent rules out its sub-brands, and
the reason says so ("POCO is a Xiaomi brand. You ruled out Xiaomi"); ruling out a sub-brand never rules out its parent.
A product whose brand is unknown is decided as before. Every request here is made up.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.contracts import Exclusion
from app.decide import decide, rules
from app.decide.rules import BRAND_FAMILIES_PATH, BrandFamily, brand_families
from tests.unit.decide.build import decide_input, filtered, group_of, known, product, rubric

A = "prd_alpha_one"


def _ruled_out(excluded: str, name: str, brand: str | None = None) -> tuple[str, list[str]]:
    """The product's group and its reasons, with `excluded` ruled out as a brand."""
    rb = rubric(exclusions=[Exclusion(kind="brand", value=excluded)])
    result = decide(decide_input(rb, [product(A, name, brand)], [known(A, "battery_life", score=7.0)]))
    group = group_of(result, A)
    return group, [] if group == "shown" else filtered(result, A)[1]


# --- a parent rules out its sub-brands ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "brand", "reason"),
    [
        pytest.param("POCO X8", None, "POCO is a Xiaomi brand. You ruled out Xiaomi", id="poco-by-name"),
        pytest.param("Redmi Note 15", None, "Redmi is a Xiaomi brand. You ruled out Xiaomi", id="redmi-by-name"),
        pytest.param("Mi 11X", None, "Mi is a Xiaomi brand. You ruled out Xiaomi", id="mi-by-name"),
        pytest.param("X8 Pro", "POCO", "POCO is a Xiaomi brand. You ruled out Xiaomi", id="poco-by-brand"),
        pytest.param("Xiaomi 15", None, "You ruled out Xiaomi", id="the-parent-itself"),
        pytest.param("Xiaomi POCO F6", None, "You ruled out Xiaomi", id="named-by-its-parent"),
    ],
)
def test_no_xiaomi_rules_out_its_sub_brands(name: str, brand: str | None, reason: str) -> None:
    assert _ruled_out("Xiaomi", name, brand) == ("filtered_out", [reason])


def test_no_vivo_rules_out_iqoo() -> None:
    assert _ruled_out("vivo", "iQOO Z10") == ("filtered_out", ["iQOO is a vivo brand. You ruled out vivo"])


def test_the_reason_reads_well_for_a_parent_starting_with_a_vowel(monkeypatch: pytest.MonkeyPatch) -> None:
    """A made-up family: a later one may start with a vowel, and the reason must still read right."""
    monkeypatch.setattr(rules, "brand_families", lambda: (BrandFamily(parent="Ondo", sub_brands=("Zest",)),))
    assert _ruled_out("Ondo", "Zest 5") == ("filtered_out", ["Zest is an Ondo brand. You ruled out Ondo"])


# --- a sub-brand never rules out its parent -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        pytest.param("Xiaomi 15", ("shown", []), id="keeps-the-parent"),
        pytest.param("Redmi Note 15", ("shown", []), id="keeps-a-sibling"),
        pytest.param("POCO X8", ("filtered_out", ["You ruled out POCO"]), id="rules-out-itself"),
        pytest.param("Xiaomi POCO F6", ("filtered_out", ["You ruled out POCO"]), id="named-after-its-parent"),
    ],
)
def test_no_poco_rules_out_poco_only(name: str, expected: tuple[str, list[str]]) -> None:
    assert _ruled_out("POCO", name) == expected


def test_no_poco_rules_out_a_poco_whose_brand_field_is_its_parent() -> None:
    assert _ruled_out("POCO", "POCO F6", brand="Xiaomi") == ("filtered_out", ["You ruled out POCO"])
    assert _ruled_out("POCO", "Note 15", brand="Xiaomi") == ("shown", [])


def test_no_iqoo_keeps_vivo() -> None:
    assert _ruled_out("iQOO", "vivo T5x") == ("shown", [])
    assert _ruled_out("iQOO", "iQOO Z10") == ("filtered_out", ["You ruled out iQOO"])


# --- unchanged ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "brand"),
    [
        pytest.param("X8 Pro", None, id="no-brand"),
        pytest.param("Nova Phone 5", None, id="made-up-name"),
        pytest.param("Nova Phone 5", "Infinix", id="another-brand"),
        pytest.param("Microsoft Surface Laptop 7", None, id="mi-is-a-whole-word"),
        pytest.param("Pocolo Phone 5", None, id="poco-is-a-whole-word"),
        pytest.param("Phone 5 for POCO fans", None, id="poco-later-in-the-name"),
    ],
)
def test_a_product_of_no_family_brand_is_decided_as_before(name: str, brand: str | None) -> None:
    assert _ruled_out("Xiaomi", name, brand) == ("shown", [])


# --- casing -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("excluded", "name", "brand", "reason"),
    [
        pytest.param("xiaomi", "poco x8", None, "POCO is a Xiaomi brand. You ruled out Xiaomi", id="lower"),
        pytest.param("XIAOMI", "Note 15", "REDMI", "Redmi is a Xiaomi brand. You ruled out Xiaomi", id="upper"),
        pytest.param("VIVO", "IQOO Z10", None, "iQOO is a vivo brand. You ruled out vivo", id="vivo-upper"),
        pytest.param("poco", "Poco X8", None, "You ruled out poco", id="sub-as-typed"),
    ],
)
def test_case_never_matters(excluded: str, name: str, brand: str | None, reason: str) -> None:
    assert _ruled_out(excluded, name, brand) == ("filtered_out", [reason])


def test_no_poco_in_capitals_still_keeps_xiaomi() -> None:
    assert _ruled_out("POCO", "XIAOMI 15") == ("shown", [])


# --- the table ----------------------------------------------------------------------------------------------------


def test_the_families_are_the_approved_ones() -> None:
    """Anything else is added only after Om approves (P-26): this test changes with the file."""
    assert brand_families() == (
        BrandFamily(parent="Xiaomi", sub_brands=("Redmi", "POCO", "Mi")),
        BrandFamily(parent="vivo", sub_brands=("iQOO",)),
    )
    assert BRAND_FAMILIES_PATH.name == "brand_families.yaml"


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("families:\n  Xiaomi: [Redmi]\n  Oppo: [redmi]\n", id="a-sub-brand-under-two-parents"),
        pytest.param("families:\n  Xiaomi: [Redmi]\n  Redmi: [Note]\n", id="a-sub-brand-that-is-a-parent"),
        pytest.param("families:\n  Xiaomi: ['  ']\n", id="an-empty-name"),
        pytest.param("families:\n  Xiaomi: []\n", id="a-parent-with-none"),
        pytest.param("families: {}\n", id="no-family"),
        pytest.param("families:\n  Xiaomi: [Redmi]\nother: 1\n", id="an-extra-key"),
        pytest.param("families:\n  Xiaomi: [Xiaomi]\n", id="its-own-sub-brand"),
        pytest.param("families:\n  Xiaomi: [Redmi, Mi]\n  Xiaomi: [POCO]\n", id="a-parent-written-twice"),
        pytest.param("families:\n  Xiaomi: [Redmi]\n  XIAOMI: [POCO]\n", id="a-parent-twice-in-other-case"),
    ],
)
def test_an_unusable_file_is_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "brand_families.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises((ValidationError, ValueError)):
        brand_families(path)


def test_a_broken_family_table_never_stops_decide(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Build rule 9: with config/brand_families.yaml unusable, a brand is still ruled out by its own name, and the
    error's type is logged, never its text."""

    def broken() -> tuple[BrandFamily, ...]:
        raise OSError("made-up failure")

    monkeypatch.setattr(rules, "brand_families", broken)
    assert rules.known_brand_families() == ()
    assert _ruled_out("Xiaomi", "Xiaomi 15") == ("filtered_out", ["You ruled out Xiaomi"])
    assert _ruled_out("Xiaomi", "POCO X8") == ("shown", [])
    assert "brand families not read: OSError" in caplog.text
    assert "made-up failure" not in caplog.text
