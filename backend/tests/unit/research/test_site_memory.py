"""US-31: a site that keeps failing is skipped for 24 hours, then tried again; a rate limit counts less than a block."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.research.cache import ResearchCache
from app.research.sites import (
    SKIP_AT,
    SKIPPED_DETAIL,
    SiteMemory,
    SiteOutcome,
    site_of,
    skipped_detail,
    skipped_sentence,
)

SITE = "https://www.reviews.example/laptops/nova-book-14"


class Now:
    def __init__(self) -> None:
        self.at = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def now() -> Now:
    return Now()


@pytest.fixture
def memory(tmp_path: Path, now: Now) -> SiteMemory:
    return SiteMemory(ResearchCache(folder=tmp_path, now=now), now=now)


def test_three_blocks_skip_a_site(memory: SiteMemory) -> None:
    memory.record(SITE, "blocked")
    memory.record(SITE, "blocked")
    assert not memory.skipped(SITE)
    memory.record(SITE, "blocked")
    assert memory.skipped(SITE)


def test_a_failure_that_repeats_counts_as_much_as_a_block(memory: SiteMemory) -> None:
    outcomes: list[SiteOutcome] = ["failed", "blocked", "failed"]
    for outcome in outcomes:
        memory.record(SITE, outcome)
    assert memory.skipped(SITE)


def test_a_429_counts_half(memory: SiteMemory) -> None:
    memory.record(SITE, "rate_limited")
    memory.record(SITE, "rate_limited")
    assert memory.score(SITE) == 1.0  # two rate limits weigh what one block does
    for _ in range(3):
        memory.record(SITE, "rate_limited")
    assert memory.score(SITE) == 2.5 and not memory.skipped(SITE)
    memory.record(SITE, "rate_limited")  # the sixth
    assert memory.skipped(SITE)


def test_the_skip_ends_after_24_hours_and_the_site_is_tried_again(memory: SiteMemory, now: Now) -> None:
    for _ in range(int(SKIP_AT)):
        memory.record(SITE, "blocked")
    now.at += timedelta(hours=23, minutes=59)
    assert memory.skipped(SITE)
    now.at += timedelta(minutes=1)
    assert not memory.skipped(SITE)
    assert memory.score(SITE) == 0  # a clean slate: one more failure does not skip it again at once
    memory.record(SITE, "blocked")
    assert not memory.skipped(SITE)


def test_failures_older_than_24_hours_no_longer_count(memory: SiteMemory, now: Now) -> None:
    memory.record(SITE, "blocked")
    memory.record(SITE, "blocked")
    now.at += timedelta(hours=24, seconds=1)
    memory.record(SITE, "blocked")
    assert memory.score(SITE) == 1.0 and not memory.skipped(SITE)


def test_a_page_read_clears_the_sites_score(memory: SiteMemory) -> None:
    memory.record(SITE, "blocked")
    memory.record(SITE, "blocked")
    memory.record(SITE, "ok")
    memory.record(SITE, "blocked")
    assert memory.score(SITE) == 1.0 and not memory.skipped(SITE)


def test_a_skipped_sites_24_hours_are_not_stretched(memory: SiteMemory, now: Now) -> None:
    for _ in range(3):
        memory.record(SITE, "blocked")
    now.at += timedelta(hours=12)
    memory.record(SITE, "blocked")  # a late reply from before the skip
    now.at += timedelta(hours=12)
    assert not memory.skipped(SITE)


def test_the_score_is_kept_between_searches(tmp_path: Path, now: Now) -> None:
    first = SiteMemory(ResearchCache(folder=tmp_path, now=now), now=now)
    for _ in range(3):
        first.record(SITE, "blocked")
    next_search = SiteMemory(ResearchCache(folder=tmp_path, now=now), now=now)  # a new cache, as each search makes
    assert next_search.skipped(SITE)
    assert (tmp_path / "sites").is_dir()


def test_with_the_cache_off_nothing_is_remembered(tmp_path: Path, now: Now) -> None:
    off = SiteMemory(ResearchCache(folder=tmp_path, now=now, enabled=False), now=now)
    for _ in range(5):
        off.record(SITE, "blocked")
    assert not off.skipped(SITE)


def test_one_site_whatever_the_page_or_case(memory: SiteMemory) -> None:
    memory.record("https://WWW.Reviews.Example/a", "blocked")
    memory.record("http://reviews.example/b?x=1", "blocked")
    memory.record("reviews.example", "blocked")
    assert memory.skipped("https://reviews.example/other-page")
    assert not memory.skipped("https://another.example/a")


def test_a_damaged_entry_never_skips_a_site(tmp_path: Path, now: Now) -> None:
    cache = ResearchCache(folder=tmp_path, now=now)
    cache.put_text(("site", "reviews.example"), "{not json")
    assert not SiteMemory(cache, now=now).skipped(SITE)


@pytest.mark.parametrize(
    ("given", "site"),
    [
        ("https://www.Reviews.example/a", "reviews.example"),
        ("reviews.example", "reviews.example"),
        ("http://shop.example:8080/x", "shop.example"),
        ("", None),
    ],
)
def test_a_site_is_its_host(given: str, site: str | None) -> None:
    assert site_of(given) == site


def test_the_note_names_at_most_three_sites() -> None:
    assert (
        skipped_sentence(skipped_detail(["b.example"])) == "Skipped b.example for 24 hours, because they kept failing."
    )
    two = skipped_sentence(skipped_detail(["b.example", "a.example"]))
    assert two == "Skipped a.example and b.example for 24 hours, because they kept failing."
    five = skipped_sentence(skipped_detail([f"{c}.example" for c in "edcba"]))
    assert five == "Skipped a.example, b.example, c.example and 2 more for 24 hours, because they kept failing."


def test_the_detail_fits_its_field() -> None:
    detail = skipped_detail([f"site-number-{i}.example" for i in range(40)])
    assert detail.startswith(SKIPPED_DETAIL) and len(detail) <= 300


def test_two_searches_at_once_never_undo_each_others_counts(tmp_path: Path, now: Now) -> None:
    """Each search has its own cache, but they share the files: a score is read fresh and written whole."""
    first = SiteMemory(ResearchCache(folder=tmp_path, now=now), now=now)
    second = SiteMemory(ResearchCache(folder=tmp_path, now=now), now=now)
    assert second.score(SITE) == 0  # read before the first search's skip
    for _ in range(3):
        first.record(SITE, "blocked")
    second.record(SITE, "rate_limited")  # a late reply in the other search
    assert first.skipped(SITE) and second.skipped(SITE)


def test_a_time_with_no_zone_is_forgotten_never_raised(tmp_path: Path, now: Now) -> None:
    cache = ResearchCache(folder=tmp_path, now=now)
    cache.put_text(("site", "reviews.example"), '{"events": [["2026-11-15T08:00:00", 1.0]], "skip_until": null}')
    memory = SiteMemory(cache, now=now)
    assert not memory.skipped(SITE)
    memory.record(SITE, "blocked")
    assert memory.score(SITE) == 1.0
