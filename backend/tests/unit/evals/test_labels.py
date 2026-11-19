"""US-49's label check (S4-13): each decide brief's hand-labelled winner, recomputed by the scoring code."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from evals import labels


def _copy(tmp_path: Path) -> Path:
    root = tmp_path / "decide"
    shutil.copytree(labels.BRIEFS, root)
    return root


def test_every_brief_with_a_winner_is_checked_and_agrees() -> None:
    names = [p.stem for p in labels.briefs()]
    assert len(names) == 6 and "close_earbuds" in names
    assert labels.disagreements() == []


def test_a_wrong_label_is_caught(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    path = root / "laptop_coding_70k.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    shown = data["expect"]["shown"]
    data["expect"]["shown"] = [shown[-1], *shown[:-1]]  # the last shown labelled as the winner
    path.write_text(json.dumps(data), encoding="utf-8")
    (problem,) = labels.disagreements(root)
    assert problem.startswith("laptop_coding_70k: labelled winner")


def test_a_label_naming_no_product_is_caught(tmp_path: Path) -> None:
    root = _copy(tmp_path)
    path = root / "monitor_27_gaming.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["expect"]["shown"][0] = "prd_made_up_missing"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert labels.disagreements(root) == [
        "monitor_27_gaming: the label names products the brief doesn't have: ['prd_made_up_missing']"
    ]


def test_a_tie_on_the_score_accepts_either_label(tmp_path: Path) -> None:
    """In the close brief Pike Air and Wren Buds 2 score the same: either may be labelled the winner."""
    root = _copy(tmp_path)
    path = root / "stability" / "close_earbuds.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    shown = data["expect"]["shown"]
    assert shown[:2] == ["Pike Air", "Wren Buds 2"]
    data["expect"]["shown"] = [shown[1], shown[0], *shown[2:]]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert labels.disagreements(root) == []


def test_a_brief_showing_nothing_has_no_winner_to_check() -> None:
    assert labels.disagreement(labels.BRIEFS / "ereader_8k.json") is None


def test_main_passes_on_the_committed_briefs() -> None:
    assert labels.main() == 0
