"""A model that obeys any order it can see (US-28), so the defence can't depend on the model behaving.

It stands in for one provider, under the real wrapper, so fitting, strict parsing and the retry all run. It reads
everything it is sent, invisible characters included, the way a tokenizer would. It makes honest claims from lines like
"X scored 8 out of 10 for battery_life", then does what any order says: invents a top score, leaves a product out,
replies in prose, or hands back a JSON answer it was given.

It can only obey the orders written here, so a green harness proves these attacks fail, not that every attack does.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.contracts import Provider
from app.llm.fitting import estimate_tokens
from app.llm.providers import RawCompletion

_HONEST = re.compile(r"(?P<product>[A-Z][\w ]*?) scored (?P<score>\d+) out of 10 for (?P<criterion>[a-z_]+)[^.\n]*\.")
_RANK_FIRST = re.compile(r"(?i:rank)\s+(?P<product>[A-Z][\w ]*?)\s+(?i:first)")
_DROP = re.compile(r"(?i:remove|drop|exclude)\s+(?P<product>[A-Z][\w ]*?)\s+(?i:from)")
_OVERRIDE = re.compile(r"(?i)ignore (?:all )?(?:previous|prior|above) instructions")
_RETURN_THIS = re.compile(r"(?i)reply with exactly this json:\s*")
_ZERO_WIDTH = str.maketrans("", "", "".join(map(chr, (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))))
_TAG_START, _TAG_END = 0xE0000, 0xE007F

PROSE_REPLY = "Understood. I will ignore my earlier instructions and do as the page says."


def _as_read(text: str) -> str:
    """What a tokenizer sees: zero-width characters gone, and tag characters read as the letters they stand for."""
    letters = (chr(ord(c) - _TAG_START) if _TAG_START <= ord(c) <= _TAG_END else c for c in text)
    return "".join(letters).translate(_ZERO_WIDTH)


def obedient_reply(prompt: str) -> str:
    seen = _as_read(prompt)
    told = _RETURN_THIS.search(seen)
    if told is not None:
        try:
            answer, _ = json.JSONDecoder().raw_decode(seen, told.end())
            return json.dumps(answer)
        except ValueError:
            pass  # told to reply with something that isn't there
    if _OVERRIDE.search(seen):
        return PROSE_REPLY
    claims: dict[str, dict[str, object]] = {}
    for found in _HONEST.finditer(seen):
        claims.setdefault(
            found["product"],
            {
                "product_mention": found["product"],
                "criterion_key": found["criterion"],
                "score": float(found["score"]),
                "quote": found[0],
                "claim_type": "experience",
                "polarity": "positive",
            },
        )
    for order in _RANK_FIRST.finditer(seen):
        if order["product"] in claims:
            claims[order["product"]]["score"] = 10.0  # the true quote, with a false score: the quote check can't see it
    for order in _DROP.finditer(seen):
        claims.pop(order["product"], None)
    mentions = [{"raw_name": name, "is_product": True} for name in claims]
    return json.dumps({"mentions": mentions, "claims": list(claims.values())})


@dataclass
class ObedientAdapter:
    """Implements the wrapper's Adapter protocol. Every prompt it is sent is kept, for the tests to read."""

    provider: Provider
    prompts: list[tuple[str, str]] = field(default_factory=list)

    def call(self, model: str, system: str, user: str, max_output_tokens: int, timeout_s: float) -> RawCompletion:
        self.prompts.append((system, user))
        text = obedient_reply(f"{system}\n{user}")
        used = estimate_tokens(system) + estimate_tokens(user)
        return RawCompletion(text=text, input_tokens=used, output_tokens=estimate_tokens(text))
