"""Counting distinct recommenders per product, with no LLM (US-13). Every thread and name here is synthetic."""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest

from app.contracts import RecommenderCount, Source, SourceComment
from app.research.reddit import recommenders as recommenders_module
from app.research.reddit.recommenders import MAX_COUNTS, ProductMatcher, count_recommenders
from tests.fakes.research import thread, web_source

ANN, BEN, CAM = "a" * 64, "b" * 64, "c" * 64


def _thread(
    name: str, *comments: tuple[str | None, str], post: str = "A made-up post.", author: str | None = None
) -> Source:
    source = thread(name, text=post, title=f"Made-up title {name}", author_digest=author)
    rows = tuple(
        SourceComment(id=f"m{i}", depth=1, upvotes=1, text=text, author_hash=who)
        for i, (who, text) in enumerate(comments)
    )
    return Source.model_validate({**source.model_dump(), "comments": rows})


def _counts(threads: list[Source], *names: str) -> dict[str, tuple[int, int]]:
    rows = count_recommenders(threads, list(names))
    return {r.product_mention: (r.distinct_recommenders, r.threads) for r in rows}


# --- the matcher -----------------------------------------------------------------------------------


def test_a_name_is_found_whatever_its_case() -> None:
    assert ProductMatcher(["WH-1000XM5"]).find("I love my wh-1000xm5 a lot") == ["WH-1000XM5"]
    assert ProductMatcher(["wh-1000xm5"]).find("I LOVE MY WH-1000XM5") == ["wh-1000xm5"]


def test_1000xm5_inside_wf_1000xm5_is_not_a_separate_product() -> None:
    both = ProductMatcher(["1000XM5", "WF-1000XM5"])
    assert both.find("The WF-1000XM5 are great.") == ["WF-1000XM5"]


def test_1000xm5_inside_wf_1000xm5_is_not_found_even_alone() -> None:
    """With the longer name unknown, it is still another product's name, not this one."""
    assert ProductMatcher(["1000XM5"]).find("The WF-1000XM5 are great.") == []


def test_xm5_and_wh_1000xm5_are_both_found() -> None:
    names = ProductMatcher(["XM5", "WH-1000XM5"])
    assert names.find("The XM5 or the WH-1000XM5?") == ["XM5", "WH-1000XM5"]


def test_xm5_is_not_found_inside_wh_1000xm5() -> None:
    assert ProductMatcher(["XM5", "WH-1000XM5"]).find("The WH-1000XM5 only.") == ["WH-1000XM5"]


def test_the_longest_name_wins_where_names_overlap() -> None:
    names = ProductMatcher(["Sony", "Sony WH-1000XM5", "WH-1000XM5"])
    assert names.find("Get the Sony WH-1000XM5 today") == ["Sony WH-1000XM5"]
    assert names.find("Sony makes others") == ["Sony"]


def test_of_two_equal_names_that_overlap_the_left_one_wins() -> None:
    assert ProductMatcher(["alpha beta", "beta gamma"]).find("alpha beta gamma") == ["alpha beta"]
    assert ProductMatcher(["zeta beta", "beta alfa"]).find("zeta beta alfa") == ["zeta beta"]  # not the first by name


def test_the_longer_of_two_partly_overlapping_names_wins() -> None:
    names = ProductMatcher(["HP Victus", "Victus 15-fa1226TX"])
    assert names.find("the HP Victus 15-fa1226TX is good") == ["Victus 15-fa1226TX"]


def test_names_come_back_in_the_order_they_appear() -> None:
    names = ProductMatcher(["XM5", "A much longer made-up name"])
    assert names.find("XM5, then a much longer made-up name") == ["XM5", "A much longer made-up name"]


@pytest.mark.parametrize(
    "text",
    ["XM50 is out", "AXM5 is out", "the xm5s are out", "xm5x", "pre-XM5", "WF-XM5 is out"],
)
def test_a_name_inside_a_longer_word_does_not_count(text: str) -> None:
    assert ProductMatcher(["XM5"]).find(text) == []


@pytest.mark.parametrize(
    "text",
    ["XM5", "XM5.", "(XM5)", "XM5, then", "the XM5!", '"XM5"', "XM5/G14", "XM5-like sound", "- XM5", "-XM5 first"],
)
def test_a_name_between_word_boundaries_counts(text: str) -> None:
    assert ProductMatcher(["XM5"]).find(text) == ["XM5"]


@pytest.mark.parametrize("dash", [0x2013, 0x2014, 0x2015, 0x2212])
def test_a_dash_used_as_punctuation_does_not_hide_a_name(dash: int) -> None:
    mark = chr(dash)
    assert ProductMatcher(["WH-1000XM5"]).find(f"my pick{mark}WH-1000XM5{mark}is great") == ["WH-1000XM5"]
    assert ProductMatcher(["XM4", "XM5"]).find(f"the XM4{mark}XM5 range") == ["XM4", "XM5"]


