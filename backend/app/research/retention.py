"""Reddit data may be kept at most 24 hours (D-53), but a saved search stays until someone deletes it.

What counts is the data class, never the source type alone (US-38): a YouTube video whose text holds its viewers'
comments carries the reddit class (P-23), so it is stripped the same way, with its own marker, and so are its quotes
and the words resting on them. A YouTube video with no comments is public, and is kept.

So Reddit's words never reach the saved row. A Reddit source keeps what says it was read: its URL, community, fetch
time, archive and counts, and each comment's ID, place in the thread and upvotes. Its title, text and comment bodies
become the marker, and its author hashes go, as a hash can follow a person from thread to thread. This runs at the save
(DatabaseResultStore.save), and in one other place: the final event a progress stream still sends once its run has been
dropped at its keep-time (app/api/search_runs.py), so that copy holds no Reddit text either. Nowhere else. So the search
itself, and the result the shopper sees, are untouched, except in that one late final event.

A quote stays only if it names a source we know isn't Reddit. A claim keeps its value, score and source link, and its
quote becomes the marker. SearchResult.citations (1.7.0) holds quotes, and a citation's quote is optional: from Reddit,
or from a source we can't name, it is dropped (None), so the evidence route can say it was withheld.

Since S3-10b verify is real, so a resolved value can be a claim's own words ("about 6 hours on a full charge"). A string
value, or an alternative's, is replaced by the marker unless every claim it rests on is cited from a source we know
isn't Reddit; the claims behind it are read from SearchResult.citations. Numbers and true/false are not words, so they
stay, and so do the claim ids. An alternative's unit is too short for the marker, so it is dropped instead (None).

What the filter read (DecideResult.checked_values, 1.10.0) and a shown product's rule notes repeat those values: for a
product and attribute whose value was replaced, every string the filter read becomes the same marker, and the product's
rule notes go. A filter key never rests on Reddit or forum words (S3-10b), but a video with viewers' comments is
editorial, so its words can (P-23). A held product's filter reason can quote such a value too, up to 80 characters
("It fails "Wi-Fi 7": it has ..."): a reason about an attribute whose value was replaced is saved as the plain reason
built from its rule instead ('It fails "Wi-Fi 7"', or 'We couldn't check "Wi-Fi 7" from its sources'), matched by the
rule it quotes or the attribute it names, so a saved row keeps no words from Reddit or a video's comments in any field
(D-53, D-70; S5-2).

Product names are names, not a poster's words, so they stay from any source (US-15; Om, 11 Nov 2026). Community notes
hold no Reddit text yet: nothing fills them until US-42. tests/unit/research/test_retention.py lists every text field in
a SearchResult, and fails on a new one, or when US-20 or US-42 makes real the stages whose fields could hold them.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from app.decide.units import attribute_words, number_text

from .dedupe import split_merged
from .reddit.subreddits import load_subreddits

REDDIT_TEXT_NOT_KEPT = "[Reddit text is not kept]"
VIDEO_TEXT_NOT_KEPT = "[Video text with viewers' comments is not kept]"
QUOTED_VALUE_CHARS = 80  # decide quotes a text value in a reason up to this long (app/decide/rules.py _value_text)
MAX_REASONS = 10  # FilteredProduct.reasons
NOT_CHECKED_RULE = {  # when no rule can be named: still no one's words
    "filtered_out": "It fails one of your must-haves",
    "cant_tell": "We couldn't check one of your must-haves",
}


@dataclass
class _Replaced:
    """A product and attribute's words that were replaced, and the marker they became."""

    marker: str
    words: set[str] = field(default_factory=set)


def without_reddit_text(result: dict[str, Any]) -> dict[str, Any]:
    """A copy of a finished search, as JSON, with no Reddit text in it."""
    saved = copy.deepcopy(result)
    sources = saved.get("sources")
    if not isinstance(sources, list):
        _strip_decision(saved, _strip_values(saved, set()))  # with no sources, none can be known not to be Reddit
        _drop_cited_quotes(saved, set())
        return saved
    others: set[Any] = set()
    markers: dict[Any, str] = {}  # a stripped source's id -> the marker its words become
    for source in sources:
        if not isinstance(source, dict):
            continue
        if source.get("type") == "reddit":
            _strip_source(source)
        elif source.get("data_class") == "reddit":  # viewers' words on a video (P-23)
            _strip_video(source)
            markers[source.get("id")] = VIDEO_TEXT_NOT_KEPT
        else:
            others.add(source.get("id"))
    _strip_decision(saved, _strip_values(saved, others, markers))
    _strip_quotes(saved, others, markers)
    _drop_cited_quotes(saved, others)
    return saved


