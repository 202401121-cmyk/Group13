"""The labelling status report (ML-1): counts, label mix, agreement and kappa, the disagreements, repeated pairs and
the next step. All labels here are scripted for the test; none is a person's real label."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pytest
from conftest import VALID_ROW, write_csv
from labelling_status import main, status_report
from make_pairs import BATCH_HEADER

WriteLabels = Callable[..., Path]  # the write_labels fixture in conftest.py


def _batch(folder: Path, name: str, size: int, first: int = 1, extra: Sequence[str] = ()) -> list[str]:
    """A batch file whose pairs all carry conftest's VALID_ROW titles; `extra` adds repeated pair ids."""
    ids = [f"p{n:04d}" for n in range(first, first + size)] + list(extra)
    rows = [
        {
            "pair_id": pair_id,
            "left_text": VALID_ROW["left_title"],
            "right_text": VALID_ROW["right_title"],
            "left_url": "",
            "right_url": "",
            "category": "laptop",
            "product_family": "nova-book",
        }
        for pair_id in ids
    ]
    (folder / "batches").mkdir(exist_ok=True)
    write_csv(folder / "batches" / f"{name}.pairs.csv", BATCH_HEADER, rows)
    return ids


def _labels(write_labels: WriteLabels, name: str, labeller: str, labels: dict[str, str]) -> Path:
    return write_labels(
        name, [{"pair_id": pair_id, "label": label, "labeller": labeller} for pair_id, label in labels.items()]
    )


def _report(folder: Path) -> str:
    lines, _ = status_report(folder)
    return "\n".join(lines)


def _all(ids: Sequence[str], labels: str) -> dict[str, str]:
    """One label per pair, from a string such as 'ssvd': s same, v variant, d different, u unsure."""
    names = {"s": "same", "v": "variant", "d": "different", "u": "unsure"}
    return {pair_id: names[letter] for pair_id, letter in zip(ids, labels, strict=True)}


def test_no_batches_says_make_agree_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "No batches yet." in out
    assert "Next step: make agree-1" in out and "--seed 101" in out


def test_counts_and_label_mix(tmp_path: Path, write_labels: WriteLabels) -> None:
    ids = _batch(tmp_path, "agree-1", 6)
    _labels(write_labels, "agree-1.alex.csv", "Alex", _all(ids, "ssvdus"))
    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids[:3], "ssv"))
    report = _report(tmp_path)
    assert "agree-1  6 pairs  alex 6/6  sam 3/6" in report
    assert "Labelled: 9 rows by 2 labellers, 6 distinct pairs" in report
    # While Sam is still labelling, the mix stays empty: Sam could subtract his own counts to learn Alex's.
    assert "Label mix (finished batches): same 0, variant 0, different 0, unsure 0" in report
    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids, "ssvdud"))
    assert "Label mix (finished batches): same 5, variant 2, different 3, unsure 2" in _report(tmp_path)


def test_an_unfinished_agreement_batch_says_label_agree_1(tmp_path: Path, write_labels: WriteLabels) -> None:
    ids = _batch(tmp_path, "agree-1", 6)
    _labels(write_labels, "agree-1.alex.csv", "Alex", _all(ids, "ssvdus"))
    report = _report(tmp_path)
    assert "Next step: label agree-1 (the second labeller has not started)" in report
    # Alex is done, but Sam has not started: not even Alex's label mix may show yet.
    assert "Label mix (finished batches): same 0, variant 0, different 0, unsure 0" in report
    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids[:2], "ss"))
    assert "Next step: label agree-1 (sam has 4 pairs left)" in _report(tmp_path)


def test_an_unfinished_batch_shows_nobody_elses_labels(tmp_path: Path, write_labels: WriteLabels) -> None:
    """While Sam is still labelling, the report must not show Alex's answers (Sam could go back and change his)."""
    ids = _batch(tmp_path, "agree-1", 6)
    _labels(write_labels, "agree-1.alex.csv", "Alex", _all(ids, "ssvdus"))
    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids[:3], "vvd"))  # three disagreements so far
    report = _report(tmp_path)
    assert "agree-1: hidden until everyone has finished it" in report
    assert "kappa" not in report
    for pair_id in ids:
        assert pair_id not in report


