"""Search queries built in code from the rubric (US-12), never from the shopper's raw text and never by an LLM.

The same rubric always gives the same 4 to 6 queries: no dates, no randomness. The use case is the shopper's own words,
so they never leave the app. A query holds a use only if the use case names a known one, and then only as we spell it.
Anything else counts as no use, so a name, place, email or phone number in the use case can't reach Serper or PullPush.

Serper may be sent 6 queries a search, and a comparison query can only be known after the first round. So the first
round sends at most 5, and one slot is held: it goes to "X vs Y" if two candidates are known, otherwise to the sixth
query.

Candidates come from titles, with no LLM call. A name must look like a product (2 to 4 capitalised words, one holding a
digit) and appear in at least two different sources. It misses names with no digit, or whose only number has 4 or more
digits, and can miscount variants. A wrong pick costs one search and adds a few sources; it never touches ranking.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from app.contracts import Budget, Rubric

from .settings import DEFAULT_KNOWN_USES

MAX_NAME_CHARS = 60
MAX_SERPER_FIRST_ROUND = 5
MIN_SOURCES_FOR_A_CANDIDATE = 2
REGION_WORDS = {"IN": "India"}
RUPEES = "INR"

# A word worth keeping from a known candidate's name: letters, digits and a few marks (c++, 3d-printing).
_NAME_TOKEN = re.compile(r"[^\W_][\w+#.-]*", re.UNICODE)
_NOT_A_WORD = re.compile(r"[\W_]+", re.UNICODE)

_NAME_WORD = re.compile(r"[a-z]*[A-Z][A-Za-z0-9+]*|[0-9]+[A-Za-z0-9+]*")
_HAS_DIGIT = re.compile(r"[0-9]")
_BIG_NUMBER = re.compile(r"[0-9]{4,}")  # a year or a price, not a model
# Words around a product's name in a title. They end a name, and they never count as part of one.
_FILLER = frozenset(
    (  # noqa: SIM905  # one readable block of words, not one word per line
        "a about after an and are at best budget buy buying can cheap compared comparison deal deals do does for from "
        "above around below inr less more over rs rupees upto within "
        # Parts, which titles name far more often than whole products. They end a name, and are never one.
        "amd android core cpu ddr3 ddr4 ddr5 dimensity exynos gb gen gpu gtx hdd i3 i5 i7 i9 intel ios mediatek nvidia "
        "nvme oled ram rtx ryzen snapdragon ssd tb ultra windows "
        "get good great guide help how i in india is it its long my new no of on or our owner owners pick picks price "
        "prices problem problems reasons recommend recommendation recommendations review reviewed reviews "
        "should test tested tests than that the this to top under use using versus vs what which why with worth you "
        "your january february march april may june july august september october november december"
    ).split()
)

Channel = Literal["web", "review"]


@dataclass(frozen=True)
class SearchQuery:
    text: str
    goes_to: Channel


def build_queries(rubric: Rubric, known_uses: Sequence[str] = DEFAULT_KNOWN_USES) -> list[SearchQuery]:
    """4 to 6 queries. It takes only the rubric, so the shopper's raw request can't reach them."""
    category = _category(rubric.category)
    use = known_use(rubric.use_case, known_uses)
    budget = _budget_words(rubric.budget)
    region = REGION_WORDS.get(rubric.region, "")
    for_use = f" for {use}" if use else ""

    queries = [
        SearchQuery(_join("best", f"{category}{for_use}", budget, region), "web"),
        SearchQuery(_join(f"{category}{for_use}", "review", region), "review"),
        SearchQuery(_join(category, "buying guide", region), "web"),
        SearchQuery(_join(f"{category}{for_use}", "recommendations"), "web"),
    ]
    if budget:
        queries.append(SearchQuery(_join(category, budget, "review", region), "review"))
    if use:
        queries.append(SearchQuery(_join(f"{category}{for_use}", "comparison", region), "web"))
    return queries


def first_round(queries: Sequence[SearchQuery]) -> list[SearchQuery]:
    return list(queries[:MAX_SERPER_FIRST_ROUND])


def held_query(queries: Sequence[SearchQuery]) -> SearchQuery | None:
    """The query that waits for the held slot, if there is one."""
    return queries[MAX_SERPER_FIRST_ROUND] if len(queries) > MAX_SERPER_FIRST_ROUND else None


def reddit_query(rubric: Rubric, known_uses: Sequence[str] = DEFAULT_KNOWN_USES) -> str:
    """Query 4: Reddit matches its words against the cached listings, with no Serper call."""
    use = known_use(rubric.use_case, known_uses)
    for_use = f" for {use}" if use else ""
    return _join(f"{_category(rubric.category)}{for_use}", "recommendations")


def comparison_query(known: Sequence[str], titles: Iterable[tuple[str, str]], category: str) -> SearchQuery | None:
    """ "X vs Y" from the names we were given, else from the titles. None without two different names."""
    names = [cleaned for name in known if (cleaned := _clean_name(name))]
    pair = _first_pair(names) or _first_pair(candidate_names(titles, category))
    return SearchQuery(f"{pair[0]} vs {pair[1]}", "web") if pair else None


def candidate_names(titles: Iterable[tuple[str, str]], category: str) -> list[str]:
    """Names found in at least two sources, most sources first. `titles` holds (source key, title) pairs."""
    category_words = {word.lower() for word in _category(category).split()}
    category_words |= {f"{word}s" for word in category_words}
    sources_of: dict[str, set[str]] = {}
    written_as: dict[str, str] = {}
    for key, title in titles:
        for name in _names_in(title, category_words):
            folded = name.casefold()
            sources_of.setdefault(folded, set()).add(key)
            written_as.setdefault(folded, name)
    counted = [
        (len(keys), written_as[name]) for name, keys in sources_of.items() if len(keys) >= MIN_SOURCES_FOR_A_CANDIDATE
    ]
    counted.sort(key=lambda pair: (-pair[0], pair[1].casefold()))
    return _without_longer_forms([name for _, name in counted])


def _names_in(title: str, category_words: set[str]) -> set[str]:
    """Each product-like name in one title, and its shorter forms, so a longer form counts for the shorter name.

    A name starts where its run of words starts. So "Acer Swift 3" never also counts as "Swift 3", and a stray
    capitalised word just before a brand costs that title's vote, which is a miss and not a wrong name.
    """
    names: set[str] = set()
    for run in _word_runs(title, category_words):
        if run[0][0].isdigit():
            continue
        for end in range(2, min(4, len(run)) + 1):
            if _is_product_like(run[:end]):
                names.add(" ".join(run[:end]))
    return names


def _word_runs(title: str, category_words: set[str]) -> list[list[str]]:
    """Runs of neighbouring name-like words, split wherever anything else comes between them."""
    runs: list[list[str]] = []
    current: list[str] = []
    for token in title.split(" "):
        word = token.strip(".,:;!?()[]{}\"'")
        fits = (
            _NAME_WORD.fullmatch(word) is not None
            and word.lower() not in _FILLER
            and word.lower() not in category_words
        )
        if fits:
            current.append(word)
        if not fits or token != word:  # filler ends a name, and so does punctuation
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _is_product_like(words: Sequence[str]) -> bool:
    return any(_HAS_DIGIT.search(word) and not _BIG_NUMBER.fullmatch(word) for word in words)


def _without_longer_forms(names: Sequence[str]) -> list[str]:
    """A name's longer form adds nothing, so only the shortest name of a family stays where counts tie or fall."""
    kept: list[str] = []
    for name in names:
        if not any(_same_family(name, other) for other in kept):
            kept.append(name)
    return kept


