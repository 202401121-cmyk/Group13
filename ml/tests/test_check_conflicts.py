"""The conflicts file checker (US-16, #30): the format, every rule and the command line. All synthetic."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

import pytest
from check_conflicts import HEADER, TIERS, CheckResult, check_file, main
from conflict_rows import as_real, claim_rows, csv_lines, header_line, text_rows, write_conflicts

ML = Path(__file__).resolve().parents[1]
SCRIPT = ML / "scripts" / "check_conflicts.py"
EXAMPLES = ML / "examples" / "conflicts_synthetic.csv"
REAL_FILE = ML / "labelling" / "conflicts.csv"
BACKEND_CONTRACTS = ML.parent / "backend" / "app" / "contracts" / "common.py"
TODAY = date(2026, 11, 8)


def _check(tmp_path: Path, rows: Sequence[Mapping[str, str]]) -> CheckResult:
    return check_file(write_conflicts(tmp_path / "conflicts.csv", rows), today=TODAY)


def _has_error(result: CheckResult, line: int, *fragments: str) -> bool:
    """True when one error on exactly this line holds every fragment."""
    prefix = f"{result.path}:{line}: "
    return any(error.startswith(prefix) and all(part in error for part in fragments) for error in result.errors)


def _assert_error(result: CheckResult, line: int, *fragments: str) -> None:
    assert _has_error(result, line, *fragments), result.errors


def _assert_clean(result: CheckResult) -> None:
    assert result.errors == []


# --- clean files -------------------------------------------------------------------------------------


def test_the_synthetic_examples_pass() -> None:
    result = check_file(EXAMPLES)
    _assert_clean(result)
    assert len(result.conflicts) == 9
    assert result.rows_checked == 26


def test_the_real_conflicts_file_passes() -> None:
    """Whatever has been entered so far: a bad row in the real file fails CI here, before anyone runs the baselines."""
    _assert_clean(check_file(REAL_FILE))


def test_a_clean_conflict_keeps_its_fields_and_claims(tmp_path: Path) -> None:
    result = _check(tmp_path, claim_rows())
    _assert_clean(result)
    conflict = result.conflicts[0]
    assert (conflict.line, conflict.conflict_id, conflict.product, conflict.unit) == (2, "syn001", "Nova Book 14", "h")
    assert conflict.known_answer == "10 h"
    assert [(claim.line, claim.tier, claim.value) for claim in conflict.claims] == [
        (2, "manufacturer", "10 h"),
        (3, "editorial", "12 h"),
    ]
    assert conflict.claims[0].retrieved_at == date(2026, 11, 1)


# --- damage to the file ------------------------------------------------------------------------------


def test_a_bad_header_names_the_wrong_column(tmp_path: Path) -> None:
    path = tmp_path / "conflicts.csv"
    path.write_text(header_line().replace("claim_tier", "tier") + "".join(csv_lines(claim_rows())), encoding="utf-8")
    result = check_file(path, today=TODAY)
    _assert_error(result, 1, "header column 12", "'tier'", "'claim_tier'")


def test_a_header_with_too_few_columns_is_named(tmp_path: Path) -> None:
    path = tmp_path / "conflicts.csv"
    path.write_text("conflict_id,category\n", encoding="utf-8")
    _assert_error(check_file(path), 1, "2 columns", f"expected {len(HEADER)}")


def test_a_missing_file_is_named(tmp_path: Path) -> None:
    result = check_file(tmp_path / "nowhere.csv")
    _assert_error(result, 1, "file not found")
    assert result.missing


def test_an_empty_file_is_named(tmp_path: Path) -> None:
    path = tmp_path / "conflicts.csv"
    path.write_bytes(b"")
    _assert_error(check_file(path), 1, "empty")


def test_a_file_not_in_utf8_is_named(tmp_path: Path) -> None:
    path = tmp_path / "conflicts.csv"
    path.write_bytes(header_line().encode("utf-8") + "".join(csv_lines(claim_rows())).encode("utf-8") + b"\xff\xfe\n")
    _assert_error(check_file(path), 4, "not UTF-8")


def test_a_bad_byte_after_a_byte_order_mark_names_the_right_line(tmp_path: Path) -> None:
    """Excel's "CSV UTF-8" writes the mark, and it must not shift the count: the bad byte starts line 3."""
    path = tmp_path / "conflicts.csv"
    path.write_bytes(b"\xef\xbb\xbf" + (header_line() + csv_lines(claim_rows())[0]).encode("utf-8") + b"\xff\n")
    _assert_error(check_file(path), 3, "not UTF-8")


def test_a_bad_byte_in_a_file_with_old_mac_line_endings_names_the_right_line(tmp_path: Path) -> None:
    path = tmp_path / "conflicts.csv"
    text = (header_line() + csv_lines(claim_rows())[0]).replace("\n", "\r")
    path.write_bytes(text.encode("utf-8") + b"\xff\r")
    _assert_error(check_file(path), 3, "not UTF-8")


@pytest.mark.parametrize(
    ("damaged", "fragment"),
    [
        ("c001,laptop,too,few\n", "expected 15 columns, found 4"),
        ('c001,laptop,"Nova Book 14,battery_life,h\n', "quote"),
        ("c001,lap\x00top,Nova Book 14\n", "NUL"),
    ],
    ids=["wrong_column_count", "unclosed_quote", "nul"],
)
def test_a_corrupt_line_is_reported_with_its_line_number(tmp_path: Path, damaged: str, fragment: str) -> None:
    """Line 4 is damaged. Line 5 breaks a rule of its own and must still be found: one bad line hides nothing."""
    rows = claim_rows([("manufacturer", "10 h"), ("editorial", "12 h"), ("blog", "9 h")])
    lines = csv_lines(rows)
    path = tmp_path / "conflicts.csv"
    path.write_bytes((header_line() + lines[0] + lines[1] + damaged + lines[2]).encode("utf-8"))
    result = check_file(path, today=TODAY)
    _assert_error(result, 4, fragment)
    _assert_error(result, 5, "claim_tier", "'blog'")