def test_low_kappa_says_discuss_disagreements_and_lists_them(tmp_path: Path, write_labels: WriteLabels) -> None:
    ids = _batch(tmp_path, "agree-1", 50)
    _labels(write_labels, "agree-1.alex.csv", "Alex", _all(ids, "ssvddu" + "s" * 44))
    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids, "svvdsd" + "s" * 44))
    report = _report(tmp_path)
    assert "agree-1, alex and sam: 50 pairs, 47 agree (94.0%)" in report
    assert "four-class kappa 0.603" in report  # by hand: p_o 2350/2500, p_e 2122/2500, kappa 228/378
    assert "p0002  alex: same       sam: variant" in report
    assert "p0006  alex: unsure     sam: different" in report
    assert "Next step: discuss the disagreements on agree-1" in report
    assert "make agree-2" in report and "never re-label agree-1" in report


def test_kappa_of_at_least_0_80_says_start_solo_1(tmp_path: Path, write_labels: WriteLabels) -> None:
    ids = _batch(tmp_path, "agree-1", 50)
    labels = "ssvddu" * 8 + "ss"
    _labels(write_labels, "agree-1.alex.csv", "Alex", _all(ids, labels))
    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids, labels))
    report = _report(tmp_path)
    assert "four-class kappa 1.000" in report
    assert "Next step: start solo-1" in report and "--seed 201 --overlap 5 --overlap-from agree-1" in report

    solo = _batch(tmp_path, "solo-1", 5, first=51)
    _labels(write_labels, "solo-1.alex.csv", "Alex", _all(solo[:2], "sd"))
    assert "Next step: label solo-1 (alex has 3 pairs left)" in _report(tmp_path)


def test_a_small_agreement_batch_cannot_pass_the_gate(tmp_path: Path, write_labels: WriteLabels) -> None:
    ids = _batch(tmp_path, "agree-1", 6)
    _labels(write_labels, "agree-1.alex.csv", "Alex", _all(ids, "ssvddu"))
    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids, "ssvddu"))
    report = _report(tmp_path)
    assert "four-class kappa 1.000" in report
    assert "Next step: agree-1 has only 6 pairs; the agreement rule needs 50" in report
    assert "solo-1" not in report


def test_every_labeller_must_finish_and_agree_with_every_other(tmp_path: Path, write_labels: WriteLabels) -> None:
    ids = _batch(tmp_path, "agree-1", 50)
    good = "ssvddu" * 8 + "ss"
    _labels(write_labels, "agree-1.alex.csv", "Alex", _all(ids, good))
    _labels(write_labels, "agree-1.om.csv", "Om", _all(ids, good))
    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids[:10], good[:10]))
    assert "Next step: label agree-1 (sam has 40 pairs left)" in _report(tmp_path)  # alex and om alone don't pass

    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids, "d" * 50))  # sam finishes, agreeing with nobody
    report = _report(tmp_path)
    assert "agree-1, alex and om: 50 pairs, 50 agree" in report
    assert "agree-1, alex and sam: 50 pairs" in report and "agree-1, om and sam: 50 pairs" in report
    assert "Next step: discuss the disagreements on agree-1" in report
    assert "alex and sam" in report.split("Next step:")[1] and "om and sam" in report.split("Next step:")[1]


def test_repeated_pairs_compare_people_and_each_person_with_themselves(
    tmp_path: Path, write_labels: WriteLabels
) -> None:
    ids = _batch(tmp_path, "agree-1", 50)
    labels = "ssvddu" * 8 + "ss"
    _labels(write_labels, "agree-1.alex.csv", "Alex", _all(ids, labels))
    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids, labels))
    # Sam's solo batch repeats p0001 and p0002 from agree-1, which keep agree-1's pair ids.
    solo = _batch(tmp_path, "solo-1", 3, first=51, extra=["p0001", "p0002"])
    _labels(write_labels, "solo-1.sam.csv", "Sam", {**_all(solo[:3], "ssd"), "p0001": "variant", "p0002": "same"})
    report = _report(tmp_path)
    assert "agree-1, alex and sam: 50 pairs, 50 agree" in report  # the later label does not rewrite agree-1
    assert "alex first, sam again: 2 pairs, 1 agree (50.0%)" in report
    assert "p0001  alex: same       sam: variant" in report
    assert "sam labelled 2 pairs twice, 1 the same (p0001 same then variant)" in report


