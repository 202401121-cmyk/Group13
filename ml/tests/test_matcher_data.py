"""S3-4: the matcher's training data. Every pair, family and record here is SYNTHETIC.

No model is trained or loaded here. The rules: WDC is refused for training, a draft label file is refused, no product
family is on both sides, and the same seed gives the same data.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import matcher_data
import own_eval
import pytest
from conftest import write_csv
from pairsets import PairSetError, family_side
from test_own_eval import DEV_A, DEV_B, TEST_A, make_tree

BRANDS = ("Zorbix", "Quillon", "Marvato", "Hexel")


def _byte(text: str, index: int) -> int:
    return hashlib.sha256(text.encode("utf-8")).digest()[index]


@pytest.mark.parametrize(
    "path",
    [
        Path("ml/data/raw/wdc_products/wdcproducts80cc20rnd000un_gs.json.gz"),
        Path("somewhere/wdc_products"),
        Path("data/WDC_Products/train.json.gz"),
    ],
)
def test_wdc_paths_are_refused_for_training(path: Path) -> None:
    with pytest.raises(PairSetError, match="WDC"):
        matcher_data.refuse_for_training(path)


def test_a_draft_or_interim_file_is_refused_for_training(tmp_path: Path) -> None:
    with pytest.raises(PairSetError, match="proposed-draft"):
        matcher_data.refuse_for_training(tmp_path / "solo-1.proposed-draft.csv")
    with pytest.raises(PairSetError, match="interim"):
        matcher_data.refuse_for_training(own_eval.INTERIM_DIR / "proposed-labels" / "x.csv")
    matcher_data.refuse_for_training(tmp_path / "solo-1.machine-a.csv")  # an ordinary label file is fine


def test_only_the_two_public_training_sets_are_allowed(tmp_path: Path) -> None:
    with pytest.raises(PairSetError, match="WDC"):
        matcher_data.leipzig_train_dev(tmp_path, "wdc_products", seed=1)
    with pytest.raises(PairSetError, match="not a training set"):
        matcher_data.leipzig_train_dev(tmp_path, "made_up", seed=1)


def _own_tree(tmp_path: Path) -> Path:
    pairs = {
        "p0001": ("Syn A one", "Syn A uno", DEV_A, DEV_A, "same_family_cross_source"),
        "p0002": ("Syn A one", "Syn B two", DEV_A, DEV_B, "hard_different"),
        "p0003": ("Syn B two", "Syn B dos", DEV_B, DEV_B, "same_family_cross_source"),
        "p0004": ("Syn T one", "Syn T uno", TEST_A, TEST_A, "same_family_cross_source"),
        "p0005": ("Syn A one", "Syn T one", DEV_A, TEST_A, "random"),
    }
    every = {"p0001": "same", "p0002": "different", "p0003": "same", "p0004": "same", "p0005": "different"}
    return make_tree(tmp_path / "labelling", pairs, {"solo-1": {"machine-a": every}})


def test_own_pairs_never_put_a_family_on_both_sides_or_train_on_the_test_side(tmp_path: Path) -> None:
    data = matcher_data.own_train_dev(_own_tree(tmp_path))
    used = {text for p in [*data.train, *data.dev] for text in (p.left, p.right)}
    assert not any("Syn T" in text for text in used)  # the test families are never used, in either part
    assert data.straddling == 1  # p0005 has one family on each side of the test split: left out
    assert data.families_train.isdisjoint(data.families_dev)
    assert all(family_side(f) == "dev" for f in data.families_train | data.families_dev)
    assert len(data.train) + len(data.dev) + data.cross_part + data.straddling + data.test_side == 5


def test_own_pairs_refuse_a_draft_file(tmp_path: Path) -> None:
    root = _own_tree(tmp_path)
    draft = root / "solo-1.proposed-draft.csv"
    draft.write_text("x", encoding="utf-8")
    with pytest.raises(PairSetError, match="proposed-draft"):
        matcher_data.own_train_dev(root, files=[draft])


def test_own_pairs_refuse_an_interim_file(tmp_path: Path) -> None:
    with pytest.raises(PairSetError, match="interim"):
        matcher_data.own_train_dev(tmp_path, files=[own_eval.INTERIM_DIR / "proposed-labels" / "a.csv"])


def _leipzig(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    folder = raw / "abt_buy"
    folder.mkdir(parents=True)
    count = 120
    write_csv(
        folder / "Abt.csv",
        ["id", "name"],
        [{"id": f"a{i}", "name": f"Syn {BRANDS[i % 4]} {i} deluxe model"} for i in range(count)],
    )
    write_csv(
        folder / "Buy.csv",
        ["id", "name"],
        [{"id": f"b{i}", "name": f"Syn {BRANDS[i % 4]} {i} deluxe edition"} for i in range(count)],
    )
    mapping = [{"idAbt": f"a{i}", "idBuy": f"b{i}"} for i in range(0, count, 2)]
    write_csv(folder / "abt_buy_perfectMapping.csv", ["idAbt", "idBuy"], mapping)
    return raw


def test_the_hard_negative_miner_repeats_for_one_seed_and_changes_with_another(tmp_path: Path) -> None:
    raw = _leipzig(tmp_path)
    first = matcher_data.leipzig_train_dev(raw, "abt_buy", seed=7)
    again = matcher_data.leipzig_train_dev(raw, "abt_buy", seed=7)
    other = matcher_data.leipzig_train_dev(raw, "abt_buy", seed=8)
    assert first == again
    assert first != other
    assert first.train and first.dev
    assert any(p.label for p in first.train) and any(not p.label for p in first.train)


def test_leipzig_train_and_dev_keep_their_records_apart_and_leave_the_test_side_alone(tmp_path: Path) -> None:
    raw = _leipzig(tmp_path)
    data = matcher_data.leipzig_train_dev(raw, "abt_buy", seed=3)
    id_of = {f"Syn {BRANDS[i % 4]} {i} deluxe model": f"a{i}" for i in range(120)}
    train_left = {id_of[p.left] for p in data.train}
    dev_left = {id_of[p.left] for p in data.dev}
    assert train_left and dev_left
    assert train_left.isdisjoint(dev_left)
    for record_id in train_left | dev_left:
        assert _byte(record_id, 0) % 2 == 0  # the dev side only: never the test side
    assert all(_byte(i, 1) < matcher_data.TRAIN_BELOW for i in train_left)
    assert all(_byte(i, 1) >= matcher_data.TRAIN_BELOW for i in dev_left)


def test_train_and_dev_share_no_record_on_either_side(tmp_path: Path) -> None:
    """The right-hand records are split too: a record that trains is never a dev negative, nor the reverse."""
    raw = _leipzig(tmp_path)
    data = matcher_data.leipzig_train_dev(raw, "abt_buy", seed=3)
    train_texts = {t for p in data.train for t in (p.left, p.right)}
    dev_texts = {t for p in data.dev for t in (p.left, p.right)}
    assert train_texts and dev_texts
    assert train_texts.isdisjoint(dev_texts)


def test_hard_negatives_are_the_most_alike_non_matches(tmp_path: Path) -> None:
    raw = _leipzig(tmp_path)
    data = matcher_data.leipzig_train_dev(raw, "abt_buy", seed=3)
    negatives = [p for p in data.train if not p.label]
    assert negatives
    assert all(p.source == "abt_buy" for p in data.train)
    # a negative for "Syn Gadget 10 ..." is another gadget: nothing is paired with its own match
    matches = {
        (f"Syn {BRANDS[i % 4]} {i} deluxe model", f"Syn {BRANDS[i % 4]} {i} deluxe edition") for i in range(0, 120, 2)
    }
    assert not any((p.left, p.right) in matches for p in negatives)
    # alike: a random pair shares the brand word one time in four; the miner's negatives almost always do
    same_brand = sum(p.left.split()[1] == p.right.split()[1] for p in negatives)
    assert same_brand / len(negatives) >= 0.8