def test_a_cell_with_a_line_break_is_reported_at_its_first_line(tmp_path: Path) -> None:
    rows = claim_rows()
    lines = csv_lines(rows)
    path = tmp_path / "conflicts.csv"
    path.write_bytes((header_line() + lines[0] + 'c001,"two\nlines",x\n' + lines[1]).encode("utf-8"))
    result = check_file(path, today=TODAY)
    _assert_error(result, 3, "quote")


def test_every_error_names_its_line_and_the_rest_are_still_found(tmp_path: Path) -> None:
    rows = claim_rows([("manufacturer", "10 h"), ("blog", "12 h"), ("editorial", "12 h"), ("lab", "11 h")])
    rows[2]["claim_retrieved_at"] = "not a date"
    lines = csv_lines(rows)
    path = tmp_path / "conflicts.csv"
    path.write_bytes((header_line() + lines[0] + lines[1] + lines[2] + "broken,line\n" + lines[3]).encode("utf-8"))
    result = check_file(path, today=TODAY)
    _assert_error(result, 3, "claim_tier")
    _assert_error(result, 4, "claim_retrieved_at")
    _assert_error(result, 5, "columns")
    numbers = [int(re.findall(r"\.csv:([0-9]+): ", error)[0]) for error in result.errors]
    assert numbers == sorted(numbers)


def test_line_numbers_count_blank_lines_and_a_byte_order_mark_is_fine(tmp_path: Path) -> None:
    rows = claim_rows([("manufacturer", "10 h"), ("blog", "12 h")])
    lines = csv_lines(rows)
    path = tmp_path / "conflicts.csv"
    path.write_bytes(("\ufeff" + header_line() + lines[0] + ",,,\n\n" + lines[1]).encode("utf-8"))
    result = check_file(path, today=TODAY)
    assert len(result.errors) == 1
    _assert_error(result, 5, "claim_tier")


def test_windows_line_endings_are_fine(tmp_path: Path) -> None:
    path = tmp_path / "conflicts.csv"
    text = header_line() + "".join(csv_lines(claim_rows()))
    path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    _assert_clean(check_file(path, today=TODAY))


def test_spaces_around_a_cell_are_ignored(tmp_path: Path) -> None:
    rows = claim_rows()
    rows[0]["product"] = " Nova Book 14 "
    rows[1]["claim_value"] = " 12 h "
    result = _check(tmp_path, rows)
    _assert_clean(result)
    assert result.conflicts[0].product == "Nova Book 14"
    assert result.conflicts[0].claims[1].value == "12 h"


# --- required fields and claim count -----------------------------------------------------------------


@pytest.mark.parametrize(
    "column",
    [
        "conflict_id",
        "category",
        "product",
        "criterion",
        "known_answer",
        "how_established",
        "added_by",
        "claim_url",
        "claim_site",
        "claim_tier",
        "claim_value",
        "claim_retrieved_at",
    ],
)
def test_each_required_field_is_named_when_empty(tmp_path: Path, column: str) -> None:
    result = _check(tmp_path, claim_rows(**{column: "  "}))
    _assert_error(result, 2, column, "empty")
    assert result.conflicts == []


def test_a_conflict_with_one_claim_is_refused(tmp_path: Path) -> None:
    result = _check(tmp_path, claim_rows([("manufacturer", "10 h")]))
    _assert_error(result, 2, "syn001", "1 claim", "at least 2")
    assert result.conflicts == []


# --- tiers -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["store", "Manufacturer", "blog", "MANUFACTURER", "RETAILER"])
def test_an_unknown_tier_is_refused(tmp_path: Path, tier: str) -> None:
    result = _check(tmp_path, claim_rows([("manufacturer", "10 h"), (tier, "12 h")]))
    _assert_error(result, 3, "claim_tier", repr(tier), "manufacturer, lab, editorial, community, retailer")


def test_each_known_tier_is_accepted(tmp_path: Path) -> None:
    assert TIERS == ("manufacturer", "lab", "editorial", "community", "retailer")
    _assert_clean(_check(tmp_path, claim_rows([(tier, f"{10 + index} h") for index, tier in enumerate(TIERS)])))


def _backend_tiers() -> list[str]:
    """SourceTier's members, lowest number first, read from its source so that no backend code is imported."""
    tree = ast.parse(BACKEND_CONTRACTS.read_text(encoding="utf-8"))
    source_tier = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "SourceTier")
    numbers: dict[str, int] = {}
    for node in source_tier.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            target = node.targets[0]
            number = node.value.value
            assert isinstance(target, ast.Name)
            assert isinstance(number, int)
            numbers[target.id.lower()] = number
    assert len(set(numbers.values())) == len(numbers), "two tiers share a number, so one is only another name"
    return sorted(numbers, key=numbers.__getitem__)


def test_the_tiers_match_the_backends_list_and_order() -> None:
    """The evaluation holds a copy, so a change to SourceTier in the backend fails here until the copy follows it."""
    assert list(TIERS) == _backend_tiers()


def test_the_examples_hold_a_retailer_claim() -> None:
    claims = [claim for conflict in check_file(EXAMPLES).conflicts for claim in conflict.claims]
    assert any(claim.tier == "retailer" for claim in claims)


# --- numbers and units -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "line"),
    [("known_answer", 2), ("claim_value", 3)],
)
def test_a_number_needs_its_unit(tmp_path: Path, column: str, line: int) -> None:
    rows = claim_rows()
    if column == "known_answer":
        for row in rows:
            row[column] = "10"
    else:
        rows[1][column] = "12"
    result = _check(tmp_path, rows)
    _assert_error(result, line, column, "'1", "no unit")


def test_units_that_differ_are_an_input_error(tmp_path: Path) -> None:
    """The spec's own example: 10 h against 600 min is the same time, and is still refused rather than converted."""
    result = _check(tmp_path, claim_rows([("manufacturer", "10 h"), ("editorial", "600 min")]))
    _assert_error(result, 3, "claim_value", "'600 min'", "'min'", "'h'", "never converted")
    assert result.conflicts == []


def test_a_known_answer_in_another_unit_is_an_input_error(tmp_path: Path) -> None:
    result = _check(tmp_path, claim_rows(known_answer="600 min"))
    _assert_error(result, 2, "known_answer", "'600 min'", "'h'")


