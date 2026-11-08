"""robots.txt read the way RFC 9309 says (US-12). Python's own parser gets several of these wrong."""

from __future__ import annotations

import pytest

from app.research.robots import RobotsRules


def _allowed(robots: str, path: str, agent: str = "ShopSense") -> bool:
    return RobotsRules(robots, agent).allowed(path)


def test_no_file_allows_everything() -> None:
    assert _allowed("", "/anything")


def test_a_plain_prefix_disallows() -> None:
    robots = "User-agent: *\nDisallow: /private"
    assert not _allowed(robots, "/private/x")
    assert _allowed(robots, "/public")


def test_an_empty_disallow_allows() -> None:
    assert _allowed("User-agent: *\nDisallow:", "/x")


def test_a_wildcard_matches() -> None:
    robots = "User-agent: *\nDisallow: /*?print="
    assert not _allowed(robots, "/nova?print=1")
    assert _allowed(robots, "/nova?page=1")


def test_a_dollar_anchors_the_end() -> None:
    robots = "User-agent: *\nDisallow: /*.pdf$"
    assert not _allowed(robots, "/a/b.pdf")
    assert _allowed(robots, "/a/b.pdf?x=1")


def test_the_longest_match_wins_whatever_the_order() -> None:
    assert not _allowed("User-agent: *\nAllow: /\nDisallow: /private", "/private/x")
    assert not _allowed("User-agent: *\nDisallow: /private\nAllow: /", "/private/x")
    assert _allowed("User-agent: *\nDisallow: /private\nAllow: /private/open", "/private/open/x")


def test_a_tie_goes_to_allow() -> None:
    assert _allowed("User-agent: *\nDisallow: /a\nAllow: /a", "/a")


def test_a_blank_line_does_not_end_a_group() -> None:
    assert not _allowed("User-agent: *\n\nDisallow: /", "/x")


def test_our_own_group_replaces_the_star_group() -> None:
    robots = "User-agent: *\nAllow: /\n\nUser-agent: ShopSense\nDisallow: /"
    assert not _allowed(robots, "/x")
    assert _allowed(robots, "/x", agent="SomeoneElse")


def test_the_agent_name_ignores_case() -> None:
    assert not _allowed("User-agent: SHOPSENSE\nDisallow: /", "/x")


def test_a_group_for_someone_else_does_not_apply() -> None:
    assert _allowed("User-agent: GoogleBot\nDisallow: /", "/x")


def test_several_agents_share_a_group() -> None:
    for first, second in (("GoogleBot", "ShopSense"), ("ShopSense", "GoogleBot")):
        robots = f"User-agent: {first}\nUser-agent: {second}\nDisallow: /a\n\nUser-agent: *\nDisallow: /b"
        assert not _allowed(robots, "/a")
        assert _allowed(robots, "/b")


def test_two_groups_for_us_are_combined() -> None:
    robots = "User-agent: ShopSense\nDisallow: /a\n\nUser-agent: ShopSense\nDisallow: /b"
    assert not _allowed(robots, "/a")
    assert not _allowed(robots, "/b")


def test_comments_and_odd_lines_are_ignored() -> None:
    robots = "# a note\nUser-agent: * # everyone\nno colon here\nSitemap: https://a.example/s.xml\nDisallow: /x # why"
    assert not _allowed(robots, "/x")
    assert _allowed(robots, "/y")


def test_a_rule_before_any_agent_is_ignored() -> None:
    assert _allowed("Disallow: /\nUser-agent: GoogleBot\nDisallow: /x", "/y")


def test_a_byte_order_mark_does_not_lose_the_first_group() -> None:
    assert not _allowed(chr(0xFEFF) + "User-agent: *\nDisallow: /x", "/x")


def test_a_rule_built_to_backtrack_is_answered_at_once() -> None:
    rule = "/" + "a*" * 5000 + "b$"
    assert _allowed(f"User-agent: *\nDisallow: {rule}", "/" + "a" * 2000)


def test_field_names_ignore_case() -> None:
    assert not _allowed("USER-AGENT: *\nDISALLOW: /x", "/x")


def test_percent_encoding_is_compared_fairly() -> None:
    robots = "User-agent: *\nDisallow: /caf%C3%A9"
    assert not _allowed(robots, "/caf%c3%a9/menu")
    assert not _allowed("User-agent: *\nDisallow: /a%2Db", "/a-b")


def test_a_regex_character_in_a_rule_means_itself() -> None:
    robots = "User-agent: *\nDisallow: /a.b"
    assert not _allowed(robots, "/a.b/c")
    assert _allowed(robots, "/axb/c")


@pytest.mark.parametrize(
    ("written", "delay"),
    [("5", 5.0), ("15.5", 15.5), ("0.5", 0.5), ("soon", 0.0), ("-3", 0.0), ("", 0.0), ("inf", 0.0), ("nan", 0.0)],
)
def test_the_crawl_delay_is_read_as_a_number(written: str, delay: float) -> None:
    assert RobotsRules(f"User-agent: *\nCrawl-delay: {written}\nAllow: /", "ShopSense").crawl_delay == delay


def test_the_crawl_delay_comes_from_our_group() -> None:
    robots = "User-agent: *\nCrawl-delay: 1\n\nUser-agent: ShopSense\nCrawl-delay: 7"
    assert RobotsRules(robots, "ShopSense").crawl_delay == 7.0
    assert RobotsRules(robots, "SomeoneElse").crawl_delay == 1.0


@pytest.mark.parametrize(
    ("rule", "path"),
    [("/x*x*y", "/xy"), ("/ab*b", "/ab"), ("/ab*b$", "/ab"), ("/a$", "/ab")],
)
def test_one_part_of_a_path_is_not_counted_twice(rule: str, path: str) -> None:
    assert _allowed(f"User-agent: *\nDisallow: {rule}", path)


def test_an_empty_path_is_the_root() -> None:
    assert not _allowed("User-agent: *\nDisallow: /", "")


@pytest.mark.parametrize(("rule", "path"), [("/my page", "/my%20page"), ("/a|b", "/a%7Cb"), ("/a%7cb", "/a|b")])
def test_a_character_a_url_cannot_hold_compares_with_its_escape(rule: str, path: str) -> None:
    assert not _allowed(f"User-agent: *\nDisallow: {rule}", path)


@pytest.mark.parametrize(
    ("rule", "path"),
    [("/a-%2A.html", "/a-*.html"), ("/foo-%24", "/foo-$"), ("/a$b", "/a$b"), ("/a$b", "/a%24b"), ("/a%2Ab", "/a%2ab")],
)
def test_an_escaped_star_or_dollar_is_the_plain_character(rule: str, path: str) -> None:
    assert not _allowed(f"User-agent: *\nDisallow: {rule}", path)


def test_an_escaped_star_is_not_a_wildcard() -> None:
    assert _allowed("User-agent: *\nDisallow: /a%2Ab", "/axxb")
