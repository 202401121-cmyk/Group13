"""Check the seeded conflicts file before the trust baselines score it (US-16, #30).

Usage: python ml/scripts/check_conflicts.py FILE [FILE ...]

One row per claim; the format and the guide are in ml/DATA.md, section 9. Every problem prints as FILE:LINE: message,
with the physical line a text editor shows, and one bad line never hides the rest. The exit code is 0 when every file
is clean (warnings allowed) and 1 otherwise. Standard library only.

Known answers are set by people only: nothing here suggests, fills in or changes one.
"""

from __future__ import annotations

import argparse
import codecs
import csv
import re
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote, urlsplit

from check_labels import ISO_DATE, MAX_TITLE_LENGTH, SLUG, use_utf8_stdout
from listings import header_problem, physical_lines

HEADER = (
    "conflict_id",
    "category",
    "product",
    "criterion",
    "unit",
    "known_answer",
    "tolerance",
    "how_established",
    "added_by",
    "claim_url",
    "claim_site",
    "claim_tier",
    "claim_value",
    "claim_retrieved_at",
    "claim_quote",
)
# Repeated on every row of a conflict, so they must match on every row.
CONFLICT_COLUMNS = HEADER[1:9]
OPTIONAL_COLUMNS = ("unit", "tolerance", "claim_quote")
REQUIRED_COLUMNS = tuple(column for column in HEADER if column not in OPTIONAL_COLUMNS)
# Highest first: a copy of SourceTier in backend/app/contracts/common.py (DR-03). The backend is never imported here, so
# test_the_tiers_match_the_backends_list_and_order keeps the two the same.
TIERS = ("manufacturer", "lab", "editorial", "community", "retailer")

MAX_VALUE_LENGTH = 80
MAX_REDDIT_VALUE_LENGTH = 30  # a value, never a poster's words (D-53)
MAX_QUOTE_LENGTH = 200
LENGTH_LIMITS = {
    "product": MAX_TITLE_LENGTH,
    "how_established": MAX_TITLE_LENGTH,
    "known_answer": MAX_VALUE_LENGTH,
    "claim_value": MAX_VALUE_LENGTH,
}
FREE_TEXT_COLUMNS = (
    "product",
    "unit",
    "known_answer",
    "how_established",
    "added_by",
    "claim_url",
    "claim_value",
    "claim_quote",
)

