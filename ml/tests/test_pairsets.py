"""Pair-set loaders (ML-0, #13): every source becomes the same Pair type, read exactly as ml/DATA.md describes."""

from __future__ import annotations

import gzip
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pairsets import (
    LEIPZIG,
    WDC_KEYS,
    MissingData,
    Pair,
    PairSetError,
    ensure_present,
    is_synthetic,
    leipzig_split,
    load_leipzig,
    load_own,
    load_wdc,
    required_files,
    wdc_subsets,
)

WriteLabels = Callable[..., Path]  # the write_labels fixture in conftest.py
ML = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
WDC_FIXTURE = FIXTURES / "wdc_pairs.json.gz"
OWN_A = FIXTURES / "own_labeller_a.csv"
OWN_B = FIXTURES / "own_labeller_b.csv"


# --- WDC Products -------------------------------------------------------------------------------------------------


def test_the_wdc_fixture_has_all_17_keys() -> None:
    assert len(WDC_KEYS) == 17
    lines = gzip.decompress(WDC_FIXTURE.read_bytes()).decode("ascii").splitlines()
    assert lines
    assert all(tuple(json.loads(line)) == WDC_KEYS for line in lines)


def test_wdc_pairs_load_with_labels_and_hard_negatives() -> None:
    pairs = load_wdc(WDC_FIXTURE, "dev")
    assert len(pairs) == 10
    assert all(isinstance(pair, Pair) and pair.split == "dev" for pair in pairs)
    assert sum(pair.label for pair in pairs) == 4
    assert sum(bool(pair.is_hard_negative) for pair in pairs) == 4
    assert not any(pair.label and pair.is_hard_negative for pair in pairs)
    first = pairs[0]
    assert (first.pair_id, first.left_text, first.right_text) == (
        "101#102",
        "Nova Book 14 16GB 512GB Silver",
        "NOVA Book 14 (16 GB, 512 GB) silver",
    )
    assert first.source == "wdc_products:wdc_pairs.json.gz"


def test_the_brand_variant_puts_the_brand_first_only_when_there_is_one() -> None:
    first = load_wdc(WDC_FIXTURE, "test", with_brand=True)[0]
    assert first.left_text == "Nova Nova Book 14 16GB 512GB Silver"
    assert first.right_text == "NOVA Book 14 (16 GB, 512 GB) silver"  # brand_right is null


def _write_gz(path: Path, records: list[dict[str, object]]) -> Path:
    path.write_bytes(gzip.compress(("\n".join(json.dumps(r) for r in records) + "\n").encode("ascii"), mtime=0))
    return path


def test_a_wdc_line_with_a_missing_key_is_a_clean_error(tmp_path: Path) -> None:
    record = json.loads(gzip.decompress(WDC_FIXTURE.read_bytes()).decode("ascii").splitlines()[0])
    del record["label"]
    path = _write_gz(tmp_path / "broken.json.gz", [record])
    with pytest.raises(PairSetError, match=r"broken\.json\.gz:1: missing keys \['label'\]"):
        load_wdc(path, "test")


def test_a_file_that_is_not_gzip_is_a_clean_error(tmp_path: Path) -> None:
    path = tmp_path / "plain.json.gz"
    path.write_text('{"label": 1}\n', encoding="utf-8")
    with pytest.raises(PairSetError, match="cannot read it as gzipped JSON lines"):
        load_wdc(path, "test")


def test_each_wdc_test_set_is_paired_with_the_large_validation_set_of_its_level(tmp_path: Path) -> None:
    subsets = wdc_subsets(tmp_path)
    assert [s.name for s in subsets[:3]] == ["80cc20rnd000un", "80cc20rnd050un", "80cc20rnd100un"]
    assert len(subsets) == 9
    first = subsets[0]
    assert first.test_file.name == "wdcproducts80cc20rnd000un_gs.json.gz"
    assert first.dev_file.name == "wdcproducts80cc20rnd000un_valid_large.json.gz"
    assert [s.name for s in wdc_subsets(tmp_path, [20])][2] == "20cc80rnd100un"
    with pytest.raises(PairSetError, match="WDC level must be one of"):
        wdc_subsets(tmp_path, [70])


# --- Abt-Buy and Amazon-Google ------------------------------------------------------------------------------------


def test_the_leipzig_fixtures_really_are_latin1_with_crlf() -> None:
    # Guards fixtures/.gitattributes: if git ever normalised these files, the loader tests would stop meaning much.
    data = (FIXTURES / "public" / "abt_buy" / "Abt.csv").read_bytes()
    assert b"\r\n" in data
    assert b"\xe9" in data  # "é" in Latin-1
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")


