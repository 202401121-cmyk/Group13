"""Label a batch of product pairs at the keyboard, one pair at a time (ML-1, D-55).

Usage: python ml/scripts/label_pairs.py ml/labelling/batches/agree-1.pairs.csv --labeller Om [--out FILE]

Keys: 1 same, 2 variant, 3 different, 4 unsure, n note, o open both URLs, d show the definitions, b back, q quit.

Every answer is saved at once to the labeller's own file (default ml/labelling/<batch>.<name>.csv, in the format of
ml/DATA.md section 2, so check_labels.py and kappa.py read it), so quitting, Ctrl+C or a closed window loses
nothing. Run the same command again to carry on where you stopped. Each labeller sees the pairs in their own
shuffled order, the same every time. The screen shows the two texts and nothing else: no score, stratum, product
family or source, and no suggested label. The labels are yours alone. Standard library only; the script itself
makes no network call ("o" asks your web browser to open the two pages).
"""

from __future__ import annotations

import argparse
import io
import os
import random
import re
import sys
import unicodedata
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from check_labels import HEADER, check_file, use_utf8_stdout
from listings import is_web_url, read_table, write_rows
from make_pairs import BATCH_HEADER

ANSWERS = {"1": "same", "2": "variant", "3": "different", "4": "unsure"}
DEFINITIONS = (
    ("same", "the same product and the same variant"),
    ("variant", "the same model, but a different configuration (RAM, storage, colour or region)"),
    ("different", "different products: another model, line or generation"),
    ("unsure", "cannot tell from the two texts; say why in a note"),
)
KEYS_HELP = "1 same   2 variant   3 different   4 unsure   n note   o open both   d definitions   b back   q quit"
IGNORED = "?"  # what a key that is not one character (an arrow, a function key, a paste) becomes


class Keys(Protocol):
    """Where key presses come from: the keyboard, piped input, or a test's script. An empty key means the end."""

    def key(self) -> str: ...

    def line(self, prompt: str) -> str: ...


def decode_key(chunk: str) -> str:
    """One read from the keyboard, as a key. Arrows, function keys and the like arrive as several characters at once
    (such as ESC [ 3 ~ for Delete); they become IGNORED, so no part of them can ever be taken for a label."""
    if chunk == "\x03":
        raise KeyboardInterrupt
    if chunk in ("", "\x04", "\x1a"):  # end of input (Ctrl+D, Ctrl+Z)
        return ""
    if len(chunk) != 1 or chunk == "\x1b":
        return IGNORED
    return chunk


def read_windows_key(getwch: Callable[[], str], kbhit: Callable[[], bool]) -> str:
    """One key from the Windows console. A special key (arrow, F-key, PgDn) comes as \\x00 or \\xe0 plus a scan code,
    and its second half is always read so it can never act as a letter. A paste comes one character at a time, so
    everything already waiting is read with it: a burst of several characters is IGNORED, never a run of labels.
    (On some keyboards à also arrives as \\xe0; it is never a key here, and it swallows the next press.)"""
    first = getwch()
    if first in ("\x00", "\xe0"):
        getwch()
        return IGNORED
    burst = first
    while kbhit():
        character = getwch()
        burst += character
        if character in ("\x00", "\xe0"):  # kbhit may already be false while the scan code waits: read it now
            burst += getwch()
    return decode_key(burst)


def read_unix_burst(read: Callable[[], bytes], more_waiting: Callable[[], bool]) -> str:
    """One key from a raw terminal: the first read, plus everything still arriving, so a paste of any length is one
    burst and decodes as IGNORED, never as the label in its last character."""
    data = read()
    while more_waiting():
        chunk = read()
        if not chunk:  # end of input: nothing more will come
            break
        data += chunk
    return decode_key(data.decode("utf-8", errors="replace"))


class TerminalKeys:
    """One key press at a time, with no Enter, on Windows, macOS and Linux."""

    def key(self) -> str:
        if sys.platform == "win32":
            import msvcrt

            return read_windows_key(msvcrt.getwch, msvcrt.kbhit)
        import select
        import termios
        import tty

        descriptor = sys.stdin.fileno()
        saved = termios.tcgetattr(descriptor)
        try:
            tty.setraw(descriptor, termios.TCSANOW)  # TCSANOW keeps keys typed ahead instead of throwing them away
            return read_unix_burst(
                lambda: os.read(descriptor, 64),
                lambda: bool(select.select([descriptor], [], [], 0.02)[0]),
            )
        finally:
            termios.tcsetattr(descriptor, termios.TCSANOW, saved)

    def line(self, prompt: str) -> str:
        try:
            return input(prompt)
        except EOFError:
            return ""


