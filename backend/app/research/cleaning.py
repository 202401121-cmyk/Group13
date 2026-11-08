"""One cleaning function for everything read from outside: pages, snippets, Reddit, video text (US-28, build rule 3).

It removes what a reader can't see, where a hostile page hides its orders, and the invisible characters that smuggle
them. An element hidden from readers goes with its contents. If its end tag was left out, it ends where a browser would
end it, at the next sibling or at its parent's end tag. One that truly never closes takes the rest of the page, because
reading hidden text would be worse than losing a page.

Text hidden by a stylesheet stays, because the parser has no CSS.

It then removes visible orders, because a model that can see an order might obey it, and no later check can notice a
claim it left out. This is a small, cautious list that catches plain wordings and leaves ordinary reviews alone, so a
reworded order gets through. That is why text still reaches a model only through wrap_as_data, its answer must be schema
JSON, its quotes must be verbatim, and code does the ranking. All of what's left is data, never instructions.
"""

from __future__ import annotations

import html
import json
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from itertools import islice

log = logging.getLogger(__name__)

_SKIPPED_TAGS = frozenset(
    "script style noscript noframes noembed template svg canvas iframe object video audio dialog datalist "  # noqa: SIM905
    "nav header footer aside".split()
)
_VOID_TAGS = frozenset("area base br col embed hr img input link meta source track wbr".split())  # noqa: SIM905
_PARAGRAPH_TAGS = frozenset(
    "p div ul ol dl tr table section article blockquote pre h1 h2 h3 h4 h5 h6".split()  # noqa: SIM905
)
_LINE_TAGS = frozenset("li td th dt dd".split())  # noqa: SIM905
# A block's end tag closes what is open inside it, as in a browser. An inline tag's can't, and neither can </p>,
# because a <div> inside a <p> has already ended that <p>.
_CLOSES_WHAT_IS_INSIDE = (_PARAGRAPH_TAGS | _LINE_TAGS) - {"p"}
# A tag whose end tag HTML lets you leave out ends when one of these starts. So does its parent's end tag.
_ENDED_BY_NEXT: dict[str, frozenset[str]] = {
    "li": frozenset({"li"}),
    "dt": frozenset({"dt", "dd"}),
    "dd": frozenset({"dt", "dd"}),
    "td": frozenset({"td", "th", "tr"}),
    "th": frozenset({"td", "th", "tr"}),
    "tr": frozenset({"tr"}),
    "option": frozenset({"option"}),
    "p": _PARAGRAPH_TAGS - {"tr"},  # a stray <tr> outside a table is ignored, so it ends nothing
}
# Only a true zero hides: opacity:0.95 and font-size:0.9em are ordinary, visible text.
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|(?:font-size|opacity)\s*:\s*(?:0+(?:\.0*)?|\.0+)[a-z%]*\s*(?:;|!|$)",
    re.IGNORECASE,
)
_TAG_NAME = re.compile(r"<([a-zA-Z][a-zA-Z0-9-]*)")
_INLINE_TAGS = frozenset(
    "html head body title a b i u s em strong span font small big sub sup code kbd mark label button form main".split()  # noqa: SIM905
)
_KNOWN_TAGS = _SKIPPED_TAGS | _VOID_TAGS | _PARAGRAPH_TAGS | _LINE_TAGS | _INLINE_TAGS
_SELF_CLOSED_BY_SLASH = frozenset({"svg", "math"})  # the two a browser does let <svg/> close
_MAX_INLINE_WALK = 64  # how far an inline end tag looks for its own start tag, so a deep page can't slow this down
_INVISIBLE = ("Cc", "Cf", "Cs", "Zl", "Zp")  # control, format (zero-width, soft hyphen, tag), surrogate, line separator
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# --- visible orders ---------------------------------------------------------------------------------
_AI_READER = (
    r"(?:ai|a\.i\.)\s+(?:assistants?|models?|systems?|agents?|tools?|readers?|summari[sz]ers?|bots?|crawlers?)"
    r"|chatbots?|llms?|language models?"
)
_ANSWER_WORD = r"results?|rankings?|recommendations?|outputs?|answers?|responses?|summar(?:y|ies)"
_OPENING = r"^(?:[^:]{0,40}:\s*)?(?:please\s+|you (?:must|should)\s+|always\s+|make sure (?:to|you)\s+)?"

