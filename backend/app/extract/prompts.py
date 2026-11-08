"""Prompt text for reading one source (US-28). P10 (US-14) owns the real wording; the rules here stay.

Retrieved text goes only inside wrap_as_data. This prompt may be sent as public, to a provider that trains on inputs,
so nothing that can hold the shopper's words goes in it (build rule 8). That rules out the category label, which can be
what they typed, and the criterion keys, which can be slugs a model made from their use case. P10 must settle how to
give the model the keys before wiring this in: lexicon keys only, or the private class.
"""

from __future__ import annotations

from app.contracts import Source, SourceType
from app.llm.data_block import DATA_RULE, wrap_as_data

SYSTEM_PROMPT = f"""You read one source about products and list what it says. You do not recommend or rank anything.

Fill in:
- mentions: each product the source names, as written.
- claims: each statement about one product, with quote set to the source's exact words.

{DATA_RULE}"""

_LABELS = {SourceType.REDDIT: "Reddit text", SourceType.YOUTUBE: "video text"}


def source_text(source: Source) -> str:
    """What a reader of this source sees, best comments first, as capping left them. A claim must quote from this."""
    return "\n\n".join([source.text, *(comment.text for comment in source.comments)])


def user_message(source: Source) -> str:
    """The title is context for the model, but no claim can rest on it: a page's title can be invisible on the page."""
    block = f"{source.title}\n\n{source_text(source)}" if source.title else source_text(source)
    return wrap_as_data(block, _LABELS.get(source.type, "web text"))
