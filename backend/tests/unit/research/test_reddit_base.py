"""The rules every Reddit source shares (US-12): permalinks, ids, hashed authors, skipped items, limits."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from app.contracts import SourceTier, SourceType
from app.research.reddit import IntervalLimiter, RequestBudget
from app.research.reddit.base import (
    author_hash,
    bare_id,
    best_matches,
    is_bot,
    is_gone,
    make_source,
    match_score,
    newest,
    permalink,
    query_words,
    utc_from_epoch,
    valid_subreddit,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def _source(**changes: object) -> object:
    args: dict[str, object] = {
        "origin": "arctic_shift",
        "subreddit": "SuggestALaptop",
        "thread_id": "1abc2de",
        "title": "Laptop for coding under 70k?",
        "text": "Laptop for coding under 70k?\n\nNeeds 16 GB RAM.",
        "published_at": datetime(2026, 10, 30, tzinfo=UTC),
        "author_digest": None,
        "fetched_at": NOW,
    }
    return make_source(**{**args, **changes})  # type: ignore[arg-type]


def test_permalinks_are_canonical_reddit_addresses() -> None:
    assert permalink("laptops", "1abc2de") == "https://www.reddit.com/r/laptops/comments/1abc2de/"
    assert permalink("laptops", "1abc2de", "kx9z") == "https://www.reddit.com/r/laptops/comments/1abc2de/_/kx9z/"


@pytest.mark.parametrize(
    ("raw", "clean"),
    [("t3_1abc2de", "1abc2de"), ("1ABC2DE", "1abc2de"), ("t1_kx9z", "kx9z"), ("", None), (None, None)],
)
def test_bare_ids(raw: str | None, clean: str | None) -> None:
    assert bare_id(raw) == clean


@pytest.mark.parametrize("bad", ["../etc", "1abc 2de", "t3_", "abc/def", "a" * 20])
def test_odd_ids_are_refused(bad: str) -> None:
    assert bare_id(bad) is None


@pytest.mark.parametrize(("name", "ok"), [("SuggestALaptop", True), ("4kTV", True), ("r/laptops", False), ("a", False)])
def test_subreddit_names(name: str, ok: bool) -> None:
    assert valid_subreddit(name) is ok


def test_author_hash_is_salted_stable_and_never_the_name() -> None:
    digest = author_hash("Coder_Priya", "salt-one")
    assert digest == author_hash("coder_priya ", "salt-one")  # the same person, however it is written
    assert digest != author_hash("Coder_Priya", "salt-two")  # without our salt, no one can rebuild it
    assert digest != hashlib.sha256(b"coder_priya").hexdigest()
    assert digest is not None and "priya" not in digest.lower() and len(digest) == 64


@pytest.mark.parametrize(("author", "salt"), [("[deleted]", "s"), ("", "s"), (None, "s"), ("Coder_Priya", None)])
def test_no_author_hash_without_a_salt_or_a_real_name(author: str | None, salt: str | None) -> None:
    assert author_hash(author, salt) is None


def test_gone_and_bot_items() -> None:
    assert all(is_gone(t) for t in ("[deleted]", "[removed]", "", "  ", None, "[Deleted]"))
    assert not is_gone("I bought this last month")
    assert is_bot("AutoModerator") and not is_bot("Coder_Priya")


def test_a_reddit_source_follows_the_contract() -> None:
    source = _source(author_digest="b" * 64, comment_id="kx9z")
    assert source is not None
    assert str(source.url) == "https://www.reddit.com/r/SuggestALaptop/comments/1abc2de/_/kx9z/"  # type: ignore[attr-defined]
    assert (source.type, source.tier, source.via) == (SourceType.REDDIT, SourceTier.COMMUNITY, "api")  # type: ignore[attr-defined]
    assert (source.community, source.source_detail) == ("r/SuggestALaptop", "arctic_shift")  # type: ignore[attr-defined]
    assert source.text_hash == hashlib.sha256(source.text.encode()).hexdigest()  # type: ignore[attr-defined]
    assert source.id == f"src_{hashlib.sha256(str(source.url).encode()).hexdigest()[:16]}"  # type: ignore[attr-defined]
    assert source.fetched_at == NOW  # type: ignore[attr-defined]


def test_long_text_is_capped_and_bad_items_are_skipped_not_raised() -> None:
    long = _source(text="word " * 5000)
    assert long is not None and 11_990 < len(long.text) <= 12_000  # type: ignore[attr-defined]
    assert long.text_hash == hashlib.sha256(long.text.encode()).hexdigest()  # type: ignore[attr-defined]
    assert _source(text="   ") is None
    assert _source(subreddit="not a sub") is None
    assert _source(thread_id="../x") is None
    assert _source(comment_id="bad id") is None


def test_dates() -> None:
    assert utc_from_epoch(1761955200) == datetime(2025, 11, 1, tzinfo=UTC)
    assert utc_from_epoch(None) is None
    assert utc_from_epoch(1e20) is None
    a, b = _source(), _source(thread_id="2xyz", published_at=datetime(2026, 11, 1, tzinfo=UTC))
    assert newest([a, b]) == datetime(2026, 11, 1, tzinfo=UTC)  # type: ignore[list-item]
    assert newest([]) is None


def test_the_request_budget_counts_down() -> None:
    budget = RequestBudget(2)
    assert budget.take() and budget.take()
    assert not budget.take()
    assert (budget.used, budget.left) == (2, 0)


def test_the_interval_limiter_spaces_requests() -> None:
    now = [100.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = IntervalLimiter(1.0, clock=lambda: now[0], sleep=sleep)
    limiter.wait()
    now[0] += 0.25
    limiter.wait()
    now[0] += 5
    limiter.wait()
    assert slept == [pytest.approx(0.75)]


def test_query_words_drop_filler_and_repeats() -> None:
    assert query_words("Best laptop for coding under 70k, laptop!") == ["laptop", "coding", "70k"]
    assert query_words("for the best") == []


def test_match_score_counts_words_and_their_longer_forms() -> None:
    words = ["laptop", "coding"]
    assert match_score(words, "Laptops for Coding?") == 2
    assert match_score(words, "a gaming laptop") == 1
    assert match_score(words, "lap top") == 0


def test_best_matches_rank_by_words_then_newest() -> None:
    old = _source(thread_id="1old", title="coding laptop", published_at=datetime(2026, 9, 1, tzinfo=UTC))
    new = _source(thread_id="2new", title="laptop", text="laptop", published_at=datetime(2026, 11, 1, tzinfo=UTC))
    newer = _source(thread_id="3new", title="laptop", text="laptop", published_at=datetime(2026, 11, 2, tzinfo=UTC))
    off = _source(thread_id="4off", title="fridge", text="fridge")
    ranked = best_matches([new, off, old, newer], ["laptop", "coding"], 3)  # type: ignore[list-item]
    assert ranked == [old, newer, new]
    assert best_matches([new, newer], [], 1) == [newer]  # type: ignore[list-item]
