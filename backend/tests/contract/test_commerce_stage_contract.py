"""US-24: the real commerce stage keeps its contract. CommerceInput in, a CommerceResult out that survives a JSON round
trip, with no example offer in it; and its fallback's answer is a valid result too (backend rules: a contract test)."""

from __future__ import annotations

from datetime import UTC, datetime

from app import contracts as C
from app.commerce import ShoppingCommerceStage
from app.contracts import examples as ex
from app.research.settings import ProviderCap
from app.research.shopping import Listing, ShoppingResult
from app.research.usage import SearchUsage, UsageBook

FETCHED = datetime(2026, 11, 15, 9, 30, tzinfo=UTC)


class OneListing:
    def search(self, query: str, usage: SearchUsage) -> ShoppingResult:
        title = f"{query} (16GB RAM, 512GB SSD) Laptop"
        listing = Listing(title=title, link="https://store.example/item", store="Made-up Store", price="₹64,990")
        return ShoppingResult(listings=(listing,), fetched_at=FETCHED)


def test_the_real_stage_takes_and_gives_its_contracts() -> None:
    book = UsageBook({"serper": ProviderCap(per_search=6, per_day=60)}, path=None)
    stage = ShoppingCommerceStage(OneListing(), usage=book)
    inp = C.CommerceInput(products=ex.match().products[:4], budget=ex.rubric().budget)
    out = stage.run(
        C.CommerceInput.model_validate_json(inp.model_dump_json()), C.StageContext(search_id="srch_contract1")
    )
    assert isinstance(out, C.CommerceResult)
    assert C.CommerceResult.model_validate_json(out.model_dump_json()) == out
    assert all(o.title_matched and o.as_of == FETCHED for o in out.offers)
    assert {o.product_id for o in out.offers} | set(out.no_offer) == {p.id for p in inp.products}
    assert out != ex.commerce()


def test_its_fallback_gives_a_valid_result() -> None:
    inp = C.CommerceInput(products=ex.match().products[:3], budget=ex.rubric().budget)
    out = ShoppingCommerceStage(OneListing()).fallback(
        inp, C.StageContext(search_id="srch_contract2"), RuntimeError("x")
    )
    assert C.CommerceResult.model_validate_json(out.model_dump_json()) == out
    assert out.offers == [] and set(out.no_offer) == {p.id for p in inp.products}
