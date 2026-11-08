"""Hand-collected store listings and short mentions (ML-1, D-55): the CSV format, reading it, and URL clean-up.

Used by check_listings.py, make_pairs.py and label_pairs.py. A listing is copied by a person from a store page
(`title`) or from a review or forum post (`mention`), never scraped. The format is in ml/DATA.md, section 8.

Every problem is reported as FILE:LINE, with the physical line a text editor shows, so a damaged line (a missing
comma, an unclosed quote, a NUL byte) can be found and fixed by hand. Standard library only.
"""

from __future__ import annotations

import contextlib
import csv
import io
import os
import re
import stat
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from check_labels import MAX_TITLE_LENGTH, SLUG

LISTING_HEADER = ("listing_id", "kind", "text", "source", "url", "product_family", "category", "added_by")
KINDS = ("title", "mention")
LISTING_ID = re.compile(r"l[0-9]{4}")
# Control characters and the line and paragraph separators web pages sometimes carry: invisible, and they break lines.
CONTROL = re.compile("[\x00-\x1f\x7f-\x9f  ]")

# Query parameters that only say where a click came from. Product parameters (pid, lid, th, psc, v...) are kept.
TRACKING_PARAMETERS = frozenset(
    {
        "gclid", "gbraid", "wbraid", "fbclid", "msclkid", "dclid", "yclid", "igshid", "mc_cid", "mc_eid", "_ga", "_gl",
        "ref", "ref_", "tag", "linkcode", "qid", "sr", "crid", "sprefix", "otracker", "otracker1", "fm", "iid", "ppt",
        "ppn", "ssid", "srno", "affid", "si",
    }
)  # fmt: skip
TRACKING_PREFIXES = ("utm_", "pf_rd_", "pd_rd_", "affextparam")
AMAZON_REF_SEGMENT = re.compile(r"/ref=[^/]*")


# --- reading a CSV with line numbers --------------------------------------------------------------


@dataclass
class Table:
    """The rows of one CSV file that have the right columns, each with its first line, plus every problem found."""

    path: str
    rows: list[tuple[int, dict[str, str]]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, line: int, message: str) -> None:
        self.errors.append(f"{self.path}:{line}: {message}")


def read_table(path: str | Path, header: Sequence[str]) -> Table:
    """Read a UTF-8 CSV whose first line must be exactly `header`.

    A damaged line is reported and skipped, and reading goes on, so one bad line never hides the rest:
    a NUL character, a quote that is never closed, a line break inside a cell, or the wrong number of columns.
    """
    table = Table(path=str(path))
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        table.add_error(1, "file not found")
        return table
    except OSError as exc:
        table.add_error(1, f"cannot read the file ({exc})")
        return table
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        table.add_error(data[: exc.start].count(b"\n") + 1, 'the file is not UTF-8 text; save it as "CSV UTF-8"')
        return table

    lines = physical_lines(text)
    for number, line in enumerate(lines, start=1):
        if "\x00" in line:
            table.add_error(number, "the line holds a NUL character, so the file is damaged here; retype the line")
            lines[number - 1] = "\n"  # read the rest of the file as if this line were blank
    if not lines:
        table.add_error(1, "the file is empty; the first line must be the header")
        return table

    reader = csv.reader(io.StringIO("".join(lines), newline=""))
    end = 0
    first = True
    while True:
        try:
            record = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:  # such as a cell longer than the csv module allows
            table.add_error(end + 1, f"this line cannot be read as CSV ({exc})")
            break
        start, end = end + 1, reader.line_num
        if first:
            first = False
            problem = header_problem(record, header)
            if problem:
                table.add_error(1, f"{problem}. The header must be exactly: {','.join(header)}")
                return table
            continue
        if not any(cell.strip() for cell in record):
            continue  # a blank line
        if any("\n" in cell or "\r" in cell for cell in record):
            table.add_error(start, "a quote is not closed, or a cell holds a line break; check the quotes on this line")
            continue
        if len(record) != len(header):
            table.add_error(
                start,
                f"expected {len(header)} columns, found {len(record)}; put quotes around text that contains commas",
            )
            continue
        table.rows.append((start, dict(zip(header, record, strict=True))))
    return table


def physical_lines(text: str) -> list[str]:
    """Lines as a text editor and the csv module count them: split at \\n, \\r\\n or \\r only.

    str.splitlines would also split at characters such as U+2028, which turn up in text copied from web pages, and
    every line number after one would be off.
    """
    return io.StringIO(text, newline="").readlines()


def header_problem(found: Sequence[str], header: Sequence[str]) -> str | None:
    """Name the first wrong header cell, which is easier to fix than a whole-line comparison."""
    if tuple(found) == tuple(header):
        return None
    if len(found) != len(header):
        return f"the header has {len(found)} columns, expected {len(header)}"
    for number, (expected, cell) in enumerate(zip(header, found, strict=True), start=1):
        if expected != cell:
            return f"header column {number} is {cell!r}, expected {expected!r}"
    return None  # unreachable: the tuples differ, so some cell differs


