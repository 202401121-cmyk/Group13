"""The labelling CSV checker (ML-0): clean files pass, every broken rule is caught with FILE:ROW."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from check_labels import HEADER, check_file, main

WriteLabels = Callable[..., Path]  # the write_labels fixture in conftest.py

TEMPLATE = Path(__file__).resolve().parents[1] / "labelling" / "pairs_template.csv"

THREE_GOOD_ROWS = [
    {"pair_id": "p0001", "label": "same"},
    {
        "pair_id": "p0002",
        "left_title": "Orion X5 5G (8GB, 128GB, Blue)",
        "right_title": "Orion X5 5G (8GB, 128GB, Green)",
        "category": "smartphone",
        "product_family": "orion-x",
        "label": "variant",
    },
    {
        "pair_id": "p0003",
        "left_title": "Kestrel Buds 2 (Black)",
        "right_title": "Kestrel Buds 2 Pro (Black)",
        "category": "earbuds",
        "product_family": "kestrel-buds",
        "label": "different",
        "notes": "Pro is a different model",
    },
]


def _only_error(path: Path) -> str:
    errors = check_file(path).errors
    assert len(errors) == 1, errors
    return errors[0]


def test_a_clean_file_passes(write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]) -> None:
    path = write_labels("clean.csv", THREE_GOOD_ROWS)
    result = check_file(path)
    assert result.ok
    assert result.rows_checked == 3
    assert [pair.pair_id for pair in result.pairs] == ["p0001", "p0002", "p0003"]

    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert f"{path}: 3 rows checked, 0 errors" in out
    assert "labels: same 1, variant 1, different 1, unsure 0" in out
    assert "families per category: earbuds 1, laptop 1, smartphone 1" in out
    assert "OK: 1 file, 3 rows checked, no errors." in out


def test_a_bad_header_fails(write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]) -> None:
    header = ["Label" if column == "label" else column for column in HEADER]
    path = write_labels("bad_header.csv", THREE_GOOD_ROWS, header=header)
    assert _only_error(path).startswith(f"{path}:1: header column 6 is 'Label', expected 'label'")
    assert main([str(path)]) == 1
    assert "FAILED: 1 error in 1 file" in capsys.readouterr().out


def test_a_header_with_a_missing_column_fails(write_labels: WriteLabels) -> None:
    path = write_labels("short_header.csv", [], header=HEADER[:-1])
    assert _only_error(path).startswith(f"{path}:1: the header has 8 columns, expected 9")


def test_a_duplicate_pair_id_fails(write_labels: WriteLabels) -> None:
    path = write_labels("duplicate.csv", [{"pair_id": "p0007"}, {"pair_id": "p0008"}, {"pair_id": "p0007"}])
    assert _only_error(path) == f"{path}:4: duplicate pair_id p0007 (first used on row 2)"
    assert main([str(path)]) == 1


@pytest.mark.parametrize("pair_id", ["1", "p001", "p00001", "P0001", "p000a", "", "p٠٠٠١"])
def test_a_badly_formed_pair_id_fails(write_labels: WriteLabels, pair_id: str) -> None:
    path = write_labels("pair_id.csv", [{"pair_id": pair_id}])
    assert f"{path}:2: pair_id {pair_id!r} must be p and 4 digits" in _only_error(path)


@pytest.mark.parametrize("label", ["Same", "same ", "match", "", "unknown"])
def test_a_bad_label_fails(write_labels: WriteLabels, label: str) -> None:
    path = write_labels("label.csv", [{"label": label}])
    assert _only_error(path) == (f"{path}:2: label {label!r} must be one of: same, variant, different, unsure")


@pytest.mark.parametrize("column", ["left_title", "right_title"])
@pytest.mark.parametrize("title", ["", "   "])
def test_an_empty_title_fails(write_labels: WriteLabels, column: str, title: str) -> None:
    path = write_labels("empty_title.csv", [{column: title}])
    assert _only_error(path) == f"{path}:2: {column} is empty"


def test_a_title_may_be_300_characters_but_not_301(write_labels: WriteLabels) -> None:
    assert check_file(write_labels("ok.csv", [{"left_title": "x" * 300}])).ok
    path = write_labels("long.csv", [{"left_title": "x" * 301}])
    assert _only_error(path) == f"{path}:2: left_title has 301 characters; the limit is 300"


@pytest.mark.parametrize("labelled_at", ["2026-02-30", "2026-13-01", "25-10-2026", "2026/10/25", "20261025", ""])
def test_a_bad_date_fails(write_labels: WriteLabels, labelled_at: str) -> None:
    path = write_labels("date.csv", [{"labelled_at": labelled_at}])
    assert _only_error(path) == f"{path}:2: labelled_at {labelled_at!r} is not a real date written as YYYY-MM-DD"


def test_a_leap_day_is_a_real_date(write_labels: WriteLabels) -> None:
    assert check_file(write_labels("leap.csv", [{"labelled_at": "2028-02-29"}])).ok


@pytest.mark.parametrize(
    ("column", "value"),
    [("category", "Laptop"), ("category", "smart phone"), ("product_family", "nova--book"), ("product_family", "")],
)
def test_a_bad_slug_fails(write_labels: WriteLabels, column: str, value: str) -> None:
    path = write_labels("slug.csv", [{column: value}])
    assert _only_error(path) == f"{path}:2: {column} {value!r} is not a lowercase slug such as nova-book"


@pytest.mark.parametrize("value", ["power_bank", "nova-book", "x5"])
def test_slugs_may_use_hyphens_underscores_and_digits(write_labels: WriteLabels, value: str) -> None:
    assert check_file(write_labels("slug.csv", [{"category": value, "product_family": value}])).ok


def test_an_empty_labeller_fails(write_labels: WriteLabels) -> None:
    path = write_labels("labeller.csv", [{"labeller": " "}])
    assert _only_error(path) == f"{path}:2: labeller is empty"


def test_every_problem_in_a_row_is_reported(write_labels: WriteLabels) -> None:
    path = write_labels("many.csv", [{"pair_id": "x", "label": "maybe", "labelled_at": "soon"}])
    result = check_file(path)
    assert len(result.errors) == 3
    assert all(error.startswith(f"{path}:2: ") for error in result.errors)
    assert result.pairs == []


def test_a_row_with_the_wrong_number_of_columns_fails(tmp_path: Path) -> None:
    path = tmp_path / "columns.csv"
    row = "p0001,Nova Book 14, 16GB,Nova Book 14,laptop,nova-book,same,Alex,2026-10-25,"  # unquoted comma in a title
    path.write_text(",".join(HEADER) + "\n" + row + "\n", encoding="utf-8")
    assert _only_error(path).startswith(f"{path}:2: expected 9 columns, found 10")


def test_row_numbers_match_a_spreadsheet_even_after_blank_lines(tmp_path: Path) -> None:
    good = "p0001,Nova Book 14,Nova Book 14 Laptop,laptop,nova-book,same,Alex,2026-10-25,"
    bad = "p0002,Nova Book 14,Nova Book 16,laptop,nova-book,maybe,Alex,2026-10-25,"
    path = tmp_path / "blank_lines.csv"
    path.write_text("\n".join([",".join(HEADER), good, "", ",,,,,,,,", bad, ""]) + "\n", encoding="utf-8")
    result = check_file(path)
    assert result.rows_checked == 2
    assert result.errors == [f"{path}:5: label 'maybe' must be one of: same, variant, different, unsure"]


def test_a_file_saved_with_a_bom_and_crlf_line_endings_passes(tmp_path: Path) -> None:
    # What Excel writes on Windows for "CSV UTF-8": a byte-order mark, then CRLF after every row.
    lines = [
        ",".join(HEADER),
        'p0001,"Nova Book 14 (16GB RAM, 512GB SSD)",Nova Book 14 16GB 512GB,laptop,nova-book,same,Alex,2026-10-25,',
        "p0002,Kestrel Buds 2 (Black),Kestrel Buds 2 Pro (Black),earbuds,kestrel-buds,different,Alex,2026-10-25,ok",
    ]
    path = tmp_path / "excel.csv"
    path.write_bytes(b"\xef\xbb\xbf" + "\r\n".join(lines).encode("utf-8") + b"\r\n")
    result = check_file(path)
    assert result.ok, result.errors
    assert [pair.pair_id for pair in result.pairs] == ["p0001", "p0002"]
    assert result.pairs[1].notes == "ok"  # no stray \r left at the end of a row


def test_a_missing_file_fails_cleanly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "missing.csv"
    assert _only_error(path) == f"{path}:1: file not found"
    assert main([str(path)]) == 1
    assert f"{path}:1: file not found" in capsys.readouterr().out


def test_an_empty_file_fails_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    assert _only_error(path) == f"{path}:1: the file is empty; the first row must be the header"


def test_a_file_that_is_not_utf8_fails_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "latin1.csv"
    path.write_bytes(
        (",".join(HEADER) + "\np0001,Caf\xe9,Caf\xe9,laptop,nova-book,same,Alex,2026-10-25,\n").encode("latin-1")
    )
    assert _only_error(path) == f'{path}:1: the file is not UTF-8 text; save it as "CSV UTF-8"'


def test_several_files_are_checked_and_any_error_fails_the_run(
    write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]
) -> None:
    good = write_labels("good.csv", THREE_GOOD_ROWS)
    bad = write_labels("bad.csv", [{"label": "maybe"}])
    assert main([str(good), str(bad)]) == 1
    out = capsys.readouterr().out
    assert f"{bad}:2: label 'maybe'" in out
    assert f"{good}: 3 rows checked, 0 errors" in out
    assert "labels (rows with errors not counted): same 0" in out
    assert "FAILED: 1 error in 2 files, 4 rows checked." in out


def test_the_committed_template_passes() -> None:
    result = check_file(TEMPLATE)
    assert result.ok, result.errors
    assert [pair.label for pair in result.pairs] == ["same", "variant", "different"]
