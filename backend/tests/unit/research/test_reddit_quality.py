"""Scoring Reddit threads and comments (US-13). Every thread and comment here is synthetic and made up."""

from __future__ import annotations

import math
import random
import re
from datetime import timedelta
from pathlib import Path

import pytest

from app.contracts import Source, SourceComment
from app.intake.lexicon import load_lexicon
from app.research.capping import cap_source
from app.research.reddit import quality as quality_module
from app.research.reddit.archive import comments_to_tree
from app.research.reddit.parsing import RawComment
from app.research.reddit.quality import (
    QualitySettings,
    age_weight,
    comment_score,
    load_quality_settings,
    names_a_model,
    score_comments,
    score_threads,
)
from app.research.reddit.recommenders import ProductMatcher
from app.research.reddit.subreddits import subreddits_for
from tests.fakes.research import thread

SETTINGS = load_quality_settings()
CATEGORY = "laptop"


def _thread(name: str = "aaa", upvotes: int | None = 50, comments: int | None = 20, **changes: object) -> Source:
    source = thread(name, upvotes=upvotes, comment_count=comments, text="A made-up post.")
    return Source.model_validate({**source.model_dump(), **changes}) if changes else source


def _aged(days: float, name: str = "aaa", **kwargs: object) -> Source:
    source = _thread(name, **kwargs)  # type: ignore[arg-type]
    return Source.model_validate({**source.model_dump(), "published_at": source.fetched_at - timedelta(days=days)})


def _score(source: Source, *peers: Source, settings: QualitySettings = SETTINGS) -> float:
    lenient = settings.model_validate(
        {**settings.model_dump(), "thread": {**settings.thread.model_dump(), "min_score": 0.0}}
    )
    scored = score_threads([source, *peers], CATEGORY, lenient).kept
    return next(s.quality for s in scored if s.id == source.id)


def _comment(text: str = "Made up.", upvotes: int = 5, author: str | None = "a" * 64) -> SourceComment:
    return SourceComment(id="m1", depth=1, upvotes=upvotes, text=text, author_hash=author)


# --- the thread score ----------------------------------------------------------------------------


def test_the_thread_score_follows_the_formula() -> None:
    t = SETTINGS.thread
    upvotes = math.log1p(50) / math.log1p(t.upvotes_full_at)
    comments = math.log1p(20) / math.log1p(t.comments_full_at)
    relevance = SETTINGS.relevance.categories["laptop"]["laptops"]
    expected = t.upvotes_weight * upvotes + t.comments_weight * comments + t.relevance_weight * relevance
    assert _score(_thread()) == pytest.approx(expected, abs=1e-5)


def test_more_upvotes_score_higher() -> None:
    assert _score(_thread(upvotes=100)) > _score(_thread(upvotes=10))


def test_more_comments_score_higher() -> None:
    assert _score(_thread(comments=80)) > _score(_thread(comments=5))


def test_upvotes_are_on_a_log_scale() -> None:
    """Each tenfold rise adds about the same, so one huge thread can't drown out the rest."""
    low, middle, high = (_score(_thread(upvotes=n)) for n in (1, 10, 100))
    assert (middle - low) == pytest.approx(high - middle, abs=0.05)
    assert high - middle < 2 * (middle - low)  # on a straight scale it would be ten times as much


def test_a_count_past_its_full_mark_counts_as_full() -> None:
    full = int(SETTINGS.thread.upvotes_full_at)
    assert _score(_thread(upvotes=full)) == _score(_thread(upvotes=full * 50))


def test_negative_upvotes_count_as_none() -> None:
    assert _score(_thread(upvotes=-40)) == _score(_thread(upvotes=0))


@pytest.mark.parametrize(
    ("subreddit", "expected"),
    [
        pytest.param("SuggestALaptop", SETTINGS.relevance.categories["laptop"]["SuggestALaptop"], id="in-the-map"),
        pytest.param("IndiaTech", SETTINGS.relevance.listed, id="searched-but-not-in-the-map"),
        pytest.param("pics", SETTINGS.relevance.unknown, id="unknown"),
    ],
)
def test_subreddit_relevance_comes_from_the_config(subreddit: str, expected: float) -> None:
    source = thread("aaa", upvotes=0, comment_count=0, subreddit=subreddit, text="A made-up post.")
    assert _score(source) == pytest.approx(SETTINGS.thread.relevance_weight * expected, abs=1e-5)


