"""Near-duplicate threads are merged at more than 60% title overlap (US-12)."""

from __future__ import annotations

import hashlib

from app.research.dedupe import merge_near_duplicates, title_overlap
from tests.fakes.research import thread


def _titles_with_overlap(shared: int, only_one_side: int) -> tuple[str, str]:
    """Two titles whose word sets share `shared` words, with the other words split between them."""
    common = [f"w{i}" for i in range(shared)]
    left = [f"x{i}" for i in range(only_one_side // 2)]
    right = [f"y{i}" for i in range(only_one_side - only_one_side // 2)]
    return " ".join(common + left), " ".join(common + right)


def test_59_percent_overlap_is_not_merged() -> None:
    left, right = _titles_with_overlap(59, 41)
    assert title_overlap(left, right) == 0.59
    merged = merge_near_duplicates([thread("aaa", left), thread("bbb", right)])
    assert len(merged) == 2


def test_61_percent_overlap_is_merged() -> None:
    left, right = _titles_with_overlap(61, 39)
    assert title_overlap(left, right) == 0.61
    merged = merge_near_duplicates([thread("aaa", left), thread("bbb", right)])
    assert len(merged) == 1


def test_exactly_60_percent_is_not_merged() -> None:
    left, right = _titles_with_overlap(60, 40)
    assert title_overlap(left, right) == 0.60
    assert len(merge_near_duplicates([thread("aaa", left), thread("bbb", right)])) == 2


def test_punctuation_case_and_common_words_do_not_count() -> None:
    assert title_overlap("Which is the BEST laptop, for coding?", "best laptop for coding!!") == 1.0
    assert title_overlap("Laptop for coding", "Phone for gaming") == 0.0


def test_the_higher_scored_thread_is_kept_with_both_urls() -> None:
    low = thread("aaa", "Laptop for coding under 70k", upvotes=3)
    high = thread("bbb", "Laptop for coding under 70k please", upvotes=40, text="The better thread's text.")
    merged = merge_near_duplicates([low, high])
    assert len(merged) == 1
    kept = merged[0]
    assert kept.url == high.url
    assert kept.upvotes == 40
    assert kept.text.startswith("The better thread's text.")
    assert str(low.url) in kept.text
    assert kept.text.endswith(f"Same topic: {low.url}")
    assert kept.text_hash != high.text_hash


def test_the_merged_text_stays_within_the_source_cap() -> None:
    long = "x" * 12_000
    low = thread("aaa", "Laptop for coding", upvotes=1)
    high = thread("bbb", "Laptop for coding", upvotes=9, text=long)
    kept = merge_near_duplicates([low, high])[0]
    assert len(kept.text) <= 12_000
    assert kept.text.endswith(f"Same topic: {low.url}")


def test_three_threads_keep_every_url() -> None:
    first = thread("aaa", "Laptop for coding", upvotes=5)
    second = thread("bbb", "Laptop for coding", upvotes=9)
    third = thread("ccc", "Laptop for coding", upvotes=1)
    merged = merge_near_duplicates([first, second, third])
    assert len(merged) == 1
    assert merged[0].url == second.url
    assert str(first.url) in merged[0].text
    assert str(third.url) in merged[0].text


def test_a_tie_keeps_the_thread_with_more_comments() -> None:
    few = thread("aaa", "Laptop for coding", upvotes=5, comment_count=1)
    many = thread("bbb", "Laptop for coding", upvotes=5, comment_count=30)
    assert merge_near_duplicates([few, many])[0].url == many.url


def test_a_full_tie_keeps_the_first_thread() -> None:
    first = thread("aaa", "Laptop for coding")
    second = thread("bbb", "Laptop for coding")
    assert merge_near_duplicates([first, second])[0].url == first.url


def test_a_title_of_only_common_words_merges_nothing() -> None:
    assert title_overlap("what is the best", "what is the best") == 0.0
    assert len(merge_near_duplicates([thread("aaa", "what is the best"), thread("bbb", "what is the best")])) == 2


def test_different_threads_stay_in_score_order() -> None:
    low = thread("aaa", "Laptop for coding", upvotes=1)
    high = thread("bbb", "Phone camera advice", upvotes=9)
    assert [t.url for t in merge_near_duplicates([low, high])] == [high.url, low.url]


def test_a_one_character_model_number_counts() -> None:
    """Swift 3 and Swift 5 are different laptops. Dropping short words would merge them and lose a thread."""
    assert title_overlap("Acer Swift 3 review", "Acer Swift 5 review") == 0.60
    assert len(merge_near_duplicates([thread("aaa", "Acer Swift 3 review"), thread("bbb", "Acer Swift 5 review")])) == 2


def test_words_in_other_scripts_count() -> None:
    hindi_one = "".join(map(chr, (0x0932, 0x0948, 0x092A, 0x091F, 0x0949, 0x092A)))
    hindi_two = "".join(map(chr, (0x092B, 0x094B, 0x0928)))
    assert title_overlap(f"{hindi_one} laptop", f"{hindi_two} laptop") < 0.60


def test_a_second_merge_keeps_the_first_merged_url_even_at_the_text_cap() -> None:
    long = "x" * 12_000
    best = thread("bbb", "Laptop for coding", upvotes=9, text=long)
    first = thread("aaa", "Laptop for coding", upvotes=5)
    second = thread("ccc", "Laptop for coding", upvotes=1)
    kept = merge_near_duplicates([first, second, best])[0]
    assert len(kept.text) <= 12_000
    assert f"Same topic: {first.url}" in kept.text
    assert f"Same topic: {second.url}" in kept.text


def test_a_posters_own_long_line_cannot_break_the_merge() -> None:
    """If it read as a merged URL, it would leave no room for the text, and the merge would lose every thread."""
    long = "Some words\nSame topic: " + "x" * 11_950
    best = thread("bbb", "Laptop for coding", upvotes=9, text=long)
    other = thread("aaa", "Laptop for coding", upvotes=1)
    kept = merge_near_duplicates([other, best])[0]
    assert len(kept.text) <= 12_000
    assert kept.text.endswith(f"Same topic: {other.url}")


def test_a_poster_filling_the_text_with_merged_lines_cannot_break_the_merge() -> None:
    line = "Same topic: https://www.reddit.com/r/laptops/comments/abc/"
    # As many whole lines as fit: with one more URL on the end, they alone would pass the limit.
    best = thread("bbb", "Laptop for coding", upvotes=9, text="\n".join([line] * 203))
    other = thread("aaa", "Laptop for coding", upvotes=1)
    kept = merge_near_duplicates([other, best])[0]
    assert len(kept.text) <= 12_000
    assert kept.text.endswith(f"Same topic: {other.url}")


def test_the_hash_is_of_the_text_as_the_contract_keeps_it() -> None:
    """A thread with no text of its own but such a line: without a strip, the hash would be of a different text."""
    title = "Same topic: https://www.reddit.com/r/laptops/comments/abc/"
    best = thread("bbb", title, upvotes=9)
    kept = merge_near_duplicates([thread("aaa", title, upvotes=1), best])[0]
    assert kept.text_hash == hashlib.sha256(kept.text.encode()).hexdigest()
