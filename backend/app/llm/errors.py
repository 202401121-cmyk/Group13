"""What can go wrong in one LLM call (US-26).

Messages name the provider and the HTTP status only. They never include the API key, the prompt or the
response body: some providers echo part of the key back in their error bodies.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from app.contracts import Provider

# The LLMCallRecord outcomes a single attempt can end with. skipped_budget: not sent, the daily budget was spent.
AttemptOutcome = Literal["ok", "invalid_output", "rate_limited", "timeout", "error", "skipped_budget"]


class ProviderFailure(Exception):
    """The provider did not give us a usable reply. The wrapper moves on to the next provider."""

    outcome: AttemptOutcome = "error"

    def __init__(self, provider: Provider, detail: str, ratelimit_headers: Mapping[str, str] | None = None) -> None:
        super().__init__(f"{provider.value}: {detail}")
        self.provider = provider
        self.ratelimit_headers = dict(ratelimit_headers or {})


class RateLimited(ProviderFailure):
    """HTTP 429."""

    outcome = "rate_limited"


class ProviderTimeout(ProviderFailure):
    """No reply within the request timeout."""

    outcome = "timeout"


class ServerError(ProviderFailure):
    """HTTP 5xx."""


class AuthFailed(ProviderFailure):
    """HTTP 401 or 403: the key is missing, wrong or not allowed to use this model."""


class NoCredit(ProviderFailure):
    """HTTP 402: the account needs a plan or more credits before it answers again."""


class ProviderError(ProviderFailure):
    """Any other failure: another HTTP status, a network error or a reply we cannot read."""


class InvalidOutput(ValueError):
    """The model replied, but not with a JSON object that matches the schema. Never guessed or repaired."""