def test_a_name_written_with_a_long_dash_still_counts_as_the_name() -> None:
    assert ProductMatcher(["WH-1000XM5"]).find(f"the WH{chr(0x2014)}1000XM5") == ["WH-1000XM5"]


def test_a_name_given_with_a_long_dash_matches_text_with_a_hyphen() -> None:
    spelled = f"WH{chr(0x2014)}1000XM5"
    assert ProductMatcher([spelled]).find("the WH-1000XM5") == [spelled]


def test_a_name_followed_by_a_hyphenated_word_counts() -> None:
    assert ProductMatcher(["Nova Book 14"]).find("the Nova Book 14-inch model") == ["Nova Book 14"]


def test_spaces_and_line_breaks_inside_a_name_do_not_matter() -> None:
    names = ProductMatcher(["Nova  Book 14"])
    assert names.find("the Nova\nBook   14 model") == ["Nova  Book 14"]


def test_a_different_kind_of_dash_still_matches() -> None:
    for dash in (0x2010, 0x2011, 0x2013, 0x2014):
        assert ProductMatcher(["WH-1000XM5"]).find(f"the WH{chr(dash)}1000XM5") == ["WH-1000XM5"]


def test_the_rupee_sign_and_hindi_english_text_do_not_break_matching() -> None:
    rupee, main = chr(0x20B9), "".join(map(chr, (0x92E, 0x947, 0x902)))  # the Hindi word for "in"
    hindi = "".join(map(chr, (0x905, 0x91A, 0x94D, 0x91B, 0x93E)))  # the Hindi word for "good"
    names = ProductMatcher(["WH-1000XM5", "G14"])
    assert names.find(f"WH-1000XM5 {rupee}24,990 {main} {hindi} hai, G14 bhi") == ["WH-1000XM5", "G14"]
    assert names.find(f"{rupee}24,990 WH-1000XM5") == ["WH-1000XM5"]


def test_hindi_letters_touching_a_latin_name_are_a_boundary() -> None:
    attached = "".join(map(chr, (0x915, 0x93E)))  # a Hindi word often written right after a name
    assert ProductMatcher(["WH-1000XM5"]).find(f"WH-1000XM5{attached} sound") == ["WH-1000XM5"]


def test_a_hindi_name_does_not_match_inside_a_longer_hindi_word() -> None:
    short = "".join(map(chr, (0x928, 0x94B, 0x935)))
    assert ProductMatcher([short]).find(f"yeh {short}{chr(0x93E)} hai") == []  # a vowel sign carries the word on
    assert ProductMatcher([short]).find(f"yeh {chr(0x915)}{chr(0x93E)}{short} hai") == []
    assert ProductMatcher([short]).find(f"yeh {short} hai") == [short]


def test_a_hindi_word_and_a_hyphen_before_a_latin_name_do_not_hide_it() -> None:
    word = "".join(map(chr, (0x92B, 0x94B, 0x928)))
    ending_in_a_vowel_sign = "".join(map(chr, (0x938, 0x94B, 0x928, 0x940)))
    assert ProductMatcher(["XM5"]).find(f"{word}-XM5") == ["XM5"]
    assert ProductMatcher(["XM5"]).find(f"{ending_in_a_vowel_sign}-XM5") == ["XM5"]


def test_a_hindi_name_on_the_end_of_a_hyphenated_hindi_name_is_not_found() -> None:
    name = "".join(map(chr, (0x928, 0x94B, 0x935, 0x93E)))
    before = "".join(map(chr, (0x92A, 0x94D, 0x930, 0x94B)))  # ends in a vowel sign
    assert ProductMatcher([name]).find(f"{before}-{name}") == []
    assert ProductMatcher([name]).find(f"{chr(0x915)}-{name}") == []


def test_a_hindi_name_can_match_too() -> None:
    name = "".join(map(chr, (0x928, 0x94B, 0x935, 0x93E)))
    assert ProductMatcher([name]).find(f"yeh {name} hai") == [name]


def test_pattern_characters_in_a_name_are_taken_literally() -> None:
    assert ProductMatcher(["C++ Book"]).find("the C++ Book is here") == ["C++ Book"]
    assert ProductMatcher(["C++ Book"]).find("the CCC Book is here") == []


def test_a_name_named_twice_is_found_twice() -> None:
    assert ProductMatcher(["XM5"]).find("XM5 and XM5") == ["XM5", "XM5"]


