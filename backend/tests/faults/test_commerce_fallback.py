"""US-24: the commerce stage's fallback. Its run() raising ends in an honest result: no offers, a store search for
each product, the report FALLBACK, and never the sample label (the fallback's answer is real, not an example)."""

from __future__ import annotations

from app import contracts as C
from app.commerce import ShoppingCommerceStage
from app.contracts import CommerceInput, StageContext
from app.contracts import examples as ex
from app.pipeline import made_by_placeholder, run_stage
from app.research.shopping import ShoppingResult
from app.research.usage import SearchUsage


class Broken:
    def search(self, query: str, usage: SearchUsage) -> ShoppingResult:
        raise RuntimeError("a made-up crash in the shopping client")


def test_a_crash_falls_back_to_store_searches_with_no_offers() -> None:
    stage = ShoppingCommerceStage(Broken())
    products = ex.match().products[:4]
    out, report = run_stage(stage, CommerceInput(products=products), StageContext(search_id="srch_commerce02"), 15)
    assert report.status is C.StageStatus.FALLBACK
    assert out is not None and out.offers == []
    assert set(out.no_offer) == {p.id for p in products}
    assert C.CommerceResult.model_validate_json(out.model_dump_json()) == out
    assert not made_by_placeholder(stage, report)  # an honest fallback is not a sample
