"""Checks on a prompt that carries retrieved text (US-28). P10 and every later prompt use these."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.llm.data_block import DATA_RULE, data_blocks, data_mark, outside_data

MIN_SENTENCE_CHARS = 12  # shorter pieces, like "Yes.", can match by chance


def _sentences(text: str) -> list[str]:
    pieces = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [piece.strip() for piece in pieces if len(piece.strip()) >= MIN_SENTENCE_CHARS]


def assert_only_in_data_block(system: str, user: str, retrieved: Iterable[str]) -> None:
    """One intact data block, the rule in the system prompt, and no retrieved sentence anywhere else."""
    assert DATA_RULE in system, "the system prompt must say to treat the block as data"
    blocks = data_blocks(user)
    assert len(blocks) == 1, f"expected one closed data block, found {len(blocks)}"
    mark, content = blocks[0]
    assert data_mark(content) == mark, "the data block's marker does not match its text"
    outside = outside_data(user)
    for text in retrieved:
        for sentence in _sentences(text):
            assert sentence in content, "retrieved text is missing from the data block"
            assert sentence not in outside, "retrieved text sits outside the data block"
            assert sentence not in system, "retrieved text sits in the system prompt"