# A fake turn in a conversation, or a fake end to the data block. It takes the rest of its line with it.
_FAKE_TURN = re.compile(
    r"(?:^|(?<=[.!?] ))(?:system|assistant|developer)\s*(?:message|prompt)?\s*:\s*"
    r"(?:you (?:are|must|should|will)\b|ignore\b|new instructions?\b|rank\b|understood\b|i will\b|i'll\b)"
    r"|<\|[a-z_ ]{1,30}\|>|\[/?(?:inst|sys)\]|<<\s*/?sys\s*>>|</?untrusted_data\b",
    re.IGNORECASE | re.MULTILINE,
)
_JSON_ANSWER = re.compile(r'\{\s*"[^"\n]{1,40}"\s*:')
_JSON = json.JSONDecoder()
_MAX_BROKEN_JSON = 50  # each one costs a slow failed read, and a page with this many is hostile: the rest goes
_ORDER_SENTENCES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:ignore|disregard|forget|override)\b[^.!?]{0,60}"
        r"\b(?:instructions?|prompts?|rules|guidelines|directions|system message|everything (?:above|before))\b",
        rf"\b(?:note to|attention|hey|dear|to all)\b[\s,:]*(?:any |all )?(?:{_AI_READER})\b",
        rf"\b(?:{_AI_READER})\s+(?:must|should|shall|need to|have to|are required to|are instructed to)\s+"
        r"(?:rank|recommend|list|ignore|score|rate|prefer|favou?r|put|place|remove|drop|exclude|say|output|answer"
        r"|reply|respond|mention)\b",
        r"\byou are (?:now )?(?:an? )?(?:ai|assistant|chatbot|language model)\b|\byou are now in \w+ mode\b",
        r"\b(?:reply|respond|answer|return|output)\s+(?:only\s+)?(?:with\s+)?(?:exactly\s+)?(?:this|the following)\s+"
        r"(?:json|text|answer)\b",
        _OPENING + r"rank\s+.{1,80}?\b(?:first|on top|at the top|number one|above (?:all|every|others))(?:\W|$)",
        _OPENING + r"(?:put|place|list|show)\s+.{1,80}?\b(?:first|on top|at the top|number one)\s+(?:in|on|of)\s+"
        r"(?:the|your|any|all|every)\b",
        _OPENING + r"(?:give|rate|score)\s+.{1,80}?\b(?:"
        rf"(?:in|on)\s+(?:your|the|any|all|every)\s+(?:{_ANSWER_WORD})|score of 10|top score|highest score)\b",
        _OPENING + r"(?:drop|remove|exclude|delete|hide|filter out|omit|leave out"
        r"|(?:do not|don['’]t|never) (?:mention|list|include|show|recommend))\s+.{1,80}?\b(?:from|in)\s+"
        rf"(?:the |your |any |all |every )?(?:{_ANSWER_WORD})\b",
    )
)


@dataclass(frozen=True)
class CleanedPage:
    title: str
    text: str


class _OpenTags:
    """A stack of open tags. Counted too, so a page of a million tags can't make each lookup slow."""

    def __init__(self) -> None:
        self.tags: list[str] = []
        self._counts: Counter[str] = Counter()

    def __contains__(self, tag: str) -> bool:
        return self._counts[tag] > 0

    def __bool__(self) -> bool:
        return bool(self.tags)

    def push(self, tag: str) -> None:
        self.tags.append(tag)
        self._counts[tag] += 1

    def close(self, tag: str) -> None:
        """Close this tag, and any left open inside it."""
        while self.tags:
            last = self.tags.pop()
            self._counts[last] -= 1
            if last == tag:
                return

    def inline_can_reach(self, tag: str) -> bool:
        """An inline end tag closes its start tag only with no block open inside it, as in a browser."""
        for open_tag in islice(reversed(self.tags), _MAX_INLINE_WALK):
            if open_tag == tag:
                return True
            if open_tag == "p" or open_tag in _CLOSES_WHAT_IS_INSIDE:
                return False
        return False

    def clear(self) -> None:
        self.tags.clear()
        self._counts.clear()