def test_a_row_whose_titles_differ_from_its_batch_is_left_out(tmp_path: Path, write_labels: WriteLabels) -> None:
    ids = _batch(tmp_path, "agree-1", 3)
    path = write_labels(
        "agree-1.alex.csv",
        [
            {"pair_id": "p0001", "labeller": "Alex"},
            {"pair_id": "p0002", "labeller": "Alex", "right_title": "Nova Book 16 (16GB, 512GB)"},
            {"pair_id": "p0009", "labeller": "Alex"},
        ],
    )
    lines, code = status_report(tmp_path)
    report = "\n".join(lines)
    assert code == 1
    assert f"{path}:3: the titles of p0002 differ from batch agree-1" in report
    assert f"{path}:4: pair p0009 is not in batch agree-1" in report
    assert "agree-1  3 pairs  alex 1/3" in report
    assert ids


def test_a_broken_labeller_file_is_reported_with_its_row(
    tmp_path: Path, write_labels: WriteLabels, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = _batch(tmp_path, "agree-1", 3)
    _labels(write_labels, "agree-1.alex.csv", "Alex", _all(ids, "ssv"))
    broken = _labels(write_labels, "agree-1.sam.csv", "Sam", {"p0001": "same", "p0002": "maybe"})
    assert main([str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert f"{broken}:3: label 'maybe' must be one of" in out
    assert "agree-1  3 pairs  alex 3/3  sam 1/3" in out  # the rest of the report still runs


def test_a_damaged_batch_file_does_not_stop_the_report(tmp_path: Path, write_labels: WriteLabels) -> None:
    (tmp_path / "batches").mkdir()
    (tmp_path / "batches" / "agree-1.pairs.csv").write_text("not,the,right,header\n", encoding="utf-8")
    _labels(write_labels, "agree-1.alex.csv", "Alex", {"p0001": "same"})
    _labels(write_labels, "agree-1.sam.csv", "Sam", {"p0001": "variant"})
    lines, code = status_report(tmp_path)
    report = "\n".join(lines)
    assert code == 1
    assert "agree-1.pairs.csv:1: the header has 4 columns" in report
    assert "p0001  alex" not in report  # nothing is compared on a batch that could not be read


def test_repeats_from_an_unfinished_batch_stay_hidden(tmp_path: Path, write_labels: WriteLabels) -> None:
    ids = _batch(tmp_path, "agree-1", 50)
    labels = "ssvddu" * 8 + "ss"
    _labels(write_labels, "agree-1.alex.csv", "Alex", _all(ids, labels))
    _labels(write_labels, "agree-1.sam.csv", "Sam", _all(ids, labels))
    solo_1 = _batch(tmp_path, "solo-1", 4, first=51)
    _labels(write_labels, "solo-1.sam.csv", "Sam", _all(solo_1[:2], "sd"))  # Sam is still on solo-1
    _batch(tmp_path, "solo-2", 2, first=55, extra=["p0051"])
    _labels(write_labels, "solo-2.alex.csv", "Alex", {"p0055": "same", "p0056": "same", "p0051": "variant"})
    report = _report(tmp_path)
    assert "sam first, alex again" not in report  # Sam could still change p0051
    assert "p0051" not in report


def test_batches_are_listed_in_the_order_they_were_made(tmp_path: Path) -> None:
    _batch(tmp_path, "agree-1", 3)
    _batch(tmp_path, "solo-1", 3, first=4)
    _batch(tmp_path, "agree-2", 3, first=7)  # made after solo-1, so its new pair ids are higher
    report = _report(tmp_path)
    assert report.index("agree-1  3 pairs") < report.index("solo-1  3 pairs") < report.index("agree-2  3 pairs")
