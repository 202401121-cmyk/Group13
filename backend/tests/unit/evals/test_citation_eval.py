"""US-22's measuring code (S5-4 DECIDED 1, 6): the rates and the threshold rule, the bootstrap, the sheet's checks
before it is used, config/nli.yaml written in place, the model comparison, and the floors with their targets. No
model: fakes and made-up numbers only."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

from evals import thresholds
from evals.citation import metrics, model_check, sheet
from evals.citation.config_file import set_keys
from evals.citation.metrics import Scored
from evals.citation.pairs import SheetProblem, load_pairs, load_split


def _pairs(rows: list[tuple[bool, float, bool]]) -> list[Scored]:
    return [Scored(f"c{i:03d}", supported, prob, ok) for i, (supported, prob, ok) in enumerate(rows, start=1)]


def test_caught_and_wrongly_rejected_follow_the_threshold_and_the_number_rule() -> None:
    pairs = _pairs([(True, 0.9, True), (True, 0.4, True), (False, 0.2, True), (False, 0.95, False)])
    assert metrics.rates(pairs, 0.5) == (1.0, 0.5)  # the wrong one at 0.95 is caught by its number
    assert metrics.rates(pairs, 0.3) == (1.0, 0.0)


def test_the_threshold_catches_the_most_within_15_percent_wrongly_rejected() -> None:
    right = [(True, p, True) for p in (0.95, 0.9, 0.85, 0.8, 0.7, 0.6, 0.5, 0.3)]
    wrong = [(False, p, True) for p in (0.75, 0.55, 0.4, 0.2, 0.1, 0.05)]
    pairs = _pairs(right + wrong)
    t = metrics.tune(pairs)
    caught, rejected = metrics.rates(pairs, t)
    assert rejected <= 0.15
    assert (caught, rejected) == (4 / 6, 1 / 8)  # only 0.3 of the right ones goes; 0.55 and 0.75 wrong ones pass
    assert all(
        metrics.rates(pairs, other)[0] <= caught
        for other in metrics.candidates(pairs)
        if metrics.rates(pairs, other)[1] <= 0.15
    )


def test_the_pairs_the_check_got_wrong_are_named_with_what_decided_each() -> None:
    pairs = _pairs([(True, 0.9, True), (True, 0.4, True), (True, 0.95, False), (False, 0.8, True), (False, 0.2, True)])
    assert metrics.misjudged(pairs, 0.5) == {
        "rejected_right": [
            {"id": "c002", "entailment": 0.4, "numbers_ok": True},
            {"id": "c003", "entailment": 0.95, "numbers_ok": False},
        ],
        "passed_wrong": [{"id": "c004", "entailment": 0.8, "numbers_ok": True}],
    }


def test_no_threshold_within_the_limit_is_said_plainly() -> None:
    pairs = _pairs([(True, 0.1, False), (True, 0.2, False), (False, 0.9, True)])
    with pytest.raises(ValueError, match="at most 15%"):
        metrics.tune(pairs)


def test_the_bootstrap_interval_holds_the_measured_rate_and_is_seeded() -> None:
    pairs = _pairs([(i % 2 == 0, (i % 10) / 10, True) for i in range(60)])
    one = metrics.bootstrap(pairs, 0.45, resamples=500)
    assert one == metrics.bootstrap(pairs, 0.45, resamples=500)
    caught, rejected = metrics.rates(pairs, 0.45)
    assert one["caught"][0] <= caught <= one["caught"][1]
    assert one["wrongly_rejected"][0] <= rejected <= one["wrongly_rejected"][1]


def test_a_number_in_the_sentence_must_be_in_the_quote() -> None:
    assert metrics.numbers_in_quote("It lasts 10 hours.", "The battery lasted 10 hours in our test.")
    assert not metrics.numbers_in_quote("It lasts 12 hours.", "The battery lasted 10 hours in our test.")


# --- the sheet, checked before use ------------------------------------------------------------------------------------


def _drawn() -> list[sheet.Quote]:
    return [
        sheet.Quote(f"Quote number {i} has enough words in it.", "https://a.example.test/", "run") for i in range(3)
    ]


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=sheet.COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _row(i: int, **changes: str) -> dict[str, str]:
    row = {
        "id": f"c{i + 1:03d}",
        "quote": f"Quote number {i} has enough words in it.",
        "source_address": "https://a.example.test/",
        "sentence": f"A sentence a person wrote about quote {i}.",
        "label": "supported" if i % 2 else "unsupported",
        "written_by": "Rushiraj Navar" if i % 2 else "jhanvi",
    }
    return {**row, **changes}


def test_a_complete_sheet_gives_its_pairs_with_each_writer_named_in_full(tmp_path: Path) -> None:
    path = tmp_path / "pairs.csv"
    _write(path, [_row(i) for i in range(3)])
    pairs = load_pairs(path, _drawn())
    assert [(p.id, p.supported, p.written_by) for p in pairs] == [
        ("c001", False, "Jhanvi Barot"),
        ("c002", True, "Rushiraj Navar"),
        ("c003", False, "Jhanvi Barot"),
    ]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"sentence": ""}, "c002: no sentence"),
        ({"label": "maybe"}, "c002: the label is not supported or unsupported"),
        ({"written_by": "Machine A"}, "c002: written_by is not Rushiraj Navar or Jhanvi Barot"),
        ({"quote": "An edited quote that now supports the sentence."}, "c002: its quote is not the one drawn"),
    ],
)
def test_a_row_that_isnt_ready_is_named_and_the_sheet_is_not_used(
    tmp_path: Path, change: dict[str, str], problem: str
) -> None:
    path = tmp_path / "pairs.csv"
    _write(path, [_row(0), _row(1, **change), _row(2)])
    with pytest.raises(SheetProblem) as caught:
        load_pairs(path, _drawn())
    assert any(p.startswith(problem) for p in caught.value.problems)


def test_a_missing_row_is_named(tmp_path: Path) -> None:
    path = tmp_path / "pairs.csv"
    _write(path, [_row(0), _row(1)])
    with pytest.raises(SheetProblem, match="1 drawn ids are missing: c003"):
        load_pairs(path, _drawn())


def test_the_repository_sheet_is_complete_written_by_the_two_people_and_split_without_overlap() -> None:
    """By-hand job 2 (D-55): Rushiraj Navar wrote c001 to c050 and Jhanvi Barot c051 to c100, by hand."""
    pairs = load_pairs()  # every quote checked against the seeded draw from the recorded runs
    assert [p.id for p in pairs] == [f"c{i:03d}" for i in range(1, 101)]
    assert {p.written_by for p in pairs[:50]} == {"Rushiraj Navar"}
    assert {p.written_by for p in pairs[50:]} == {"Jhanvi Barot"}
    tune, test = load_split()
    assert (len(tune), len(test)) == (40, 60)
    assert not set(tune) & set(test)
    supported = {p.id: p.supported for p in pairs}
    assert sum(supported[i] for i in tune) == 20  # half right and half deliberately wrong, in each part
    assert sum(supported[i] for i in test) == 30


def test_the_committed_record_was_measured_at_the_configs_threshold_on_the_60_held_out() -> None:
    from app.explain.nli_settings import load_nli_settings
    from evals.citation.run import CITATION_OUT

    record = json.loads(CITATION_OUT.read_text(encoding="utf-8"))
    assert record["threshold"] == load_nli_settings(env={}).threshold
    assert (record["tuning"]["pairs"], record["test"]["pairs"]) == (40, 60)
    assert record["writers"] == {"Jhanvi Barot": 50, "Rushiraj Navar": 50}
    assert record["test"]["targets"] == {"caught": 0.8, "wrongly_rejected": 0.15}


# --- config/nli.yaml, written in place --------------------------------------------------------------------------------

CONFIG = """# a comment that stays
repo: "x"
model_file: ""
threshold: null  # tuned on the 40
reference: []  # written by the model check
quoted_words: 6
"""


def test_set_keys_changes_only_the_values_and_keeps_every_comment() -> None:
    text = set_keys(
        CONFIG,
        {
            "model_file": "onnx/model_quint8_avx2.onnx",
            "threshold": 0.62,
            "reference": [{"premise": "p", "hypothesis": "h", "entailment": 0.9}],
        },
    )
    assert "# a comment that stays" in text and "threshold: 0.62  # tuned on the 40" in text
    data = yaml.safe_load(text)
    assert data["model_file"] == "onnx/model_quint8_avx2.onnx" and data["quoted_words"] == 6
    assert data["reference"] == [{"premise": "p", "hypothesis": "h", "entailment": 0.9}]
    again = set_keys(text, {"reference": [{"premise": "q", "hypothesis": "h", "entailment": 0.1}]})
    assert yaml.safe_load(again)["reference"] == [{"premise": "q", "hypothesis": "h", "entailment": 0.1}]
    with pytest.raises(KeyError):
        set_keys(CONFIG, {"not_there": 1})


# --- the model comparison ---------------------------------------------------------------------------------------------


class _Scorer:
    def __init__(self, shift: float = 0.0) -> None:
        self.shift = shift
        self.batches: list[list[tuple[str, str]]] = []

    def premises(self, quote: str) -> list[str]:
        return quote.split("|")

    def entailment(self, pairs: Sequence[tuple[str, str]]) -> list[float]:
        self.batches.append(list(pairs))
        return [min(1.0, len(p) / 100 + self.shift) for p, _ in pairs]


def test_the_best_chunk_counts_for_each_pair() -> None:
    scorer = _Scorer()
    found = model_check.best_entailment(scorer, [("a|" + "b" * 50, "s1"), ("c" * 10, "s2")], batch=1)
    assert found == [0.5, 0.1]
    assert len(scorer.batches) == 2


def test_the_smallest_file_within_002_of_the_full_model_is_chosen() -> None:
    full = [0.1, 0.5, 0.9]
    probabilities = {
        model_check.FULL: full,
        "onnx/model_quint8_avx2.onnx": [0.11, 0.53, 0.9],  # 0.03 off on one pair
        "onnx/model_O4.onnx": [0.1, 0.501, 0.899],
    }
    sizes = {model_check.FULL: 284, "onnx/model_quint8_avx2.onnx": 87, "onnx/model_O4.onnx": 151}
    rows, chosen = model_check.compare(probabilities, sizes)
    assert chosen == "onnx/model_O4.onnx"
    assert rows["onnx/model_quint8_avx2.onnx"]["over_tolerance"] == 1


# --- the floors -------------------------------------------------------------------------------------------------------


def test_wrongly_rejected_is_a_ceiling_and_its_15_percent_target_is_met_from_below() -> None:
    record = {
        "generated": "2026-11-22",
        "commit": "abcdef1",
        "runner": "python -m evals.citation.run (offline)",
        "file": "backend/evals/results/citation.json",
        "measures": [
            {
                "id": "citation.test.caught_wrong",
                "value": 0.83,
                "unit": "rate",
                "status": "measured",
                "source": "s",
                "detail": "d",
            },
            {
                "id": "citation.test.wrongly_rejected",
                "value": 0.1,
                "unit": "rate",
                "status": "measured",
                "source": "s",
                "detail": "d",
            },
            {
                "id": "citation.check_seconds_slowest",
                "value": 1.2,
                "unit": "seconds",
                "status": "measured",
                "source": "s",
                "detail": "d",
            },
        ],
    }
    built = thresholds.build([record])["measures"]
    caught, rejected, slowest = (
        built["citation.test.caught_wrong"],
        built["citation.test.wrongly_rejected"],
        built["citation.check_seconds_slowest"],
    )
    assert (caught["direction"], caught["floor"], caught["target"]["value"], caught["target"]["note"]) == (
        "at_least",
        0.78,
        0.8,
        "met",
    )
    assert (rejected["direction"], rejected["floor"], rejected["target"]["value"], rejected["target"]["note"]) == (
        "at_most",
        0.15,
        0.15,
        "met",
    )
    assert (slowest["direction"], slowest["floor"]) == ("at_most", 5.0)
    thresholds.Thresholds.model_validate(thresholds.build([record]))  # every floor is a valid one
