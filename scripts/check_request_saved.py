"""Was a search request saved to the database? Prints only "saved" or "not found".

Run from the repository root, with the backend's virtual environment active (it needs psycopg):
    python scripts/check_request_saved.py sess_0123456789abcdef01234567

It reads DATABASE_URL the way the backend does (the environment, then the root .env), looks for the row in
public.requests by session ID, and exits 0 for "saved" and 1 for "not found". Anything else (no DATABASE_URL, a
paused database, a malformed session ID) goes to stderr with exit code 2. It never prints the URL, the request text
or anything else from the row.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor import find_env_file, settings

SESSION_ID = re.compile(r"^sess_[A-Za-z0-9]{6,40}$")  # the contract's SessionId
QUERY = "select exists (select 1 from public.requests where session_id = %s)"


def connect_psycopg(url: str) -> Any:
    import psycopg  # only here, so the rest of the script (and its tests) need no driver

    return psycopg.connect(url, connect_timeout=5)


def is_saved(session_id: str, url: str, connect: Callable[[str], Any] = connect_psycopg) -> bool:
    with connect(url) as conn:
        row = conn.execute(QUERY, (session_id,)).fetchone()
    return bool(row and row[0])


def main(
    argv: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
    connect: Callable[[str], Any] = connect_psycopg,
) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not SESSION_ID.match(args[0]):
        print("Give one session ID, like sess_0123456789abcdef01234567.", file=sys.stderr)
        return 2
    values = settings(find_env_file(), os.environ if environ is None else environ)
    url = (values.get("DATABASE_URL") or "").strip()
    if not url:
        print("DATABASE_URL is not set, so there is no database to check.", file=sys.stderr)
        return 2
    try:
        saved = is_saved(args[0], url, connect)
    except Exception as exc:  # the type only: a driver message can contain the host
        print(f"The database could not be reached ({type(exc).__name__}).", file=sys.stderr)
        return 2
    print("saved" if saved else "not found")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