def test_leipzig_csvs_are_read_as_latin1_with_quoted_commas() -> None:
    cross = load_leipzig(FIXTURES / "public", "abt_buy")
    texts = {record.id: record.text for record in cross.left}
    assert texts["1001"] == "Nova Café Speaker - NCS10"
    assert texts["1002"] == "Kestrel Slim 15, Grey - KS15G"  # the quoted comma stays inside one field
    assert not any("\r" in record.text for record in cross.left + cross.right)
    assert len(cross.left) == 4 and len(cross.right) == 6


def test_leipzig_labels_come_from_the_perfect_mapping() -> None:
    cross = load_leipzig(FIXTURES / "public", "abt_buy")
    assert cross.matches == {("1001", "2001"), ("1002", "2002"), ("1002", "2003"), ("1004", "2006")}
    pairs = [pair for split in ("dev", "test") for pair in cross.iter_pairs(split)]
    assert len(pairs) == 4 * 6  # every left record against every right record
    assert {pair.pair_id for pair in pairs if pair.label} == {"1001#2001", "1002#2002", "1002#2003", "1004#2006"}
    assert all(pair.is_hard_negative is None for pair in pairs)


def test_amazon_google_uses_the_amazon_title_and_the_google_name() -> None:
    cross = load_leipzig(FIXTURES / "public", "amazon_google")
    assert cross.left[1].text == "kestrel photo editor, deluxe"
    assert cross.right[0].text == "nova cafe studio 2"
    assert len(cross.matches) == 3


def test_a_mapping_that_names_an_unknown_id_is_a_clean_error(tmp_path: Path) -> None:
    folder = tmp_path / "abt_buy"
    folder.mkdir()
    for name in ("Abt.csv", "Buy.csv"):
        (folder / name).write_bytes((FIXTURES / "public" / "abt_buy" / name).read_bytes())
    (folder / "abt_buy_perfectMapping.csv").write_bytes(b"idAbt,idBuy\r\n9999,2001\r\n")
    with pytest.raises(PairSetError, match="1 matches name unknown ids"):
        load_leipzig(tmp_path, "abt_buy")


def test_a_leipzig_file_without_its_text_column_is_a_clean_error(tmp_path: Path) -> None:
    folder = tmp_path / "amazon_google"
    folder.mkdir()
    (folder / "Amazon.csv").write_bytes(b"id,name\r\nb1,something\r\n")  # Amazon uses "title", not "name"
    with pytest.raises(PairSetError, match=r"missing columns \['title'\]"):
        load_leipzig(tmp_path, "amazon_google")


def test_the_parity_split_is_fixed_and_puts_each_left_record_in_one_split() -> None:
    # Pinned: sha256("1001")[0] is even, sha256("1004")[0] and sha256("b0003orio")[0] are odd.
    assert [leipzig_split(i) for i in ("1001", "1004", "b0003orio")] == ["dev", "test", "test"]
    for name in LEIPZIG:
        cross = load_leipzig(FIXTURES / "public", name)
        dev, test = cross.left_in("dev"), cross.left_in("test")
        assert dev and test
        assert {r.id for r in dev} | {r.id for r in test} == {r.id for r in cross.left}
        assert not {r.id for r in dev} & {r.id for r in test}
        assert cross.left_in("dev") == dev  # the same on every call
        assert cross.pair_count("dev") == len(dev) * len(cross.right)


# --- Our own labelled pairs ---------------------------------------------------------------------------------------


def test_one_labelling_file_drops_and_counts_unsure_rows() -> None:
    own = load_own([OWN_A])
    assert own.unsure_dropped == 1
    assert [pair.pair_id for pair in own.pairs] == ["p0001", "p0002", "p0004", "p0005", "p0006"]
    assert [pair.label for pair in own.pairs] == [True, False, True, False, True]
    assert [pair.is_hard_negative for pair in own.pairs] == [False, True, False, False, False]  # "variant"
    assert all(pair.split == "test" for pair in own.pairs)
    assert not own.synthetic


def test_two_labelling_files_keep_only_pairs_both_labellers_agree_on() -> None:
    own = load_own([OWN_A, OWN_B])
    assert [pair.pair_id for pair in own.pairs] == ["p0001", "p0002", "p0005"]
    assert own.unsure_dropped == 1  # p0003: unsure in A
    assert own.disagreements_dropped == 1  # p0004: same in A, different in B
    assert own.only_in_one_file == 1  # p0006 is only in A
    assert own.title_mismatches == 0
    assert own.pairs[1].is_hard_negative  # variant in both files
    assert own.pairs[0].source == "own:own_labeller_a.csv+own_labeller_b.csv"