def test_a_category_we_have_no_list_for_uses_the_default() -> None:
    source = thread("aaa", upvotes=0, comment_count=0, subreddit="laptops", text="A made-up post.")
    lenient = SETTINGS.model_validate(
        {**SETTINGS.model_dump(), "thread": {**SETTINGS.thread.model_dump(), "min_score": 0.0}}
    )
    (kept,) = score_threads([source], "no_such_category", lenient).kept
    assert kept.quality == pytest.approx(SETTINGS.thread.relevance_weight * SETTINGS.relevance.unknown, abs=1e-5)


def test_a_subreddit_matches_in_any_case() -> None:
    source = thread("aaa", upvotes=0, comment_count=0, subreddit="SUGGESTALAPTOP", text="A made-up post.")
    best = SETTINGS.relevance.categories["laptop"]["SuggestALaptop"]
    assert _score(source) == pytest.approx(SETTINGS.thread.relevance_weight * best, abs=1e-5)


def test_the_map_names_only_real_categories_and_their_own_subreddits() -> None:
    known = set(load_lexicon().categories)
    for category, subreddits in SETTINGS.relevance.categories.items():
        assert category in known, category
        assert set(subreddits) <= set(subreddits_for(category)), category


# --- age ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("days", "weight"), [(0, 1.0), (180, 0.5), (360, 0.25), (90, 0.5**0.5)])
def test_age_weight_halves_every_180_days(days: float, weight: float) -> None:
    assert age_weight(days, SETTINGS.half_life_days) == pytest.approx(weight)


def test_a_180_day_old_thread_has_half_the_score_of_a_new_one() -> None:
    assert _score(_aged(180)) == pytest.approx(_score(_aged(0)) / 2, abs=1e-5)


def test_a_360_day_old_thread_has_a_quarter_of_the_score_of_a_new_one() -> None:
    assert _score(_aged(360)) == pytest.approx(_score(_aged(0)) / 4, abs=1e-5)


def test_a_date_in_the_future_is_no_bonus() -> None:
    assert _score(_aged(-30)) == _score(_aged(0))


def test_age_is_counted_from_when_the_thread_was_fetched() -> None:
    """Not from today's clock, so the same thread always gets the same score."""
    old = _aged(180)
    later = Source.model_validate({**old.model_dump(), "fetched_at": old.fetched_at + timedelta(days=180)})
    assert _score(later) == pytest.approx(_score(old) / 2, abs=1e-5)


# --- unknown is not bad ----------------------------------------------------------------------------


def test_a_missing_count_scores_exactly_the_average_of_its_peers() -> None:
    t = SETTINGS.thread
    low, high = _thread("bbb", upvotes=1), _thread("ccc", upvotes=199)
    scale = (math.log1p(1) / math.log1p(t.upvotes_full_at) + math.log1p(199) / math.log1p(t.upvotes_full_at)) / 2
    known = _score(_thread("aaa", upvotes=0), low, high)
    assert _score(_thread("aaa", upvotes=None), low, high) == pytest.approx(known + t.upvotes_weight * scale, abs=1e-5)


def test_a_missing_comment_count_scores_exactly_the_average_of_its_peers() -> None:
    t = SETTINGS.thread
    low, high = _thread("bbb", comments=1), _thread("ccc", comments=99)
    scale = (math.log1p(1) / math.log1p(t.comments_full_at) + math.log1p(99) / math.log1p(t.comments_full_at)) / 2
    known = _score(_thread("aaa", comments=0), low, high)
    assert _score(_thread("aaa", comments=None), low, high) == pytest.approx(
        known + t.comments_weight * scale, abs=1e-5
    )


def test_missing_upvotes_take_their_peers_average() -> None:
    """Build rule 6: an unknown is never scored as zero."""
    unknown = _thread("aaa", upvotes=None)
    low, high = _thread("bbb", upvotes=1), _thread("ccc", upvotes=199)
    assert _score(_thread("zzz", upvotes=0), low, high) < _score(unknown, low, high)


