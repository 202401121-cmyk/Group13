"""S5-4b DECIDED 6 and 7: the evidence sheet. The pipeline's own sentences go to it in a fixed order until 20 are
kept; a labelled sheet is never written over; every row must be labelled by one of the two people before it is
measured; and NLI turns on by default only at 80% kept and 80% caught. Fakes and made-up text only; no model loads."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app.explain.facts import ProductFacts, facts_for
from app.explain.parsing import Draft
from evals.citation import evidence_sheet as es
from evals.citation.pairs import SheetProblem
from tests.unit.explain.build import every_claim, kestrel, nova


def _products() -> list[es.Product]:
    claims = every_claim()
    return [("run_a", facts_for(nova(), claims), claims), ("run_b", facts_for(kestrel(), claims), claims)]


def _ask(per_product: int) -> es.Ask:
    def ask(facts: ProductFacts, claims: list) -> list[Draft]:  # type: ignore[type-arg]
        return [
            Draft(f.key, f"A made-up sentence {i} about {facts.product_id}.", f.claim_ids, ("A made-up quote.", "Two."))
            for i, f in enumerate(facts.top[:per_product])
        ]

    return ask


def test_rows_come_in_the_order_given_and_stop_at_the_count() -> None:
    rows = es.collect(_products(), _ask(3), count=4)
    assert [r.id for r in rows] == ["e001", "e002", "e003", "e004"]
    assert [r.run for r in rows] == ["run_a", "run_a", "run_a", "run_b"]
    assert rows[0].quotes == ("A made-up quote.", "Two.")


def test_rows_already_written_are_kept_and_their_products_not_asked_again() -> None:
    asked: list[str] = []
    ask = _ask(3)

    def counting(facts: ProductFacts, claims: list) -> list[Draft]:  # type: ignore[type-arg]
        asked.append(facts.product_id)
        return ask(facts, claims)

    first = es.collect(_products()[:1], _ask(1), count=4)
    rows = es.collect(_products(), counting, count=4, done=first)
    assert rows[0] == first[0]
    assert asked == [kestrel().product_id]  # nova's row was already written
    assert [r.id for r in rows] == ["e001", "e002", "e003", "e004"]


def test_the_recorded_products_go_by_rank_across_runs() -> None:
    products = es.recorded_products()
    ranks = [facts.rank for _, facts, _ in products]
    assert ranks == sorted(ranks)  # every run's #1, then every #2, then every #3
    assert len({run for run, _, _ in products}) == 5


def _write(tmp_path: Path) -> tuple[Path, Path, list[es.Row]]:
    sheet, records = tmp_path / "sheet.csv", tmp_path / "sheet.json"
    rows = es.collect(_products(), _ask(2), count=3)
    es.write(rows, sheet, records)
    return sheet, records, rows


def _label(sheet: Path, labels: dict[str, tuple[str, str]]) -> None:
    with sheet.open(encoding="utf-8", newline="") as file:
        lines = list(csv.DictReader(file))
    for line in lines:
        line["label"], line["written_by"] = labels.get(line["id"], ("", ""))
    with sheet.open("w", encoding="utf-8", newline="") as file:
        out = csv.DictWriter(file, fieldnames=es.COLUMNS, lineterminator="\n")
        out.writeheader()
        out.writerows(lines)


def test_the_blank_sheet_leaves_the_labels_to_people_and_is_never_written_over(tmp_path: Path) -> None:
    sheet, records, rows = _write(tmp_path)
    with sheet.open(encoding="utf-8", newline="") as file:
        lines = list(csv.DictReader(file))
    assert [(line["label"], line["written_by"]) for line in lines] == [("", "")] * 3
    assert lines[0]["quote"] == "A made-up quote. | Two."
    assert [r["id"] for r in json.loads(records.read_text(encoding="utf-8"))] == [r.id for r in rows]
    _label(sheet, {"e001": ("supported", "Rushiraj Navar")})
    with pytest.raises(SheetProblem, match="never written over"):
        es.write(rows, sheet, records)


def test_a_fully_labelled_sheet_loads_with_each_labeller_named(tmp_path: Path) -> None:
    sheet, records, _ = _write(tmp_path)
    _label(
        sheet,
        {"e001": ("supported", "rushiraj"), "e002": ("Unsupported", "Jhanvi Barot"), "e003": ("supported", "Jhanvi")},
    )
    loaded = es.load(sheet, records)
    assert [(row.id, label, who) for row, label, who in loaded] == [
        ("e001", True, "Rushiraj Navar"),
        ("e002", False, "Jhanvi Barot"),
        ("e003", True, "Jhanvi Barot"),
    ]


def test_a_row_not_labelled_by_one_of_the_two_people_is_named(tmp_path: Path) -> None:
    sheet, records, _ = _write(tmp_path)
    _label(sheet, {"e001": ("supported", "Machine A"), "e002": ("maybe", "Jhanvi Barot")})
    with pytest.raises(SheetProblem) as caught:
        es.load(sheet, records)
    assert caught.value.problems == [
        "e001: written_by is not Rushiraj Navar or Jhanvi Barot",
        "e002: the label is not supported or unsupported",
        "e003: the label is not supported or unsupported",
        "e003: written_by is not Rushiraj Navar or Jhanvi Barot",
    ]


def test_a_sentence_changed_after_it_was_written_is_named(tmp_path: Path) -> None:
    sheet, records, _ = _write(tmp_path)
    _label(sheet, {i: ("supported", "Jhanvi Barot") for i in ("e001", "e002", "e003")})
    text = sheet.read_text(encoding="utf-8").replace("A made-up sentence 0", "An edited sentence 0", 1)
    sheet.write_text(text, encoding="utf-8", newline="\n")
    with pytest.raises(SheetProblem, match="e001: its sentence or quote is not the one written"):
        es.load(sheet, records)


def test_shares_and_the_80_percent_rule_for_turning_nli_on() -> None:
    found = es.shares([(True, True)] * 8 + [(True, False)] * 2 + [(False, False)] * 8 + [(False, True)] * 2, 500)
    assert {k: found[k] for k in ("supported", "unsupported", "kept_supported", "caught_unsupported")} == {
        "supported": 10,
        "unsupported": 10,
        "kept_supported": 0.8,
        "caught_unsupported": 0.8,
    }
    low, high = found["intervals_95"]["kept_supported"]
    assert low <= 0.8 <= high and found == es.shares(
        [(True, True)] * 8 + [(True, False)] * 2 + [(False, False)] * 8 + [(False, True)] * 2, 500
    )  # seeded
    assert es.meets(found)
    assert not es.meets(es.shares([(True, True)] * 7 + [(True, False)] * 3 + [(False, False)] * 10))
    assert not es.meets(es.shares([(True, True)] * 5))  # nothing unsupported to catch: not shown