def test_a_pair_whose_titles_differ_between_the_files_is_dropped(write_labels: WriteLabels) -> None:
    a = write_labels("a.csv", [{"pair_id": "p0001"}, {"pair_id": "p0002"}])
    b = write_labels("b.csv", [{"pair_id": "p0001"}, {"pair_id": "p0002", "right_title": "Nova Book 16"}])
    own = load_own([a, b])
    assert own.title_mismatches == 1
    assert [pair.pair_id for pair in own.pairs] == ["p0001"]


def test_an_invalid_labelling_file_fails_with_the_label_checker_error(write_labels: WriteLabels) -> None:
    path = write_labels("bad.csv", [{"label": "maybe"}])
    with pytest.raises(PairSetError) as error:
        load_own([path])
    assert f"{path}:2: label 'maybe' must be one of" in str(error.value)


def test_one_or_two_files_only() -> None:
    with pytest.raises(PairSetError, match="one labelling file, or two"):
        load_own([OWN_A, OWN_B, OWN_A])


def test_files_under_ml_examples_are_marked_synthetic() -> None:
    assert is_synthetic(ML / "examples" / "pairs_labeller_a.csv")
    assert not is_synthetic(OWN_A)
    assert load_own([ML / "examples" / "pairs_labeller_a.csv"]).synthetic


# --- Missing files ------------------------------------------------------------------------------------------------


def test_required_files_name_every_raw_file_a_dataset_needs(tmp_path: Path) -> None:
    assert len(required_files(tmp_path, "wdc_products")) == 18
    assert len(required_files(tmp_path, "wdc_products", [80])) == 6
    assert [p.name for p in required_files(tmp_path, "amazon_google")] == [
        "Amazon.csv",
        "GoogleProducts.csv",
        "Amzon_GoogleProducts_perfectMapping.csv",
    ]


def test_ensure_present_lists_every_missing_file(raw_tree: Path) -> None:
    ensure_present(required_files(raw_tree, "abt_buy"))  # all there: no error
    (raw_tree / "abt_buy" / "Buy.csv").unlink()
    (raw_tree / "abt_buy" / "Abt.csv").unlink()
    with pytest.raises(MissingData) as error:
        ensure_present(required_files(raw_tree, "abt_buy"))
    assert [p.name for p in error.value.paths] == ["Abt.csv", "Buy.csv"]


# --- the split by product family (ML-1, L2): S3-4 trains and tests on the same one --------------------------------


def test_family_side_is_pinned() -> None:
    """The same family always lands on the same side, on every machine: sha256, as leipzig_split."""
    from pairsets import family_side

    sides = {family: family_side(family) for family in ("apple-iphone", "vivo-y", "xiaomi-redmi", "syn-family")}
    assert sides == {family: family_side(family) for family in sides}
    assert set(sides.values()) <= {"dev", "test"}
    pinned = ("agaro-regal", "amazfit-gtr", "amazfit-gts", "apple-iphone", "apple-macbook-air")
    assert [family_side(f) for f in pinned] == ["dev", "dev", "test", "test", "test"]


def _families(side: str, count: int) -> list[str]:
    """SYNTHETIC family names that land on one side."""
    from pairsets import family_side

    names = (f"syn-family-{i}" for i in range(1000))
    return [name for name in names if family_side(name) == side][:count]


def test_a_family_never_crosses_the_split() -> None:
    from pairsets import split_by_family

    dev, test = _families("dev", 3), _families("test", 3)
    items = [(f"syn{i}", {family}) for i, family in enumerate(dev + test)] * 2
    split = split_by_family(items, lambda item: item[1])
    assert split.dev_families == frozenset(dev)
    assert split.test_families == frozenset(test)
    assert not split.dev_families & split.test_families
    assert len(split.dev) == len(split.test) == 6
    assert split.straddling == []


def test_a_pair_spanning_both_sides_is_left_out_and_counted() -> None:
    from pairsets import split_by_family

    (dev,), (test,) = _families("dev", 1), _families("test", 1)
    items = [("syn-dev", {dev}), ("syn-test", {test}), ("syn-both", {dev, test}), ("syn-dev-twice", {dev, dev})]
    split = split_by_family(items, lambda item: item[1])
    assert [name for name, _ in split.dev] == ["syn-dev", "syn-dev-twice"]
    assert [name for name, _ in split.test] == ["syn-test"]
    assert [name for name, _ in split.straddling] == ["syn-both"]
    assert test not in split.dev_families and dev not in split.test_families
    with pytest.raises(PairSetError, match="no product family"):
        split_by_family([("syn-none", set())], lambda item: item[1])
