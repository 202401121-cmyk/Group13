"""The keyboard labelling tool (ML-1): every answer is saved at once, a session resumes where it stopped, the order is
shuffled per labeller, and the screen gives no hint. Key presses are scripted; no person's labels are made here."""

from __future__ import annotations

import io
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import label_pairs
import pytest
from check_labels import check_file
from conftest import write_csv
from label_pairs import (
    DEFINITIONS,
    IGNORED,
    decode_key,
    default_output,
    labeller_slug,
    labelling_order,
    read_unix_burst,
    read_windows_key,
    run_session,
)
from listings import FileBusy, write_rows
from make_pairs import BATCH_HEADER

TODAY = date(2026, 11, 8)

# Synthetic titles from ml/examples (fictional brands only).
PAIRS = [
    ("p0001", "Nova Book 14 (16GB RAM, 512GB SSD, Silver)", "Nova Book 14 Laptop 16GB 512GB SSD Silver", "nova-book"),
    ("p0002", "Nova Book 14 (16GB RAM, 512GB SSD)", "Nova Book 14 (8GB RAM, 512GB SSD)", "nova-book"),
    ("p0003", "Orion X5 5G (8GB, 128GB, Blue)", "Orion X5 5G (8GB, 128GB, Green)", "orion-x"),
    ("p0004", "Kestrel Buds 2 (Black)", "Kestrel Buds 2 Pro (Black)", "kestrel-buds"),
    (
        "p0005",
        "Kestrel Slim 15 (16GB, 1TB SSD, Grey)",
        "Kestrel Slim 15 Thin Laptop - 16GB / 1TB - Grey",
        "kestrel-slim",
    ),
    ("p0006", "Orion X5 5G (8GB, 128GB, Blue)", "Orion X5 5G Blue", "orion-x"),
]
CATEGORY = {"nova-book": "laptop", "kestrel-slim": "laptop", "orion-x": "smartphone", "kestrel-buds": "earbuds"}


class ScriptedKeys:
    """Key presses and typed lines from a script; an empty key means the input has ended."""

    def __init__(self, keys: str | Sequence[str], lines: Sequence[str] = (), watch: Path | None = None) -> None:
        self.keys = list(keys)
        self.lines = list(lines)
        self.prompts: list[str] = []
        self.watch = watch
        self.seen_on_disk: list[str] = []  # the output file's text each time a key is read

    def key(self) -> str:
        if self.watch is not None:
            self.seen_on_disk.append(self.watch.read_text(encoding="utf-8") if self.watch.exists() else "")
        return self.keys.pop(0) if self.keys else ""

    def line(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.lines.pop(0) if self.lines else ""


class Screen:
    def __init__(self) -> None:
        self.text: list[str] = []

    def __call__(self, text: str) -> None:
        self.text.append(text)

    def __str__(self) -> str:
        return "\n".join(self.text)


def _batch(folder: Path, name: str = "agree-1", pairs: Sequence[tuple[str, str, str, str]] = PAIRS) -> Path:
    batches = folder / "batches"
    batches.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "pair_id": pair_id,
            "left_text": left,
            "right_text": right,
            "left_url": f"https://shop-a.example/p/{pair_id}-l",
            "right_url": f"https://shop-b.example/p/{pair_id}-r",
            "category": CATEGORY[family],
            "product_family": family,
        }
        for pair_id, left, right, family in pairs
    ]
    return write_csv(batches / f"{name}.pairs.csv", BATCH_HEADER, rows)


def _session(
    batch: Path, keys: ScriptedKeys, labeller: str = "Alex", opened: list[str] | None = None
) -> tuple[int, str]:
    screen = Screen()
    code = run_session(
        batch,
        labeller,
        default_output(batch, labeller),
        keys,
        today=TODAY,
        opener=(opened.append if opened is not None else lambda url: None),
        write=screen,
    )
    return code, str(screen)


def _labels(batch: Path, labeller: str = "Alex") -> dict[str, str]:
    result = check_file(default_output(batch, labeller))
    assert result.ok, result.errors
    return {pair.pair_id: pair.label for pair in result.pairs}


def _order(labeller: str = "Alex") -> list[str]:
    return labelling_order("agree-1", labeller, [pair_id for pair_id, *_ in PAIRS])


