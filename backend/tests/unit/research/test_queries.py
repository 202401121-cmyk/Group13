"""Queries come from the rubric, in code, the same every time (US-12). The candidate names cost no LLM call."""

from __future__ import annotations

import pytest

from app.research.queries import build_queries, candidate_names, comparison_query, first_round, held_query, reddit_query
from tests.fakes.research import rubric


def _texts(**kwargs: object) -> list[str]:
    return [q.text for q in build_queries(rubric(**kwargs))]  # type: ignore[arg-type]


def test_the_same_rubric_gives_the_same_queries() -> None:
    assert build_queries(rubric()) == build_queries(rubric())


def test_the_queries_for_the_example_rubric_are_pinned() -> None:
    assert _texts() == [
        "best laptop for coding under 70000 rupees India",
        "laptop for coding review India",
        "laptop buying guide India",
        "laptop for coding recommendations",
        "laptop under 70000 rupees review India",
        "laptop for coding comparison India",
    ]
    assert [q.goes_to for q in build_queries(rubric())] == ["web", "review", "web", "web", "review", "web"]


def test_no_budget_and_no_use_still_give_4_queries() -> None:
    assert _texts(use=None, at_most=None) == [
        "best laptop India",
        "laptop review India",
        "laptop buying guide India",
        "laptop recommendations",
    ]


def test_budget_only_and_use_only_give_5_queries() -> None:
    assert _texts(use=None) == [
        "best laptop under 70000 rupees India",
        "laptop review India",
        "laptop buying guide India",
        "laptop recommendations",
        "laptop under 70000 rupees review India",
    ]
    assert _texts(at_most=None) == [
        "best laptop for coding India",
        "laptop for coding review India",
        "laptop buying guide India",
        "laptop for coding recommendations",
        "laptop for coding comparison India",
    ]


def test_budget_and_use_give_6_queries() -> None:
    assert len(_texts()) == 6


def test_a_budget_range_and_a_minimum_read_plainly() -> None:
    assert _texts(use=None, at_least=50000, at_most=70000)[0] == "best laptop between 50000 and 70000 rupees India"
    assert _texts(use=None, at_least=50000, at_most=None)[0] == "best laptop above 50000 rupees India"


def test_a_budget_not_in_rupees_is_left_out() -> None:
    assert _texts(use=None, currency="USD") == _texts(use=None, at_most=None)


def test_another_region_is_left_out_of_the_words() -> None:
    assert _texts(use=None, at_most=None, region="GLOBAL")[0] == "best laptop"


@pytest.mark.parametrize(
    ("use", "expected"),
    [
        ("coding", "coding"),
        ("CODING", "coding"),
        ("mostly coding at home", "coding"),
        ("Video-Editing", "video editing"),
        ("video   editing and music", "video editing"),
        ("music and video editing", "music"),
        ("for the students", "students"),
    ],
)
def test_a_known_use_appears_as_we_spell_it(use: str, expected: str) -> None:
    assert build_queries(rubric(use=use, at_most=None))[0].text == f"best laptop for {expected} India"
    assert reddit_query(rubric(use=use)) == f"laptop for {expected} recommendations"


@pytest.mark.parametrize(
    "use",
    [
        "a gift for Zarvi Ostrand",
        "near Koramangala in Bengaluru",
        "zarvi.ostrand@mail.example",
        "call 9876543210",
        "call 987-654-3210",
        '"site:evil.example" OR hobby',
        "decoding old radios",
        "a musician's friend",
        "zarvi@gym.example",
    ],
)
def test_anything_but_a_known_use_is_left_out(use: str) -> None:
    assert _texts(use=use) == _texts(use=None)
    assert reddit_query(rubric(use=use)) == reddit_query(rubric(use=None))


def test_a_known_use_beside_personal_words_goes_alone() -> None:
    use = "coding for Zarvi Ostrand in Bengaluru, call 9876543210 or zarvi@mail.example"
    assert _texts(use=use) == _texts()


def test_the_list_of_known_uses_can_be_changed() -> None:
    assert build_queries(rubric(use="fishing", at_most=None), ("fishing",))[0].text == "best laptop for fishing India"
    assert build_queries(rubric(use="coding", at_most=None), ("fishing",))[0].text == "best laptop India"
    assert reddit_query(rubric(use="fishing"), ("fishing",)) == "laptop for fishing recommendations"


def test_a_category_slug_reads_as_words() -> None:
    assert _texts(category="power_bank", use=None, at_most=None)[0] == "best power bank India"
    assert _texts(category="smart-watch", use=None, at_most=None)[0] == "best smart watch India"


def test_the_first_round_holds_back_a_slot_only_when_there_are_6() -> None:
    six = build_queries(rubric())
    assert [q.text for q in first_round(six)] == [q.text for q in six[:5]]
    held = held_query(six)
    assert held is not None
    assert held.text == six[5].text
    five = build_queries(rubric(use=None))
    assert len(first_round(five)) == 5
    assert held_query(five) is None


def test_reddit_uses_the_recommendations_query() -> None:
    assert reddit_query(rubric()) == "laptop for coding recommendations"
    assert reddit_query(rubric(use=None, at_most=None)) == "laptop recommendations"


# --- candidates ------------------------------------------------------------------------------------


def test_candidates_need_two_sources_and_a_model_token() -> None:
    titles = [
        ("a", "HP Pavilion 15 review: a good laptop"),
        ("b", "Best budget pick: the HP Pavilion 15"),
        ("a", "Lenovo LOQ 15 long-term notes"),
        ("c", "Lenovo LOQ 15 or something else?"),
        ("d", "Acer Swift 3 is fine"),  # only one source
        ("e", "Dell Inspiron is good"),  # no model token
        ("f", "Dell Inspiron is good"),
    ]
    assert candidate_names(titles, "laptop") == ["HP Pavilion 15", "Lenovo LOQ 15"]