def _same_family(left: str, right: str) -> bool:
    a, b = left.casefold().split(" "), right.casefold().split(" ")
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer[: len(shorter)] == shorter


def _first_pair(names: Sequence[str]) -> tuple[str, str] | None:
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            if not _same_family(first, second):
                return first, second
    return None


def _category(slug: str) -> str:
    return slug.replace("_", " ").replace("-", " ").strip()


def _join(*parts: str) -> str:
    return " ".join(part for part in parts if part)


def known_use(use_case: str | None, known_uses: Sequence[str]) -> str:
    """The first known use the shopper's words name, as we spell it, or nothing. Their own words never go further."""
    if not use_case:
        return ""
    words = " ".join(token for token in use_case.split() if "@" not in token)  # an email's domain is not a use
    plain = _NOT_A_WORD.sub(" ", words.casefold()).strip()
    text = f" {plain} "
    found = [(text.index(f" {use} "), use) for use in known_uses if f" {use} " in text]
    return min(found)[1] if found else ""


def _clean_name(name: str) -> str:
    words = [word for token in name.split() if ":" not in token for word in _NAME_TOKEN.findall(token)]
    return " ".join(words)[:MAX_NAME_CHARS].strip()


def _budget_words(budget: Budget | None) -> str:
    if budget is None:
        return ""
    low, high = budget.min, budget.max
    if (low and low.currency != RUPEES) or (high and high.currency != RUPEES):
        return ""  # budgets here are in rupees; another currency would search for the wrong thing
    if low and high:
        return f"between {low.amount:.0f} and {high.amount:.0f} rupees"
    if high:
        return f"under {high.amount:.0f} rupees"
    return f"above {low.amount:.0f} rupees" if low else ""