def test_names_differing_only_in_case_get_one_fixed_spelling() -> None:
    assert ProductMatcher(["xm5", "XM5"]).find("the xm5") == ["XM5"]
    assert ProductMatcher(["XM5", "xm5"]).find("the xm5") == ["XM5"]


def test_an_empty_dictionary_finds_nothing() -> None:
    assert ProductMatcher([]).find("anything at all") == []


def test_a_blank_name_is_ignored() -> None:
    assert ProductMatcher(["", "   ", "XM5"]).find("the XM5") == ["XM5"]


def test_empty_text_finds_nothing() -> None:
    assert ProductMatcher(["XM5"]).find("") == []


def test_names_sharing_prefixes_and_suffixes_are_all_found() -> None:
    """The classic case for the failure links: each name ends inside another."""
    names = ProductMatcher(["he", "she", "his", "hers"])
    assert names.find("he she his hers") == ["he", "she", "his", "hers"]


def test_a_failure_link_keeps_a_match_that_starts_inside_a_false_start() -> None:
    assert ProductMatcher(["ab cd ef", "cd"]).find("ab cd xx") == ["cd"]


def _slow_find(names: list[str], text: str) -> list[str]:
    """The same rule written the slow, obvious way, to check the matcher against."""
    hits = []
    taken = [False] * len(text)
    for name in {n.casefold(): n for n in names}:
        for start in range(len(text)):
            end = start + len(name)
            if text[start:end] != name:
                continue
            before = text[start - 1] if start else ""
            after = text[end] if end < len(text) else ""
            hyphened = before == "-" and start >= 2 and text[start - 2].isalnum()
            if not before.isalnum() and not after.isalnum() and not hyphened:
                hits.append((start - end, start, end, name))
    found = []
    for _, start, end, name in sorted(hits):
        if not any(taken[start:end]):
            taken[start:end] = [True] * (end - start)
            found.append((start, name))
    return [name for _, name in sorted(found)]


def test_the_matcher_agrees_with_a_slow_check_on_many_random_texts() -> None:
    rng = random.Random(1313)
    words = ["ab", "abc", "bc", "c", "x1", "abc-x1", "x1 ab", "b", "ab abc"]
    for _ in range(400):
        names = rng.sample(words, rng.randint(1, 6))
        text = "".join(
            rng.choice(["ab", "abc", "bc", "c", "x1", "b", " ", "-", ".", " "]) for _ in range(rng.randint(0, 30))
        )
        assert ProductMatcher(names).find(text) == _slow_find(names, " ".join(text.split())), (names, text)


# --- counting --------------------------------------------------------------------------------------


def test_one_author_posting_ten_times_counts_once() -> None:
    comments = [(ANN, f"The XM5 is good, part {i}.") for i in range(10)]
    assert _counts([_thread("aaa", *comments)], "XM5") == {"XM5": (1, 1)}


def test_the_same_author_in_two_threads_still_counts_once() -> None:
    threads = [_thread("aaa", (ANN, "The XM5.")), _thread("bbb", (ANN, "The XM5 again."))]
    assert _counts(threads, "XM5") == {"XM5": (1, 2)}


def test_two_authors_count_as_two() -> None:
    assert _counts([_thread("aaa", (ANN, "The XM5."), (BEN, "Yes, the XM5."))], "XM5") == {"XM5": (2, 1)}


def test_one_author_can_recommend_two_products() -> None:
    assert _counts([_thread("aaa", (ANN, "The XM5 or the G14."))], "XM5", "G14") == {"XM5": (1, 1), "G14": (1, 1)}


def test_the_threads_author_counts_for_the_post_and_the_title() -> None:
    assert _counts([_thread("aaa", post="The XM5 is good.", author=ANN)], "XM5") == {"XM5": (1, 1)}
    source = thread("bbb", title="XM5 question", text="A made-up post.", author_digest=BEN)
    assert _counts([source], "XM5") == {"XM5": (1, 1)}


def test_deleted_authors_are_not_counted() -> None:
    threads = [_thread("aaa", (None, "The XM5."), (None, "The XM5 again."), (ANN, "The XM5."))]
    assert _counts(threads, "XM5") == {"XM5": (1, 1)}


def test_a_product_named_only_by_deleted_authors_has_no_count() -> None:
    assert _counts([_thread("aaa", (None, "The XM5."))], "XM5") == {}


def test_a_thread_where_only_a_deleted_author_names_it_is_not_counted() -> None:
    threads = [_thread("aaa", (ANN, "The XM5.")), _thread("bbb", (None, "The XM5."))]
    assert _counts(threads, "XM5") == {"XM5": (1, 1)}


def test_a_name_not_named_is_left_out() -> None:
    assert _counts([_thread("aaa", (ANN, "The XM5."))], "XM5", "G14") == {"XM5": (1, 1)}