@lru_cache(maxsize=1)
def _searched_subreddits() -> frozenset[str]:
    """Every subreddit research lists threads from, so the only ones a merged thread can be in. None if unreadable."""
    try:
        config = load_subreddits()
    except Exception:  # a save must never fail on this: with no list, no merged line can be kept
        return frozenset()
    names = [name for names in config.categories.values() for name in names]
    names += [name for group in config.shared for name in group.subreddits]
    return frozenset(name.lower() for name in names)


def _kept_link(line: str) -> bool:
    """A "Same topic" line names a thread in one of our own subreddits, so it holds an address and no free words."""
    subreddit = line.removeprefix("Same topic: https://www.reddit.com/r/").split("/", 1)[0]
    return subreddit.lower() in _searched_subreddits()


def _strip_source(source: dict[str, Any]) -> None:
    # A merged thread's other URL survives (dedupe.py): only the lines at the end, in the exact form we write, as dedupe
    # reads them. A poster's own line in that form, anywhere else, goes. One at the end survives only if it names a
    # thread in a subreddit we search, so the most it can hold is a thread's ID: an address, never a sentence.
    _, merged = split_merged(str(source.get("text", "")))
    text = "\n".join([REDDIT_TEXT_NOT_KEPT, *(line for line in merged if _kept_link(line))])
    comments = source.get("comments")
    source.update(
        title=REDDIT_TEXT_NOT_KEPT,
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
        comments=[_strip_comment(c) for c in comments] if isinstance(comments, list) else [],
        author_hash=None,
    )


def _strip_video(source: dict[str, Any]) -> None:
    """A video with viewers' comments: its title, description and comments go, its link and dates stay."""
    source.update(
        title=VIDEO_TEXT_NOT_KEPT,
        text=VIDEO_TEXT_NOT_KEPT,
        text_hash=hashlib.sha256(VIDEO_TEXT_NOT_KEPT.encode()).hexdigest(),
        author_hash=None,
    )


def _strip_comment(comment: Any) -> Any:
    return {**comment, "text": REDDIT_TEXT_NOT_KEPT, "author_hash": None} if isinstance(comment, dict) else None


def _claims_kept(saved: dict[str, Any], others: set[Any]) -> set[Any]:
    """The claims cited from a source we know isn't Reddit: only words resting on these alone may be kept."""
    citations = saved.get("citations")
    if not isinstance(citations, list):
        return set()
    return {c.get("claim_id") for c in citations if isinstance(c, dict) and c.get("source_id") in others}


def _may_keep(claim_ids: Any, kept: set[Any]) -> bool:
    return isinstance(claim_ids, list) and bool(claim_ids) and all(i in kept for i in claim_ids)


def _cited_from(saved: dict[str, Any]) -> dict[Any, Any]:
    """Each claim's source, from SearchResult.citations."""
    citations = saved.get("citations")
    if not isinstance(citations, list):
        return {}
    return {c.get("claim_id"): c.get("source_id") for c in citations if isinstance(c, dict)}


def _marker(claim_ids: Any, kept: set[Any], cited: dict[Any, Any], markers: dict[Any, str]) -> str:
    """The video marker when every claim that can't be kept is from a video with comments, else Reddit's."""
    lost = [i for i in claim_ids if i not in kept] if isinstance(claim_ids, list) else []
    found = {markers.get(cited.get(i), REDDIT_TEXT_NOT_KEPT) for i in lost}
    return found.pop() if len(found) == 1 else REDDIT_TEXT_NOT_KEPT