class LineKeys:
    """Piped input: one key per line, so a session can be scripted from a file."""

    def key(self) -> str:
        line = sys.stdin.readline()
        if not line:
            return ""
        return decode_key(line.strip() or IGNORED)  # a pasted "16GB RAM" is ignored, never read as its "1"

    def line(self, prompt: str) -> str:
        print(prompt, end="", flush=True)
        return sys.stdin.readline().rstrip("\r\n")


@dataclass(frozen=True)
class BatchPair:
    pair_id: str
    left_text: str
    right_text: str
    left_url: str
    right_url: str
    category: str
    product_family: str


@dataclass(frozen=True)
class Answer:
    label: str
    labeller: str
    labelled_at: str
    notes: str


def labeller_slug(labeller: str) -> str:
    """The name as it appears in file names: lower case, letters, marks and digits in any script, joined by -.

    Marks (Unicode categories M*) are kept, so vowel signs in Indian scripts tell "राम" from "रीम".
    """
    kept = "".join(c if unicodedata.category(c)[0] in "LMN" else "-" for c in labeller.strip().lower())
    return re.sub(r"-+", "-", kept).strip("-")


def default_output(batch_path: Path, labeller: str) -> Path:
    """ml/labelling/batches/agree-1.pairs.csv and Om give ml/labelling/agree-1.om.csv, from any working folder."""
    return batch_path.resolve().parent.parent / f"{batch_name(batch_path)}.{labeller_slug(labeller)}.csv"


def batch_name(batch_path: Path) -> str:
    return batch_path.name.removesuffix(".csv").removesuffix(".pairs")


def labelling_order(batch: str, labeller: str, pair_ids: Sequence[str]) -> list[str]:
    """This labeller's own shuffled order: the same on every run, different from the other labeller's."""
    order = sorted(pair_ids)
    random.Random(f"{batch}:{labeller_slug(labeller)}").shuffle(order)
    return order


def read_batch(path: Path) -> tuple[list[BatchPair], list[str]]:
    table = read_table(path, BATCH_HEADER)
    pairs = [BatchPair(**values) for _, values in table.rows]
    errors = list(table.errors)
    seen: set[str] = set()
    for (line, values), pair in zip(table.rows, pairs, strict=True):
        if pair.pair_id in seen:
            errors.append(f"{path}:{line}: duplicate pair_id {pair.pair_id}")
        seen.add(pair.pair_id)
        if not values["left_text"].strip() or not values["right_text"].strip():
            errors.append(f"{path}:{line}: a pair needs both texts")
    return pairs, errors


def run_session(
    batch_path: Path,
    labeller: str,
    out_path: Path,
    keys: Keys,
    *,
    today: date,
    opener: Callable[[str], object],
    write: Callable[[str], None] = print,
) -> int:
    """Label until the labeller quits. Returns the exit code: 0, or 1 if a file is broken or belongs to someone else."""
    if not labeller_slug(labeller):
        write("The labeller's name must hold letters, such as --labeller Om.")
        return 1
    pairs, errors = read_batch(batch_path)
    if errors or not pairs:
        write("\n".join(errors) if errors else f"{batch_path} holds no pairs.")
        write("The batch file is damaged; restore it from git or ask whoever made it.")
        return 1
    by_id = {pair.pair_id: pair for pair in pairs}
    answers, problem = load_answers(out_path, by_id, labeller)
    if problem:
        write(problem)
        return 1

    order = labelling_order(batch_name(batch_path), labeller, list(by_id))
    session = _Session(order, by_id, answers, labeller, today, out_path, write, opener)
    first = session.next_open(-1)
    if first is None:
        write(f"All {len(order)} pairs are already labelled in {out_path.name}.")
        write("Press b to go back through them and change an answer, or q to quit.")
        session.position = len(order) - 1
    else:
        write(f"Labelling {batch_name(batch_path)} as {labeller}. Every answer is saved at once to {out_path.name}.")
        write("Decide from the two texts only (ml/DATA.md, section 3). Press d for the definitions.")
        session.position = first
    session.show()
    while True:
        finished = session.press(keys.key(), keys)
        if finished is not None:
            return finished


