"""Tests for the survey counter, on made-up rows. Run from the repository root:

python -m unittest discover -s scripts/elicitation
"""

from __future__ import annotations

import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from count_survey import OTHER, QUESTIONS, main, question_for, report, share, split_choices

WHERE = next(question for question in QUESTIONS if question.number == 2)
Q1, Q2, Q4, Q8 = (next(q.text for q in QUESTIONS if q.number == n) for n in (1, 2, 4, 8))
SECRET = "A made-up story that must never be printed"


def write_csv(folder: str, rows: list[list[str]], encoding: str = "utf-8") -> Path:
    path = Path(folder) / "survey.csv"
    with path.open("w", encoding=encoding, newline="") as file:
        csv.writer(file).writerows(rows)
    return path


class SplitChoices(unittest.TestCase):
    def test_an_option_with_its_own_comma_stays_whole(self) -> None:
        cell = "Google search, Store reviews (Amazon, Flipkart), YouTube"
        self.assertEqual(
            split_choices(cell, WHERE.options), ["Google search", "Store reviews (Amazon, Flipkart)", "YouTube"]
        )

    def test_typed_text_counts_as_other_once(self) -> None:
        self.assertEqual(split_choices("Reddit, my cousin, a forum", WHERE.options), ["Reddit", OTHER])

    def test_a_listed_word_inside_typed_text_is_not_a_choice(self) -> None:
        self.assertEqual(split_choices("Reddit threads", WHERE.options), [OTHER])

    def test_an_empty_cell_has_no_choices(self) -> None:
        self.assertEqual(split_choices("  ", WHERE.options), [])


class Share(unittest.TestCase):
    def test_uses_the_form_the_kit_asks_for(self) -> None:
        self.assertEqual(share(21, 34), "21 of 34 (62%)")


class Headers(unittest.TestCase):
    def test_matches_a_question_with_extra_words_after_it(self) -> None:
        question = question_for("If two products are nearly tied, would you rather…")
        self.assertIsNotNone(question)
        assert question is not None
        self.assertEqual(question.number, 7)

    def test_an_unknown_header_is_no_question(self) -> None:
        self.assertIsNone(question_for("Favourite colour"))


class Report(unittest.TestCase):
    def test_counts_each_kind_of_question(self) -> None:
        rows = [
            ["Timestamp", Q1, Q2, Q4, Q8],
            ["2026-10-01", "Always", "Reddit, Store reviews (Amazon, Flipkart)", "4 to 6", SECRET],
            ["2026-10-02", "Rarely", "Reddit", "4 to 6", ""],
            ["2026-10-03", "Always", "", "More than 10", SECRET],
            ["", "", "", "", ""],
        ]
        with tempfile.TemporaryDirectory() as folder:
            text = report(write_csv(folder, rows))
        self.assertIn("Responses: 3", text)
        self.assertIn("  Always: 2 of 3 (67%)", text)
        self.assertIn("(answered: 2)\n  Google search: 0 of 2 (0%)", text)
        self.assertIn("  Reddit: 2 of 2 (100%)", text)
        self.assertIn("  Store reviews (Amazon, Flipkart): 1 of 2 (50%)", text)
        self.assertIn("  4 to 6: 2 of 3 (67%)", text)
        self.assertIn(f"8. {Q8} (answered: 2)", text)
        self.assertIn("(not in this file)", text)

    def test_free_text_is_never_printed(self) -> None:
        rows = [[Q8, "Favourite colour"], [SECRET, SECRET]]
        with tempfile.TemporaryDirectory() as folder:
            text = report(write_csv(folder, rows))
        self.assertNotIn(SECRET, text)
        self.assertIn("Other column: Favourite colour (answered: 1)", text)

    def test_an_unlisted_single_answer_is_counted_as_other_and_not_printed(self) -> None:
        rows = [[Q1], ["Always"], [SECRET]]
        with tempfile.TemporaryDirectory() as folder:
            text = report(write_csv(folder, rows))
        self.assertIn(f"  {OTHER}: 1 of 2 (50%)", text)
        self.assertNotIn(SECRET, text)

    def test_reads_the_byte_order_mark_a_spreadsheet_adds(self) -> None:
        rows = [[Q1], ["Always"]]
        with tempfile.TemporaryDirectory() as folder:
            text = report(write_csv(folder, rows, encoding="utf-8-sig"))
        self.assertIn("  Always: 1 of 1 (100%)", text)


class Main(unittest.TestCase):
    def run_main(self, path: str) -> tuple[int, str]:
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = main([path])
        return code, errors.getvalue()

    def test_an_identifying_column_stops_the_run_without_showing_an_answer(self) -> None:
        for header in ("Name", "Email address", "Phone number", "Roll number"):
            with self.subTest(header=header), tempfile.TemporaryDirectory() as folder:
                path = write_csv(folder, [[header, Q1], [SECRET, "Always"]])
                code, message = self.run_main(str(path))
                self.assertEqual(code, 2)
                self.assertIn(header, message)
                self.assertNotIn(SECRET, message)

    def test_a_missing_file_is_a_clean_stop(self) -> None:
        code, message = self.run_main("no-such-file.csv")
        self.assertEqual(code, 2)
        self.assertIn("Stopped: could not read the file", message)


if __name__ == "__main__":
    unittest.main()
