"""Seeded batches of pairs (ML-1): the same seed gives the same batch, the strata hold, nothing repeats, overlap
comes only from earlier batches, and the batch file gives the labeller no hint."""

from __future__ import annotations

import csv
import os
import subprocess
import sys
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
from conftest import LISTINGS_HEADER, example_listing_rows, write_csv
from make_pairs import BATCH_HEADER, META_HEADER, STRATA, PairingError, make_batch, stratum_counts


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _meta(out: Path, batch: str) -> list[dict[str, str]]:
    return [row for row in _read(out / "pairs.meta.csv") if row["batch"] == batch]


def _listings_by_id() -> dict[str, dict[str, str]]:
    return {row["listing_id"]: row for row in example_listing_rows()}


def _key(row: dict[str, str]) -> frozenset[str]:
    return frozenset((row["left_listing"], row["right_listing"]))


def test_the_same_seed_gives_the_same_batch(example_listings: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    make_batch([example_listings], "agree-1", seed=101, out_dir=first)
    make_batch([example_listings], "agree-1", seed=101, out_dir=second)
    for name in ("agree-1.pairs.csv", "pairs.meta.csv"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_another_seed_gives_another_batch(example_listings: Path, tmp_path: Path) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path / "one")
    make_batch([example_listings], "agree-1", seed=102, out_dir=tmp_path / "two")
    one = {_key(row) for row in _meta(tmp_path / "one", "agree-1")}
    two = {_key(row) for row in _meta(tmp_path / "two", "agree-1")}
    assert one != two


def test_strata_shares_hold_within_one_pair_for_a_batch_of_50(example_listings: Path, tmp_path: Path) -> None:
    assert sum(share for _, share in STRATA) == 100
    for seed in (101, 201, 7):
        out = tmp_path / str(seed)
        make_batch([example_listings], "agree-1", seed=seed, out_dir=out)
        counts = Counter(row["stratum"] for row in _meta(out, "agree-1"))
        assert sum(counts.values()) == 50
        for stratum, share in STRATA:
            assert abs(counts[stratum] - share * 50 / 100) <= 1, (stratum, counts)
    assert stratum_counts(50) == {
        "same_family_cross_source": 18,
        "same_family_same_source": 7,
        "mention_vs_title": 10,
        "hard_different": 10,
        "random": 5,
    }


def test_each_stratum_holds_the_right_kind_of_pair(example_listings: Path, tmp_path: Path) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    listing = _listings_by_id()
    for row in _meta(tmp_path, "agree-1"):
        left, right = listing[row["left_listing"]], listing[row["right_listing"]]
        same_family = left["product_family"] == right["product_family"]
        kinds = {left["kind"], right["kind"]}
        if row["stratum"] == "same_family_cross_source":
            assert same_family and kinds == {"title"} and left["source"] != right["source"]
        elif row["stratum"] == "same_family_same_source":
            assert same_family and kinds == {"title"} and left["source"] == right["source"]
        elif row["stratum"] == "mention_vs_title":
            assert same_family and kinds == {"title", "mention"}
        elif row["stratum"] == "hard_different":
            assert not same_family and kinds == {"title"} and left["category"] == right["category"]
        else:
            assert row["stratum"] == "random"
        assert left["listing_id"] != right["listing_id"]


def test_no_pair_repeats_within_or_across_batches(example_listings: Path, tmp_path: Path) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    make_batch([example_listings], "agree-2", seed=102, out_dir=tmp_path)
    first = [_key(row) for row in _meta(tmp_path, "agree-1")]
    second = [_key(row) for row in _meta(tmp_path, "agree-2")]
    assert len(set(first)) == len(first) == 50
    assert len(set(second)) == len(second) == 50
    assert not set(first) & set(second)


def test_overlap_pairs_come_only_from_earlier_batches(example_listings: Path, tmp_path: Path) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    make_batch([example_listings], "solo-1", seed=201, out_dir=tmp_path, overlap=5)
    make_batch([example_listings], "solo-2", seed=202, out_dir=tmp_path, overlap=5)
    earlier = {row["pair_id"]: row for row in _meta(tmp_path, "agree-1")}
    agree_rows = {row["pair_id"]: row for row in _read(tmp_path / "agree-1.pairs.csv")}

    solo_1 = _meta(tmp_path, "solo-1")
    repeats = [row for row in solo_1 if row["overlap_of"]]
    assert len(solo_1) == 50 and len(repeats) == 5
    assert {row["overlap_of"] for row in repeats} == {"agree-1"}
    assert {row["pair_id"] for row in repeats} <= set(earlier)  # they keep their pair_id
    solo_rows = {row["pair_id"]: row for row in _read(tmp_path / "solo-1.pairs.csv")}
    for row in repeats:
        assert _key(row) == _key(earlier[row["pair_id"]])
        assert solo_rows[row["pair_id"]] == agree_rows[row["pair_id"]]  # the same titles, so kappa can compare
    fresh = [row for row in solo_1 if not row["overlap_of"]]
    assert not {_key(row) for row in fresh} & {_key(row) for row in earlier.values()}

    solo_2 = [row for row in _meta(tmp_path, "solo-2") if row["overlap_of"]]
    assert {row["overlap_of"] for row in solo_2} <= {"agree-1", "solo-1"}  # never the batch being made
    originals = {row["pair_id"] for batch in ("agree-1", "solo-1") for row in _meta(tmp_path, batch)}
    assert {row["pair_id"] for row in solo_2} <= originals


def test_overlap_needs_an_earlier_batch(example_listings: Path, tmp_path: Path) -> None:
    with pytest.raises(PairingError, match="no earlier batch"):
        make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path, overlap=5)
    assert not (tmp_path / "agree-1.pairs.csv").exists()