class _Session:
    """The answers so far, the pair on screen, and what each key does. Saves the whole file after every answer."""

    def __init__(
        self,
        order: list[str],
        by_id: dict[str, BatchPair],
        answers: dict[str, Answer],
        labeller: str,
        today: date,
        out_path: Path,
        write: Callable[[str], None],
        opener: Callable[[str], object],
    ) -> None:
        self.order, self.by_id, self.answers = order, by_id, answers
        self.labeller, self.today, self.out_path, self.write, self.opener = (
            labeller.strip(),
            today,
            out_path,
            write,
            opener,
        )
        self.notes: dict[str, str] = {}  # notes typed before the pair is answered
        self.answered_here: set[str] = set()  # pairs this window answered; for the rest, the file on disk wins
        self.position = 0

    @property
    def pair_id(self) -> str:
        return self.order[self.position]

    def press(self, key: str, keys: Keys) -> int | None:
        """Act on one key. Returns the exit code when the session is over, else None."""
        if key == "" or key.lower() == "q":
            self.write(f"\nStopped. Your {len(self.answers)} answers are saved in {self.out_path.name}.")
            self.write("Run the same command to carry on where you stopped.")
            return 0
        if key in ANSWERS:
            self._label(ANSWERS[key], keys)
            return None
        actions = {"n": self._note, "o": self._open, "d": self._definitions, "b": self._back}
        action = actions.get(key.lower())
        if action is None:
            shown = "That key" if key == IGNORED else f"{key!r}"
            self.write(f"{shown} is not a key here. The keys: {KEYS_HELP}")
        else:
            action(keys)
        return None

    def _label(self, label: str, keys: Keys) -> None:
        if label == "unsure" and not self._note_of(self.pair_id):
            self.notes[self.pair_id] = keys.line(
                "Why unsure? A few words, such as 'right title has no RAM' (Enter to skip): "
            )
        if not self.answer(self.pair_id, label):
            return  # not saved: the message says why, and the pair stays on screen
        following = self.next_open(self.position)
        if following is None:
            self.show()  # every pair is done: show the pair just answered, so a stray key can't change it unseen
            self.write(f"\nAll {len(self.order)} pairs are labelled. Your answers are saved in {self.out_path.name}.")
            self.write("Press b to go back and change an answer, or q to quit.")
            return
        self.position = following
        self.show()

    def _note(self, keys: Keys) -> None:
        pair_id = self.pair_id
        self.notes[pair_id] = keys.line("Note: ").replace("\n", " ").strip()
        if pair_id not in self.answers:
            self.write("Note kept; it is saved with your answer.")
            return
        if pair_id not in self.answered_here:
            # A label loaded at start may have been changed since by another window: keep the label on disk.
            saved, _ = load_answers(self.out_path, self.by_id, self.labeller)
            if pair_id in saved:
                self.answers[pair_id] = saved[pair_id]
        if self.answer(pair_id, self.answers[pair_id].label, note_only=True):
            self.write("Note saved.")

    def _open(self, keys: Keys) -> None:
        pair = self.by_id[self.pair_id]
        urls = [url for url in (pair.left_url, pair.right_url) if url]
        web = [url for url in urls if is_web_url(url)]  # never a local path or another kind of link
        opened = [url for url in web if self.opener(url) is not False]
        if not urls:
            self.write("No pages to open.")
        elif len(opened) == len(urls):
            self.write("Asked your browser to open both pages." if len(urls) == 2 else "Asked your browser to open it.")
        else:
            self.write("Could not open every page. Copy the links from the batch file into your browser.")

    def _definitions(self, keys: Keys) -> None:
        self.write("\n" + "\n".join(f"  {label:<10} {meaning}" for label, meaning in DEFINITIONS))

    def _back(self, keys: Keys) -> None:
        if self.position == 0:
            self.write("This is the first pair.")
            return
        self.position -= 1
        self.show()

    def _note_of(self, pair_id: str) -> str:
        return self.notes.get(pair_id) or (self.answers[pair_id].notes if pair_id in self.answers else "")

    def next_open(self, after: int) -> int | None:
        """The next unlabelled position after `after`, going round to the start; None when every pair is done."""
        count = len(self.order)
        for step in range(1, count + 1):
            position = (after + step) % count
            if self.order[position] not in self.answers:
                return position
        return None

    def show(self) -> None:
        pair_id = self.pair_id
        pair = self.by_id[pair_id]
        to_go = sum(1 for other in self.order if other not in self.answers)
        lines = [
            "",
            f"Pair {self.position + 1} of {len(self.order)} ({to_go} to go)",
            "",
            f"  A: {pair.left_text}",
            f"  B: {pair.right_text}",
            "",
        ]
        if pair_id in self.answers:
            lines.append(f"  Your label: {self.answers[pair_id].label}")
        note = self._note_of(pair_id)
        if note:
            lines.append(f"  Your note: {note}")
        lines.append(f"  {KEYS_HELP}")
        self.write("\n".join(lines))

    def answer(self, pair_id: str, label: str, note_only: bool = False) -> bool:
        """Record and save one answer. If the file cannot be saved, the change is taken back and False returned."""
        earlier = self.answers.get(pair_id)
        note = self.notes.get(pair_id, earlier.notes if earlier else "").replace("\n", " ").strip()
        labeller = earlier.labeller if earlier else self.labeller  # an earlier row keeps the name it was saved with
        self.answers[pair_id] = Answer(label, labeller, self.today.isoformat(), note)
        answered_before = pair_id in self.answered_here
        self.answered_here.add(pair_id)  # before saving, so the merge keeps this answer over the file's
        try:
            self._save()
        except OSError as exc:
            if earlier is None:
                del self.answers[pair_id]
            else:
                self.answers[pair_id] = earlier
            if not answered_before:
                self.answered_here.discard(pair_id)
            note_dropped = self.notes.pop(pair_id, None) is not None  # nothing unsaved stays on screen as if kept
            if note_only:
                what = "Your note was not kept"
            else:
                what = "Your answer and note were not kept" if note_dropped else "Your answer was not kept"
            if isinstance(exc, FileChanged):
                fix = f"check it with python ml/scripts/check_labels.py {self.out_path}, fix it, then run again"
            else:
                fix = "try again once the file is free"
            self.write(f"Not saved: {exc}. {what}; {fix}.")
            return False
        self.notes.pop(pair_id, None)
        return True

    def _save(self) -> None:
        self._merge_answers_saved_elsewhere()
        rows = []
        for pair_id in sorted(self.answers):
            pair, answer = self.by_id[pair_id], self.answers[pair_id]
            rows.append(
                [
                    pair_id,
                    pair.left_text,
                    pair.right_text,
                    pair.category,
                    pair.product_family,
                    answer.label,
                    answer.labeller,
                    answer.labelled_at,
                    answer.notes,
                ]
            )
        write_rows(self.out_path, HEADER, rows)

    def _merge_answers_saved_elsewhere(self) -> None:
        """If a second window saved into the same file, keep its answers and edits instead of writing over them.

        For a pair this window answered, this window's answer stands; for every other pair, the file on disk wins.
        A file that no longer checks (edited in a spreadsheet, say) is never written over.
        """
        saved, problem = load_answers(self.out_path, self.by_id, self.labeller)
        if problem:
            raise FileChanged(f"{self.out_path.name} was changed outside this tool and no longer checks")
        for pair_id, answer in saved.items():
            if pair_id not in self.answered_here:
                self.answers[pair_id] = answer


