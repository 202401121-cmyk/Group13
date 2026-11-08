"""Check hand-collected listings files, and strip tracking parameters from their URLs (ML-1).

Usage: python ml/scripts/check_listings.py FILE [FILE ...] [--fix]

The format is in ml/DATA.md, section 8. Every error prints as FILE:LINE: message. With --fix, tracking parameters
(utm_*, gclid, fbclid, Amazon's ref=... and the like) are removed from every URL first: only the URL text changes,
the rest of the file is kept byte for byte, and a file with nothing to remove is not touched at all. The exit code
is 0 when every file is clean and 1 otherwise. Standard library only; nothing here opens a URL.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from check_labels import use_utf8_stdout
from listings import (
    LISTING_HEADER,
    ListingsResult,
    check_listings_file,
    clean_url,
    physical_lines,
    read_table,
    replace_file,
)


def fix_file(path: Path) -> list[str]:
    """Remove tracking parameters from the file's URLs in place. Returns one line per change, as LINE: old -> new.

    Each URL is replaced inside its own line's text, so quoting, line endings and every other cell stay as written.
    """
    table = read_table(path, LISTING_HEADER)
    if not table.rows:
        return []
    data = path.read_bytes()
    bom = data.startswith(b"\xef\xbb\xbf")
    lines = physical_lines(data.decode("utf-8-sig"))  # the line numbers read_table gave
    changes: list[str] = []
    for line, values in table.rows:
        url = values["url"]
        cleaned = clean_url(url)
        if cleaned == url or url not in lines[line - 1]:
            continue
        lines[line - 1] = lines[line - 1].replace(url, cleaned, 1)
        changes.append(f"{line}: {url} -> {cleaned}")
    if changes:
        replace_file(path, (b"\xef\xbb\xbf" if bom else b"") + "".join(lines).encode("utf-8"))
    return changes


def summary_line(result: ListingsResult) -> str:
    kinds = Counter(listing.kind for listing in result.listings)
    families = {listing.product_family for listing in result.listings}
    sources = {listing.source for listing in result.listings}
    errors = len(result.errors)
    return (
        f"{result.path}: {result.rows_checked} listings checked, {errors} error{'' if errors == 1 else 's'};"
        f" {kinds['title']} titles, {kinds['mention']} mentions, {len(families)} families, {len(sources)} sources"
    )


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(
        prog="check_listings.py", description="Check listings CSV files against the format in ml/DATA.md, section 8."
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="a listings CSV file")
    parser.add_argument("--fix", action="store_true", help="remove tracking parameters from the URLs first, in place")
    args = parser.parse_args(argv)
    paths = [Path(file) for file in args.files]

    if args.fix:
        for path in paths:
            if not path.is_file():
                continue
            try:
                changes = fix_file(path)
            except OSError as exc:  # FileBusy says to close the file in the spreadsheet
                print(f"{path}: {exc}")
                return 1
            for change in changes:
                print(f"{path}:{change}")
    results = [check_listings_file(path) for path in paths]
    errors = [error for result in results for error in result.errors]
    for error in errors:
        print(error)
    if errors:
        print()
    for result in results:
        print(summary_line(result))
    files = f"{len(results)} file{'' if len(results) == 1 else 's'}"
    if errors:
        print(f"FAILED: {len(errors)} error{'' if len(errors) == 1 else 's'} in {files}.")
        return 1
    print(f"OK: {files}, no errors.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