def _strip_values(
    saved: dict[str, Any], others: set[Any], markers: dict[Any, str] | None = None
) -> dict[tuple[Any, Any], _Replaced]:
    """A resolved value's words, and its alternatives', unless every claim behind them is cited from a source we know
    isn't Reddit or a video's viewers. A number or true/false is not words, so it stays. Answers each product and
    attribute whose words were replaced, with the marker they became and the words they were."""
    replaced: dict[tuple[Any, Any], _Replaced] = {}
    products = saved.get("products")
    if not isinstance(products, list):
        return replaced
    kept = _claims_kept(saved, others)
    cited = _cited_from(saved)
    markers = markers or {}
    for summary in products:
        values = summary.get("values") if isinstance(summary, dict) else None
        for value in values if isinstance(values, list) else []:
            if not isinstance(value, dict):
                continue
            supporting = value.get("supporting_claim_ids")
            pair = (value.get("product_id"), value.get("criterion_key"))
            if isinstance(value.get("value"), str) and not _may_keep(supporting, kept):
                marker = _marker(supporting, kept, cited, markers)
                replaced.setdefault(pair, _Replaced(marker)).words.add(value["value"])
                replaced[pair].marker = marker
                value["value"] = marker
            alternatives = value.get("alternatives")
            for alternative in alternatives if isinstance(alternatives, list) else []:
                if not isinstance(alternative, dict) or _may_keep(alternative.get("claim_ids"), kept):
                    continue
                marker = _marker(alternative.get("claim_ids"), kept, cited, markers)
                if isinstance(alternative.get("value"), str):
                    replaced.setdefault(pair, _Replaced(marker)).words.add(alternative["value"])
                    alternative["value"] = marker
                unit, number = alternative.get("unit"), alternative.get("value")
                if unit is not None:
                    alternative["unit"] = None  # too short for the marker (16 characters at most)
                    side = replaced.setdefault(pair, _Replaced(marker))
                    if isinstance(number, int | float) and not isinstance(number, bool):
                        side.words.add(f"{number_text(float(number))} {unit}")  # as decide's sides() writes it
    return replaced


def _strip_decision(saved: dict[str, Any], replaced: dict[tuple[Any, Any], _Replaced]) -> None:
    """What the filter read repeats a value's words: for a product and attribute whose value was replaced, every string
    it read becomes that marker (numbers stay), and the product's rule notes go (1.10.0). A held product's reason that
    quotes those words becomes the plain reason built from its rule (S5-2)."""
    decision = saved.get("decision")
    if not replaced or not isinstance(decision, dict):
        return
    checked = decision.get("checked_values")
    for read in checked if isinstance(checked, list) else []:
        found = replaced.get((read.get("product_id"), read.get("attribute"))) if isinstance(read, dict) else None
        if found is not None and isinstance(read.get("values"), list):
            read["values"] = [found.marker if isinstance(v, str) else v for v in read["values"]]
    touched = {product_id for product_id, _ in replaced}
    ranked = decision.get("ranked")
    for item in ranked if isinstance(ranked, list) else []:
        if isinstance(item, dict) and item.get("product_id") in touched and item.get("rule_notes"):
            item["rule_notes"] = []
    _strip_reasons(decision, replaced, _hard_rules(saved))


