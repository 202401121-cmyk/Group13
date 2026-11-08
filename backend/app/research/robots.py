"""robots.txt, read the way RFC 9309 says (US-12).

Python's own parser does not: it has no wildcards, takes the first matching rule rather than the longest, ends a group
at a blank line and reads only whole-number crawl delays. So a site's "no" could read as a "yes".

Rules come in groups, each for one or more user agents. Ours is every group naming our agent, combined, or else every
group for `*`. Blank lines, comments and lines we don't know are ignored. The longest matching rule wins, and Allow wins
a tie. `*` matches anything, and a final `$` means the end of the path. Crawl-delay is not in the RFC, but sites use it
and we honour it.

Matching uses no regular expressions, so a hostile file can't stall a worker with a rule built to backtrack.
"""

from __future__ import annotations

import math
import re
import string
from dataclasses import dataclass, field
from urllib.parse import quote

BYTE_ORDER_MARK = chr(0xFEFF)
_ESCAPED = re.compile(r"%([0-9a-fA-F]{2})")
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")
_ALLOWED_IN_A_URL = _UNRESERVED | frozenset(":/?#[]@!$&'()*+,;=%")


def _normalise(text: str) -> str:
    """One spelling for one address: what a URL can't hold encoded, unreserved characters decoded, hex in upper case."""
    encoded = "".join(c if c in _ALLOWED_IN_A_URL else quote(c, safe="") for c in text)

    def one(found: re.Match[str]) -> str:
        char = chr(int(found.group(1), 16))
        return char if char in _UNRESERVED else f"%{found.group(1).upper()}"

    return _ESCAPED.sub(one, encoded)


@dataclass(frozen=True)
class _Rule:
    allow: bool
    length: int
    parts: tuple[str, ...]  # the rule's text between its wildcards
    anchored: bool

    @classmethod
    def read(cls, allow: bool, written: str) -> _Rule:
        text = _normalise(written)
        anchored = text.endswith("$")
        body = (text[:-1] if anchored else text).replace("$", "%24")  # only a final $ means the end
        return cls(allow, len(text), tuple(body.split("*")), anchored)

    def matches(self, path: str) -> bool:
        if not path.startswith(self.parts[0]):
            return False
        at = len(self.parts[0])
        for part in self.parts[1:-1]:  # the earliest place for each part leaves the most room for the next
            found = path.find(part, at)
            if found < 0:
                return False
            at = found + len(part)
        if len(self.parts) == 1:
            return at == len(path) or not self.anchored
        last = self.parts[-1]
        if self.anchored:
            return len(path) - len(last) >= at and path.endswith(last)
        return path.find(last, at) >= 0


@dataclass
class _Group:
    rules: list[_Rule] = field(default_factory=list)
    delay: float = 0.0
    found: bool = False


def _delay(written: str) -> float:
    """The seconds asked for, or nothing if it isn't a usable number."""
    try:
        seconds = float(written)
    except ValueError:
        return 0.0
    return seconds if math.isfinite(seconds) else 0.0  # a negative one loses to the group's 0 anyway


class RobotsRules:
    """What one robots.txt tells one user agent. A file with no group for it allows everything."""

    def __init__(self, text: str, agent: str) -> None:
        mine, everyone = _Group(), _Group()
        name = agent.casefold()
        agents: set[str] = set()
        in_rules = False
        for raw in text.removeprefix(BYTE_ORDER_MARK).splitlines():
            label, colon, written = raw.split("#", 1)[0].partition(":")
            if not colon:
                continue
            key, value = label.strip().casefold(), written.strip()
            if key == "user-agent":
                if in_rules:  # a rule has been read, so this agent starts a new group
                    agents, in_rules = set(), False
                agents.add(value.split("/")[0].strip().casefold())
                mine.found = mine.found or name in agents
                everyone.found = everyone.found or "*" in agents
            elif key in ("allow", "disallow", "crawl-delay"):
                in_rules = True
                groups = [g for g, says in ((mine, name in agents), (everyone, "*" in agents)) if says]
                for group in groups:
                    if key == "crawl-delay":
                        group.delay = max(group.delay, _delay(value))
                    elif value:  # an empty Disallow says nothing
                        group.rules.append(_Rule.read(key == "allow", value))
        ours = mine if mine.found else everyone
        self._rules = ours.rules
        self.crawl_delay = ours.delay

    def allowed(self, path: str) -> bool:
        """`path` is the URL's path and query, as sent."""
        target = _normalise(path or "/").replace("*", "%2A").replace("$", "%24")  # in a path they are plain characters
        best: _Rule | None = None
        for rule in self._rules:
            if rule.matches(target) and (
                best is None or rule.length > best.length or (rule.length == best.length and rule.allow)
            ):
                best = rule
        return best is None or best.allow