def test_missing_comment_counts_take_their_peers_average() -> None:
    unknown = _thread("aaa", comments=None)
    low, high = _thread("bbb", comments=1), _thread("ccc", comments=99)
    assert _score(_thread("zzz", comments=0), low, high) < _score(unknown, low, high)


def test_missing_values_are_marked_estimated() -> None:
    scored = score_threads([_thread("aaa", upvotes=None), _thread("bbb")], CATEGORY, SETTINGS)
    marks = {s.url.rstrip("/").split("/")[-1]: s.estimated for s in scored.scores}
    assert marks == {"aaa": True, "bbb": False}


def test_with_no_peers_a_missing_count_is_middling_not_zero() -> None:
    assert _score(_thread(upvotes=None, comments=None)) > _score(_thread(upvotes=0, comments=0))


def test_a_missing_date_takes_its_peers_average_age() -> None:
    unknown = _thread("aaa", published_at=None)
    assert _score(unknown, _aged(180, "bbb")) == pytest.approx(_score(_aged(180, "aaa"), _aged(180, "bbb")), abs=1e-5)


def test_with_no_peers_a_missing_date_costs_nothing() -> None:
    assert _score(_thread(published_at=None)) == _score(_aged(0))


# --- dropping --------------------------------------------------------------------------------------


def test_a_thread_below_the_threshold_is_dropped() -> None:
    good, poor = _aged(0, "aaa"), _aged(2000, "bbb")
    scored = score_threads([good, poor], CATEGORY, SETTINGS)
    assert [s.id for s in scored.kept] == [good.id]
    (gone,) = scored.dropped
    assert gone.url == str(poor.url)
    assert gone.score < SETTINGS.thread.min_score


def test_a_thread_exactly_at_the_threshold_is_kept() -> None:
    source = _thread(upvotes=0, comments=0)
    score = _score(source)
    at = SETTINGS.model_validate(
        {**SETTINGS.model_dump(), "thread": {**SETTINGS.thread.model_dump(), "min_score": score}}
    )
    busy = thread("zzz", upvotes=190, comment_count=90, subreddit="SuggestALaptop", text="A made-up post.")
    scored = score_threads([source, busy], CATEGORY, at)  # with one that passes, so the floor plays no part
    assert {s.id for s in scored.kept} == {source.id, busy.id}


def _busy(name: str = "zzz") -> Source:
    """A thread that always passes, so the threshold decides about the others."""
    return thread(name, upvotes=190, comment_count=90, subreddit="SuggestALaptop", text="A made-up post.")


def test_a_new_quiet_thread_in_the_categorys_best_subreddit_is_kept() -> None:
    source = thread("aaa", upvotes=0, comment_count=0, subreddit="SuggestALaptop", text="A made-up post.")
    assert score_threads([source, _busy()], CATEGORY, SETTINGS).dropped == []


def test_a_new_quiet_thread_in_a_merely_searched_subreddit_is_dropped() -> None:
    source = thread("aaa", upvotes=0, comment_count=0, subreddit="IndiaTech", text="A made-up post.")
    (gone,) = score_threads([source, _busy()], CATEGORY, SETTINGS).dropped
    assert gone.url == str(source.url)


def test_a_thread_from_an_unknown_subreddit_with_no_votes_is_dropped() -> None:
    source = thread("aaa", upvotes=0, comment_count=0, subreddit="pics", text="A made-up post.")
    assert [row.url for row in score_threads([source, _busy()], CATEGORY, SETTINGS).dropped] == [str(source.url)]


def test_a_thread_with_a_little_interest_loses_its_place_with_age() -> None:
    assert score_threads([_aged(0, upvotes=1, comments=1), _busy()], CATEGORY, SETTINGS).dropped == []
    assert len(score_threads([_aged(200, upvotes=1, comments=1), _busy()], CATEGORY, SETTINGS).dropped) == 1