@pytest.mark.parametrize(
    "values",
    [("10 h", "600 min"), ("10h", "600min")],
    ids=["with_spaces", "without_spaces"],
)
def test_units_that_differ_with_a_blank_unit_column_are_an_input_error(tmp_path: Path, values: tuple[str, str]) -> None:
    rows = claim_rows(
        [("manufacturer", values[0]), ("editorial", values[1])], unit="", known_answer=values[0], tolerance=""
    )
    result = _check(tmp_path, rows)
    assert result.conflicts == []
    assert any("fill in the unit column" in error for error in result.errors), result.errors


def test_a_unit_differing_only_in_case_is_an_input_error(tmp_path: Path) -> None:
    """Gb is gigabits and GB is gigabytes, so case counts."""
    rows = claim_rows(
        [("manufacturer", "16 GB"), ("editorial", "16 Gb")], unit="GB", known_answer="16 GB", tolerance="0"
    )
    _assert_error(_check(tmp_path, rows), 3, "'Gb'", "'GB'")


@pytest.mark.parametrize(
    "value",
    ["5,000 h", "5,000h", "about 10 h", "1e3 h", "NaN h", "Infinity h", "1.2.3 h", ".5 h", "٣ h", "h", "10 h 30 min"],
)
def test_badly_written_numbers_are_refused(tmp_path: Path, value: str) -> None:
    result = _check(tmp_path, claim_rows([("manufacturer", "10 h"), ("editorial", value)]))
    _assert_error(result, 3, "claim_value", repr(value))


@pytest.mark.parametrize(
    "value",
    ["11h", "10.5 h", "-5 h", "0 h", "11\u00a0h"],
    ids=["no_space", "decimal", "negative", "zero", "web_page_space"],
)
def test_well_written_numbers_pass(tmp_path: Path, value: str) -> None:
    _assert_clean(_check(tmp_path, claim_rows([("manufacturer", "10 h"), ("editorial", value)])))


@pytest.mark.parametrize("unit", ["hours per charge", "m2"], ids=["space", "digit"])
def test_a_unit_with_a_digit_or_a_space_is_refused(tmp_path: Path, unit: str) -> None:
    _assert_error(_check(tmp_path, claim_rows(unit=unit)), 2, "unit", repr(unit), "no digits")


@pytest.mark.parametrize(
    "values",
    [("5G", "4G"), ("5G", "5g"), ("Wi-Fi 6", "Wi-Fi 5"), ("2 x USB-C", "1 x USB-C"), ("1920 x 1080", "2560 x 1600")],
    ids=["network", "same_ending_in_another_case", "wifi", "ports", "resolution"],
)
def test_a_text_value_such_as_5g_is_not_taken_for_a_number(tmp_path: Path, values: tuple[str, str]) -> None:
    _assert_clean(_check(tmp_path, text_rows(values, known_answer=values[0])))


def test_a_bare_number_in_a_text_conflict_is_refused(tmp_path: Path) -> None:
    result = _check(tmp_path, text_rows(("USB-C", "2")))
    _assert_error(result, 3, "claim_value", "'2'", "fill in the unit column")


def test_a_number_with_a_unit_in_a_text_conflict_is_refused(tmp_path: Path) -> None:
    """Same unit on both, so no difference to spot, but a number needs its tolerance."""
    result = _check(tmp_path, text_rows(("10 h", "12 h"), known_answer="10 h"))
    _assert_error(result, 2, "fill in the unit column")


@pytest.mark.parametrize(
    "values",
    [
        ("5,000 mAh", "4,500 mAh"),
        ("5,000mAh", "4,500mAh"),
        ("1,00,000 ₹", "90,000 ₹"),
        ("about 10 h", "about 600 min"),
        ("Up to 10 h", "12 hours"),
        ("~10h", "~12h"),
        ("₹62,990", "₹59,990"),
        ("₹62990", "₹59990"),
        ("$750", "$700"),
        ("Rs 62990", "Rs. 59990"),
        ("5,000", "4,500"),
        ("~10", "~12"),
        ("about 10", "about 12"),
        ("10-12 h", "12-14 h"),
        ("10 to 12 h", "12 to 14 h"),
        ("10+ h", "12+ h"),
        ("over 10 h", "under 12 h"),
        ("Upto 10 hours", "12 hours"),
        ("US$750", "US$700"),
        ("¥750", "¥700"),
    ],
    ids=[
        "commas",
        "commas_no_space",
        "indian_commas",
        "about",
        "up_to",
        "tilde",
        "price_with_commas",
        "price",
        "dollars",
        "rupees_in_letters",
        "commas_no_unit",
        "tilde_no_unit",
        "about_no_unit",
        "range",
        "range_in_words",
        "plus",
        "over",
        "upto_as_one_word",
        "us_dollars",
        "yen",
    ],
)
def test_an_amount_written_another_way_in_a_text_conflict_is_refused(tmp_path: Path, values: tuple[str, str]) -> None:
    """Plainly amounts, which a number conflict refuses too. Whatever this list misses still gets the warning below."""
    result = _check(tmp_path, text_rows(values, known_answer=values[0]))
    _assert_error(result, 2, "known_answer", repr(values[0]), "fill in the unit column")
    assert result.conflicts == []


@pytest.mark.parametrize(
    "values",
    [
        ("10h", "12h"),
        ("5G", "4G"),
        ("USB 3.2", "USB 2.0"),
        ("10 h 30 min", "12 h 15 min"),
        ("₹59,990 - ₹62,990", "₹60,000 - ₹65,000"),
        ("5000 mAh battery", "4500 mAh battery"),
        ("1.6 kg approx", "1.9 kg approx"),
    ],
    ids=["an_amount", "a_name", "a_longer_name", "two_units", "price_range", "with_a_word_after", "approx_after"],
)
def test_values_holding_a_number_warn_in_a_text_conflict(tmp_path: Path, values: tuple[str, str]) -> None:
    """A program can't tell 10h from 5G, and no list of spellings is complete, so a person decides. Never silent."""
    result = _check(tmp_path, text_rows(values, known_answer=values[0]))
    _assert_clean(result)
    assert len(result.warnings) == 1
    assert result.warnings[0].startswith(f"{result.path}:2: warning: ")
    assert "hold numbers" in result.warnings[0]