def test_pair_ids_continue_across_batches(example_listings: Path, tmp_path: Path) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    make_batch([example_listings], "solo-1", seed=201, out_dir=tmp_path, overlap=5)
    assert sorted(row["pair_id"] for row in _meta(tmp_path, "agree-1")) == [f"p{n:04d}" for n in range(1, 51)]
    fresh = sorted(row["pair_id"] for row in _meta(tmp_path, "solo-1") if not row["overlap_of"])
    assert fresh == [f"p{n:04d}" for n in range(51, 96)]  # 45 new pairs


def test_a_batch_cannot_be_made_twice(example_listings: Path, tmp_path: Path) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    before = (tmp_path / "pairs.meta.csv").read_bytes()
    with pytest.raises(PairingError, match="agree-1 already exists"):
        make_batch([example_listings], "agree-1", seed=999, out_dir=tmp_path)
    assert (tmp_path / "pairs.meta.csv").read_bytes() == before


def test_too_few_listings_for_a_stratum_fails_plainly(tmp_path: Path) -> None:
    rows = [row for row in example_listing_rows() if row["product_family"] in ("nova-book", "orion-x")][:8]
    path = write_csv(tmp_path / "few.csv", LISTINGS_HEADER, rows)
    with pytest.raises(PairingError) as raised:
        make_batch([path], "agree-1", seed=101, out_dir=tmp_path / "out")
    message = str(raised.value)
    assert "not enough listings for" in message and "needs" in message and "has" in message
    assert not (tmp_path / "out" / "agree-1.pairs.csv").exists()


def test_the_batch_file_holds_no_stratum_or_hint(example_listings: Path, tmp_path: Path) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    with (tmp_path / "agree-1.pairs.csv").open(encoding="utf-8", newline="") as file:
        header = next(csv.reader(file))
    assert tuple(header) == BATCH_HEADER
    assert not {"stratum", "score", "similarity", "right_family", "source"} & set(header)
    listing = _listings_by_id()
    left_of = {row["pair_id"]: listing[row["left_listing"]] for row in _meta(tmp_path, "agree-1")}
    for row in _read(tmp_path / "agree-1.pairs.csv"):
        left = left_of[row["pair_id"]]
        assert row["left_text"] == left["text"]
        assert (row["category"], row["product_family"]) == (left["category"], left["product_family"])
    assert tuple(_read(tmp_path / "pairs.meta.csv")[0]) == META_HEADER


def test_listings_with_errors_are_refused(tmp_path: Path) -> None:
    rows = example_listing_rows()
    rows[3] = {**rows[3], "kind": "review"}
    path = write_csv(tmp_path / "bad.csv", LISTINGS_HEADER, rows)
    with pytest.raises(PairingError, match="fix the listings first") as raised:
        make_batch([path], "agree-1", seed=101, out_dir=tmp_path / "out")
    assert f"{path}:5:" in str(raised.value)
    assert not (tmp_path / "out").exists()


def test_a_repeated_pair_is_never_repeated_again(example_listings: Path, tmp_path: Path) -> None:
    """Repeats come from the pairs each batch made itself, so a pair can't appear twice in one batch."""
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    make_batch([example_listings], "solo-1", seed=201, out_dir=tmp_path, overlap=45)
    make_batch([example_listings], "solo-2", seed=202, out_dir=tmp_path, overlap=8)
    meta = _read(tmp_path / "pairs.meta.csv")
    made_in = {row["pair_id"]: row["batch"] for row in meta if not row["overlap_of"]}
    for batch in ("solo-1", "solo-2"):
        rows = _meta(tmp_path, batch)
        assert len({row["pair_id"] for row in rows}) == len(rows) == 50  # no pair twice in one batch
        for row in rows:
            if row["overlap_of"]:
                assert row["overlap_of"] == made_in[row["pair_id"]]  # named after the batch that made the pair
    repeated = [row["pair_id"] for row in meta if row["overlap_of"]]
    assert len(repeated) == len(set(repeated)) == 53  # each pair repeated at most once, across all batches