def test_when_no_thread_passes_the_best_few_are_kept() -> None:
    """An old archive's threads can't reach the threshold, and an empty result would say nothing about why."""
    old = [_aged(600, f"a{i}", upvotes=i, comments=i) for i in range(8)]
    limit = SETTINGS.thread.keep_if_none_pass
    scored = score_threads(old, CATEGORY, SETTINGS)
    assert all(row.score < SETTINGS.thread.min_score for row in scored.scores)
    assert limit == 5
    assert [t.upvotes for t in scored.kept] == [7, 6, 5, 4, 3]
    assert len(scored.dropped) == 3
    assert all(t.quality < SETTINGS.thread.min_score for t in scored.kept)  # their low scores say how much they count


def test_the_floor_only_applies_when_nothing_passes() -> None:
    old = [_aged(600, f"a{i}", upvotes=i, comments=i) for i in range(8)]
    scored = score_threads([*old, _busy()], CATEGORY, SETTINGS)
    assert [t.upvotes for t in scored.kept] == [190]
    assert len(scored.dropped) == 8


def test_the_floor_can_be_turned_off() -> None:
    strict = SETTINGS.model_validate(
        {**SETTINGS.model_dump(), "thread": {**SETTINGS.thread.model_dump(), "keep_if_none_pass": 0}}
    )
    assert score_threads([_aged(600)], CATEGORY, strict).kept == []


def test_kept_threads_come_best_first_and_ties_go_by_id() -> None:
    best, tie_one, tie_two = _thread("ccc", upvotes=190), _thread("aaa", upvotes=5), _thread("bbb", upvotes=5)
    kept = score_threads([tie_two, best, tie_one], CATEGORY, SETTINGS).kept
    assert kept[0].id == best.id
    assert [s.id for s in kept[1:]] == sorted([tie_one.id, tie_two.id])


def test_each_kept_thread_carries_its_score_as_its_quality() -> None:
    (kept,) = score_threads([_thread()], CATEGORY, SETTINGS).kept
    assert kept.quality == _score(_thread())
    assert kept.quality != _thread().quality


def test_nothing_else_about_a_thread_changes() -> None:
    source = _thread()
    (kept,) = score_threads([source], CATEGORY, SETTINGS).kept
    assert {**kept.model_dump(), "quality": 0} == {**source.model_dump(), "quality": 0}


def test_scores_stay_between_0_and_1() -> None:
    top = _thread(upvotes=10**7, comments=10**7)
    source = thread("aaa", upvotes=10**7, comment_count=10**7, subreddit="SuggestALaptop", text="A made-up post.")
    assert 0.0 <= _score(top) <= 1.0
    assert _score(source) == pytest.approx(1.0)


def test_shuffled_threads_get_the_same_scores() -> None:
    threads = [_thread(f"a{i}", upvotes=None if i % 3 == 0 else i * 7, comments=i) for i in range(12)]
    first = score_threads(threads, CATEGORY, SETTINGS)
    shuffled = list(threads)
    random.Random(7).shuffle(shuffled)
    second = score_threads(shuffled, CATEGORY, SETTINGS)
    assert first == second
    assert first == score_threads(threads, CATEGORY, SETTINGS)


def test_no_threads_is_fine() -> None:
    scored = score_threads([], CATEGORY, SETTINGS)
    assert (scored.kept, scored.dropped, scored.scores) == ([], [], [])


# --- the comment score -----------------------------------------------------------------------------


def test_the_comment_score_follows_the_formula() -> None:
    c = SETTINGS.comment
    text = "The Nova XM5 is good."
    upvotes = math.log1p(9) / math.log1p(c.upvotes_full_at)
    length = len(text) / c.length_full_at
    expected = c.upvotes_weight * upvotes + c.length_weight * length + c.model_weight
    assert comment_score(_comment(text, upvotes=9), SETTINGS) == pytest.approx(expected, abs=1e-5)


def test_a_comment_with_more_upvotes_scores_higher() -> None:
    assert comment_score(_comment(upvotes=40), SETTINGS) > comment_score(_comment(upvotes=2), SETTINGS)


def test_negative_comment_upvotes_count_as_none() -> None:
    assert comment_score(_comment(upvotes=-9), SETTINGS) == comment_score(_comment(upvotes=0), SETTINGS)


def test_a_longer_comment_scores_higher_up_to_the_cap() -> None:
    full = SETTINGS.comment.length_full_at
    short, at_cap, past_cap = (comment_score(_comment("x" * n), SETTINGS) for n in (20, full, full * 3))
    assert short < at_cap
    assert at_cap == past_cap


