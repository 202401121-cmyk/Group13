"""L2 (ML-1): the fuzzy baseline on our own labelled pairs. Every pair and family here is SYNTHETIC.

The tests build a small labelling folder in the same shape as ml/labelling: listings.csv, batches/<batch>.pairs.csv,
batches/pairs.meta.csv and one <batch>.<labeller>.csv per labeller.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import own_eval
import pytest
from check_labels import HEADER as LABEL_HEADER
from conftest import write_csv
from listings import LISTING_HEADER
from make_pairs import BATCH_HEADER, META_HEADER
from match_metrics import Choice
from pairsets import PairSetError, family_side

SYNTHETIC = "SYNTHETIC test row"

# pair id -> (left text, right text, left family, right family, stratum)
PairSpec = tuple[str, str, str, str, str]


def _family(side: str, index: int) -> str:
    names = (f"syn-family-{i}" for i in range(1000))
    return [name for name in names if family_side(name) == side][index]


DEV_A, DEV_B, TEST_A, TEST_B = _family("dev", 0), _family("dev", 1), _family("test", 0), _family("test", 1)


def make_tree(
    root: Path,
    pairs: Mapping[str, PairSpec],
    labels: Mapping[str, Mapping[str, Mapping[str, str]]],
    labeller_names: Mapping[str, str] | None = None,
) -> Path:
    """labels: batch -> labeller slug -> pair id -> label. Every row is SYNTHETIC."""
    names = {"machine-a": "Machine A", "machine-b": "Machine B", **(labeller_names or {})}
    (root / "batches").mkdir(parents=True)
    listings: list[dict[str, str]] = []
    ids: dict[tuple[str, str], str] = {}
    for left, right, left_family, right_family, _ in pairs.values():
        for text, family in ((left, left_family), (right, right_family)):
            if (text, family) not in ids:
                ids[(text, family)] = f"l{len(ids) + 1:04d}"
                listings.append(
                    {
                        "listing_id": ids[(text, family)],
                        "kind": "title",
                        "text": text,
                        "source": "syn-shop",
                        "url": f"https://shop.example/{len(ids)}",
                        "product_family": family,
                        "category": "laptop",
                        "added_by": "SYNTHETIC",
                    }
                )
    write_csv(root / "listings.csv", LISTING_HEADER, listings)
    meta: list[dict[str, str]] = []
    for batch, by_labeller in labels.items():
        in_batch = sorted({pair_id for rows in by_labeller.values() for pair_id in rows})
        batch_rows = []
        for pair_id in in_batch:
            left, right, left_family, right_family, stratum = pairs[pair_id]
            batch_rows.append(
                {
                    "pair_id": pair_id,
                    "left_text": left,
                    "right_text": right,
                    "left_url": "https://shop.example/l",
                    "right_url": "https://shop.example/r",
                    "category": "laptop",
                    "product_family": left_family,
                }
            )
            meta.append(
                {
                    "batch": batch,
                    "pair_id": pair_id,
                    "left_listing": ids[(left, left_family)],
                    "right_listing": ids[(right, right_family)],
                    "stratum": stratum,
                    "seed": "1",
                    "overlap_of": "",
                }
            )
        write_csv(root / "batches" / f"{batch}.pairs.csv", BATCH_HEADER, batch_rows)
        for slug, rows in by_labeller.items():
            label_rows = []
            for pair_id, label in rows.items():
                left, right, left_family, _, _ = pairs[pair_id]
                label_rows.append(
                    {
                        "pair_id": pair_id,
                        "left_title": left,
                        "right_title": right,
                        "category": "laptop",
                        "product_family": left_family,
                        "label": label,
                        "labeller": names[slug],
                        "labelled_at": "2026-11-10",
                        "notes": SYNTHETIC,
                    }
                )
            write_csv(root / f"{batch}.{slug}.csv", LABEL_HEADER, label_rows)
    write_csv(root / "batches" / "pairs.meta.csv", META_HEADER, meta)
    return root


PAIRS: dict[str, PairSpec] = {
    "p0001": ("Nova Book 14 16GB", "Nova Book 14 (16 GB)", DEV_A, DEV_A, "same_family_cross_source"),
    "p0002": ("Nova Book 14 16GB", "Nova Book 14 8GB", DEV_A, DEV_A, "same_family_same_source"),
    "p0003": ("Orion Play 15", "Kestrel Slim 15", DEV_B, DEV_B, "hard_different"),
    "p0004": ("Vela Pro 14", "Vela Pro 14 Laptop", TEST_A, TEST_A, "mention_vs_title"),
    "p0005": ("Vela Pro 14", "Aurora Air 13", TEST_A, TEST_B, "random"),
    "p0006": ("Aurora Air 13", "Aurora Air 13 2025", TEST_B, TEST_B, "same_family_cross_source"),
    "p0007": ("Nova Book 14 16GB", "Vela Pro 14", DEV_A, TEST_A, "random"),
}


# --- the gate (DECIDED 1) -------------------------------------------------------------------------------------------


def test_the_gate_refuses_a_kappa_of_0_79() -> None:
    problems = own_eval.gate_problems(0.79, 200)
    assert problems and "0.79" in problems[0]
    assert own_eval.gate_problems(0.80, 200) == []
    assert own_eval.gate_problems(None, 200)  # no agreement batch at all


def test_the_gate_refuses_149_labelled_pairs() -> None:
    problems = own_eval.gate_problems(0.95, 149)
    assert problems and "149" in problems[0]
    assert own_eval.gate_problems(0.95, 150) == []


def test_the_gate_prints_both_numbers_and_the_usable_count_either_way(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    labels = {
        "agree-1": {
            "machine-a": {"p0001": "same", "p0002": "variant", "p0003": "different"},
            "machine-b": {"p0001": "same", "p0002": "variant", "p0003": "different"},
        }
    }
    tree = make_tree(tmp_path / "labelling", PAIRS, labels)
    assert own_eval.main(["--labels", str(tree)]) == 1  # 3 labelled pairs: refused
    out = capsys.readouterr().out
    assert "four-class kappa 1.000 (needs 0.80)" in out
    assert "labelled pairs 3 (needs 150)" in out
    assert "usable pairs 3" in out
    assert "Refused" in out


# --- gold labels (DECIDED 2 and 8) ----------------------------------------------------------------------------------


def test_unsure_and_disagreeing_pairs_are_dropped(tmp_path: Path) -> None:
    labels = {
        "agree-1": {
            "machine-a": {"p0001": "same", "p0002": "unsure", "p0003": "same", "p0004": "variant"},
            "machine-b": {"p0001": "same", "p0002": "same", "p0003": "different", "p0004": "different"},
        }
    }
    gold = own_eval.read_gold(make_tree(tmp_path / "labelling", PAIRS, labels))
    assert [p.pair_id for p in gold.pairs] == ["p0001", "p0004"]
    assert gold.dropped == {"unsure": 1, "labellers disagree": 1, "texts differ": 0}
    assert gold.labelled == 4
    assert [p.kind for p in gold.pairs] == ["same", "mixed"]  # variant against different: both say not same


def test_a_variant_is_not_a_match(tmp_path: Path) -> None:
    labels = {"solo-1": {"machine-a": {"p0001": "same", "p0002": "variant", "p0003": "different"}}}
    gold = own_eval.read_gold(make_tree(tmp_path / "labelling", PAIRS, labels))
    assert {p.pair_id: (p.label, p.kind) for p in gold.pairs} == {
        "p0001": (True, "same"),
        "p0002": (False, "variant"),
        "p0003": (False, "different"),
    }


def test_files_are_combined_by_pair_id_across_batches(tmp_path: Path) -> None:
    """A pair repeated in a later batch counts once, and is dropped when the two labels disagree (p0178's case)."""
    labels = {
        "solo-1": {"machine-a": {"p0001": "same", "p0003": "different"}},
        "solo-2": {"machine-b": {"p0001": "same", "p0003": "unsure", "p0004": "same"}},
    }
    gold = own_eval.read_gold(make_tree(tmp_path / "labelling", PAIRS, labels))
    assert [p.pair_id for p in gold.pairs] == ["p0001", "p0004"]
    assert gold.labelled == 3
    assert gold.dropped["unsure"] == 1
    assert {p.pair_id: p.families for p in gold.pairs}["p0001"] == frozenset({DEV_A})
    assert {p.pair_id: p.stratum for p in gold.pairs}["p0004"] == "mention_vs_title"


def test_a_proposed_draft_file_is_refused(tmp_path: Path) -> None:
    labels = {"solo-1": {"proposed-draft": {"p0001": "same"}}}
    tree = make_tree(tmp_path / "labelling", PAIRS, labels, {"proposed-draft": "proposed-draft"})
    with pytest.raises(PairSetError, match="proposed-draft"):
        own_eval.read_gold(tree)


def test_an_interim_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A valid label file under ml/data/interim/ is still refused, by the guard and before it is read."""
    tree = make_tree(tmp_path / "labelling", PAIRS, {"solo-1": {"machine-a": {"p0001": "same"}}})
    interim = tmp_path / "interim"
    interim.mkdir()
    copy = interim / "solo-1.machine-a.csv"
    copy.write_bytes((tree / "solo-1.machine-a.csv").read_bytes())  # a well-formed SYNTHETIC file
    monkeypatch.setattr(own_eval, "INTERIM_DIR", interim)
    with pytest.raises(PairSetError, match="is a draft, never a label: refused"):
        own_eval.read_gold(tree, [copy])
    assert own_eval.main(["--labels", str(interim)]) == 1  # not as a labelling folder either
    assert "is a draft, never a label: refused" in capsys.readouterr().out


def test_a_draft_file_is_refused_by_its_name_before_it_is_read(tmp_path: Path) -> None:
    """A file named for the proposed-draft labeller is refused, whatever its labeller column says."""
    tree = make_tree(tmp_path / "labelling", PAIRS, {"solo-1": {"machine-a": {"p0001": "same"}}})
    renamed = tree / "solo-1.proposed-draft.csv"
    (tree / "solo-1.machine-a.csv").rename(renamed)  # its rows still say Machine A
    with pytest.raises(PairSetError, match="proposed-draft"):
        own_eval.read_gold(tree)


def test_the_agreement_kappa_refuses_a_draft_file_too(tmp_path: Path) -> None:
    labels = {"agree-1": {"machine-a": {"p0001": "same"}, "proposed-draft": {"p0001": "same"}}}
    tree = make_tree(tmp_path / "labelling", PAIRS, labels, {"proposed-draft": "proposed-draft"})
    with pytest.raises(PairSetError, match="proposed-draft"):
        own_eval.agreement_kappa(tree)


# --- the numbers (DECIDED 3 and 4) ----------------------------------------------------------------------------------


def test_the_bootstrap_repeats_for_one_seed() -> None:
    outcomes = [(True, True), (True, False), (False, False), (False, True), (True, True), (False, False)] * 3
    first = own_eval.bootstrap(outcomes, resamples=200, seed=7)
    assert own_eval.bootstrap(outcomes, resamples=200, seed=7) == first
    assert set(first) == {"precision", "recall", "f1"}
    for interval in first.values():
        assert interval.low is not None and interval.high is not None
        assert 0.0 <= interval.low <= interval.high <= 1.0


def test_a_metric_that_is_undefined_in_a_resample_is_counted_not_guessed() -> None:
    intervals = own_eval.bootstrap([(False, False), (True, True)], resamples=50, seed=1)
    assert intervals["precision"].undefined > 0  # some resamples hold no pair called a match


def test_the_rates_by_stratum_and_kind_count_variants_and_differents_called_a_match(tmp_path: Path) -> None:
    labels = {"solo-1": {"machine-a": {"p0001": "same", "p0002": "variant", "p0003": "different", "p0006": "variant"}}}
    gold = own_eval.read_gold(make_tree(tmp_path / "labelling", PAIRS, labels))
    called = {"p0001": True, "p0002": True, "p0003": False, "p0006": False}
    rates = own_eval.called_match_rates(gold.pairs, [called[p.pair_id] for p in gold.pairs])
    assert rates["by_kind"]["variant"] == {"pairs": 2, "called_match": 1, "share": 0.5}
    assert rates["by_kind"]["different"] == {"pairs": 1, "called_match": 0, "share": 0.0}
    assert rates["by_kind"]["same"] == {"pairs": 1, "called_match": 1, "share": 1.0}
    same_source = rates["by_stratum"]["same_family_same_source"]
    assert same_source["variant_called_match"] == 1 and same_source["variant"] == 1


def test_the_evaluation_splits_by_family_and_chooses_on_dev_only(tmp_path: Path) -> None:
    labels = {
        "solo-1": {
            "machine-a": {
                "p0001": "same",
                "p0002": "variant",
                "p0003": "different",
                "p0004": "same",
                "p0005": "different",
                "p0006": "variant",
                "p0007": "different",
            }
        }
    }
    gold = own_eval.read_gold(make_tree(tmp_path / "labelling", PAIRS, labels))
    wdc80 = Choice("token_set_ratio", 79, 0.322)
    result = own_eval.evaluate_own(gold, wdc80, resamples=50, seed=3)
    assert [p.pair_id for p in result.split.dev] == ["p0001", "p0002", "p0003"]
    assert [p.pair_id for p in result.split.test] == ["p0004", "p0005", "p0006"]
    assert [p.pair_id for p in result.split.straddling] == ["p0007"]
    assert not result.split.dev_families & result.split.test_families
    assert result.at_wdc80.evaluation.choice == wdc80
    assert result.at_dev_choice.evaluation.test_pairs == 3


def test_only_the_dev_side_picks_the_threshold(tmp_path: Path) -> None:
    """Flipping every test label leaves the choice as it was: the test side is never read to choose."""
    labels = {
        "solo-1": {
            "machine-a": {
                "p0001": "same",
                "p0002": "variant",
                "p0003": "different",
                "p0004": "same",
                "p0005": "different",
                "p0006": "variant",
            }
        }
    }
    gold = own_eval.read_gold(make_tree(tmp_path / "labelling", PAIRS, labels))
    test_side = {"p0004", "p0005", "p0006"}
    flipped = [
        dataclasses.replace(p, label=not p.label, kind="different" if p.label else "same")
        if p.pair_id in test_side
        else p
        for p in gold.pairs
    ]
    wdc80 = Choice("token_set_ratio", 79, 0.322)
    first = own_eval.evaluate_own(gold, wdc80, resamples=10, seed=3).at_dev_choice.evaluation.choice
    again = own_eval.evaluate_own(dataclasses.replace(gold, pairs=flipped), wdc80, resamples=10, seed=3)
    assert again.at_dev_choice.evaluation.choice == first
    assert (
        again.at_dev_choice.evaluation.test != own_eval.evaluate_own(gold, wdc80, 10, 3).at_dev_choice.evaluation.test
    )


def test_a_full_run_passes_the_gate_prints_the_split_and_records_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """160 SYNTHETIC pairs: the gate passes, the report prints both sides and the small-test note, --out records."""
    families = [_family("dev", i) for i in range(4)] + [_family("test", i) for i in range(4)]
    strata = ["same_family_cross_source", "same_family_same_source", "mention_vs_title", "hard_different"]
    kinds = ["same", "variant", "different", "different", "unsure"]
    pairs: dict[str, PairSpec] = {}
    chosen: dict[str, str] = {}
    for i in range(160):
        pair_id, family, kind = f"p{i + 1:04d}", families[i % 8], kinds[i % 5]
        left = f"Syn Model {i} 16GB"
        right = {"same": f"Syn Model {i} (16 GB)", "variant": f"Syn Model {i} 8GB"}.get(kind, f"Other Thing {i}")
        pairs[pair_id] = (left, right, family, family, strata[i % 4])
        chosen[pair_id] = kind
    agree = dict(list(chosen.items())[:50])
    labels = {
        "agree-1": {"machine-a": agree, "machine-b": agree},
        "solo-1": {"machine-a": dict(list(chosen.items())[50:])},
    }
    tree = make_tree(tmp_path / "labelling", pairs, labels)
    results = tmp_path / "matching_baseline.json"
    wdc = {"dataset": "wdc_products", "subset": "80cc20rnd000un", "text": "title", "scorer": "token_set_ratio",
           "threshold": 0.79, "dev_f1": 0.322}  # fmt: skip
    results.write_text(json.dumps({"results": [wdc]}), encoding="utf-8")
    code = own_eval.main(["--labels", str(tree), "--results", str(results), "--out", str(results)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "four-class kappa 1.000 (needs 0.80); labelled pairs 160 (needs 150); usable pairs 128" in out
    assert "Split by product family: dev 64 pairs in 4 families; test 64 pairs in 4 families; 0 pairs left out" in out
    assert own_eval.SMALL_NOTE in out
    assert "At the threshold chosen on dev" in out and "At the WDC 80cc20rnd000un threshold" in out
    rows = json.loads(results.read_text(encoding="utf-8"))["results"]
    assert rows[0] == wdc
    assert [row["dataset"] for row in rows] == ["wdc_products", "own_pairs", "own_pairs"]
    assert all(row["labels"] == own_eval.LABELS_NOTE for row in rows[1:])


# --- the records (DECIDED 5) ----------------------------------------------------------------------------------------


def test_the_json_entries_say_machine_labels_and_keep_the_other_results(tmp_path: Path) -> None:
    results = tmp_path / "matching_baseline.json"
    others = [
        {"dataset": "wdc_products", "subset": "80cc20rnd000un", "text": "title", "scorer": "token_set_ratio",
         "threshold": 0.79, "dev_f1": 0.322},
        {"dataset": "abt_buy", "subset": "all pairs", "text": "name / name"},
        {"dataset": "own_pairs", "subset": "an old run", "text": "title"},
    ]  # fmt: skip
    results.write_text(json.dumps({"created": "2026-10-26", "results": others}), encoding="utf-8")
    assert own_eval.wdc80_choice(results) == Choice("token_set_ratio", 79, 0.322)
    labels = {
        "solo-1": {"machine-a": {"p0001": "same", "p0002": "variant", "p0003": "different", "p0004": "same"}},
        "solo-2": {"machine-b": {"p0005": "different", "p0006": "variant"}},
    }
    gold = own_eval.read_gold(make_tree(tmp_path / "labelling", PAIRS, labels))
    evaluation = own_eval.evaluate_own(gold, own_eval.wdc80_choice(results), resamples=20, seed=5)
    entries = own_eval.json_entries(evaluation, gold, kappa=0.933, commit="abc1234", date="2026-11-11")
    data = own_eval.merge_results(results, entries)
    rows = data["results"]
    assert isinstance(rows, list)
    kept = [entry for entry in rows if entry["dataset"] != "own_pairs"]
    assert kept == others[:2]
    own = [entry for entry in rows if entry["dataset"] == "own_pairs"]
    assert len(own) == 2 and {entry["subset"] for entry in own} != {"an old run"}
    for entry in own:
        assert entry["labels"] == own_eval.LABELS_NOTE and "machine labels" in entry["labels"]
        assert entry["commit"] == "abc1234" and entry["date"] == "2026-11-11"
        assert entry["pairs"]["labelled"] == 6 and entry["pairs"]["usable"] == 6
        assert set(entry["intervals"]) == {"precision", "recall", "f1"}
        assert "small" in entry["intervals_note"]
    assert json.loads(results.read_text(encoding="utf-8")) == data


def _check_listing_header(header: Sequence[str]) -> None:  # keeps the imports honest if a header moves
    assert "product_family" in header


def test_the_tree_helper_writes_the_real_formats() -> None:
    _check_listing_header(LISTING_HEADER)
    assert "stratum" in META_HEADER and "product_family" in LABEL_HEADER