def _hard_rules(saved: dict[str, Any]) -> list[dict[str, Any]]:
    request = saved.get("request")
    rubric = request.get("rubric") if isinstance(request, dict) else None
    rules = rubric.get("hard_rules") if isinstance(rubric, dict) else None
    return [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []


def _strip_reasons(
    decision: dict[str, Any], replaced: dict[tuple[Any, Any], _Replaced], rules: list[dict[str, Any]]
) -> None:
    """A held product's reason about an attribute whose value was replaced becomes the plain reason built from its rule
    (S5-2). Each reason is matched by what decide writes (app/decide/rules.py), never by loose words:
    - one that quotes an active rule ('It fails "Wi-Fi 7": ...') is about that rule's attribute;
    - "Sources disagree on its Wi-Fi: one says ..." names the attribute in words;
    - "Sources disagree: A or B" names none, so it is the attribute whose replaced words it lists.
    One reason becomes one plain reason, so the list never grows. A reason about another attribute, a budget on its
    own numbers or a leave-out stays as it was. Last, any other reason holding a replaced value's words as a whole
    becomes the plain reason that names no rule."""
    held = decision.get("filtered")
    active = _active(rules)
    for item in held if isinstance(held, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("reasons"), list):
            continue
        pid = item.get("product_id")
        mine = {attribute: found for (product_id, attribute), found in replaced.items() if product_id == pid}
        if mine:
            item["reasons"] = list(dict.fromkeys(_plain(r, mine, active) for r in item["reasons"]))[:MAX_REASONS]


def _active(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Active rules with their words, once each, as decide checks them."""
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for rule in rules:
        if rule.get("active", True) is not False and rule.get("source_text") and rule.get("id") not in seen:
            seen.add(rule.get("id"))
            out.append(rule)
    return out


def _plain(reason: Any, mine: dict[Any, _Replaced], rules: list[dict[str, Any]]) -> Any:
    """`reason` as saved: itself, or the plain reason for the replaced attribute it is about."""
    if not isinstance(reason, str):
        return reason
    fails = reason.startswith("It fails")
    for rule in rules:
        if f'"{rule["source_text"]}"' in reason:
            return _plain_for_rule(rule, fails) if rule.get("attribute") in mine else reason
    for attribute, found in mine.items():
        words = attribute_words(str(attribute))
        if reason.startswith(f"Sources disagree on its {words}: "):
            return _plain_for_attribute(attribute, rules, fails)
        if reason.startswith("Sources disagree: ") and _lists(reason.removeprefix("Sources disagree: "), found):
            return _plain_for_attribute(attribute, rules, fails)
    if any(_holds(reason, word) for found in mine.values() for word in _quoted(found)):
        return NOT_CHECKED_RULE["filtered_out" if fails else "cant_tell"]
    return reason


def _plain_for_rule(rule: dict[str, Any], fails: bool) -> str:
    said = rule["source_text"]
    return f'It fails "{said}"' if fails else f'We couldn\'t check "{said}" from its sources'


def _plain_for_attribute(attribute: Any, rules: list[dict[str, Any]], fails: bool) -> str:
    """The one active rule on the attribute, else the attribute in words: still no one's words."""
    on_it = [rule for rule in rules if rule.get("attribute") == attribute]
    if len(on_it) == 1:
        return _plain_for_rule(on_it[0], fails)
    words = attribute_words(str(attribute))
    return f"It fails your must-have on its {words}" if fails else f"We couldn't check its {words} from its sources"


def _quoted(found: _Replaced) -> set[str]:
    """The replaced words as decide quotes them: stripped, at most 80 characters."""
    return {text for word in found.words if (text := str(word).strip()[:QUOTED_VALUE_CHARS])}


def _lists(named: str, found: _Replaced) -> bool:
    """Does "A, B or C" (decide's either()) list one of the replaced words as a whole item?"""
    head, _, last = named.rpartition(" or ")
    items = {item.strip() for item in [*head.split(", "), last] if item.strip()}
    return bool(items & _quoted(found)) or any(_holds(named, word) for word in _quoted(found))


def _holds(text: str, word: str) -> bool:
    """`word` in `text` as a whole: not "8" inside "₹80,000"."""
    return re.search(rf"(?<![\w.]){re.escape(word)}(?![\w.])", text) is not None


def _drop_cited_quotes(saved: dict[str, Any], others: set[Any]) -> None:
    """A citation's quote is optional, so one that can't be kept is dropped, not replaced by the marker."""
    citations = saved.get("citations")
    if not isinstance(citations, list):
        return
    for citation in citations:
        if isinstance(citation, dict) and citation.get("source_id") not in others:
            citation["quote"] = None


def _strip_quotes(node: Any, others: set[Any], markers: dict[Any, str] | None = None) -> None:
    """Keep a quote only if its source is one we know isn't Reddit or a video's viewers: if we can't tell, it can't
    be kept."""
    markers = markers or {}
    if isinstance(node, dict):
        if "quote" in node and node.get("source_id") not in others:
            node["quote"] = markers.get(node.get("source_id"), REDDIT_TEXT_NOT_KEPT)
        for value in node.values():
            _strip_quotes(value, others, markers)
    elif isinstance(node, list):
        for value in node:
            _strip_quotes(value, others, markers)