def test_naming_a_model_scores_higher() -> None:
    assert comment_score(_comment("The WH-1000XM5 is good."), SETTINGS) > comment_score(
        _comment("The headphones are good."), SETTINGS
    )


def test_a_comment_score_stays_between_0_and_1() -> None:
    top = _comment("The XM5 " * 200, upvotes=10**6)
    assert comment_score(top, SETTINGS) == pytest.approx(1.0)
    assert comment_score(_comment("Ok.", upvotes=-5), SETTINGS) >= 0.0


@pytest.mark.parametrize(
    "text",
    [
        "WH-1000XM5",
        "wh-1000xm5 is great",
        "the XM5 is fine",
        "my G14 runs hot",
        "Legion 15irx10",
        "XPS-13 owner",
        "PS5",
        "RTX4060 laptop",
        "Galaxy M34",
        "Logitech M331",
        "the HP 15-fa1226TX",
    ],
)
def test_a_model_like_token_counts_as_naming_a_model(text: str) -> None:
    assert names_a_model(text)


@pytest.mark.parametrize(
    "text",
    [
        "16GB of memory",
        "under 70k",
        "144Hz screen",
        "512gb drive",
        "the 2nd one",
        "4K video",
        "1080p webcam",
        "a 16-inch screen",
        "a 2-year warranty",
        "2-in-1",
        "10hrs of battery",
        "5000mAh",
        "65W charger",
        "USB-C port",
        "a USB-3 port",
        "Rs50000 or Rs70k or INR50000",
        "1lakh, 1.5lakh or 2lac",
        "50rupees and 70krs",
        "50MP and 108MP cameras",
        "a 260L fridge, 500ml bottle",
        "a 1.5ton AC, 5star rating",
        "1400rpm and 10A and 3kw",
        "a 1920x1080 screen, 24x7 support",
        "a 2in1 or 3in1",
        "LPDDR5X and LPDDR4X and GDDR6",
        "Wi-Fi6 and WiFi6E",
        "at 6pm or 10am",
        "IP68 rated",
        "a 12th-gen chip",
        "INR-50000 or RS-70000",
        "only 300nits, 100mbps or 1gbps",
        "10bit, 4nm, 6ft, 500gm, 5kwh, 8core",
        "500rupaye mein",
        "4K60 and 1080p60",
        "HDR10, QC3, WPA3, R32, Win11",
        "see amzn.in/d/3abc for it",
        "at https://a.example/x9z today",
        "an HDMI-2 cable",
        "DDR5 memory",
        "Wi-Fi 6 and WiFi6",
        "in 2026 it cost 30000",
        "an i7 chip",
        "no numbers here at all",
        "₹30k",
    ],
)
def test_a_unit_or_a_plain_number_does_not(text: str) -> None:
    assert not names_a_model(text)


def test_a_product_from_the_dictionary_counts_as_naming_a_model() -> None:
    names = ProductMatcher(["Nova Book"])
    plain = "The Nova Book is good."
    assert not names_a_model(plain)
    assert comment_score(_comment(plain), SETTINGS, names) > comment_score(_comment(plain), SETTINGS)


def test_scoring_a_thread_scores_each_comment_and_keeps_the_rest() -> None:
    comments = (
        SourceComment(id="m1", depth=1, upvotes=30, text="The XM5 is good.", author_hash="a" * 64),
        SourceComment(id="m2", parent_id="m1", depth=2, upvotes=0, text="Ok."),
    )
    source = Source.model_validate({**_thread(quality=0.77).model_dump(), "comments": comments})
    scored = score_comments(source, SETTINGS)
    assert [round(c.quality, 6) for c in scored.comments] == [
        round(comment_score(c, SETTINGS), 6) for c in source.comments
    ]
    assert scored.comments[0].quality > scored.comments[1].quality
    assert scored.quality == 0.77
    assert [c.model_dump(exclude={"quality"}) for c in scored.comments] == [
        c.model_dump(exclude={"quality"}) for c in source.comments
    ]


