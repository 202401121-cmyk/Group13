"""S3-4: the threshold picker (DECIDED 5) and the checkpoint finder, on SYNTHETIC scores and files."""

from __future__ import annotations

from pathlib import Path

import pytest
from checkpoints import checkpoint_dir, latest_checkpoint, mark_done
from matcher_threshold import choose_threshold


def test_the_picker_takes_the_highest_recall_with_precision_of_0_95() -> None:
    # 20 matches from 0.99 down to 0.80, then a wrong one at 0.75, then 5 more matches below it.
    scores = [0.99 - 0.01 * i for i in range(20)] + [0.75] + [0.70, 0.69, 0.68, 0.67, 0.66]
    labels = [True] * 20 + [False] + [True] * 5
    choice = choose_threshold(scores, labels)
    assert choice.met
    assert choice.precision >= 0.95
    # Going on down to 0.66 keeps 25 of 26 right (0.962) and catches every match: the highest recall at precision 0.95.
    assert choice.threshold == pytest.approx(0.66)
    assert choice.recall == 1.0


def test_a_lower_threshold_with_the_same_precision_wins_by_recall() -> None:
    choice = choose_threshold([0.9, 0.8, 0.7, 0.6], [True, True, True, True])
    assert (choice.threshold, choice.recall, choice.precision) == (0.6, 1.0, 1.0)


def test_the_picker_says_so_when_no_threshold_reaches_the_target() -> None:
    choice = choose_threshold([0.9, 0.8, 0.7, 0.6, 0.5], [False, True, False, True, False])
    assert not choice.met
    assert choice.precision < 0.95


def test_the_picker_cannot_split_a_tie() -> None:
    tied = choose_threshold([0.8, 0.8, 0.8, 0.3], [True, True, False, False])
    assert not tied.met  # all three at 0.8 are called together: 2 of 3 are right


def test_the_picker_handles_empty_and_mismatched_input() -> None:
    assert not choose_threshold([], []).met
    with pytest.raises(ValueError, match="same length"):
        choose_threshold([0.5], [True, False])


def test_resume_picks_the_last_finished_checkpoint(tmp_path: Path) -> None:
    assert latest_checkpoint(tmp_path) is None
    for epoch in (1, 2):
        folder = checkpoint_dir(tmp_path, epoch)
        folder.mkdir(parents=True)
        mark_done(folder)
    checkpoint_dir(tmp_path, 3).mkdir()  # the run stopped while writing epoch 3: no DONE marker
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert latest_checkpoint(tmp_path) == (2, checkpoint_dir(tmp_path, 2))


def test_a_lower_threshold_that_adds_no_recall_is_not_taken() -> None:
    """20 matches from 0.99 down to 0.80, then one wrong at 0.50: precision is still 20/21 = 0.952, but recall is the
    same, so the threshold stays at 0.80, not 0.50."""
    scores = [0.99 - 0.01 * i for i in range(20)] + [0.50]
    choice = choose_threshold(scores, [True] * 20 + [False])
    assert choice.met
    assert choice.threshold == pytest.approx(0.80)
    assert choice.recall == 1.0