def test_one_source_saying_a_name_twice_counts_once() -> None:
    titles = [("a", "Acer Swift 3 review"), ("a", "Acer Swift 3 again"), ("b", "Asus Vivobook 15 review")]
    assert candidate_names(titles, "laptop") == []


def test_filler_and_years_are_not_candidates() -> None:
    titles = [
        ("a", "Best Laptops 2026"),
        ("b", "Best Laptops 2026"),
        ("c", "Top 10 Laptops Under 70000"),
        ("d", "Top 10 Laptops Under 70000"),
        ("e", "Laptop Buying Guide 2026"),
        ("f", "Laptop Buying Guide 2026"),
    ]
    assert candidate_names(titles, "laptop") == []


def test_prices_are_not_candidates() -> None:
    titles = [
        ("a", "Laptops Under Rs 70000"),
        ("b", "Top 5 Windows Laptops under Rs 70000"),
        ("c", "Laptops Under Rs. 75000"),
    ]
    assert candidate_names(titles, "laptop") == []


def test_computer_parts_are_not_candidates() -> None:
    titles = [
        (k, t) for k in "ab" for t in ("RTX 5060 or RTX 5070", "DDR5 RAM upgrade", "Intel Core Ultra 9", "Ryzen 7 CPU")
    ]
    assert candidate_names(titles, "laptop") == []


def test_a_name_with_a_part_after_it_still_counts() -> None:
    titles = [("a", "ASUS TUF A15 RTX 4060 thermals"), ("b", "ASUS TUF A15 battery")]
    assert candidate_names(titles, "laptop") == ["ASUS TUF A15"]


def test_mixed_case_brands_are_read() -> None:
    titles = [("a", "iPhone 15 review"), ("b", "The iPhone 15 after a year"), ("c", "MacBook Air M2")]
    assert candidate_names(titles, "smartphone") == ["iPhone 15"]


def test_a_longer_form_counts_for_the_shorter_name() -> None:
    titles = [("a", "HP Pavilion 15 Eg3000 review"), ("b", "HP Pavilion 15 notes")]
    assert candidate_names(titles, "laptop")[0] == "HP Pavilion 15"


def test_candidates_are_ordered_by_count_then_name() -> None:
    titles = [
        ("a", "Zeta Book 3 review"),
        ("b", "Zeta Book 3 notes"),
        ("a", "Acer Swift 3 review"),
        ("b", "Acer Swift 3 notes"),
        ("c", "Zeta Book 3 again"),
    ]
    assert candidate_names(titles, "laptop") == ["Zeta Book 3", "Acer Swift 3"]


def test_known_candidates_win_over_titles() -> None:
    titles = [("a", "Acer Swift 3"), ("b", "Acer Swift 3"), ("a", "Asus Vivobook 15"), ("b", "Asus Vivobook 15")]
    query = comparison_query(["Nova Book 14", "Kestrel Air 2"], titles, "laptop")
    assert query is not None
    assert query.text == "Nova Book 14 vs Kestrel Air 2"
    assert query.goes_to == "web"


def test_titles_give_the_comparison_when_no_names_are_known() -> None:
    titles = [("a", "Acer Swift 3"), ("b", "Acer Swift 3"), ("a", "Asus Vivobook 15"), ("b", "Asus Vivobook 15")]
    query = comparison_query([], titles, "laptop")
    assert query is not None
    assert query.text == "Acer Swift 3 vs Asus Vivobook 15"


def test_no_comparison_query_without_two_candidates() -> None:
    titles = [("a", "Acer Swift 3"), ("b", "Acer Swift 3")]
    assert comparison_query([], titles, "laptop") is None
    assert comparison_query(["Only One 1"], titles, "laptop") is None


def test_a_name_and_its_longer_form_are_not_compared() -> None:
    titles = [("a", "HP Pavilion 15 Eg3000"), ("b", "HP Pavilion 15 Eg3000")]
    assert comparison_query([], titles, "laptop") is None


def test_known_candidates_are_cleaned_of_search_operators() -> None:
    query = comparison_query(['Nova "Book" 14', "site:evil.example Kestrel 2"], [], "laptop")
    assert query is not None
    assert query.text == "Nova Book 14 vs Kestrel 2"


def test_known_names_that_are_one_family_are_not_compared() -> None:
    assert comparison_query(["HP Pavilion 15", "HP Pavilion 15 Eg3000"], [], "laptop") is None


def test_a_pure_number_is_not_a_model_token() -> None:
    titles = [
        ("a", "Best Laptops Below 70000 in India"),
        ("b", "Laptops Within 50000 Rupees"),
        ("c", "Laptops Within 50000"),
    ]
    titles += [("d", "Best Laptops Below 70000"), ("e", "Lenovo 3000 notes"), ("f", "Lenovo 3000 again")]
    assert candidate_names(titles, "laptop") == []


def test_chips_and_systems_are_not_candidates() -> None:
    titles = [(k, t) for k in "ab" for t in ("Snapdragon 8 phones", "Windows 11 laptops", "Dimensity 9000 phones")]
    assert candidate_names(titles, "laptop") == []


def test_a_price_word_ends_a_name() -> None:
    titles = [("a", "Laptops Below 50k"), ("b", "Laptops Below 50k"), ("c", "Rs 40k laptops"), ("d", "Rs 40k picks")]
    assert candidate_names(titles, "laptop") == []