def test_a_hindi_price_is_not_a_model() -> None:
    rupee_word = "".join(map(chr, (0x930, 0x941)))
    assert not names_a_model(f"15000{rupee_word} mein")


def test_comments_with_equal_votes_are_capped_the_same_in_any_order() -> None:
    """The cap keeps the highest voted, and ties keep their order, so that order must not depend on the archive's."""
    rows = [
        SourceComment(id=f"m{i:02d}", depth=1, upvotes=1, text=f"Made up {i}. " + "x" * 1200, author_hash="a" * 64)
        for i in range(30)
    ]
    kept = []
    for seed in range(3):
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        source = Source.model_validate({**_thread().model_dump(), "comments": tuple(shuffled)})
        kept.append([c.id for c in cap_source(score_comments(source, SETTINGS), 3000).comments])
    assert kept[0] == kept[1] == kept[2]
    assert 0 < len(kept[0]) < 30


def test_a_comment_at_depth_4_is_ignored() -> None:
    """Comment trees are read three levels deep, and no deeper."""
    tid = "abc123"
    levels = [("c1", f"t3_{tid}"), ("c2", "t1_c1"), ("c3", "t1_c2"), ("c4", "t1_c3")]
    rows = [
        RawComment(
            id=cid, link_id=f"t3_{tid}", parent_id=parent, subreddit="laptops", body="Made up.", author="m1", score=1
        )
        for cid, parent in levels
    ]
    tree = comments_to_tree(rows, tid, 3, "test-salt")
    assert [(c.id, c.depth) for c in tree] == [("c1", 1), ("c2", 2), ("c3", 3)]


# --- settings --------------------------------------------------------------------------------------


def test_the_values_come_from_the_config_file() -> None:
    assert SETTINGS.half_life_days == 180
    t, c = SETTINGS.thread, SETTINGS.comment
    assert (t.upvotes_weight, t.comments_weight, t.relevance_weight) == (0.4, 0.3, 0.3)
    assert (t.upvotes_full_at, t.comments_full_at, t.min_score, t.keep_if_none_pass) == (200, 100, 0.25, 5)
    assert (c.upvotes_weight, c.length_weight, c.model_weight) == (0.4, 0.2, 0.4)
    assert (c.upvotes_full_at, c.length_full_at) == (50, 500)
    assert (SETTINGS.relevance.listed, SETTINGS.relevance.unknown) == (0.7, 0.3)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("thread: [not, a, mapping", id="broken-yaml"),
        pytest.param("- a list", id="not-a-mapping"),
        pytest.param("half_life_days: 0\n", id="a-bad-half-life"),
        pytest.param("surprise: 1\n", id="an-unknown-key"),
        pytest.param("relevance:\n  categories:\n    laptop:\n      laptops: 10\n", id="relevance-above-1"),
        pytest.param("relevance:\n  categories:\n    laptop:\n      laptops: -1\n", id="relevance-below-0"),
        pytest.param("relevance:\n  categories:\n    laptop:\n      laptops: .nan\n", id="relevance-not-a-number"),
        pytest.param("thread:\n  upvotes_weight: 0.9\n", id="weights-that-do-not-add-up"),
        pytest.param("comment:\n  model_weight: 0.9\n", id="comment-weights-that-do-not-add-up"),
    ],
)
def test_a_broken_file_uses_the_defaults(tmp_path: Path, content: str) -> None:
    path = tmp_path / "reddit_quality.yaml"
    path.write_text(content, encoding="utf-8")
    assert load_quality_settings(path) == QualitySettings()


def test_a_missing_file_uses_the_defaults(tmp_path: Path) -> None:
    assert load_quality_settings(tmp_path / "nothing.yaml") == QualitySettings()


def test_the_defaults_match_the_shipped_numbers() -> None:
    defaults = QualitySettings()
    assert defaults.half_life_days == SETTINGS.half_life_days
    assert defaults.thread == SETTINGS.thread
    assert defaults.comment == SETTINGS.comment


def test_the_module_never_writes_a_file_or_calls_a_model() -> None:
    """Reddit text stays in memory (D-53), and code decides (build rule 2)."""
    code = Path(quality_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\bopen\(|write_text|write_bytes|LLMClient|app\.llm", code)