def test_one_value_with_a_number_is_enough_to_warn(tmp_path: Path) -> None:
    result = _check(tmp_path, text_rows(("USB-C", "USB 3.2")))
    assert any("hold numbers" in warning for warning in result.warnings)


def test_ordinary_text_values_do_not_warn_about_amounts(tmp_path: Path) -> None:
    assert _check(tmp_path, text_rows()).warnings == []


def test_a_number_in_a_text_conflict_is_told_how_to_keep_it_as_a_name(tmp_path: Path) -> None:
    """3.5 mm against USB-C: filling in the unit column can't work here, so the message offers the way that does."""
    result = _check(tmp_path, text_rows(("3.5 mm", "USB-C"), known_answer="USB-C"))
    _assert_error(result, 2, "claim_value", "'3.5 mm'", "without the space", "in words")


@pytest.mark.parametrize(
    "values",
    [
        ("3.5mm", "USB-C"),
        ("2.4GHz", "5GHz"),
        ("16:9", "16:10"),
        ("over-ear", "on-ear"),
        ("RS-232", "HDMI"),
        ("RS232", "HDMI"),
        ("INR18650", "ICR18650"),
    ],
    ids=["jack", "band", "ratio", "starts_with_a_listed_word", "port_with_dash", "port", "cell_type"],
)
def test_names_with_numbers_pass_as_text(tmp_path: Path, values: tuple[str, str]) -> None:
    _assert_clean(_check(tmp_path, text_rows(values, known_answer=values[1])))


@pytest.mark.parametrize("values", [("802.11ac", "802.11ax"), ("1080p", "4K")], ids=["wifi_standard", "resolution"])
def test_names_with_different_endings_are_asked_for_in_words(tmp_path: Path, values: tuple[str, str]) -> None:
    """These read just like 10h against 600min, which must never pass, so the person is told the way out."""
    result = _check(tmp_path, text_rows(values, known_answer=values[0]))
    _assert_error(result, 3, "claim_value", repr(values[1]), "in words")


# --- tolerance ---------------------------------------------------------------------------------------


def test_tolerance_is_required_for_a_number(tmp_path: Path) -> None:
    _assert_error(_check(tmp_path, claim_rows(tolerance="")), 2, "tolerance", "needs")


def test_a_tolerance_of_zero_is_allowed(tmp_path: Path) -> None:
    result = _check(tmp_path, claim_rows(tolerance="0"))
    _assert_clean(result)
    assert result.conflicts[0].tolerance == 0


@pytest.mark.parametrize("tolerance", ["-1", "abc", "0.5 h", "1,000", "NaN", "1e2"])
def test_tolerance_must_be_a_plain_number_of_zero_or_more(tmp_path: Path, tolerance: str) -> None:
    _assert_error(_check(tmp_path, claim_rows(tolerance=tolerance)), 2, "tolerance", repr(tolerance))


def test_a_text_answer_has_no_tolerance(tmp_path: Path) -> None:
    _assert_error(_check(tmp_path, text_rows(tolerance="1")), 2, "tolerance", "text")


# --- Reddit (D-53) -----------------------------------------------------------------------------------

REDDIT = "https://www.reddit.com/r/laptops/comments/zzz000/"


def _with_reddit_claim(
    url: str = REDDIT, tier: str = "community", site: str = "reddit.com", quote: str = ""
) -> list[dict[str, str]]:
    rows = claim_rows()
    rows[1].update(claim_url=url, claim_tier=tier, claim_site=site, claim_quote=quote)
    return rows


def test_a_reddit_claim_with_a_quote_is_refused(tmp_path: Path) -> None:
    result = _check(tmp_path, _with_reddit_claim(quote="Mine lasts 12 hours."))
    _assert_error(result, 3, "claim_quote", "Reddit", "D-53")


@pytest.mark.parametrize("tier", ["editorial", "retailer"])
def test_a_reddit_claim_must_be_tier_community(tmp_path: Path, tier: str) -> None:
    _assert_error(_check(tmp_path, _with_reddit_claim(tier=tier)), 3, "community", "Reddit")


def test_a_reddit_claim_must_name_the_site_reddit_com(tmp_path: Path) -> None:
    _assert_error(_check(tmp_path, _with_reddit_claim(site="old.reddit.com")), 3, "claim_site", "reddit.com")


@pytest.mark.parametrize(
    "url",
    [
        REDDIT,
        "https://old.reddit.com/r/laptops/comments/zzz000/",
        "https://redd.it/zzz000",
        "HTTPS://WWW.REDDIT.COM/r/laptops/comments/zzz000/",
    ],
    ids=["www", "old", "short_link", "upper_case"],
)
def test_a_reddit_claim_without_a_quote_passes(tmp_path: Path, url: str) -> None:
    _assert_clean(_check(tmp_path, _with_reddit_claim(url=url)))