def test_pair_ids_say_nothing_about_how_a_pair_was_chosen(example_listings: Path, tmp_path: Path) -> None:
    """Ids are given after the shuffle, so sorting a labeller's file by pair_id never groups a stratum together."""
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    in_id_order = [row["stratum"] for row in sorted(_meta(tmp_path, "agree-1"), key=lambda row: row["pair_id"])]
    changes = sum(1 for before, after in pairwise(in_id_order) if before != after)
    assert changes > 20  # grouped by stratum it would be 4
    longest = run = 1
    for before, after in pairwise(in_id_order):
        run = run + 1 if before == after else 1
        longest = max(longest, run)
    assert longest < 10  # no stratum's 10 hard pairs in a row


def test_the_same_seed_gives_the_same_batch_in_separate_runs(example_listings: Path, tmp_path: Path) -> None:
    """Two separate runs with different hash seeds, as on two people's machines: nothing may depend on set order."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "make_pairs.py"
    for run, hash_seed in (("one", "1"), ("two", "4242")):
        done = subprocess.run(
            [
                sys.executable,
                str(script),
                str(example_listings),
                "--batch",
                "agree-1",
                "--seed",
                "101",
                "--dir",
                str(tmp_path / run),
            ],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            timeout=120,
        )
        assert done.returncode == 0, done.stdout.decode("utf-8")
    for name in ("agree-1.pairs.csv", "pairs.meta.csv"):
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()


def test_an_overlap_larger_than_the_earlier_batches_fails_plainly(example_listings: Path, tmp_path: Path) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path, size=10)
    with pytest.raises(PairingError, match="asks for more pairs than the earlier batches hold"):
        make_batch([example_listings], "solo-1", seed=201, out_dir=tmp_path, overlap=12)


def test_overlap_from_limits_where_repeats_come_from(example_listings: Path, tmp_path: Path) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    make_batch([example_listings], "solo-1", seed=201, out_dir=tmp_path, overlap=5)
    make_batch([example_listings], "solo-2", seed=202, out_dir=tmp_path, overlap=5, overlap_from=["solo-1"])
    repeats = [row for row in _meta(tmp_path, "solo-2") if row["overlap_of"]]
    assert {row["overlap_of"] for row in repeats} == {"solo-1"}
    with pytest.raises(PairingError, match="do not exist yet: solo-9"):
        make_batch([example_listings], "solo-3", seed=203, out_dir=tmp_path, overlap=5, overlap_from=["solo-9"])


def test_a_batch_file_is_never_overwritten_even_without_the_meta_file(example_listings: Path, tmp_path: Path) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    (tmp_path / "pairs.meta.csv").unlink()  # lost, so the meta file can no longer say agree-1 exists
    before = (tmp_path / "agree-1.pairs.csv").read_bytes()
    with pytest.raises(PairingError, match="agree-1 already exists"):
        make_batch([example_listings], "agree-1", seed=7, out_dir=tmp_path)
    assert (tmp_path / "agree-1.pairs.csv").read_bytes() == before


def test_the_strata_shares_hold_for_the_new_pairs_of_a_batch_with_overlap(
    example_listings: Path, tmp_path: Path
) -> None:
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    make_batch([example_listings], "solo-1", seed=201, out_dir=tmp_path, overlap=5)
    fresh = Counter(row["stratum"] for row in _meta(tmp_path, "solo-1") if not row["overlap_of"])
    assert dict(fresh) == stratum_counts(45)


def test_a_failed_meta_save_leaves_no_batch_behind(
    example_listings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from listings import FileBusy, write_rows

    def meta_busy(path: Path, *args: Any) -> None:
        if path.name == "pairs.meta.csv":
            raise FileBusy("cannot save pairs.meta.csv: close it in any other program, then try again")
        write_rows(path, *args)

    monkeypatch.setattr("make_pairs.write_rows", meta_busy)
    with pytest.raises(FileBusy):
        make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    assert not (tmp_path / "agree-1.pairs.csv").exists()  # so trying again works, and no pair id is reused
    monkeypatch.undo()
    make_batch([example_listings], "agree-1", seed=101, out_dir=tmp_path)
    assert (tmp_path / "agree-1.pairs.csv").exists()