class FileChanged(OSError):
    """The labels file was changed by something else and no longer passes check_labels; it is left as it is."""


def load_answers(out_path: Path, by_id: dict[str, BatchPair], labeller: str) -> tuple[dict[str, Answer], str | None]:
    """The answers already saved, or a message saying why the file can't be used (it is then never written to)."""
    if not out_path.exists():
        return {}, None
    result = check_file(out_path)
    problems = list(result.errors)
    for pair in result.pairs:
        batch_pair = by_id.get(pair.pair_id)
        if labeller_slug(pair.labeller) != labeller_slug(labeller):
            problems.append(f"{out_path}:{pair.row}: this row is {pair.labeller}'s, not {labeller}'s")
        if batch_pair is None:
            problems.append(f"{out_path}:{pair.row}: pair {pair.pair_id} is not in this batch")
        elif (pair.left_title, pair.right_title) != (batch_pair.left_text, batch_pair.right_text):
            problems.append(f"{out_path}:{pair.row}: the titles of {pair.pair_id} differ from the batch file")
    if problems:
        problems.append(f"{out_path} cannot be used as it is, so nothing was changed. Fix it, then run again.")
        return {}, "\n".join(problems)
    answers = {
        pair.pair_id: Answer(pair.label, pair.labeller, pair.labelled_at.isoformat(), pair.notes)
        for pair in result.pairs
    }
    return answers, None


def use_utf8_stdin() -> None:
    """Piped or redirected input on Windows is read in the legacy code page; read it as UTF-8, so ₹ survives."""
    if isinstance(sys.stdin, io.TextIOWrapper):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(prog="label_pairs.py", description="Label a batch of pairs at the keyboard.")
    parser.add_argument("batch", type=Path, metavar="BATCH.pairs.csv", help="a batch made by make_pairs.py")
    parser.add_argument("--labeller", required=True, help="your first name, such as Om")
    parser.add_argument("--out", type=Path, help="your labels file (default ml/labelling/<batch>.<name>.csv)")
    args = parser.parse_args(argv)
    out_path: Path = args.out or default_output(args.batch, args.labeller)
    keys: Keys
    if sys.stdin.isatty():
        keys = TerminalKeys()
    else:
        use_utf8_stdin()
        print("Keys are read one per line here: press Enter after each key.")
        keys = LineKeys()
    try:
        return run_session(args.batch, args.labeller, out_path, keys, today=date.today(), opener=webbrowser.open)
    except KeyboardInterrupt:
        print(f"\nStopped. Everything you labelled is saved in {out_path.name}. Run the same command to carry on.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