class _TextReader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.og_title = ""
        self._open = _OpenTags()  # what's open around the text we read
        self._skipping = _OpenTags()  # a hidden element, and what's open inside it
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            values = dict(attrs)
            if values.get("property") == "og:title" and not self.og_title:
                self.og_title = values.get("content") or ""
            return
        if tag == "title" and not self._skipping:
            self._in_title = True
            return
        if tag in _VOID_TAGS:
            if tag == "br" and not self._skipping:
                self.parts.append("\n")
            return
        if self._skipping:
            if len(self._skipping.tags) > 1 or tag not in _ENDED_BY_NEXT.get(self._skipping.tags[0], ()):
                self._skipping.push(tag)
                return
            self._skipping.clear()  # its end tag was left out, and this tag ends it
        if tag in _SKIPPED_TAGS or _is_hidden(attrs):
            self._skipping.push(tag)
            return
        self._open.push(tag)
        self._break(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """A browser ignores the slash in <div hidden/>, so it opens like any other start tag."""
        if tag not in _SELF_CLOSED_BY_SLASH or self._skipping:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if self._skipping:
            if tag in self._skipping:
                if tag == "p" or tag in _CLOSES_WHAT_IS_INSIDE or self._skipping.inline_can_reach(tag):
                    self._skipping.close(tag)
                return
            if tag not in _CLOSES_WHAT_IS_INSIDE or tag not in self._open:
                return
            self._skipping.clear()  # its parent's end tag ends it too
        if tag in self._open:
            self._open.close(tag)
            if tag in _PARAGRAPH_TAGS:
                self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif not self._skipping:
            self.parts.append(data)

    def settle_the_end(self) -> None:
        """Whatever the parser couldn't finish is still waiting. Python versions differ on reading it out as text, and
        a browser hides a comment or tag that never closes, so it goes. A < that starts no tag we know is plain text.
        """
        left = self.rawdata
        if not left.startswith("<"):
            return
        self.rawdata = ""
        name = _TAG_NAME.match(left)
        if left[1:2] not in ("!", "?", "/") and (name is None or name[1].lower() not in _KNOWN_TAGS):
            self.handle_data(html.unescape(left))

    def _break(self, tag: str) -> None:
        if tag in _PARAGRAPH_TAGS:
            self.parts.append("\n\n")
        elif tag in _LINE_TAGS:
            self.parts.append("\n")


def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    values = dict(reversed(attrs))  # a repeated attribute keeps its first value, as in a browser
    if "hidden" in values or values.get("aria-hidden") == "true":
        return True
    return bool(_HIDDEN_STYLE.search(values.get("style") or ""))


def _visible(text: str) -> str:
    kept = "".join(c for c in text if c in "\n\t" or unicodedata.category(c) not in _INVISIBLE)
    return unicodedata.normalize("NFC", kept)


def _paragraphs(text: str) -> list[str]:
    """Single spaces, no empty lines, and one blank line between paragraphs."""
    kept: list[str] = []
    for paragraph in _PARAGRAPH_BREAK.split(text):
        lines = (" ".join(line.split()) for line in paragraph.split("\n"))
        tidied = "\n".join(line for line in lines if line)
        if tidied:
            kept.append(tidied)
    return kept


def _is_order(sentence: str) -> bool:
    return any(pattern.search(sentence) for pattern in _ORDER_SENTENCES)


def _without_json(text: str) -> tuple[str, int]:
    """Drop the lines holding a JSON object, however it is laid out. One that never closes takes its paragraph."""
    kept: list[str] = []
    position = removed = broken = 0
    while (found := _JSON_ANSWER.search(text, position)) is not None:
        try:
            _, end = _JSON.raw_decode(text, found.start())
        except (ValueError, RecursionError):
            broken += 1
            paragraph_end = -1 if broken >= _MAX_BROKEN_JSON else text.find("\n\n", found.end())
            end = len(text) if paragraph_end == -1 else paragraph_end
        line_start = text.rfind("\n", position, found.start()) + 1 or position
        line_end = text.find("\n", end)
        kept.append(text[position:line_start])
        position = len(text) if line_end == -1 else line_end + 1
        removed += 1
    kept.append(text[position:])
    return "".join(kept), removed


def _rejoined(lines: list[str]) -> list[str]:
    """Put an order split over two lines back together, so it can be seen. Other lines stay as they are."""
    joined: list[str] = []
    for line in lines:
        if joined and joined[-1][-1:] not in (".", "!", "?", ":"):
            before, after = _SENTENCE_END.split(joined[-1])[-1], _SENTENCE_END.split(line)[0]
            if _is_order(f"{before} {after}") and not _is_order(before) and not _is_order(after):
                joined[-1] = f"{joined[-1]} {line}"
                continue
        joined.append(line)
    return joined


def _without_orders(paragraphs: list[str]) -> tuple[list[str], int]:
    kept: list[str] = []
    removed = 0
    for paragraph in paragraphs:
        lines: list[str] = []
        for line in _rejoined(paragraph.split("\n")):
            turn = _FAKE_TURN.search(line)
            if turn is not None:
                removed += 1
                line = line[: turn.start()].rstrip()
            sentences = [sentence for sentence in _SENTENCE_END.split(line) if sentence]
            clean = [sentence for sentence in sentences if not _is_order(sentence)]
            removed += len(sentences) - len(clean)
            if clean:
                lines.append(" ".join(clean))
        if lines:
            kept.append("\n".join(lines))
    return kept, removed


def _finish(text: str) -> str:
    plain, json_removed = _without_json("\n\n".join(_paragraphs(_visible(text))))
    kept, removed = _without_orders(_paragraphs(plain))
    if removed or json_removed:
        log.info("cleaning: removed %d order-like passages", removed + json_removed)  # a count: never a page's words
    return "\n\n".join(kept)


def clean_html(raw: str) -> CleanedPage:
    """A page's readable text and title. It never raises: a page we can't read must not crash a search (rule 9)."""
    reader = _TextReader()
    try:
        reader.feed(raw)
        reader.settle_the_end()
        reader.close()
    except Exception as exc:
        log.info("a page could not be parsed (%s)", type(exc).__name__)
    title = reader.og_title or "".join(reader.title_parts)
    return CleanedPage(" ".join(_finish(title).split()), _finish("".join(reader.parts)))


def clean_text(raw: str) -> str:
    """The one way retrieved text gets in, whether it arrived as HTML or as plain text."""
    return clean_html(raw).text


def decode_page(data: bytes, charset: str | None) -> str:
    """Bytes as text, whatever they are. Bad bytes become replacement characters, and an unknown charset means UTF-8."""
    try:
        return data.decode(charset or "utf-8", errors="replace")
    except (LookupError, ValueError):  # no such charset, or one like idna that can't decode a page
        return data.decode("utf-8", errors="replace")