@pytest.mark.parametrize(
    "url",
    [
        "https://web.archive.org/web/2026/https://www.reddit.com/r/laptops/comments/zzz000/",
        "https://arctic-shift.photon-reddit.com/r/laptops/comments/zzz000",
        "https://api.pullpush.io/reddit/search/comment?link_id=zzz000",
        "https://api.pushshift.io/reddit/comment/search?ids=abc",
        "https://web.archive.org/web/2026/https%3A%2F%2Freddit.com%2Fr%2Flaptops%2Fcomments%2Fzzz000",
        "https://reddit.app.link/abc",
        "https://www.reveddit.com/v/laptops/comments/zzz000/",
        "https://unddit.com/r/laptops/comments/zzz000/",
        "https://teddit.net/r/laptops/comments/zzz000/",
    ],
    ids=[
        "web_archive_copy",
        "arctic_shift",
        "pullpush",
        "pushshift",
        "encoded_web_archive_copy",
        "share_link",
        "reveddit",
        "unddit",
        "teddit",
    ],
)
def test_an_archive_copy_of_reddit_text_counts_as_reddit(tmp_path: Path, url: str) -> None:
    """The text is still Reddit text, so the D-53 rules hold whichever host it was read from."""
    _assert_clean(_check(tmp_path, _with_reddit_claim(url=url)))
    _assert_error(_check(tmp_path, _with_reddit_claim(url=url, quote="Mine lasts 12 hours.")), 3, "claim_quote", "D-53")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.reddit.com/user/someone/comments/zzz000/",
        "https://www.reddit.com/u/someone",
        "https://www.reddit.com/U/someone",
        "https://www.reddit.com/r/u_someone/comments/zzz000/",
        "https://api.pullpush.io/reddit/search/comment?author=someone",
        "https://web.archive.org/web/2026/https%3A%2F%2Fwww.reddit.com%2Fuser%2Fsomeone",
        "https://www.reveddit.com/y/someone/",
        "https://www.reddit.com/search?q=author%3Asomeone",
    ],
    ids=["user", "u", "upper_case", "profile_community", "author_search", "encoded_copy", "reveddit", "search_words"],
)
def test_a_reddit_link_to_a_persons_profile_is_refused(tmp_path: Path, url: str) -> None:
    _assert_error(_check(tmp_path, _with_reddit_claim(url=url)), 3, "claim_url", "profile")


def test_a_tracking_parameter_does_not_make_a_page_a_reddit_claim(tmp_path: Path) -> None:
    rows = claim_rows(how_established="https://nova.example/specs?utm_source=reddit.com")
    rows[1].update(claim_url="https://site2.example/review?utm_source=reddit.com", claim_quote="It lasts 12 hours.")
    _assert_clean(_check(tmp_path, rows))


@pytest.mark.parametrize(
    "tracking",
    ["utm_source=reddit", "utm_source=reddit.com", "ref=reddit", "ref=reddit.com"],
)
def test_a_tracking_parameter_does_not_make_how_established_a_reddit_source(tmp_path: Path, tracking: str) -> None:
    _assert_clean(_check(tmp_path, claim_rows(how_established=f"https://nova.example/specs?{tracking}")))


def test_a_reddit_link_inside_another_query_still_counts(tmp_path: Path) -> None:
    url = "https://mirror.example/view?url=https://www.reddit.com/r/laptops/comments/zzz000/&utm_source=x"
    _assert_error(_check(tmp_path, _with_reddit_claim(url=url, quote="Mine lasts 12 hours.")), 3, "claim_quote", "D-53")


def test_a_reddit_claim_keeps_its_value_never_the_posters_words(tmp_path: Path) -> None:
    rows = text_rows(("USB-C", "USB-C"))
    rows[1].update(
        claim_url=REDDIT,
        claim_site="reddit.com",
        claim_tier="community",
        claim_value="mine died after a year, the port is micro usb lol",
    )
    _assert_error(_check(tmp_path, rows), 3, "claim_value", "Reddit", "D-53")


def test_a_short_value_on_a_reddit_claim_passes(tmp_path: Path) -> None:
    rows = text_rows(("USB-C", "USB-C"))
    rows[1].update(claim_url=REDDIT, claim_site="reddit.com", claim_tier="community", claim_value="Micro-USB")
    _assert_clean(_check(tmp_path, rows))


@pytest.mark.parametrize(
    ("url", "site"),
    [("https://notreddit.com/a", "notreddit.com"), ("https://reddit.com.example/a", "reddit.com.example")],
    ids=["suffix", "prefix"],
)
def test_a_lookalike_host_is_not_taken_for_reddit(tmp_path: Path, url: str, site: str) -> None:
    rows = claim_rows()
    rows[1].update(claim_url=url, claim_site=site, claim_tier="editorial", claim_quote="It lasts 12 hours.")
    _assert_clean(_check(tmp_path, rows))


@pytest.mark.parametrize(
    "text",
    [
        REDDIT,
        "From www.reddit.com/r/laptops",
        "See https://redd.it/zzz000 for this",
        "REDDIT.COM thread",
        "A post on Reddit in r/laptops said 10 h",
        "Read it on reddit.",
        "https://web.archive.org/web/2026/https%3A%2F%2Freddit.com%2Fr%2Flaptops",
    ],
)
def test_a_reddit_post_cannot_establish_the_known_answer(tmp_path: Path, text: str) -> None:
    _assert_error(_check(tmp_path, claim_rows(how_established=text)), 2, "how_established", "Reddit")


@pytest.mark.parametrize("text", ["https://notreddit.com/a", "https://reddit.com.example/a"], ids=["suffix", "prefix"])
def test_a_lookalike_in_how_established_is_fine(tmp_path: Path, text: str) -> None:
    _assert_clean(_check(tmp_path, claim_rows(how_established=text)))


@pytest.mark.parametrize(
    "text", ["Timed the r/w speed by hand", "Read the motor label: 1400 r/min", "https://nova.example/r/manual"]
)
def test_text_like_r_slash_is_not_taken_for_reddit(tmp_path: Path, text: str) -> None:
    _assert_clean(_check(tmp_path, claim_rows(how_established=text)))


@pytest.mark.parametrize(
    "text",
    ["https://[paste link here]", "See https://", "http://", "https://<paste link here>", "https://paste link here"],
)
def test_a_link_in_how_established_that_cannot_be_read_is_named(tmp_path: Path, text: str) -> None:
    _assert_error(_check(tmp_path, claim_rows(how_established=text)), 2, "how_established", "link")


@pytest.mark.parametrize(
    "text",
    [
        "[https://nova.example]",
        "See https://nova.example.",
        "(https://nova.example/specs)",
        "<https://nova.example>",
        "“https://nova.example”",
    ],
)
def test_punctuation_around_a_link_in_how_established_is_fine(tmp_path: Path, text: str) -> None:
    _assert_clean(_check(tmp_path, claim_rows(how_established=text)))


# --- quotes ------------------------------------------------------------------------------------------


def _with_quote(quote: str) -> list[dict[str, str]]:
    rows = claim_rows()
    rows[0]["claim_quote"] = quote
    return rows


def test_a_long_quote_is_refused(tmp_path: Path) -> None:
    _assert_error(_check(tmp_path, _with_quote("a" * 201)), 2, "claim_quote", "201", "200")


