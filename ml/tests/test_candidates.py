"""S3-4: candidate pairs for later labelling, and the hard-negative miner. Every title and listing here is SYNTHETIC."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import candidates
import pytest
from pairsets import PairSetError

LISTINGS = [
    candidates.Listing("l1", "Syn Nova Book 14 (16GB, Silver)", "nova-book", "laptop", "https://shop.example/1"),
    candidates.Listing("l2", "Syn Nova Book 14 Laptop 16 GB Grey", "nova-book", "laptop", "https://shop.example/2"),
    candidates.Listing("l3", "Syn Nova Book 15 (16GB, Silver)", "nova-book-15", "laptop", "https://shop.example/3"),
    candidates.Listing("l4", "Syn Orion Play 15 Gaming Laptop", "orion-play", "laptop", "https://shop.example/4"),
    candidates.Listing("l5", "Syn Kettle Pro 1.7L Steel", "kettle-pro", "kettle", "https://shop.example/5"),
    candidates.Listing("l6", "Syn Kettle Pro 1.7 L Steel Grey", "kettle-pro-b", "kettle", "https://shop.example/6"),
]


def test_the_miner_repeats_for_one_seed_and_pairs_only_different_families_of_one_category() -> None:
    first = candidates.mine_hard_negatives(LISTINGS, seed=5, limit=3)
    assert first == candidates.mine_hard_negatives(LISTINGS, seed=5, limit=3)
    assert first
    by_id = {item.listing_id: item for item in LISTINGS}
    for pair in first:
        left, right = by_id[pair.left_id], by_id[pair.right_id]
        assert left.category == right.category
        assert left.family != right.family
    scores = [pair.score for pair in first]
    assert scores == sorted(scores, reverse=True)  # the most alike come first


def test_a_tie_in_the_miner_is_broken_by_the_seed_not_by_the_input_order() -> None:
    twins = [
        candidates.Listing(f"t{i}", "Syn Same Title", f"fam-{i}", "laptop", f"https://s.example/{i}") for i in range(6)
    ]
    one = candidates.mine_hard_negatives(twins, seed=1, limit=4)
    two = candidates.mine_hard_negatives(list(reversed(twins)), seed=1, limit=4)
    other = candidates.mine_hard_negatives(twins, seed=2, limit=4)
    assert one == two
    assert one != other


def test_a_reddit_mention_is_refused() -> None:
    with pytest.raises(PairSetError, match="Reddit"):
        candidates.check_mention(
            candidates.Mention("Syn Nova Book 14", "reddit", "https://www.reddit.com/r/x/comments/1/")
        )
    with pytest.raises(PairSetError, match="Reddit"):
        candidates.check_mention(candidates.Mention("Syn Nova Book 14", "web_search", "https://old.reddit.com/r/x/"))
    with pytest.raises(PairSetError, match="Reddit"):
        candidates.check_mention(candidates.Mention("Syn Nova Book 14", "web_search", "https://redd.it/abc"))
    candidates.check_mention(candidates.Mention("Syn Nova Book 14", "review_site", "https://reviews.example/nova"))


def test_the_hard_quota_goes_to_the_next_most_alike_when_a_pair_is_already_in_a_batch() -> None:
    top = candidates.mine_hard_negatives(LISTINGS, seed=5, limit=1)[0]
    skip = {candidates.pair_key(top.left_text, top.right_text)}
    again = candidates.mine_hard_negatives(LISTINGS, seed=5, limit=1, skip=skip)
    assert len(again) == 1
    assert candidates.pair_key(again[0].left_text, again[0].right_text) not in skip


def test_mentions_pair_with_listing_titles_and_skip_pairs_already_in_a_batch() -> None:
    mention = candidates.Mention("Syn Nova Book 14 review", "review_site", "https://reviews.example/nova")
    skip = {candidates.pair_key(mention.text, LISTINGS[0].text)}
    found = candidates.candidate_pairs([mention], LISTINGS, skip, seed=3, per_mention=2)
    assert len(found) == 2
    assert all(candidates.pair_key(p.left_text, p.right_text) not in skip for p in found)
    assert {p.right_text for p in found} <= {item.text for item in LISTINGS}
    assert all(p.kind == "mention_vs_title" for p in found)


def test_the_tool_never_labels_and_writes_no_label_column(tmp_path: Path) -> None:
    out = tmp_path / "candidates.csv"
    pairs = candidates.mine_hard_negatives(LISTINGS, seed=5, limit=2)
    candidates.write_candidates(out, pairs, LISTINGS)
    header = next(csv.reader(out.open(encoding="utf-8")))
    assert "label" not in header and "labeller" not in header
    assert header == list(candidates.CANDIDATE_HEADER)


def test_mentions_are_read_from_a_recorded_search_and_reddit_is_left_out(tmp_path: Path) -> None:
    folder = tmp_path / "run"
    folder.mkdir()
    organic = [
        {"title": "Syn best laptops", "link": "https://www.digit.in/best-laptops"},
        {"title": "Syn thread", "link": "https://www.reddit.com/r/laptops/comments/1/"},
        {"title": "Syn short link", "link": "https://redd.it/abc"},
        {"title": "Syn shop page", "link": "https://www.flipkart.com/laptops"},
    ]
    (folder / "serper.json").write_text(json.dumps([{"response_body": {"organic": organic}}]), encoding="utf-8")
    found = candidates.mentions_from_recording(folder)
    assert [m.text for m in found] == ["Syn best laptops", "Syn shop page"]
    assert [m.source_type for m in found] == ["review_site", "web_search"]
