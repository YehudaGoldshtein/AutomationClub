"""Tests for BambinoApiAdapter (Phase 2).

Single-GET master feed, driven by a fake httpx transport (no network):
  GET https://api.bambinok.com/cache/Bambino
"""
from __future__ import annotations

from decimal import Decimal

import httpx

from inventory_sync.adapters.bambino import BambinoApiAdapter
from inventory_sync.domain import VendorProductId
from inventory_sync.log import get

URL = "https://api.bambinok.com/cache/Bambino"

MASTER = {
    "products": [
        {"id": 1, "catalogNumber": "100000001", "title": "עגלה", "name": "A",
         "brand": "Graco", "price": 399, "quantity": 5, "images": ["http://img/1.jpg"],
         "types": [{"id": 28, "name": "טיולונים"}]},
        {"id": 2, "catalogNumber": "100000002", "title": "בוסטר", "name": "B",
         "brand": "Joie", "price": 500, "quantity": 0,
         "types": [{"id": 22, "name": "בוסטרים"}]},  # OOS
        {"id": 3, "catalogNumber": "100000003", "title": "כסא", "name": "C",
         "brand": "Infanti", "price": 800, "quantity": 3,
         "discount": {"type": "overwrite", "amount": 650,
                      "startDate": "01/01/2026", "endDate": "12/31/2026"}},
    ],
    "websites": [
        {"brand": "Joie", "policies": {"warranty": "<p>שנתיים</p>"}},
        {"brand": "Graco", "policies": {"warranty": "<p>שנה</p>"}},
    ],
}


def _adapter(calls: list | None = None) -> BambinoApiAdapter:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        assert str(request.url) == URL
        return httpx.Response(200, json=MASTER)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return BambinoApiAdapter(client=client, logger=get("test"))


class TestFetch:
    def test_fetch_all_products(self):
        prods = _adapter().fetch_all_products()
        assert [p.catalog_number for p in prods] == ["100000001", "100000002", "100000003"]
        assert prods[0].price == Decimal("399")

    def test_warranties(self):
        assert _adapter().warranties() == {"Joie": "<p>שנתיים</p>", "Graco": "<p>שנה</p>"}

    def test_master_fetched_once_and_memoized(self):
        calls: list = []
        a = _adapter(calls)
        a.fetch_all_products()
        a.warranties()
        a.fetch_snapshots([VendorProductId("100000001")])
        assert len(calls) == 1  # one round-trip shared across all reads


class TestSnapshots:
    def test_in_stock_exact_count(self):
        snaps = _adapter().fetch_snapshots([VendorProductId("100000001")])
        s = snaps[VendorProductId("100000001")]
        assert s.is_available is True and s.stock_count == 5
        assert s.name == "עגלה A"

    def test_out_of_stock(self):
        snaps = _adapter().fetch_snapshots([VendorProductId("100000002")])
        s = snaps[VendorProductId("100000002")]
        assert s.is_available is False and s.stock_count == 0

    def test_active_discount_reflected_in_price(self):
        snaps = _adapter().fetch_snapshots([VendorProductId("100000003")])
        assert snaps[VendorProductId("100000003")].price == Decimal("650")

    def test_unknown_sku_omitted(self):
        assert _adapter().fetch_snapshots([VendorProductId("999999999")]) == {}

    def test_only_requested_returned(self):
        snaps = _adapter().fetch_snapshots([VendorProductId("100000001")])
        assert set(snaps) == {VendorProductId("100000001")}


class TestErrors:
    def test_non_200_raises(self):
        def handler(request):
            return httpx.Response(503, text="down")
        client = httpx.Client(transport=httpx.MockTransport(handler))
        adapter = BambinoApiAdapter(client=client, logger=get("test"))
        try:
            adapter.fetch_all_products()
            assert False, "expected HTTPStatusError"
        except httpx.HTTPStatusError:
            pass
