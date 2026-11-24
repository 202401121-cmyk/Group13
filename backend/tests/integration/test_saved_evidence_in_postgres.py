"""US-29 on a real Postgres: every cited claim of a saved result is found in the database with its source, its quote
(or none, for Reddit) and its time, and the saved row holds no Reddit text (D-53) (db).

The five recorded gate runs (tests/fixtures/recorded/gate_*) are replayed offline, as evals/gate.py does, and each
result is saved the way the pipeline saves it (DatabaseResultStore, so retention runs at the save). Then, with a fixed
seed, 20 of each result's cited claims are looked up in the saved row by SQL alone. Same database rules as
test_requests_table.py, which owns the fixture: a throwaway database with every migration applied, never Supabase.
"""

from __future__ import annotations

import json
import random
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest

from app.contracts import Claim, SearchResult, Source
from app.contracts import examples as ex
from app.db import DatabaseSettings, PostgresRequestStore
from app.pipeline import DatabaseResultStore
from app.research.retention import REDDIT_TEXT_NOT_KEPT, VIDEO_TEXT_NOT_KEPT
from evals.gate import recordings, replay
from tests.integration import test_requests_table as requests_table

pytestmark = pytest.mark.db

database_url = requests_table.database_url  # a fresh database of its own for this file
SEED = 29
PICKED = 20
MARKERS = (REDDIT_TEXT_NOT_KEPT, VIDEO_TEXT_NOT_KEPT)

# One cited claim, as the saved row holds it: the citation joined to its source, by SQL alone.
FIND_CLAIM = """
select c ->> 'source_id', c ->> 'quote', c ->> 'retrieved_at', c -> 'value', c ->> 'polarity', c ->> 'claim_type',
       s ->> 'url', s ->> 'fetched_at'
from public.searches,
     jsonb_array_elements(result -> 'citations') as c,
     jsonb_array_elements(result -> 'sources') as s
where id = %s and c ->> 'claim_id' = %s and s ->> 'id' = c ->> 'source_id'
"""


@pytest.fixture
def store(database_url: str) -> Iterator[PostgresRequestStore]:
    built = PostgresRequestStore(DatabaseSettings(url=database_url, timeout_s=5.0))
    yield built
    built.close()


@pytest.fixture
def owner(database_url: str) -> uuid.UUID:
    user = uuid.uuid4()
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("insert into auth.users (id) values (%s)", (user,))
    return user


def _owned(result: SearchResult, owner: uuid.UUID) -> SearchResult:
    request = {**result.request.model_dump(), "user_id": str(owner)}
    return SearchResult.model_validate({**result.model_dump(), "request": request})


def _save(store: PostgresRequestStore, database_url: str, result: SearchResult) -> uuid.UUID:
    """Saved as the pipeline saves it, then found by its search ID."""
    assert DatabaseResultStore(store).save(result)
    with psycopg.connect(database_url) as conn:
        row = conn.execute(
            "select id from public.searches where result ->> 'search_id' = %s order by created_at desc limit 1",
            (result.search_id,),
        ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def _reddit_class(source: Source) -> bool:
    return source.data_class.value == "reddit"


def _cited(result: SearchResult) -> list[str]:
    return list(dict.fromkeys(c.claim_id for c in result.citations))


def _replays() -> list[tuple[str, SearchResult, dict[str, Claim]]]:
    runs = []
    for folder in recordings():
        replayed = replay(folder)
        claims = {c.id: c for out in replayed.results for c in out.claims}
        runs.append((folder.name, replayed.result, claims))
    return runs


def test_twenty_random_cited_claims_of_each_recorded_run_are_found_in_the_database_with_their_evidence(
    store: PostgresRequestStore, database_url: str, owner: uuid.UUID
) -> None:
    runs = _replays()
    assert len(runs) == 5
    misses: list[str] = []
    looked = 0
    for name, result, claims in runs:
        cited = _cited(result)
        assert cited, f"{name}: the replay cites no claim"
        saved_id = _save(store, database_url, _owned(result, owner))
        sources = {s.id: s for s in result.sources}
        picked = random.Random(SEED).sample(cited, min(PICKED, len(cited)))
        with psycopg.connect(database_url) as conn:
            for claim_id in picked:
                looked += 1
                claim = claims[claim_id]
                row = conn.execute(FIND_CLAIM, (saved_id, claim_id)).fetchone()
                if row is None:
                    misses.append(f"{name}: {claim_id} has no citation with its source in the saved row")
                    continue
                source_id, quote, retrieved, value, polarity, kind, url, fetched = row
                source = sources[claim.source_id]
                wanted_quote = None if _reddit_class(source) else claim.quote
                checks = {
                    "source": source_id == claim.source_id and url == str(source.url) and fetched is not None,
                    "quote": quote == wanted_quote,
                    "time": retrieved is not None and _same_time(retrieved, claim),
                    "reading": polarity == claim.polarity and kind == claim.claim_type.value,
                    "value": value == _saved_value(claim, source),
                }
                misses += [f"{name}: {claim_id} {what}" for what, ok in checks.items() if not ok]
    assert looked >= len(runs)  # at least one claim from each run, at most PICKED
    assert misses == []


def _same_time(saved: str, claim: Claim) -> bool:
    from datetime import datetime

    return datetime.fromisoformat(saved.replace("Z", "+00:00")) == claim.retrieved_at


def _saved_value(claim: Claim, source: Source) -> Any:
    """What the saved row must hold for the claim's value: the marker for text from Reddit or a video's viewers."""
    if isinstance(claim.value, str) and _reddit_class(source):
        return VIDEO_TEXT_NOT_KEPT if source.type.value == "youtube" else REDDIT_TEXT_NOT_KEPT
    return claim.value


def _reddit_words(result: SearchResult, claims: dict[str, Claim]) -> set[str]:
    """Every piece of Reddit or video-viewer text the in-memory result holds or cites, long enough to be told apart."""
    words: set[str] = set()
    reddit = {s.id: s for s in result.sources if _reddit_class(s)}
    for source in reddit.values():
        words |= {source.title, source.text, *(comment.text for comment in source.comments)}
    for claim in claims.values():
        if claim.source_id in reddit:
            words.add(claim.quote)
            if isinstance(claim.value, str):
                words.add(claim.value)
    return {w for w in words if w and len(w) >= 12 and w not in MARKERS}


def test_a_saved_row_holds_no_reddit_text(store: PostgresRequestStore, database_url: str, owner: uuid.UUID) -> None:
    """D-53, for the recorded runs and for the example result, which cites a Reddit claim with a text value."""
    example_claims = {c.id: c for c in ex.claims()}
    runs = [*_replays(), ("example", ex.search_result(), example_claims)]
    checked = 0
    for name, result, claims in runs:
        words = _reddit_words(result, claims)
        saved_id = _save(store, database_url, _owned(result, owner))
        with psycopg.connect(database_url) as conn:
            row = conn.execute("select result::text from public.searches where id = %s", (saved_id,)).fetchone()
        assert row is not None
        saved = json.loads(row[0])
        text = json.dumps(saved, ensure_ascii=False)
        assert not [w for w in words if w in text], f"{name}: Reddit words reached the saved row"
        checked += len(words)
    assert checked > 0  # the example's Reddit thread and claim at least