CONFLICT_ID = re.compile(r"(?:c|syn)[0-9]{3}")
SYNTHETIC_PREFIX = "syn"
# [0-9] rather than \d in the numbers, which would also accept digits from other scripts such as "٣".
_NUMBER = r"-?[0-9]+(?:\.[0-9]+)?"
BARE_NUMBER = re.compile(_NUMBER)
PLAIN_NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?")
UNIT = re.compile(r"[^\s\d]+")
AMOUNT = re.compile(rf"({_NUMBER})\s*({UNIT.pattern})")
# Plainly a number: 10 h, 5,000, about 10, ₹62,990, 10-12 h, 10+ h. In a text conflict only the plain form with no
# space, such as 5G or 3.5mm, can be a name, so that one form stays text and the rest are refused. No list of spellings
# is complete, which is why any other text value holding a digit gets a warning. Currency in letters needs its space
# or full stop, so that the names RS-232 and INR18650 stay text.
NUMBER_LIKE = re.compile(
    r"(?:(?:about|around|approx\.?|approximately|nearly|roughly|up ?to|over|under|more than|less than|at least"
    r"|~|₹|\$|us\$|€|£|¥|rs\.)\s*|(?:rs|inr|usd)\s+)?"
    r"-?[0-9][0-9,.]*(?:\s*(?:-|–|to)\s*[0-9][0-9,.]*)?\+?\s*[^\s\d]*",
    re.IGNORECASE,
)
PLAIN_NAME_WITH_NUMBER = re.compile(rf"{_NUMBER}{UNIT.pattern}")
DOMAIN = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")
# Names set aside for examples. No real page uses them, so only synthetic rows may.
RESERVED_SITES = ("example", "test", "invalid", "example.com", "example.net", "example.org")
# A link inside free text, and what may trail it in a sentence or brackets.
URL_IN_TEXT = re.compile(r"https?://[^\s,;)]*", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,:!?]}>'\"”’"
# Control, format (zero-width, soft hyphen) and line or paragraph separator characters: invisible, and web pages
# carry them. A value holding one would look right and still count as a different value.
HIDDEN_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")
# The dashes web pages use for a hyphen. NFKC leaves these alone, or turns the no-break hyphen into another of them.
DASHES = dict.fromkeys((0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2212), "-")
# The end of a sentence, then a capital. A decimal point or "approx. 10" has no capital after it.
SECOND_SENTENCE = re.compile(r"[.!?][\"'”’)]*\s+[\"'“‘(]*[A-Z]")
REDDIT_SITE = "reddit.com"
# Reddit's own hosts, the archives D-53 names (Pushshift to ban it) and the best-known copies. They count anywhere in a
# decoded link, tracking parameters aside, so a
# web-archive copy of a thread counts too. A person must still never copy Reddit text: no list can find every copy.
REDDIT_HOSTS = (
    "reddit.com",
    "redd.it",
    "reddit.app.link",
    "photon-reddit.com",
    "pullpush.io",
    "pushshift.io",
    "reveddit.com",
    "unddit.com",
    "teddit.net",
)
# The guards around the hosts keep lookalikes such as notreddit.com and reddit.com.example out.
REDDIT_IN_TEXT = re.compile(
    rf"(?<![a-z0-9-])(?:{'|'.join(re.escape(host) for host in REDDIT_HOSTS)})(?![a-z0-9-]|\.[a-z])", re.IGNORECASE
)
REDDIT_WORD = re.compile(r"\breddit\b(?!\.[a-z])", re.IGNORECASE)
REDDIT_PROFILE = re.compile(r"/(?:user|u|y)/|/r/u_|\bauthor[=:]", re.IGNORECASE)
# A thread id after /comments/, or in an archive's link_id. search, tree and ids are the archives' own paths.
REDDIT_THREAD = re.compile(
    r"(?:/comments/(?!(?:search|tree|ids)\b)|[?&]link_id=(?:t3_)?)([a-z0-9]+)(?:/[^?#]*/([a-z0-9]+))?", re.IGNORECASE
)
TRACKING_PARAMETER = re.compile(r"[?&](?:utm_[^&#\s]*|ref=[^&#\s]*)", re.IGNORECASE)

Row = tuple[int, dict[str, str]]


@dataclass(frozen=True)
class Amount:
    """A number and its unit, as written: "10 h" or "10h"."""

    number: Decimal
    unit: str


def parse_amount(text: str) -> Amount | None:
    match = AMOUNT.fullmatch(text)
    if match is None:
        return None
    return Amount(Decimal(match.group(1)), match.group(2))


def normal_text(text: str) -> str:
    """Text values compare without regard to case, width, spacing or dash style: "USB-C" is " usb-c "."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(DASHES).casefold().split())


def hosts_in_text(text: str) -> list[str]:
    """The hosts of the links in free text, without www. A link that can't be read gives none; the checker names it."""
    hosts = (_host_of(link) for link in _links_in(text))
    return [host.removeprefix("www.") for host in hosts if host]


def _links_in(text: str) -> list[str]:
    return [link.rstrip(TRAILING_PUNCTUATION) for link in URL_IN_TEXT.findall(text)]


def _host_of(link: str) -> str | None:
    try:
        host = urlsplit(link).hostname
    except ValueError:  # such as a stray bracket: https://[paste link here]
        return None
    return host if host and DOMAIN.fullmatch(host) else None  # https://<paste link here> has no real host either


@dataclass(frozen=True)
class Claim:
    """One source's statement about the conflict's criterion."""

    line: int
    url: str
    site: str
    tier: str
    value: str
    retrieved_at: date
    quote: str