def test_each_answer_is_saved_at_once(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    keys = ScriptedKeys("123q", watch=default_output(batch, "Alex"))
    code, _ = _session(batch, keys)
    assert code == 0
    saved_rows = [text.count("\n") - 1 if text else 0 for text in keys.seen_on_disk]
    assert saved_rows == [0, 1, 2, 3]  # before each key press, every earlier answer was already on disk


def test_the_output_passes_check_labels(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    code, screen = _session(batch, ScriptedKeys("123412", lines=["right title has no RAM"]))
    assert code == 0
    assert "All 6 pairs are labelled" in screen
    result = check_file(default_output(batch, "Alex"))
    assert result.ok, result.errors
    order = _order()
    assert {pair.pair_id: pair.label for pair in result.pairs} == dict(
        zip(order, ["same", "variant", "different", "unsure", "same", "variant"], strict=True)
    )
    assert [pair.pair_id for pair in result.pairs] == sorted(order)  # saved in pair_id order
    assert {(pair.labeller, pair.labelled_at) for pair in result.pairs} == {("Alex", TODAY)}
    by_id = {pair.pair_id: pair for pair in result.pairs}
    for pair_id, left, right, family in PAIRS:
        assert (by_id[pair_id].left_title, by_id[pair_id].right_title) == (left, right)
        assert (by_id[pair_id].product_family, by_id[pair_id].category) == (family, CATEGORY[family])


def test_a_half_labelled_file_resumes_at_the_right_pair(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    _session(batch, ScriptedKeys("123q"))
    order = _order()
    text_of = {pair_id: left for pair_id, left, *_ in PAIRS}
    _, screen = _session(batch, ScriptedKeys("q"))
    assert "Pair 4 of 6 (3 to go)" in screen
    assert text_of[order[3]] in screen
    _session(batch, ScriptedKeys("3q"))
    assert _labels(batch) == {order[0]: "same", order[1]: "variant", order[2]: "different", order[3]: "different"}


def test_back_changes_the_previous_answer(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    code, screen = _session(batch, ScriptedKeys("1b3q"))
    assert code == 0
    assert "Your label: same" in screen  # going back shows the labeller's own earlier answer
    assert _labels(batch) == {_order()[0]: "different"}


def test_a_note_is_saved_with_its_pair(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    _session(batch, ScriptedKeys("n1q", lines=["only the wording differs"]))
    [pair] = check_file(default_output(batch, "Alex")).pairs
    assert (pair.pair_id, pair.label, pair.notes) == (_order()[0], "same", "only the wording differs")


def test_unsure_asks_why(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    keys = ScriptedKeys("4q", lines=["right title has no storage"])
    _session(batch, keys)
    assert any("Why unsure" in prompt for prompt in keys.prompts)
    [pair] = check_file(default_output(batch, "Alex")).pairs
    assert (pair.label, pair.notes) == ("unsure", "right title has no storage")


def test_open_and_definitions_do_not_label(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    opened: list[str] = []
    code, screen = _session(batch, ScriptedKeys("odq"), opened=opened)
    assert code == 0
    first = _order()[0]
    assert opened == [f"https://shop-a.example/p/{first}-l", f"https://shop-b.example/p/{first}-r"]
    for label, meaning in DEFINITIONS:
        assert label in screen and meaning in screen
    assert not default_output(batch, "Alex").exists()  # nothing was labelled


def test_quit_keeps_what_was_labelled(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    code, screen = _session(batch, ScriptedKeys("12q"))
    assert code == 0 and "saved" in screen.lower()
    assert len(_labels(batch)) == 2
    code, _ = _session(batch, ScriptedKeys("3"))  # the input simply ends: the same as q
    assert code == 0
    assert len(_labels(batch)) == 3


def test_the_order_is_shuffled_per_labeller_and_stable_across_restarts(tmp_path: Path) -> None:
    ids = [pair_id for pair_id, *_ in PAIRS]
    alex, sam = _order("Alex"), _order("Sam")
    assert sorted(alex) == sorted(sam) == ids
    assert alex != sam and alex != ids
    assert labelling_order("agree-1", "Alex", ids) == alex  # the same every time
    batch = _batch(tmp_path)
    text_of = {pair_id: left for pair_id, left, *_ in PAIRS}
    for _ in range(2):
        _, screen = _session(batch, ScriptedKeys("q"), labeller="Sam")
        assert text_of[sam[0]] in screen.split("Pair 1 of 6")[1]


def test_the_screen_shows_no_score_stratum_family_or_hint(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    _, screen = _session(batch, ScriptedKeys("1b23412", lines=["unsure why"]))
    lowered = screen.lower()
    for hint in ("stratum", "score", "similar", "match", "family", "source", "category", "shop-a", "shop-b"):
        assert hint not in lowered, hint
    for family in CATEGORY:
        assert family not in lowered


def test_an_unknown_key_shows_the_keys(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    _, screen = _session(batch, ScriptedKeys("xq"))
    assert "'x' is not a key here" in screen
    assert "1 same" in screen and "q quit" in screen
    assert not default_output(batch, "Alex").exists()


def test_a_corrupt_batch_line_is_reported_with_its_line_number(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    lines = batch.read_text(encoding="utf-8").splitlines()
    lines[3] = lines[3].rsplit(",", 1)[0]  # drop the last column of line 4
    batch.write_text("\n".join(lines) + "\n", encoding="utf-8")
    code, screen = _session(batch, ScriptedKeys("1"))
    assert code == 1
    assert f"{batch}:4: expected 7 columns, found 6" in screen
    assert not default_output(batch, "Alex").exists()


def test_a_broken_output_file_is_never_overwritten(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    _session(batch, ScriptedKeys("12q"))
    output = default_output(batch, "Alex")
    broken = output.read_text(encoding="utf-8").replace(",same,", ",SAME,").replace(",variant,", ",VARIANT,")
    output.write_text(broken, encoding="utf-8")
    code, screen = _session(batch, ScriptedKeys("3"))
    assert code == 1
    assert f"{output}:2:" in screen and "nothing was changed" in screen
    assert output.read_text(encoding="utf-8") == broken


def test_the_default_output_is_named_after_the_batch_and_labeller(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    assert default_output(batch, "Rushiraj") == (tmp_path / "agree-1.rushiraj.csv").resolve()
    assert default_output(batch, "Om") == (tmp_path / "agree-1.om.csv").resolve()


def test_a_bare_batch_name_from_inside_the_batches_folder_saves_in_the_same_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch = _batch(tmp_path)
    monkeypatch.chdir(batch.parent)
    assert default_output(Path(batch.name), "Om") == (tmp_path / "agree-1.om.csv").resolve()


@pytest.mark.parametrize(
    "chunk",
    ["\x1b[3~", "\x1b[2~", "\x1b[1~", "\x1b[4~", "\x1b[15~", "\x1b[1;2A", "\x1b[B", "\x1bOP", "\x1b", "12", "same"],
    ids=["delete", "insert", "home", "end", "f5", "shift_up", "down", "f1", "escape", "paste_12", "paste_word"],
)
def test_arrows_function_keys_and_pastes_never_become_a_label(chunk: str) -> None:
    assert decode_key(chunk) == IGNORED


def test_single_keys_and_the_end_of_input_decode_as_they_are() -> None:
    assert [decode_key(key) for key in ("1", "4", "b", "q", "\u00e0")] == ["1", "4", "b", "q", "\u00e0"]
    assert decode_key("") == decode_key("\x04") == decode_key("\x1a") == ""
    with pytest.raises(KeyboardInterrupt):
        decode_key("\x03")


def test_ignored_keys_label_nothing(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    _, screen = _session(batch, ScriptedKeys([IGNORED, IGNORED, "q"]))
    assert "That key is not a key here" in screen
    assert not default_output(batch, "Alex").exists()


def test_another_labellers_file_is_never_taken_over(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    _session(batch, ScriptedKeys("12q"), labeller="Alex")
    alex_file = default_output(batch, "Alex")
    before = alex_file.read_bytes()
    screen = Screen()
    code = run_session(batch, "Sam", alex_file, ScriptedKeys("3"), today=TODAY, opener=lambda url: None, write=screen)
    assert code == 1
    assert "this row is Alex's, not Sam's" in str(screen)
    assert alex_file.read_bytes() == before


def test_an_earlier_row_keeps_the_name_it_was_saved_with(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    _session(batch, ScriptedKeys("1q"), labeller="Om")
    _session(batch, ScriptedKeys("2q"), labeller="OM")  # the same person, typed differently
    pairs = check_file(default_output(batch, "Om")).pairs
    assert sorted(pair.labeller for pair in pairs) == ["OM", "Om"]


def test_a_file_held_open_elsewhere_is_reported_and_the_answer_can_be_given_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    batch = _batch(tmp_path)
    failures = [FileBusy("cannot save agree-1.alex.csv: close it in any other program, then try again")]

    def busy_once(*args: Any) -> None:
        if failures:
            raise failures.pop()
        write_rows(*args)

    monkeypatch.setattr("label_pairs.write_rows", busy_once)
    code, screen = _session(batch, ScriptedKeys("11q"))
    assert code == 0
    assert "Not saved: cannot save agree-1.alex.csv: close it in any other program" in screen
    assert _labels(batch) == {_order()[0]: "same"}  # the second press, once the file was free, saved it


def test_an_answer_that_could_not_be_saved_is_not_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _batch(tmp_path)

    def always_busy(*args: Any) -> None:
        raise FileBusy("cannot save agree-1.alex.csv: close it in any other program, then try again")

    monkeypatch.setattr("label_pairs.write_rows", always_busy)
    _, screen = _session(batch, ScriptedKeys("1q"))
    assert "Your 0 answers are saved" in screen  # never claims an answer the file does not hold
    assert "Pair 1 of 6 (6 to go)" in screen.split("Not saved:")[0]
    assert not default_output(batch, "Alex").exists()


def test_two_windows_on_one_file_keep_both_sets_of_answers(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    order = _order()

    class FirstWindow(ScriptedKeys):
        """Before this window's second answer, a second window on the same file labels the next two pairs."""

        def key(self) -> str:
            key = super().key()
            if key == "2":
                _session(batch, ScriptedKeys("33q"), labeller="Alex")
            return key

    _session(batch, FirstWindow("12q"))
    labels = _labels(batch)
    assert labels[order[0]] == "same"
    assert labels[order[1]] == "variant"  # this window's own answer wins for the pair both labelled
    assert labels[order[2]] == "different"  # the other window's answer survived this window's save
    assert len(labels) == 3


def test_the_last_answer_can_still_be_changed(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    code, screen = _session(batch, ScriptedKeys("1111112q"))  # six answers, then the last one changed
    assert code == 0
    assert "Press b to go back and change an answer" in screen
    assert screen.count("Your label: variant") == 1  # the change is shown, so a stray key can't go unseen
    assert _labels(batch)[_order()[-1]] == "variant"
    _, screen = _session(batch, ScriptedKeys("b3q"))  # a finished file opens at its last pair
    assert "All 6 pairs are already labelled" in screen
    assert _labels(batch)[_order()[-2]] == "different"


def test_only_web_pages_are_opened(tmp_path: Path) -> None:
    pairs = PAIRS[:1]
    batch = _batch(tmp_path, pairs=pairs)
    text = batch.read_text(encoding="utf-8").replace(
        "https://shop-a.example/p/p0001-l", "file:///C:/Windows/notepad.exe"
    )
    batch.write_text(text, encoding="utf-8")
    opened: list[str] = []
    _, screen = _session(batch, ScriptedKeys("oq"), opened=opened)
    assert opened == ["https://shop-b.example/p/p0001-r"]
    assert "Could not open every page" in screen


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda text: text.replace("p0003", "p0099"), "pair p0099 is not in this batch"),
        (lambda text: text.replace("Orion X5 5G (8GB, 128GB, Green)", "Orion X5 5G (8GB, 256GB, Green)"), "differ"),
    ],
    ids=["unknown_pair", "changed_titles"],
)
def test_labels_for_another_batch_are_refused(tmp_path: Path, change: Callable[[str], str], message: str) -> None:
    batch = _batch(tmp_path)
    _session(batch, ScriptedKeys("111111"))
    output = default_output(batch, "Alex")
    output.write_text(change(output.read_text(encoding="utf-8")), encoding="utf-8")
    before = output.read_bytes()
    code, screen = _session(batch, ScriptedKeys("1"))
    assert code == 1
    assert message in screen and "nothing was changed" in screen
    assert output.read_bytes() == before


def test_a_piped_note_with_the_rupee_sign_is_saved_as_typed(tmp_path: Path) -> None:
    """Run as a script with the keys piped in, as on Windows without a terminal: the note must arrive as UTF-8."""
    batch = _batch(tmp_path, pairs=PAIRS[:2])
    script = Path(__file__).resolve().parents[1] / "scripts" / "label_pairs.py"
    # Without UTF-8 mode, as a plain Windows install runs: piped input would otherwise be read in the code page.
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    done = subprocess.run(
        [sys.executable, str(script), str(batch), "--labeller", "Alex"],
        input="4\n\u20b9 price only, no RAM\nq\n".encode(),
        env={**env, "PYTHONUTF8": "0"},
        capture_output=True,
        timeout=60,
    )
    out = done.stdout.decode("utf-8")
    assert done.returncode == 0, out
    assert "press Enter after each key" in out
    [pair] = check_file(default_output(batch, "Alex")).pairs
    assert (pair.label, pair.notes) == ("unsure", "\u20b9 price only, no RAM")


def test_the_rupee_sign_shows_and_round_trips(tmp_path: Path) -> None:
    pairs = [("p0001", "Nova Book 14 (16GB, 512GB) ₹54,990", "Nova Book 14 16GB 512GB ₹ 54990", "nova-book")]
    batch = _batch(tmp_path, pairs=pairs)
    _, screen = _session(batch, ScriptedKeys("1"))
    assert "₹54,990" in screen
    output = default_output(batch, "Alex")
    assert "₹54,990".encode() in output.read_bytes()
    [pair] = check_file(output).pairs
    assert (pair.left_title, pair.right_title) == (pairs[0][1], pairs[0][2])


class FakeConsole:
    """A stand-in for msvcrt: getwch gives one character at a time, kbhit says whether more is already waiting."""

    def __init__(self, typed: str, waiting_after: int) -> None:
        self.typed = list(typed)
        self.waiting_after = waiting_after  # how many characters may be read before kbhit turns false
        self.read = 0

    def getwch(self) -> str:
        self.read += 1
        return self.typed.pop(0)

    def kbhit(self) -> bool:
        return bool(self.typed) and self.read < self.waiting_after


def test_a_windows_arrow_key_is_read_whole_and_ignored() -> None:
    for prefix in ("\x00", "\xe0"):
        console = FakeConsole(prefix + "Q" + "1", waiting_after=0)  # PgDn's scan code is Q; kbhit is already false
        assert read_windows_key(console.getwch, console.kbhit) == IGNORED
        assert console.typed == ["1"]  # the scan code was used up, never taken as q (quit)


def test_a_windows_paste_is_one_burst_and_labels_nothing() -> None:
    console = FakeConsole("Kestrel Buds 2 Pro", waiting_after=99)
    assert read_windows_key(console.getwch, console.kbhit) == IGNORED
    assert console.typed == []
    single = FakeConsole("2", waiting_after=0)
    assert read_windows_key(single.getwch, single.kbhit) == "2"


def test_a_long_unix_paste_is_one_burst_even_if_it_ends_in_a_digit() -> None:
    chunks = [b"x" * 64, b"y" * 64, b"1"]  # a paste split into reads, its last read a single "1"
    assert read_unix_burst(lambda: chunks.pop(0), lambda: bool(chunks)) == IGNORED
    assert chunks == []  # the whole paste went in one key read, so its last 1 can never act on its own
    assert read_unix_burst(lambda: b"\x1b[3~", lambda: False) == IGNORED  # Delete
    assert read_unix_burst(lambda: b"3", lambda: False) == "3"


def test_names_in_indian_scripts_do_not_share_a_file() -> None:
    assert labeller_slug("\u0930\u093e\u092e") != labeller_slug("\u0930\u0940\u092e")  # Ram and Reem in Devanagari
    assert labeller_slug("  Om ") == labeller_slug("OM") == "om"


def test_an_edit_from_another_window_is_kept(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    order = _order()
    _session(batch, ScriptedKeys("1q"))  # order[0] is "same" in the file

    class SecondWindow(ScriptedKeys):
        """While this window is open, the first window goes back and changes order[0] to "variant"."""

        def key(self) -> str:
            if not self.seen_on_disk:
                self.seen_on_disk.append("edited")
                _session(batch, ScriptedKeys("b2q"))
            return super().key()

    _session(batch, SecondWindow("3q"))  # this window then labels order[1]
    labels = _labels(batch)
    assert labels[order[0]] == "variant"  # the other window's edit was not written over with the old "same"
    assert labels[order[1]] == "different"


def test_a_labels_file_broken_during_a_session_is_never_written_over(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    output = default_output(batch, "Alex")
    _session(batch, ScriptedKeys("1q"))

    class EditedInASpreadsheet(ScriptedKeys):
        def key(self) -> str:
            if not self.seen_on_disk:
                self.seen_on_disk.append("broken")
                output.write_text(output.read_text(encoding="utf-8").replace("2026-11-08", "08-11-2026"), "utf-8")
            return super().key()

    code, screen = _session(batch, EditedInASpreadsheet("2q"))
    after = output.read_text(encoding="utf-8")
    assert code == 0
    assert "was changed outside this tool and no longer checks" in screen
    assert "check it with python ml/scripts/check_labels.py" in screen  # the way out, not "try again"
    assert "08-11-2026" in after and after.count("\n") == 2  # still the one row, exactly as it was edited


def test_a_note_that_could_not_be_saved_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _batch(tmp_path)
    _session(batch, ScriptedKeys("1b"))  # one answer saved, then back on that pair

    def busy(*args: Any) -> None:
        raise FileBusy("cannot save agree-1.alex.csv: close it in any other program, then try again")

    monkeypatch.setattr("label_pairs.write_rows", busy)
    _, screen = _session(batch, ScriptedKeys("bnq", lines=["right title has no storage"]))
    assert "Your note was not kept" in screen
    assert "Your note: right title has no storage" not in screen


def test_a_windows_scan_code_at_the_end_of_a_burst_is_read_with_it() -> None:
    console = FakeConsole("3\xe0QZ", waiting_after=2)  # "3" then PgDn queued; kbhit turns false after the prefix
    assert read_windows_key(console.getwch, console.kbhit) == IGNORED
    assert console.typed == ["Z"]  # the Q (quit) was read with its prefix, never as a key of its own


def test_a_unix_read_that_ends_stops_the_burst() -> None:
    reads = [b"1", b"", b""]
    assert read_unix_burst(lambda: reads.pop(0), lambda: True) == "1"  # stops at the empty read, never spins
    assert reads == [b""]


def test_a_pasted_line_in_line_mode_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("16GB RAM\n1\n"))
    keys = label_pairs.LineKeys()
    assert keys.key() == IGNORED  # never its first character, "1"
    assert keys.key() == "1"
    assert keys.key() == ""


def test_a_note_never_undoes_another_windows_label(tmp_path: Path) -> None:
    batch = _batch(tmp_path)
    order = _order()
    _session(batch, ScriptedKeys("1q"))  # order[0] is "same"

    class OtherWindowChangesIt(ScriptedKeys):
        def key(self) -> str:
            if not self.seen_on_disk:
                self.seen_on_disk.append("changed")
                _session(batch, ScriptedKeys("b2q"))  # meanwhile, order[0] becomes "variant"
            return super().key()

    _session(batch, OtherWindowChangesIt("bnq", lines=["only the wording differs"]))  # back to order[0], add a note
    [pair] = [pair for pair in check_file(default_output(batch, "Alex")).pairs if pair.pair_id == order[0]]
    assert (pair.label, pair.notes) == ("variant", "only the wording differs")


def test_a_lost_note_is_named_when_an_answer_cannot_be_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    batch = _batch(tmp_path)

    def busy(*args: Any) -> None:
        raise FileBusy("cannot save agree-1.alex.csv: close it in any other program, then try again")

    monkeypatch.setattr("label_pairs.write_rows", busy)
    _, screen = _session(batch, ScriptedKeys("n1q", lines=["right title has no storage"]))
    assert "Your answer and note were not kept" in screen
