"""Prompt text for reading one source (US-14, US-28).

Retrieved text goes only inside wrap_as_data. Our own lists go before it, in the user message.

A public call may reach a provider that trains on inputs (build rule 8), so what's in it matters. A criterion key or
category label from our config holds nothing the shopper typed, so it may go in any call. Anything else may have come
from their words: a generated key can be a slug made from their use case, and an unknown category can be what they
typed. Those go only into Reddit calls, whose route has no provider that trains.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.contracts import DataClass, ExtractInput, Source, SourceType
from app.criteria.fallback import fallback_criteria
from app.intake.lexicon import load_lexicon
from app.llm.data_block import DATA_RULE, wrap_as_data

SYSTEM_PROMPT = f"""You read one source about products and report what it says. You never judge, rank or recommend.

Fill in:
- mentions: at most 15. Each product the source names, as written. is_product is true for a product someone can buy,
  such as "Nova Book 14", and false for a kind of product, such as "memory foam".
- claims: at most 10, the most useful ones. Each is one statement about one product or kind of product, on one of the
  criteria listed in the message:
  - product_mention: its name, as written in the source.
  - is_product_type: true if it is a kind of product and not one you can buy.
  - criterion_key: the key from the list, exactly.
  - value: a measured fact, as a number or a few words, with unit if it has one, such as 1.4 with unit kg. For a yes or
    no fact, use "yes" or "no".
  - score: an opinion, from 0 (very bad) to 10 (excellent), such as about 8 for "great" and about 3 for "poor". Anything
    that is not a measured fact is a score. Give a value or a score, never both.
  - quote: the source's own words, copied exactly, at most 25 words.
  - polarity: positive, negative or neutral.

Leave out any field you do not use. If no listed criterion fits a statement, leave that statement out. Never guess.
If the source says nothing useful, return empty lists.

{DATA_RULE}"""

_LABELS = {SourceType.REDDIT: "Reddit text", SourceType.YOUTUBE: "video text"}
# A title isn't counted in the source's cap, so a long one could push the data block's end past what fits a call.
MAX_TITLE_CHARS = 100
NO_CRITERIA = "No criteria for this source: list the products it names, and leave claims empty."


@dataclass(frozen=True)
class PromptCriteria:
    """What this one call may carry. A claim on any other key is dropped, because the model was never asked for it."""

    keys: tuple[str, ...]
    lines: tuple[str, ...]
    category: str | None


def _words(slug: str) -> str:
    return slug.replace("_", " ")


def allowed_criteria(inp: ExtractInput, data_class: DataClass) -> PromptCriteria:
    """Lexicon keys and labels go in any call. Anything else goes only in a call no provider that trains can see."""
    slug = inp.category_label
    lexicon = load_lexicon()
    fixed = {c.key: c.definition for c in fallback_criteria(slug).criteria}
    may_hold_their_words = data_class is not DataClass.PUBLIC
    keys = tuple(key for key in inp.criterion_keys if key in fixed or may_hold_their_words)
    lines = tuple(f"- {key}: {fixed.get(key, _words(key))}" for key in keys)
    if slug in lexicon.categories:
        return PromptCriteria(keys, lines, lexicon.label(slug))
    return PromptCriteria(keys, lines, _words(slug) if may_hold_their_words and slug else None)


def source_text(source: Source) -> str:
    """What a reader of this source sees, best comments first, as capping left them. A claim must quote from this."""
    return "\n\n".join([source.text, *(comment.text for comment in source.comments)])


def user_message(source: Source, criteria: PromptCriteria) -> str:
    """The title is context for the model, but no claim can rest on it: a page's title can be invisible on the page."""
    title = source.title[:MAX_TITLE_CHARS]
    block = f"{title}\n\n{source_text(source)}" if title else source_text(source)
    head = [f"Category: {criteria.category}"] if criteria.category else []
    head += ["Criteria:", *criteria.lines] if criteria.lines else [NO_CRITERIA]
    return "\n".join(head) + "\n\n" + wrap_as_data(block, _LABELS.get(source.type, "web text"))