@dataclass(frozen=True)
class Conflict:
    """One conflict that passed every check, with its claims in file order. A blank unit means a text conflict."""

    line: int
    conflict_id: str
    category: str
    product: str
    criterion: str
    unit: str
    known_answer: str
    tolerance: Decimal | None
    how_established: str
    added_by: str
    claims: tuple[Claim, ...]

    @property
    def synthetic(self) -> bool:
        return self.conflict_id.startswith(SYNTHETIC_PREFIX)

    def vote(self, value: str) -> Decimal | str:
        """What a written value counts as: 10 h and 10.0 h are one number, USB-C and usb-c are one text."""
        amount = parse_amount(value) if self.unit else None
        return amount.number if amount is not None else normal_text(value)

    @property
    def all_agree(self) -> bool:
        """True when the sources don't really disagree: the same text, or numbers all within the tolerance."""
        votes = {self.vote(claim.value) for claim in self.claims}
        numbers = [vote for vote in votes if isinstance(vote, Decimal)]
        if self.tolerance is not None and len(numbers) == len(votes):
            return max(numbers) - min(numbers) <= self.tolerance
        return len(votes) == 1


@dataclass
class CheckResult:
    """What one file held: the conflicts that passed, every error and every warning."""

    path: str
    rows_checked: int = 0
    missing: bool = False
    conflicts: list[Conflict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class _Problems:
    """Problems found so far; they print in line order, however the checks happened to find them."""

    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []

    def add(self, line: int, message: str) -> None:
        self.found.append((line, message))


def check_file(path: str | Path, today: date | None = None) -> CheckResult:
    """Read one conflicts CSV and check the file, every row and every conflict."""
    result = CheckResult(path=str(path))
    problems = _Problems()
    rows = _read_rows(Path(path), result, problems)
    result.rows_checked = len(rows)

    last_allowed = (today or datetime.now(UTC).date()) + timedelta(days=1)
    by_id: dict[str, list[Row]] = {}
    bad_ids: set[str] = set()
    for line, values in rows:
        found = _row_problems(values, last_allowed)
        for message in found:
            problems.add(line, message)
        conflict_id = values["conflict_id"]
        if CONFLICT_ID.fullmatch(conflict_id):
            by_id.setdefault(conflict_id, []).append((line, values))
            if found:
                bad_ids.add(conflict_id)

    for conflict_id, conflict_rows in by_id.items():
        found_in_conflict = _conflict_problems(conflict_id, conflict_rows)
        for line, message in found_in_conflict:
            problems.add(line, message)
        if found_in_conflict:
            bad_ids.add(conflict_id)
    bad_ids |= _file_problems(by_id, problems)

    for conflict_id, conflict_rows in by_id.items():
        if conflict_id not in bad_ids:
            conflict = _build_conflict(conflict_id, conflict_rows)
            result.conflicts.append(conflict)
            if conflict.all_agree:
                result.warnings.append(
                    f"{result.path}:{conflict.line}: warning: all {len(conflict.claims)} claims in {conflict_id} "
                    "agree, so this is not a real conflict (ml/DATA.md section 9). It still counts in the baselines"
                )
            if _text_values_hold_numbers(conflict):
                result.warnings.append(
                    f"{result.path}:{conflict.line}: warning: the text values in {conflict_id} hold numbers. If they "
                    "are amounts, fill in the unit column and the tolerance, or they must match exactly to count. "
                    "If they are names, this is fine"
                )

    problems.found.sort(key=lambda item: item[0])
    result.errors = [f"{result.path}:{line}: {message}" for line, message in problems.found]
    return result


# --- reading ------------------------------------------------------------------------------------------


def _read_rows(path: Path, result: CheckResult, problems: _Problems) -> list[Row]:
    """The rows with the right columns. Cells never hold a line break, so each physical line is read on its own."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        result.missing = True
        problems.add(1, "file not found")
        return []
    except OSError as exc:
        problems.add(1, f"cannot read the file ({exc})")
        return []
    # Excel's "CSV UTF-8" adds this mark. It comes off here, not through utf-8-sig, whose error offsets leave it out.
    data = data.removeprefix(codecs.BOM_UTF8)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        before = data[: exc.start].replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        problems.add(before.count(b"\n") + 1, 'the file is not UTF-8 text; save it as "CSV UTF-8"')
        return []
    lines = physical_lines(text)  # split at \n, \r\n or \r only, as listings.py does
    if not lines:
        problems.add(1, "the file is empty; the first line must be the header")
        return []

    rows: list[Row] = []
    for number, line in enumerate(lines, start=1):
        record = _read_line(line, number, problems)
        if record is None:
            if number == 1:
                return []
            continue
        if number == 1:
            problem = header_problem(record, HEADER)
            if problem:
                problems.add(1, f"{problem}. The header must be exactly: {','.join(HEADER)}")
                return []
            continue
        if not any(cell.strip() for cell in record):
            continue  # a blank line, often left at the end by spreadsheets
        if len(record) != len(HEADER):
            problems.add(
                number,
                f"expected {len(HEADER)} columns, found {len(record)}; put quotes around text that contains commas",
            )
            continue
        rows.append((number, {column: cell.strip() for column, cell in zip(HEADER, record, strict=True)}))
    return rows


def _read_line(line: str, number: int, problems: _Problems) -> list[str] | None:
    if "\x00" in line:
        problems.add(number, "the line holds a NUL character, so the file is damaged here; retype the line")
        return None
    try:
        return next(csv.reader([line.rstrip("\r\n")], strict=True), [])
    except csv.Error as exc:
        problems.add(number, f"this line cannot be read as CSV ({exc}); check the quotes on this line")
        return None


# --- one row ------------------------------------------------------------------------------------------


def _row_problems(values: dict[str, str], last_allowed: date) -> list[str]:
    problems = [f"{column} is empty" for column in REQUIRED_COLUMNS if not values[column]]

    conflict_id = values["conflict_id"]
    if conflict_id and not CONFLICT_ID.fullmatch(conflict_id):
        problems.append(
            f"conflict_id {conflict_id!r} must be c and 3 digits such as c001 (syn001 for a made-up example)"
        )
    for column in ("category", "criterion"):
        if values[column] and not SLUG.fullmatch(values[column]):
            problems.append(f"{column} {values[column]!r} is not a lowercase slug such as battery_life")
    for column, limit in LENGTH_LIMITS.items():
        if len(values[column]) > limit:
            problems.append(f"{column} has {len(values[column])} characters; the limit is {limit}")
    for column in FREE_TEXT_COLUMNS:
        if any(unicodedata.category(character) in HIDDEN_CATEGORIES for character in values[column]):
            problems.append(f"{column} holds a hidden character that looks like nothing; retype it")

    problems += _answer_problems(values)
    problems += _how_established_problems(values["how_established"], conflict_id)
    problems += _source_problems(values)
    written_date = values["claim_retrieved_at"]
    if written_date:
        problems += _date_problems(written_date, last_allowed)
    problems += _quote_problems(values["claim_quote"])
    return problems


def _how_established_problems(text: str, conflict_id: str) -> list[str]:
    problems: list[str] = []
    if _points_to_reddit(text) or REDDIT_WORD.search(TRACKING_PARAMETER.sub("", text)):
        problems.append(
            "how_established points to Reddit, and a Reddit post can't establish the known answer; "
            "use the maker's spec page, a lab's measurement or the product itself"
        )
    links = _links_in(text)
    if any(_has_login(link) for link in links):  # the link stays out of this message, as in _url_problem
        problems.append("how_established holds a user name or password in a link; take them out")
    hosts = [_host_of(link) for link in links]
    if None in hosts:
        problems.append("how_established holds a link that can't be read; fix it, or write it in words")
    if _is_real_id(conflict_id) and any(host and _is_made_up_site(host) for host in hosts):
        problems.append("how_established links to a made-up site, which only a synthetic example (syn001) may use")
    return problems


def _has_login(link: str) -> bool:
    try:
        parts = urlsplit(link)
        return parts.username is not None or parts.password is not None
    except ValueError:
        return False


def _is_made_up_site(site: str) -> bool:
    return any(host_is_under(site, reserved) for reserved in RESERVED_SITES)


def _points_to_reddit(text: str) -> bool:
    return REDDIT_IN_TEXT.search(unquote(TRACKING_PARAMETER.sub("", text))) is not None


def _answer_problems(values: dict[str, str]) -> list[str]:
    """The known answer, the claim's value and the tolerance. A blank unit means a text conflict."""
    unit, tolerance = values["unit"], values["tolerance"]
    if unit and not UNIT.fullmatch(unit):
        return [f"unit {unit!r} must be one word with no digits, such as h, mAh or GB"]

    problems: list[str] = []
    for column in ("known_answer", "claim_value"):
        value = values[column]
        if value:
            problem = _number_problem(column, value, unit) if unit else _text_problem(column, value)
            if problem:
                problems.append(problem)
    if not unit:
        if tolerance:
            problems.append(f"tolerance {tolerance!r} must be empty for a text answer")
    elif not tolerance:
        problems.append("tolerance is empty; a number needs its tolerance, such as 0.5 (0 means the exact number)")
    elif not PLAIN_NUMBER.fullmatch(tolerance):
        problems.append(f"tolerance {tolerance!r} must be a plain number of 0 or more, in {unit}, such as 0.5")
    return problems


def _number_problem(column: str, value: str, unit: str) -> str | None:
    if BARE_NUMBER.fullmatch(value):
        return f"{column} {value!r} has no unit; write it as {value} {unit}"
    amount = parse_amount(value)
    if amount is None:
        return f"{column} {value!r} must be a number and a unit, such as 10 {unit}, with no commas or words"
    if amount.unit != unit:
        return (
            f"{column} {value!r} has the unit {amount.unit!r}, but this conflict's unit is {unit!r}; "
            "work it out by hand and retype it, because units are never converted for you"
        )
    return None


def _text_problem(column: str, value: str) -> str | None:
    if NUMBER_LIKE.fullmatch(value) and not PLAIN_NAME_WITH_NUMBER.fullmatch(value):
        return (
            f"{column} {value!r} reads as a number. If it is an amount, fill in the unit column and the tolerance, and "
            "write every value as a number and that unit. If it is a name, write it without the space, such as 3.5mm, "
            "or in words"
        )
    return None


def _source_problems(values: dict[str, str]) -> list[str]:
    url, site, tier, quote = values["claim_url"], values["claim_site"], values["claim_tier"], values["claim_quote"]
    problems: list[str] = []
    if tier and tier not in TIERS:
        problems.append(f"claim_tier {tier!r} must be one of: {', '.join(TIERS)}")
    url_problem = _url_problem(url) if url else None
    if url_problem:
        problems.append(url_problem)
    good_url = bool(url) and url_problem is None

    if good_url and _points_to_reddit(url):
        if tier and tier != "community":
            problems.append(
                f"claim_tier {tier!r}: a Reddit claim must be tier community, because Reddit is never editorial"
            )
        if quote:
            problems.append("claim_quote must be empty on a Reddit claim: no Reddit text is ever stored (D-53)")
        if site and site != REDDIT_SITE:
            problems.append(f"claim_site {site!r} must be {REDDIT_SITE} on a Reddit claim")
        if len(values["claim_value"]) > MAX_REDDIT_VALUE_LENGTH:
            problems.append(
                f"claim_value has {len(values['claim_value'])} characters; a Reddit claim keeps only the value, in at "
                f"most {MAX_REDDIT_VALUE_LENGTH} characters, and never the poster's words (D-53)"
            )
        if REDDIT_PROFILE.search(unquote(url)):
            problems.append(
                "claim_url links to a person's profile; link to the thread, as user names are not kept (D-53)"
            )
    elif site:
        if not DOMAIN.fullmatch(site):
            problems.append(f"claim_site {site!r} must be a lowercase domain such as techdesk.example")
        elif site.startswith("www."):
            problems.append(f"claim_site {site!r} must leave out the www.")
        elif _is_real_id(values["conflict_id"]) and _is_made_up_site(site):
            problems.append(f"claim_site {site!r} is a made-up site, which only a synthetic example (syn001) may use")
        elif good_url and not host_is_under(urlsplit(url).hostname or "", site):
            problems.append(f"claim_site {site!r} does not match the host of claim_url")
    return problems


def _is_real_id(conflict_id: str) -> bool:
    return CONFLICT_ID.fullmatch(conflict_id) is not None and not conflict_id.startswith(SYNTHETIC_PREFIX)


def _url_problem(url: str) -> str | None:
    # A URL with an @ in it may hold a login, so it stays out of every message: a real password never reaches a log.
    shown = "claim_url" if "@" in url else f"claim_url {url!r}"
    try:
        parts = urlsplit(url)
        has_login = parts.username is not None or parts.password is not None
        hostname = parts.hostname
    except ValueError:
        return f"{shown} is not a valid web address"
    if has_login:
        return "claim_url holds a user name or password; take them out"
    if parts.scheme not in ("http", "https"):
        return f"{shown} must start with http:// or https://"
    if not hostname or any(character.isspace() for character in url):
        return f"{shown} is not a valid web address"
    return None


def host_is_under(host: str, site: str) -> bool:
    """True for the site itself and for anything under it: support.nova.example is under nova.example."""
    host = host.lower()
    return host == site or host.endswith(f".{site}")


def _page_key(url: str) -> str:
    """Two links to one page: the scheme, www., the host's case, a trailing slash, a #fragment and utm_ tracking don't
    count. A Reddit thread is one page on any host or archive, and each comment in it is a page of its own."""
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    path = parts.path.rstrip("/")
    if host_is_under(host, "redd.it"):
        return f"reddit:{path.strip('/').lower()}"
    thread = REDDIT_THREAD.search(unquote(url)) if _points_to_reddit(url) else None
    if thread is not None:
        return f"reddit:{thread[1].lower()}" + (f"/{thread[2].lower()}" if thread[2] else "")
    query = "&".join(piece for piece in parts.query.split("&") if not piece.lower().startswith(("utm_", "ref=")))
    return f"{host}{path}?{query}"


def _date_problems(written: str, last_allowed: date) -> list[str]:
    if not ISO_DATE.fullmatch(written):
        return [f"claim_retrieved_at {written!r} is not a real date written as YYYY-MM-DD"]
    try:
        retrieved = date.fromisoformat(written)
    except ValueError:  # such as 2026-02-30
        return [f"claim_retrieved_at {written!r} is not a real date written as YYYY-MM-DD"]
    if retrieved > last_allowed:
        return [f"claim_retrieved_at {written!r} is in the future"]
    return []


def _quote_problems(quote: str) -> list[str]:
    if len(quote) > MAX_QUOTE_LENGTH:
        return [f"claim_quote has {len(quote)} characters; keep it to one short sentence of at most {MAX_QUOTE_LENGTH}"]
    if SECOND_SENTENCE.search(quote):
        return ["claim_quote holds more than one sentence; keep it to one short sentence"]
    return []


# --- one conflict, and the whole file -----------------------------------------------------------------


def _conflict_problems(conflict_id: str, rows: Sequence[Row]) -> list[tuple[int, str]]:
    first_line, first = rows[0]
    problems: list[tuple[int, str]] = []
    if len(rows) < 2:
        problems.append((first_line, f"{conflict_id} has {len(rows)} claim; a conflict needs at least 2 claims"))

    pages: dict[str, int] = {}
    for line, values in rows:
        for column in CONFLICT_COLUMNS:
            if values[column] != first[column]:
                # A value with an @ in it may hold a login, so it stays out of the message.
                held = "" if "@" in values[column] + first[column] else f" {values[column]!r} (not {first[column]!r})"
                problems.append(
                    (
                        line,
                        f"{column}{held} differs from line {first_line}, the first row of {conflict_id}; "
                        "it must match on every row of a conflict",
                    )
                )
        url = values["claim_url"]
        if url and _url_problem(url) is None:
            key = _page_key(url)
            if key in pages:
                problems.append(
                    (line, f"claim_url is the same page as line {pages[key]}; each claim is its own source")
                )
            else:
                pages.setdefault(key, line)
    if not first["unit"]:
        problems += _mixed_unit_problems(rows)
    return problems


def _mixed_unit_problems(rows: Sequence[Row]) -> list[tuple[int, str]]:
    """Blank unit, and values such as 10h and 600min. 5G against 4G or 5g stays text: the same ending on both."""
    first_line, first = rows[0]
    written = [(first_line, "known_answer", first["known_answer"])]
    written += [(line, "claim_value", values["claim_value"]) for line, values in rows]
    baseline: tuple[int, str, str] | None = None
    problems: list[tuple[int, str]] = []
    for line, column, value in written:
        amount = parse_amount(value)
        if amount is None:
            continue
        if baseline is None:
            baseline = (line, value, amount.unit)
        elif amount.unit.casefold() != baseline[2].casefold():
            problems.append(
                (
                    line,
                    f"{column} {value!r} has a different unit from {baseline[1]!r} on line {baseline[0]}; for amounts, "
                    "fill in the unit column and retype them in one unit. For names, write them out in words",
                )
            )
    return problems


def _file_problems(by_id: dict[str, list[Row]], problems: _Problems) -> set[str]:
    """Checks across conflicts. Returns the ids they make unusable."""
    bad_ids: set[str] = set()
    first_id = next(iter(by_id), "")
    first_of: dict[tuple[str, str], str] = {}
    for conflict_id, rows in by_id.items():
        line, values = rows[0]
        if conflict_id.startswith(SYNTHETIC_PREFIX) != first_id.startswith(SYNTHETIC_PREFIX):
            problems.add(
                line,
                f"{conflict_id} can't share a file with {first_id}: synthetic and real conflicts stay apart",
            )
            bad_ids.add(conflict_id)
        key = (normal_text(values["product"]), values["criterion"])
        if key in first_of:
            problems.add(
                line,
                f"{conflict_id} has the same product and criterion as {first_of[key]}; a conflict is entered only once",
            )
            bad_ids.add(conflict_id)
        else:
            first_of[key] = conflict_id
    return bad_ids


def _text_values_hold_numbers(conflict: Conflict) -> bool:
    """10h against 12h, or 5G against 4G. A program can't tell an amount from a name here, so a person decides."""
    if conflict.unit:
        return False
    values = (conflict.known_answer, *(claim.value for claim in conflict.claims))
    return any(character.isdigit() for value in values for character in value)


def _build_conflict(conflict_id: str, rows: Sequence[Row]) -> Conflict:
    """Every value here has passed its check."""
    line, first = rows[0]
    return Conflict(
        line=line,
        conflict_id=conflict_id,
        category=first["category"],
        product=first["product"],
        criterion=first["criterion"],
        unit=first["unit"],
        known_answer=first["known_answer"],
        tolerance=Decimal(first["tolerance"]) if first["tolerance"] else None,
        how_established=first["how_established"],
        added_by=first["added_by"],
        claims=tuple(
            Claim(
                line=claim_line,
                url=values["claim_url"],
                site=values["claim_site"],
                tier=values["claim_tier"],
                value=values["claim_value"],
                retrieved_at=date.fromisoformat(values["claim_retrieved_at"]),
                quote=values["claim_quote"],
            )
            for claim_line, values in rows
        ),
    )


# --- the command line ---------------------------------------------------------------------------------


class Parser(argparse.ArgumentParser):
    """Usage errors exit with 1, so that exit code 2 from the runner always means "the file is missing"."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


def summary_line(result: CheckResult) -> str:
    return (
        f"{result.path}: {_count(result.rows_checked, 'row')} checked, {_count(len(result.conflicts), 'conflict')}, "
        f"{_count(len(result.errors), 'error')}, {_count(len(result.warnings), 'warning')}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    parser = Parser(
        prog="check_conflicts.py",
        description="Check seeded conflicts files against the format in ml/DATA.md, section 9.",
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="a conflicts CSV file")
    args = parser.parse_args(argv)
    paths: list[str] = args.files

    results = [check_file(path) for path in paths]
    for result in results:
        for line in (*result.errors, *result.warnings, summary_line(result)):
            print(line)
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