def test_aliases_count_as_their_product() -> None:
    threads = [_thread("aaa", (ANN, "The XM5."), (BEN, "The WH-1000XM5."))]
    rows = count_recommenders(threads, ["WH-1000XM5"], {"XM5": "WH-1000XM5"})
    assert [(r.product_mention, r.distinct_recommenders) for r in rows] == [("WH-1000XM5", 2)]


def test_an_author_using_a_name_and_its_alias_counts_once() -> None:
    threads = [_thread("aaa", (ANN, "The XM5 is the WH-1000XM5."))]
    rows = count_recommenders(threads, ["WH-1000XM5"], {"XM5": "WH-1000XM5"})
    assert [(r.product_mention, r.distinct_recommenders) for r in rows] == [("WH-1000XM5", 1)]


def test_names_differing_only_in_case_are_one_product_with_a_fixed_spelling() -> None:
    threads = [_thread("aaa", (ANN, "The XM5."), (BEN, "the xm5"))]
    first = count_recommenders(threads, ["xm5", "XM5"])
    second = count_recommenders(threads, ["XM5", "xm5"])
    assert first == second
    assert [(r.product_mention, r.distinct_recommenders) for r in first] == [("XM5", 2)]


@pytest.mark.parametrize("name", ["A", "14", "2026", " ", "7-8"])
def test_a_name_too_short_or_with_no_letter_is_not_counted(name: str) -> None:
    """A model gave these names, and one like "A" or "14" would match almost every post."""
    assert _counts([_thread("aaa", (ANN, "A 14 inch in 2026, for 7-8 hours."))], name) == {}


def test_a_short_real_name_still_counts() -> None:
    assert _counts([_thread("aaa", (ANN, "The X1 is good."))], "X1") == {"X1": (1, 1)}


def test_a_product_type_is_not_counted_because_it_is_not_given() -> None:
    assert _counts([_thread("aaa", (ANN, "Memory foam, and the XM5."))], "XM5") == {"XM5": (1, 1)}


def test_counts_come_most_first_then_by_name() -> None:
    threads = [_thread("aaa", (ANN, "The XM5, G14, A15."), (BEN, "The G14."), (CAM, "The G14 and XM5."))]
    rows = count_recommenders(threads, ["A15", "XM5", "G14"])
    assert [r.product_mention for r in rows] == ["G14", "XM5", "A15"]


def test_at_most_the_contracts_limit_of_counts_comes_back() -> None:
    names = [f"Model{i:03d}" for i in range(MAX_COUNTS + 20)]
    rows = count_recommenders([_thread("aaa", (ANN, " ".join(names)))], names)
    assert len(rows) == MAX_COUNTS == 200


def test_the_results_are_contract_counts() -> None:
    (row,) = count_recommenders([_thread("aaa", (ANN, "The XM5."))], ["XM5"])
    assert isinstance(row, RecommenderCount)


def test_a_same_topic_line_is_not_the_authors_own_words() -> None:
    """Merging near-duplicates adds these lines, and a link there says nothing about what this author wrote."""
    line = "Same topic: https://www.reddit.com/r/laptops/comments/xm5/"
    source = thread("aaa", text=f"A made-up post.\n{line}", author_digest=ANN)
    assert _counts([source], "xm5") == {}


def test_only_reddit_threads_are_counted() -> None:
    page = web_source("https://a.example/", text="The XM5 is good.")
    signed = Source.model_validate({**page.model_dump(), "author_hash": ANN})
    assert _counts([page, signed], "XM5") == {}


def test_the_same_threads_always_give_the_same_counts() -> None:
    threads = [
        _thread(f"t{i}", *[(chr(97 + (i * j) % 7) * 64, f"The XM5 and A{j}0 G14.") for j in range(6)]) for i in range(8)
    ]
    names = ["XM5", "G14", "A10", "A20", "A30"]
    first = count_recommenders(threads, names)
    assert first == count_recommenders(threads, names)
    for seed in range(5):
        shuffled_threads, shuffled_names = list(threads), list(names)
        rng = random.Random(seed)
        rng.shuffle(shuffled_threads)
        rng.shuffle(shuffled_names)
        assert first == count_recommenders(shuffled_threads, shuffled_names)


def test_no_threads_or_no_names_give_no_counts() -> None:
    assert count_recommenders([], ["XM5"]) == []
    assert count_recommenders([_thread("aaa", (ANN, "The XM5."))], []) == []


def test_the_module_never_writes_a_file_or_calls_a_model() -> None:
    """Counted by code with no LLM (build rule 2), and Reddit text stays in memory (D-53)."""
    code = Path(recommenders_module.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\bopen\(|write_text|write_bytes|LLMClient|app\.llm", code)