def write_rows(path: Path, header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    """Write a UTF-8 CSV with LF line endings, all at once: a crash mid-write never leaves half a file."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    replace_file(path, buffer.getvalue().encode("utf-8"))


class FileBusy(OSError):
    """The file could not be replaced, usually because a spreadsheet has it open (Windows locks open files)."""


def replace_file(path: Path, data: bytes, tries: int = 5, pause_s: float = 0.2) -> None:
    """Write next to the target, then swap it in, so the old file stays whole until the new one is complete.

    Windows refuses the swap while another program holds the file (a spreadsheet, or a virus scanner for a moment),
    so it tries a few times before giving up with FileBusy, whose message says what to do.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        # mkstemp makes the file readable by its owner only; keep the old file's permissions, or the usual ones. Best
        # effort, and always writable by its owner, so a read-only target or a drive without chmod never blocks a save.
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        with contextlib.suppress(OSError):
            os.chmod(temporary, mode | stat.S_IWUSR)
        for attempt in range(tries):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == tries - 1:
                    raise FileBusy(f"cannot save {path.name}: close it in any other program, then try again") from None
                time.sleep(pause_s)
    finally:
        Path(temporary).unlink(missing_ok=True)  # gone already after a successful replace


# --- listings -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Listing:
    """One listing that passed every check."""

    line: int
    listing_id: str
    kind: str
    text: str
    source: str
    url: str
    product_family: str
    category: str
    added_by: str


@dataclass
class ListingsResult:
    path: str
    listings: list[Listing] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rows_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def check_listings_file(path: str | Path) -> ListingsResult:
    """Read one listings CSV and check the header and every line."""
    table = read_table(path, LISTING_HEADER)
    result = ListingsResult(path=table.path, errors=list(table.errors), rows_checked=len(table.rows))
    first_line_of_id: dict[str, int] = {}
    first_line_of_url: dict[str, int] = {}
    for line, values in table.rows:
        problems = _listing_problems(values, line, first_line_of_id, first_line_of_url)
        result.errors += [f"{table.path}:{line}: {problem}" for problem in problems]
        if not problems:
            result.listings.append(Listing(line=line, **values))
    result.errors.sort(key=_line_of)
    return result


def _line_of(error: str) -> int:
    match = re.search(r":([0-9]+): ", error)
    return int(match.group(1)) if match else 0


def _listing_problems(
    values: dict[str, str], line: int, first_line_of_id: dict[str, int], first_line_of_url: dict[str, int]
) -> list[str]:
    problems: list[str] = []
    listing_id = values["listing_id"]
    if not LISTING_ID.fullmatch(listing_id):
        problems.append(f"listing_id {listing_id!r} must be l and 4 digits, such as l0001")
    elif listing_id in first_line_of_id:
        problems.append(f"duplicate listing_id {listing_id} (first used on line {first_line_of_id[listing_id]})")
    else:
        first_line_of_id[listing_id] = line

    if values["kind"] not in KINDS:
        problems.append(f"kind {values['kind']!r} must be title or mention")
    text = values["text"]
    if not text.strip():
        problems.append("text is empty")
    elif len(text) > MAX_TITLE_LENGTH:
        problems.append(f"text has {len(text)} characters; the limit is {MAX_TITLE_LENGTH}")
    elif CONTROL.search(text):
        problems.append("text holds a hidden control or line-separator character; retype it")
    for column in ("source", "product_family", "category"):
        if not SLUG.fullmatch(values[column]):
            problems.append(f"{column} {values[column]!r} is not a lowercase slug such as nova-book")
    if "reddit" in values["source"] or "redd-it" in values["source"] or is_reddit_url(values["url"]):
        problems.append("Reddit text is never used for training (D-53); copy mentions from other sites")

    url = values["url"]
    if url:
        problem = url_problem(url)
        if problem:
            problems.append(problem)
        elif url in first_line_of_url:
            problems.append(f"the same url as line {first_line_of_url[url]}; copy each listing once")
        else:
            first_line_of_url[url] = line
    elif values["kind"] == "title":
        problems.append("a title needs its url, the store page it was copied from")
    if not values["added_by"].strip():
        problems.append("added_by is empty")
    return problems


def is_web_url(url: str) -> bool:
    return url_problem(url) is None


def is_reddit_url(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host in ("reddit.com", "redd.it") or host.endswith((".reddit.com", ".redd.it"))


def url_problem(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return f"url {url!r} is not a valid web address"
    if parts.scheme not in ("http", "https"):
        return f"url {url!r} must start with http:// or https://"
    if not parts.hostname or any(character.isspace() for character in url):
        return f"url {url!r} is not a valid web address"
    return None


# --- URL clean-up ---------------------------------------------------------------------------------


def clean_url(url: str) -> str:
    """The URL without tracking parameters. A URL with nothing to remove comes back exactly as it was.

    The query is split on & and the kept parameters are joined back as they were written, never re-encoded.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    path = parts.path
    if parts.hostname and "amazon." in parts.hostname:
        path = AMAZON_REF_SEGMENT.sub("", path)
    pieces = parts.query.split("&") if parts.query else []
    kept = [piece for piece in pieces if not _is_tracking(piece.split("=", 1)[0])]
    if path == parts.path and len(kept) == len(pieces):
        return url
    return urlunsplit((parts.scheme, parts.netloc, path, "&".join(kept), parts.fragment))


def _is_tracking(name: str) -> bool:
    key = unquote(name).strip().lower()
    return key in TRACKING_PARAMETERS or key.startswith(TRACKING_PREFIXES)
