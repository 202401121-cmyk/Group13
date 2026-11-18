"""Stage enrich (US-41): prices from store listings for products held back only by an unknown price."""

from .stage import EnrichStage, PriceLookupStage
from .targets import price_targets, wants_prices


def default_enrich_stage() -> PriceLookupStage:
    """The real stage: Serper shopping prices (US-41). The stub is its fallback."""
    return PriceLookupStage()


__all__ = ["EnrichStage", "PriceLookupStage", "default_enrich_stage", "price_targets", "wants_prices"]