def test_a_quote_of_200_characters_passes(tmp_path: Path) -> None:
    _assert_clean(_check(tmp_path, _with_quote("a" * 200)))


@pytest.mark.parametrize(
    "quote",
    [
        "It lasts 10 hours. The screen is bright.",
        "Is it good? Yes it is.",
        'It lasts "10 hours." The screen is bright.',
    ],
)
def test_a_two_sentence_quote_is_refused(tmp_path: Path, quote: str) -> None:
    _assert_error(_check(tmp_path, _with_quote(quote)), 2, "claim_quote", "one short sentence")


@pytest.mark.parametrize(
    "quote",
    ["It weighs 1.6 kg and lasts 10.5 hours.", "Rated at approx. 10 hours.", "It lasts 10 hours!"],
)
def test_a_quote_with_a_decimal_is_one_sentence(tmp_path: Path, quote: str) -> None:
    _assert_clean(_check(tmp_path, _with_quote(quote)))


# --- one conflict, many rows -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "other"),
    [
        ("category", "phone"),
        ("product", "Nova Book 15"),
        ("criterion", "weight"),
        ("known_answer", "11 h"),
        ("tolerance", "1"),
        ("how_established", "Asked the shop"),
        ("added_by", "Sam"),
    ],
)
def test_conflict_fields_must_agree_on_every_row(tmp_path: Path, column: str, other: str) -> None:
    rows = claim_rows()
    rows[1][column] = other
    result = _check(tmp_path, rows)
    _assert_error(result, 3, column, repr(other), "line 2")
    assert result.conflicts == []


def test_rows_of_a_conflict_may_be_sorted_apart(tmp_path: Path) -> None:
    """Sorting in a spreadsheet must not break a file: rows join by conflict_id, not by position."""
    first = claim_rows()
    second = claim_rows(conflict_id="syn002", criterion="weight", unit="kg", known_answer="1.5 kg")
    second[0]["claim_value"] = "1.5 kg"
    second[1]["claim_value"] = "1.9 kg"
    result = _check(tmp_path, [first[0], second[0], first[1], second[1]])
    _assert_clean(result)
    assert [conflict.conflict_id for conflict in result.conflicts] == ["syn001", "syn002"]
    assert [claim.line for claim in result.conflicts[0].claims] == [2, 4]


@pytest.mark.parametrize(
    "second_url",
    [
        "https://site1.example/page",
        "https://site1.example/page/",
        "http://SITE1.example/page",
        "https://site1.example/page#results",
        "https://www.site1.example/page",
        "https://site1.example/page?utm_source=x&utm_medium=y",
        "https://site1.example/page?ref=x",
    ],
    ids=["exact", "trailing_slash", "scheme_and_case", "fragment", "www", "tracking", "referrer"],
)
def test_the_same_url_twice_in_a_conflict_is_refused(tmp_path: Path, second_url: str) -> None:
    rows = claim_rows()
    rows[1].update(claim_url=second_url, claim_site="site1.example")
    _assert_error(_check(tmp_path, rows), 3, "claim_url", "line 2")


def test_two_pages_of_one_site_are_two_claims(tmp_path: Path) -> None:
    rows = claim_rows()
    rows[1].update(claim_url="https://site1.example/other?id=2", claim_site="site1.example")
    _assert_clean(_check(tmp_path, rows))


def test_a_query_that_names_another_page_is_another_page(tmp_path: Path) -> None:
    rows = claim_rows()
    rows[0].update(claim_url="https://site1.example/page?id=1&utm_source=x")
    rows[1].update(claim_url="https://site1.example/page?id=2&utm_source=x", claim_site="site1.example")
    _assert_clean(_check(tmp_path, rows))


def _two_reddit_claims(first: str, second: str) -> list[dict[str, str]]:
    rows = claim_rows([("community", "10 h"), ("community", "12 h")], claim_site="reddit.com")
    rows[0]["claim_url"] = first
    rows[1]["claim_url"] = second
    return rows


@pytest.mark.parametrize(
    "second",
    [
        "https://old.reddit.com/r/laptops/comments/zzz000/",
        "https://redd.it/zzz000",
        "https://www.reddit.com/r/laptops/comments/ZZZ000/some_title/",
    ],
    ids=["old_host", "short_link", "with_its_title"],
)
def test_the_same_reddit_thread_twice_is_refused(tmp_path: Path, second: str) -> None:
    _assert_error(_check(tmp_path, _two_reddit_claims(REDDIT, second)), 3, "claim_url", "line 2")


@pytest.mark.parametrize(
    "second",
    [
        "https://www.reveddit.com/v/laptops/comments/zzz000/",
        "https://web.archive.org/web/2026/https://www.reddit.com/r/laptops/comments/zzz000/",
        "https://web.archive.org/web/2026/https%3A%2F%2Fwww.reddit.com%2Fr%2Flaptops%2Fcomments%2Fzzz000",
    ],
    ids=["copy_site", "web_archive", "encoded_web_archive"],
)
def test_a_reddit_thread_and_its_archive_copy_are_one_page(tmp_path: Path, second: str) -> None:
    _assert_error(_check(tmp_path, _two_reddit_claims(REDDIT, second)), 3, "claim_url", "line 2")


@pytest.mark.parametrize(
    "second",
    [
        "https://api.pullpush.io/reddit/search/comment?link_id=zzz000",
        "https://arctic-shift.photon-reddit.com/api/comments/search?link_id=zzz000",
        "https://arctic-shift.photon-reddit.com/api/comments/tree?link_id=t3_zzz000",
    ],
    ids=["pullpush", "arctic_shift", "arctic_shift_tree"],
)
def test_a_reddit_thread_and_an_archive_search_for_it_are_one_page(tmp_path: Path, second: str) -> None:
    _assert_error(_check(tmp_path, _two_reddit_claims(REDDIT, second)), 3, "claim_url", "line 2")


def test_archive_searches_for_two_threads_are_two_claims(tmp_path: Path) -> None:
    search = "https://arctic-shift.photon-reddit.com/api/comments/search?link_id="
    _assert_clean(_check(tmp_path, _two_reddit_claims(f"{search}aaa111", f"{search}bbb222")))


