"""US-22's blank sheet (S5-4 DECIDED 5): 100 quotes drawn, seeded, from the recorded runs' public sources, never
Reddit or YouTube; the columns people fill in left blank; a fixed 40/60 split; and a filled sheet never written over."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from evals.citation import sheet

COLUMNS = ["id", "quote", "source_address", "sentence", "label", "written_by"]


def _run(root: Path, name: str, sources: list[dict[str, object]], quotes: list[str]) -> None:
    folder = root / name
    folder.mkdir(parents=True)
    record = {
        "sources": sources,
        "llm_replies": [
            {"task": "extract_claims", "reply": {"claims": [{"quote": q} for q in quotes]}},
            {"task": "write_explanation", "reply": {"claims": [{"quote": "a quote that is not from extraction"}]}},
        ],
    }
    (folder / "pipeline.json").write_text(json.dumps([record]), encoding="utf-8")


def _source(url: str, text: str, *, kind: str = "review_site", data_class: str = "public") -> dict[str, object]:
    return {"id": url, "url": url, "type": kind, "data_class": data_class, "text": text}


def test_only_public_web_quotes_are_drawn_never_reddit_or_youtube(tmp_path: Path) -> None:
    web = "The battery lasted a full working day in our test."
    reddit = "Mine died after two hours of gaming every single time."
    youtube = "The speakers are much louder than I expected them to be."
    hidden_host = "Its keyboard is the best one we have typed on this year."
    _run(
        tmp_path,
        "run_a",
        [
            _source("https://reviews.example.test/a", f"Intro. {web} More."),
            _source("https://www.reddit.com/r/laptops/1", reddit, kind="reddit", data_class="reddit"),
            _source("https://www.youtube.com/watch?v=x", youtube, kind="youtube"),
            _source("https://old.reddit.com/r/x/2", hidden_host, kind="web_search"),
        ],
        [web, reddit, youtube, hidden_host, "A quote found in no source text at all here."],
    )
    found = sheet.candidates(tmp_path)
    assert [(q.quote, q.source_address) for q in found] == [(web, "https://reviews.example.test/a")]


def test_short_and_repeated_quotes_are_left_out(tmp_path: Path) -> None:
    text = "Great value. The screen is bright enough to use outdoors at noon."
    _run(tmp_path, "run_a", [_source("https://a.example.test/", text)], ["Great value.", text[13:]])
    _run(tmp_path, "run_b", [_source("https://b.example.test/", text)], [text[13:].upper()])
    assert [q.quote for q in sheet.candidates(tmp_path)] == [text[13:]]


def test_page_sentences_are_whole_public_sentences_with_no_links_markup_or_boilerplate(tmp_path: Path) -> None:
    good = "The basket is easy to clean and fits a whole chicken without trouble."
    text = " ".join(
        [
            good,
            "and this fragment starts in the middle of a longer sentence somewhere.",
            "We may earn an affiliate commission when you buy through links on our site.",
            "Read the full review at https://reviews.example.test/full for more details today.",
            "Too short to use.",
            "The lid felt flimsy � and the hinge creaked after a single week of use.",
        ]
    )
    reddit_text = good.replace("chicken", "duck")
    _run(
        tmp_path,
        "run_a",
        [
            _source("https://a.example.test/", text),
            _source("https://www.reddit.com/r/x/1", reddit_text, kind="reddit", data_class="reddit"),
        ],
        [],
    )
    assert [(q.quote, q.kind) for q in sheet.passages(tmp_path)] == [(good, "page")]
    assert sheet.passages(tmp_path, taken={sheet.key(good)}) == []


def test_the_draw_takes_every_extraction_quote_then_fills_with_page_sentences_by_the_seed() -> None:
    found = sheet.candidates()
    extra = sheet.passages(taken={sheet.key(q.quote) for q in found})
    first = sheet.draw(found, extra)
    assert [q.quote for q in first] == [q.quote for q in sheet.draw(found, extra)]
    assert len(first) == sheet.COUNT == 100
    assert len({sheet.key(q.quote) for q in first}) == 100
    assert {q.quote for q in found} <= {q.quote for q in first}  # every real quote is in: fewer than 100 of them
    assert all("reddit." not in q.source_address and "youtu" not in q.source_address for q in first)


def test_the_draw_refuses_when_there_are_too_few_quotes() -> None:
    few = [sheet.Quote(f"Quote number {i} with enough words.", "https://a.example.test/", "run") for i in range(3)]
    with pytest.raises(ValueError, match="only 3"):
        sheet.draw(few, [], count=4)


def test_the_split_is_40_to_tune_and_60_to_test_by_a_fixed_seed() -> None:
    ids = [f"c{i:03d}" for i in range(1, 101)]
    split = sheet.split_ids(ids)
    assert split == sheet.split_ids(list(reversed(ids)))  # the seed decides, not the order given
    assert (len(split["tune"]), len(split["test"])) == (40, 60)
    assert sorted(split["tune"] + split["test"]) == ids


def test_the_blank_sheet_has_the_columns_and_leaves_the_writing_to_people(tmp_path: Path) -> None:
    quotes = [sheet.Quote("A quote with enough words in it.", "https://a.example.test/", "run_a")]
    path = tmp_path / "pairs.csv"
    sheet.write_sheet(path, quotes)
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert list(rows[0]) == COLUMNS
    assert rows[0]["id"] == "c001"
    assert rows[0]["sentence"] == rows[0]["label"] == rows[0]["written_by"] == ""


def test_a_sheet_people_have_started_is_never_written_over(tmp_path: Path) -> None:
    quotes = [sheet.Quote("A quote with enough words in it.", "https://a.example.test/", "run_a")]
    path = tmp_path / "pairs.csv"
    sheet.write_sheet(path, quotes)
    text = path.read_text(encoding="utf-8").replace(",,,\n", ",Their own sentence.,supported,Rushiraj Navar\n", 1)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(sheet.SheetStarted):
        sheet.write_sheet(path, quotes)
    assert "Their own sentence." in path.read_text(encoding="utf-8")
