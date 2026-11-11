"""S3-4: the threshold picker (DECIDED 5) and the checkpoint finder, on SYNTHETIC scores and files."""

from __future__ import annotations

from pathlib import Path

import pytest
from checkpoints import checkpoint_dir, latest_checkpoint, mark_done
from matcher_threshold import choose_best_f1_threshold, choose_threshold


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


# --- the best-F1 threshold, for the comparison rows only ------------------------------------------------------------


def test_best_f1_picks_the_threshold_with_the_highest_f1() -> None:
    # Matches at 0.9, 0.8 and 0.6; non-matches at 0.7 and 0.3. F1 at each threshold:
    # >= 0.9: 1 right, 2 missed: 0.50.  >= 0.8: 2 right, 1 missed: 0.80.  >= 0.7: 2 right, 1 wrong, 1 missed: 0.67.
    # >= 0.6: 3 right, 1 wrong: 0.857.  >= 0.3: 3 right, 2 wrong: 0.75.
    choice = choose_best_f1_threshold([0.9, 0.8, 0.7, 0.6, 0.3], [True, True, False, True, False])
    assert choice.threshold == pytest.approx(0.6)
    assert choice.f1 == pytest.approx(6 / 7)
    assert choice.precision == pytest.approx(0.75)
    assert (choice.recall, choice.called) == (1.0, 4)


def test_best_f1_ties_go_to_the_higher_threshold() -> None:
    # Matches at 0.9, 0.7 and 0.4; non-matches at 0.8, 0.6 and 0.5.
    # >= 0.7: 2 right, 1 wrong, 1 missed: F1 4/6.  >= 0.4: 3 right, 3 wrong: F1 6/9, the same 2/3.
    # Nothing else is higher.
    choice = choose_best_f1_threshold([0.9, 0.8, 0.7, 0.6, 0.5, 0.4], [True, False, True, False, False, True])
    assert choice.threshold == pytest.approx(0.7)
    assert choice.f1 == pytest.approx(2 / 3)


def test_best_f1_never_splits_equal_scores() -> None:
    # Three pairs score 0.8: two matches and one non-match. They are called together, so the best F1 is 0.8
    # (2 right, 1 wrong), never the 1.0 that splitting them would give.
    choice = choose_best_f1_threshold([0.8, 0.8, 0.8, 0.2], [True, True, False, False])
    assert choice.threshold == pytest.approx(0.8)
    assert choice.precision == pytest.approx(2 / 3)
    assert choice.recall == 1.0
    assert choice.f1 == pytest.approx(0.8)


def test_best_f1_with_no_positives_calls_nothing() -> None:
    choice = choose_best_f1_threshold([0.9, 0.5], [False, False])
    assert (choice.f1, choice.recall, choice.called) == (0.0, 0.0, 0)
    assert choice.threshold == 1.0  # no pair scores above it, so nothing is called a match
    assert choose_best_f1_threshold([], []).called == 0
    with pytest.raises(ValueError, match="same length"):
        choose_best_f1_threshold([0.5], [True, False])