def test_a_comment_path_on_two_other_sites_is_two_pages(tmp_path: Path) -> None:
    """Only a Reddit link has a thread id to compare. Forums use the same path for their own pages."""
    rows = claim_rows()
    rows[0].update(claim_url="https://forum-a.example/comments/123", claim_site="forum-a.example")
    rows[1].update(claim_url="https://forum-b.example/comments/123", claim_site="forum-b.example")
    _assert_clean(_check(tmp_path, rows))


@pytest.mark.parametrize("middle", ["some_title", "some_title/comment"], ids=["old_form", "new_form"])
def test_two_comments_in_a_reddit_thread_are_two_claims(tmp_path: Path, middle: str) -> None:
    thread = f"https://www.reddit.com/r/laptops/comments/zzz000/{middle}"
    _assert_clean(_check(tmp_path, _two_reddit_claims(f"{thread}/aaa111/", f"{thread}/bbb222/")))


def test_the_same_product_and_criterion_twice_is_refused(tmp_path: Path) -> None:
    rows = [*claim_rows(), *claim_rows(conflict_id="syn002", product="NOVA  book 14")]
    _assert_error(_check(tmp_path, rows), 4, "syn002", "syn001", "same product and criterion")


def test_the_same_product_with_another_criterion_is_fine(tmp_path: Path) -> None:
    rows = [*claim_rows(), *claim_rows(conflict_id="syn002", criterion="runtime")]
    _assert_clean(_check(tmp_path, rows))


# --- sites, URLs, dates and ids ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("site", "fragment"),
    [
        ("other.example", "does not match"),
        ("ite1.example", "does not match"),
        ("Site1.Example", "lowercase domain"),
        ("www.site1.example", "www."),
        ("site1", "lowercase domain"),
    ],
    ids=["other_host", "tail_of_a_host", "upper_case", "www", "no_dot"],
)
def test_the_site_must_match_the_url(tmp_path: Path, site: str, fragment: str) -> None:
    rows = claim_rows()
    rows[0]["claim_site"] = site
    _assert_error(_check(tmp_path, rows), 2, "claim_site", fragment)


def test_a_site_may_be_the_parent_of_the_host(tmp_path: Path) -> None:
    rows = claim_rows()
    rows[0].update(claim_url="https://support.site1.example/page", claim_site="site1.example")
    _assert_clean(_check(tmp_path, rows))


@pytest.mark.parametrize(
    "url",
    [
        "ftp://site1.example/page",
        "site1.example/page",
        "https://",
        "https://site1.example/a page",
        "javascript:alert(1)",
    ],
)
def test_a_bad_url_is_refused(tmp_path: Path, url: str) -> None:
    rows = claim_rows()
    rows[0]["claim_url"] = url
    _assert_error(_check(tmp_path, rows), 2, "claim_url", repr(url))


@pytest.mark.parametrize(
    "template",
    ["https://{}@site1.example/page", "https://{}@[bad/", "{}@site1.example/page", "ftp://{}@site1.example/page"],
    ids=["plain", "not_a_valid_address", "no_scheme", "other_scheme"],
)
def test_a_url_holding_a_login_is_refused_and_not_printed(tmp_path: Path, template: str) -> None:
    rows = claim_rows()
    login = "someone" + ":" + "not-a-real-one"
    rows[0]["claim_url"] = template.format(login)
    result = _check(tmp_path, rows)
    _assert_error(result, 2, "claim_url")
    assert not any(login in error for error in result.errors)


def test_a_login_in_how_established_is_refused_and_not_printed(tmp_path: Path) -> None:
    login = "someone" + ":" + "not-a-real-one"
    result = _check(tmp_path, claim_rows(how_established=f"https://{login}@nova.example/x"))
    _assert_error(result, 2, "how_established", "user name or password")
    assert not any(login in error for error in result.errors)


@pytest.mark.parametrize("row_with_login", [0, 1], ids=["first_row", "second_row"])
def test_a_login_is_not_printed_when_how_established_differs_between_rows(tmp_path: Path, row_with_login: int) -> None:
    login = "someone" + ":" + "not-a-real-one"
    rows = claim_rows()
    rows[row_with_login]["how_established"] = f"https://{login}@nova.example/x"
    result = _check(tmp_path, rows)
    _assert_error(result, 3, "how_established", "line 2")
    assert not any(login in error for error in result.errors)


@pytest.mark.parametrize(
    "written", ["2026-11-10", "2999-01-01", "2026-02-30", "01-11-2026", "20261101", "2026-11-01T10:00", "yesterday"]
)
def test_a_future_or_impossible_date_is_refused(tmp_path: Path, written: str) -> None:
    _assert_error(_check(tmp_path, claim_rows(claim_retrieved_at=written)), 2, "claim_retrieved_at", repr(written))


def test_tomorrow_is_allowed_because_of_time_zones(tmp_path: Path) -> None:
    _assert_clean(_check(tmp_path, claim_rows(claim_retrieved_at="2026-11-09")))


@pytest.mark.parametrize("conflict_id", ["1", "c1", "c0001", "C001", "syn1", "SYN001", "x001", "c٠٠١"])
def test_a_bad_conflict_id_is_refused(tmp_path: Path, conflict_id: str) -> None:
    _assert_error(_check(tmp_path, claim_rows(conflict_id=conflict_id)), 2, "conflict_id", repr(conflict_id))


@pytest.mark.parametrize(
    ("rows", "second_id"),
    [
        ([*as_real(claim_rows()), *claim_rows(criterion="runtime")], "syn001"),
        ([*claim_rows(), *as_real(claim_rows(criterion="runtime"))], "c001"),
    ],
    ids=["real_first", "synthetic_first"],
)
def test_real_and_synthetic_ids_in_one_file_are_refused(
    tmp_path: Path, rows: list[dict[str, str]], second_id: str
) -> None:
    _assert_error(_check(tmp_path, rows), 4, second_id, "synthetic", "real")


