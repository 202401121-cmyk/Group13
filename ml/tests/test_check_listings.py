"""The listings checker (ML-1): clean files pass, broken rules and corrupt lines are reported with FILE:LINE, and
--fix strips tracking parameters without touching anything else."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

import pytest
from check_listings import main
from conftest import LISTINGS_HEADER, example_listing_rows, write_csv
from listings import check_listings_file, clean_url

ML = Path(__file__).resolve().parents[1]
TEMPLATE = ML / "labelling" / "listings_template.csv"

GOOD = {
    "listing_id": "l0001",
    "kind": "title",
    "text": "Nova Book 14 (16GB RAM, 512GB SSD, Silver)",
    "source": "shop-a",
    "url": "https://shop-a.example/p/nova-book-14",
    "product_family": "nova-book",
    "category": "laptop",
    "added_by": "Alex",
}


def _write(path: Path, *rows: dict[str, str]) -> Path:
    return write_csv(path, LISTINGS_HEADER, [{**GOOD, **row} for row in rows])


def test_a_clean_listings_file_passes(example_listings: Path, capsys: pytest.CaptureFixture[str]) -> None:
    result = check_listings_file(example_listings)
    assert result.ok, result.errors
    assert len(result.listings) == len(example_listing_rows())
    assert main([str(example_listings)]) == 0
    out = capsys.readouterr().out
    assert "OK: 1 file" in out
    assert "titles" in out and "mentions" in out


def test_the_listings_template_is_valid() -> None:
    result = check_listings_file(TEMPLATE)
    assert result.ok, result.errors
    assert {listing.kind for listing in result.listings} == {"title", "mention"}


def test_a_bad_header_names_the_wrong_column(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    path.write_text("listing_id,kind,title,source,url,product_family,category,added_by\n", encoding="utf-8")
    [error] = check_listings_file(path).errors
    assert error.startswith(f"{path}:1: header column 3 is 'title', expected 'text'")


@pytest.mark.parametrize(
    ("bad_line", "message"),
    [
        ("l0002,title,Nova Book 16,shop-a,https://shop-a.example/p/2,nova-book,laptop", "expected 8 columns, found 7"),
        ('l0002,title,"Nova Book 16,shop-a,https://shop-a.example/p/2,nova-book,laptop,Alex', "a quote is not closed"),
        ("l0002,title,Nova Book\x00 16,shop-a,https://shop-a.example/p/2,nova-book,laptop,Alex", "NUL character"),
    ],
    ids=["column_count", "unclosed_quote", "nul_byte"],
)
def test_a_corrupt_line_is_reported_with_its_line_number(tmp_path: Path, bad_line: str, message: str) -> None:
    path = tmp_path / "listings.csv"
    first = 'l0001,title,"Nova Book 14 (16GB RAM, 512GB SSD, Silver)",shop-a,https://shop-a.example/p/1,nova-book,laptop,Alex'
    last = "l0003,title,Orion X5 5G Blue,shop-b,https://shop-b.example/p/3,orion-x,smartphone,Alex"
    swallows_the_rest = message == "a quote is not closed"  # everything after an open quote is inside it
    lines = [",".join(LISTINGS_HEADER), first, bad_line]
    if not swallows_the_rest:
        lines.append(last)
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    result = check_listings_file(path)
    assert any(error.startswith(f"{path}:3: ") and message in error for error in result.errors), result.errors
    assert not any(error.startswith(f"{path}:2:") for error in result.errors)  # the good line before it is fine
    if not swallows_the_rest:
        assert [listing.listing_id for listing in result.listings] == ["l0001", "l0003"]  # checking went on


def test_each_broken_rule_is_reported(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "listings.csv",
        {"listing_id": "l0001"},
        {"listing_id": "L2", "url": "https://shop-a.example/p/2"},  # not l and 4 digits
        {"listing_id": "l0001", "url": "https://shop-a.example/p/other"},  # duplicate id
        {"listing_id": "l0004", "kind": "review", "url": "https://shop-a.example/p/4"},
        {"listing_id": "l0005", "text": " ", "url": "https://shop-a.example/p/5"},
        {"listing_id": "l0006", "text": "x" * 301, "url": "https://shop-a.example/p/6"},
        {"listing_id": "l0007", "source": "Shop A", "url": "https://shop-a.example/p/7"},
        {"listing_id": "l0008", "product_family": "Nova Book", "url": "https://shop-a.example/p/8"},
        {"listing_id": "l0009", "category": "", "url": "https://shop-a.example/p/9"},
        {"listing_id": "l0010", "url": "ftp://shop-a.example/p/10"},
        {"listing_id": "l0011", "url": ""},  # a title needs its URL
        {"listing_id": "l0012", "added_by": " ", "url": "https://shop-a.example/p/12"},
        {"listing_id": "l0013"},  # the same URL as line 2
        {"listing_id": "l0014", "kind": "mention", "text": "Nova Book 14", "source": "forum-x", "url": ""},  # fine
    )
    errors = check_listings_file(path).errors
    expected = [
        (3, "listing_id 'L2' must be l and 4 digits"),
        (4, "duplicate listing_id l0001 (first used on line 2)"),
        (5, "kind 'review' must be title or mention"),
        (6, "text is empty"),
        (7, "text has 301 characters; the limit is 300"),
        (8, "source 'Shop A' is not a lowercase slug"),
        (9, "product_family 'Nova Book' is not a lowercase slug"),
        (10, "category '' is not a lowercase slug"),
        (11, "url 'ftp://shop-a.example/p/10' must start with http:// or https://"),
        (12, "a title needs its url"),
        (13, "added_by is empty"),
        (14, "the same url as line 2"),
    ]
    for line, message in expected:
        assert any(error.startswith(f"{path}:{line}: ") and message in error for error in errors), (line, errors)
    assert len(errors) == len(expected), errors


@pytest.mark.parametrize(
    ("url", "clean"),
    [
        ("https://shop.example/p/1?utm_source=x&utm_medium=y", "https://shop.example/p/1"),
        ("https://shop.example/p/1?pid=ABC123&gclid=zz&lid=LST9", "https://shop.example/p/1?pid=ABC123&lid=LST9"),
        ("https://shop.example/p/1?fbclid=1&otracker=search&otracker1=a&fm=b&iid=c&ssid=d", "https://shop.example/p/1"),
        (
            "https://www.amazon.in/Nova-Book-14/dp/B0ABC12345/ref=sr_1_3?crid=1&keywords=nova&qid=2&sr=8-3&th=1",
            "https://www.amazon.in/Nova-Book-14/dp/B0ABC12345?keywords=nova&th=1",
        ),
        (
            "https://www.amazon.in/dp/B0ABC12345?tag=aff-21&linkCode=ll1&psc=1",
            "https://www.amazon.in/dp/B0ABC12345?psc=1",
        ),
        ("https://youtu.be/abc123?si=share", "https://youtu.be/abc123"),
        ("https://shop.example/p/1?affExtParam1=a&pf_rd_r=b&pd_rd_w=c#reviews", "https://shop.example/p/1#reviews"),
    ],
)
def test_fix_strips_tracking_parameters_and_keeps_product_ones(url: str, clean: str) -> None:
    assert clean_url(url) == clean


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example/p/1",
        "https://shop.example/p/1?pid=ABC%20123&lid=x&q=nova+book",
        "https://shop.example/p/1?a=1&&b=2;c=3",
        "https://www.amazon.in/dp/B0ABC12345?th=1&psc=1#customerReviews",
        "https://shop.example/p/₹-deal?colour=Silver%2FGrey",
        "https://shop.example/p/1?",  # rebuilding a URL would drop the bare ?
        "HTTPS://shop.example/p/1?#top",  # ... and lower-case the scheme
    ],
)
def test_fix_leaves_a_url_without_tracking_parameters_unchanged(url: str, tmp_path: Path) -> None:
    assert clean_url(url) == url
    path = _write(tmp_path / "listings.csv", {"url": url})
    before = path.read_bytes()
    assert main([str(path), "--fix"]) == 0
    assert path.read_bytes() == before  # not one byte changed


def test_fix_rewrites_only_the_urls_and_reports_each_change(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "listings.csv"
    lines = [
        ",".join(LISTINGS_HEADER),
        'l0001,title,"Nova Book 14 (16GB, 512GB)",shop-a,https://shop-a.example/p/1?utm_source=a&pid=1,nova-book,laptop,Alex',
        'l0002,title,"Nova Book 14 (16GB, 512GB)",shop-b,"https://shop-b.example/p/2?ref=x",nova-book,laptop,Alex',
        "l0003,mention,Nova Book 14,forum-x,,nova-book,laptop,Alex",
    ]
    path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))  # a spreadsheet's CRLF, kept as it is
    assert main([str(path), "--fix"]) == 0
    out = capsys.readouterr().out
    assert "2: https://shop-a.example/p/1?utm_source=a&pid=1 -> https://shop-a.example/p/1?pid=1" in out
    assert "3: https://shop-b.example/p/2?ref=x -> https://shop-b.example/p/2" in out
    expected = [lines[0], lines[1].replace("utm_source=a&", ""), lines[2].replace("?ref=x", ""), lines[3]]
    assert path.read_bytes() == ("\r\n".join(expected) + "\r\n").encode("utf-8")


def test_the_rupee_sign_round_trips_through_check_and_fix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    text = "Nova Book 14 (16GB RAM, 512GB SSD) ₹54,990"
    path = _write(tmp_path / "listings.csv", {"text": text, "url": "https://shop-a.example/p/1?utm_campaign=sale"})
    assert main([str(path), "--fix"]) == 0
    [listing] = check_listings_file(path).listings
    assert listing.text == text
    assert "₹54,990".encode() in path.read_bytes()  # UTF-8 on disk, whatever the platform's default
    capsys.readouterr()


def test_check_listings_runs_as_a_script(tmp_path: Path) -> None:
    path = _write(tmp_path / "listings.csv", {"text": "Nova Book 14 ₹54,990", "listing_id": "x1"})
    done = subprocess.run(
        [sys.executable, str(ML / "scripts" / "check_listings.py"), str(path)],
        capture_output=True,
        timeout=60,
    )
    assert done.returncode == 1
    out = done.stdout.decode("utf-8")
    assert f"{path}:2: listing_id 'x1' must be l and 4 digits" in out
    assert "FAILED" in out


def test_a_hidden_line_separator_is_reported_and_later_urls_are_still_fixed(tmp_path: Path) -> None:
    """U+2028 comes along with text copied from web pages; it must not shift the line numbers after it."""
    path = tmp_path / "listings.csv"
    lines = [
        ",".join(LISTINGS_HEADER),
        "l0001,title,Nova Book 14\u2028Silver,shop-a,https://shop-a.example/p/1,nova-book,laptop,Alex",
        "l0002,title,Nova Book 14 Grey,shop-b,https://shop-b.example/p/2?utm_source=x,nova-book,laptop,Alex",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    assert main([str(path), "--fix"]) == 1  # the separator is an error
    fixed = path.read_text(encoding="utf-8")
    assert "https://shop-b.example/p/2,nova-book" in fixed and "utm_source" not in fixed  # line 3 was still cleaned
    [error] = check_listings_file(path).errors
    assert error.startswith(f"{path}:2: text holds a hidden control or line-separator character")


def test_reddit_is_not_a_source(tmp_path: Path) -> None:
    path = _write(tmp_path / "listings.csv", {"kind": "mention", "source": "reddit", "url": ""})
    [error] = check_listings_file(path).errors
    assert "Reddit text is never used for training (D-53)" in error


def test_a_spreadsheets_bom_and_crlf_are_read_and_kept_by_fix(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    lines = [
        ",".join(LISTINGS_HEADER),
        'l0001,title,"Nova Book 14 (16GB, 512GB)",shop-a,https://shop-a.example/p/1?gclid=1,nova-book,laptop,Alex',
    ]
    path.write_bytes(b"\xef\xbb\xbf" + ("\r\n".join(lines) + "\r\n").encode("utf-8"))
    assert main([str(path), "--fix"]) == 0
    expected = "\r\n".join([lines[0], lines[1].replace("?gclid=1", "")]) + "\r\n"
    assert path.read_bytes() == b"\xef\xbb\xbf" + expected.encode("utf-8")


def test_a_quote_closed_on_a_later_line_is_reported_and_reading_goes_on(tmp_path: Path) -> None:
    path = tmp_path / "listings.csv"
    lines = [
        ",".join(LISTINGS_HEADER),
        'l0001,title,"Nova Book 14',  # the quote closes on the next line
        'Silver",shop-a,https://shop-a.example/p/1,nova-book,laptop,Alex',
        "l0002,title,Nova Book 14 Grey,shop-b,https://shop-b.example/p/2,nova-book,laptop,Alex",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    result = check_listings_file(path)
    assert [error.split(": ", 1)[0] for error in result.errors] == [f"{path}:2"]
    assert [listing.listing_id for listing in result.listings] == ["l0002"]  # line 4, read after the damaged lines
    assert result.listings[0].line == 4


def test_fix_on_a_file_open_in_a_spreadsheet_says_what_to_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(tmp_path / "listings.csv", {"url": "https://shop-a.example/p/1?utm_source=x"})
    before = path.read_bytes()

    def locked(source: str, target: str) -> None:
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr("listings.os.replace", locked)  # what Windows does while a spreadsheet holds the file
    monkeypatch.setattr("listings.time.sleep", lambda seconds: None)
    assert main([str(path), "--fix"]) == 1
    assert "close it in any other program, then try again" in capsys.readouterr().out
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]  # no temporary file left behind


@pytest.mark.parametrize(
    "url", ["https://www.reddit.com/r/IndianGaming/comments/abc/", "https://old.reddit.com/r/x/", "https://redd.it/abc"]
)
def test_a_reddit_url_is_refused_whatever_the_source(tmp_path: Path, url: str) -> None:
    path = _write(tmp_path / "listings.csv", {"kind": "mention", "source": "forum-x", "url": url})
    [error] = check_listings_file(path).errors
    assert "Reddit text is never used for training (D-53)" in error


def test_a_rewritten_file_keeps_its_permissions(tmp_path: Path) -> None:
    path = _write(tmp_path / "listings.csv", {"url": "https://shop-a.example/p/1?utm_source=x"})
    path.chmod(0o640)
    before = stat.S_IMODE(path.stat().st_mode)
    assert main([str(path), "--fix"]) == 0
    assert stat.S_IMODE(path.stat().st_mode) == before  # not the owner-only mode of a fresh temporary file


def test_a_drive_without_chmod_still_saves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_chmod(*args: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr("listings.os.chmod", no_chmod)
    path = _write(tmp_path / "listings.csv", {"url": "https://shop-a.example/p/1?utm_source=x"})
    assert main([str(path), "--fix"]) == 0
    assert "utm_source" not in path.read_text(encoding="utf-8")
