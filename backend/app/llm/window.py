"""The extraction send window (US-46, S4-29): until when a search still sends new sources.

Extraction sends a public page only when a public provider has room, and only while the search has time left for the
later stages. Inside that window a public page may wait for Groq's room, its last resort, instead of being skipped.
A context variable carries the window to the wrapper, as the token meter does, so the LLMClient contract is unchanged
and a call made outside a window (enrich, anything else) behaves as before.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_until: ContextVar[float | None] = ContextVar("llm_send_window_until", default=None)


@contextmanager
def send_window(until: float | None) -> Iterator[None]:
    """Calls made inside this block belong to a send window ending at `until` (the client's clock), or none (None)."""
    token = _until.set(until)
    try:
        yield
    finally:
        _until.reset(token)


def window_until() -> float | None:
    """When the current send window ends, or None outside one."""
    return _until.get()