@pytest.mark.parametrize("site", ["site1.example", "support.site1.example", "site1.test", "site1.invalid"])
def test_a_real_conflict_cannot_cite_a_made_up_site(tmp_path: Path, site: str) -> None:
    """Example rows copied into the real file, with their ids changed, must not pass as a real baseline."""
    rows = as_real(claim_rows())
    rows[0].update(claim_url=f"https://{site}/page", claim_site=site)
    _assert_error(_check(tmp_path, rows), 2, "claim_site", repr(site), "made-up")


@pytest.mark.parametrize("site", ["example.com", "www.example.com", "shop.example.org", "example.net"])
def test_a_real_conflict_cannot_cite_the_example_domains(tmp_path: Path, site: str) -> None:
    rows = as_real(claim_rows())
    rows[0].update(claim_url=f"https://{site}/page", claim_site=site.removeprefix("www."))
    _assert_error(_check(tmp_path, rows), 2, "claim_site", "made-up")


@pytest.mark.parametrize("link", ["https://nova.example/book-14/specs", "See https://example.com/specs."])
def test_a_real_conflict_cannot_rest_its_answer_on_a_made_up_link(tmp_path: Path, link: str) -> None:
    rows = as_real(claim_rows())
    for row in rows:
        row["how_established"] = link
    _assert_error(_check(tmp_path, rows), 2, "how_established", "made-up")


def test_a_real_conflict_on_ordinary_sites_passes(tmp_path: Path) -> None:
    _assert_clean(_check(tmp_path, as_real(claim_rows())))


def test_the_id_marks_a_conflict_as_synthetic(tmp_path: Path) -> None:
    assert _check(tmp_path, claim_rows()).conflicts[0].synthetic is True
    assert _check(tmp_path, as_real(claim_rows())).conflicts[0].synthetic is False


@pytest.mark.parametrize("column", ["category", "criterion"])
@pytest.mark.parametrize("cell", ["Laptop", "two words"])
def test_category_and_criterion_are_slugs(tmp_path: Path, column: str, cell: str) -> None:
    _assert_error(_check(tmp_path, claim_rows(**{column: cell})), 2, column, repr(cell))


@pytest.mark.parametrize(
    ("column", "limit"),
    [("product", 300), ("how_established", 300), ("known_answer", 80), ("claim_value", 80)],
)
def test_long_cells_are_refused(tmp_path: Path, column: str, limit: int) -> None:
    rows = text_rows()
    for row in rows:
        row[column] = "x" * (limit + 1)
    _assert_error(_check(tmp_path, rows), 2, column, str(limit + 1), str(limit))


@pytest.mark.parametrize(
    "column", ["product", "claim_value", "claim_quote", "how_established", "added_by", "claim_url"]
)
@pytest.mark.parametrize(
    "hidden",
    ["\x01", chr(0x2028), "\x85", chr(0x200B), chr(0xAD), chr(0xFEFF)],
    ids=["control", "line_separator", "next_line", "zero_width", "soft_hyphen", "byte_order_mark"],
)
def test_hidden_characters_are_refused(tmp_path: Path, column: str, hidden: str) -> None:
    rows = text_rows()
    for row in rows:
        row[column] = f"USB{hidden}C"
    _assert_error(_check(tmp_path, rows), 2, column, "hidden")


# --- warnings ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rows",
    [
        claim_rows([("manufacturer", "10 h"), ("editorial", "10.0 h")]),
        claim_rows([("manufacturer", "10 h"), ("editorial", "10.5 h")]),
        text_rows(("USB-C", "usb-c")),
    ],
    ids=["same_number", "within_tolerance", "same_text"],
)
def test_all_sources_agreeing_is_a_warning_not_an_error(tmp_path: Path, rows: list[dict[str, str]]) -> None:
    result = _check(tmp_path, rows)
    _assert_clean(result)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].all_agree
    assert len(result.warnings) == 1
    assert result.warnings[0].startswith(f"{result.path}:2: warning: ")
    assert "not a real conflict" in result.warnings[0]


def test_sources_just_outside_the_tolerance_do_not_agree(tmp_path: Path) -> None:
    result = _check(tmp_path, claim_rows([("manufacturer", "10 h"), ("editorial", "10.6 h")]))
    assert not result.conflicts[0].all_agree
    assert result.warnings == []


def test_the_examples_warn_about_their_one_all_agree_conflict() -> None:
    result = check_file(EXAMPLES)
    assert len(result.warnings) == 1
    assert "syn005" in result.warnings[0]


# --- the command line --------------------------------------------------------------------------------


def test_the_summary_and_exit_code_of_a_clean_file(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(EXAMPLES)]) == 0
    out = capsys.readouterr().out
    assert "26 rows checked, 9 conflicts, 0 errors, 1 warning" in out
    assert "warning: " in out


def test_a_file_with_errors_exits_1_and_prints_them(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_conflicts(tmp_path / "conflicts.csv", claim_rows([("manufacturer", "10 h"), ("blog", "12 h")]))
    assert main([str(path)]) == 1
    out = capsys.readouterr().out
    assert f"{path}:3: " in out
    assert "1 error" in out


def test_usage_errors_exit_1() -> None:
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 1


def test_several_files_are_all_checked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = write_conflicts(tmp_path / "bad.csv", claim_rows([("manufacturer", "10 h"), ("blog", "12 h")]))
    assert main([str(bad), str(EXAMPLES)]) == 1
    out = capsys.readouterr().out
    assert "bad.csv" in out
    assert "26 rows checked" in out


def test_the_rupee_sign_is_read_and_printed(tmp_path: Path) -> None:
    """The real thing on Windows: a child process with the console's default encoding, and no UTF-8 mode to help."""
    rows = claim_rows(
        [("manufacturer", "62990 ₹"), ("editorial", "750 $")], unit="₹", known_answer="62990 ₹", tolerance="100"
    )
    path = write_conflicts(tmp_path / "conflicts.csv", rows)
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    done = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, env=env, check=False)
    assert done.returncode == 1
    assert "'₹'" in done.stdout.decode("utf-8")
    assert done.stderr == b""


def test_check_conflicts_runs_as_a_script(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), str(EXAMPLES)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=tmp_path,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "26 rows checked" in done.stdout
