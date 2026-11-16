"""Stage commerce (US-24): the current price and a buy link for the exact model of the top products."""

from .stage import CommerceStage, ShoppingCommerceStage


def default_commerce_stage() -> ShoppingCommerceStage:
    """Real prices from Serper shopping; the stub stays its fallback. Tests swap in the stub (tests/conftest.py)."""
    return ShoppingCommerceStage()


__all__ = ["CommerceStage", "ShoppingCommerceStage", "default_commerce_stage"]
