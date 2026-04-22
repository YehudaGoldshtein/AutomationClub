"""Tests for LauraDesignScraperAdapter.

Uses httpx.MockTransport so no live network calls run in CI. Includes a
SupplierContract subclass so the adapter is validated against the same
contract that InMemorySupplier passes — pluggability enforced in code.
"""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from inventory_sync.adapters.laura_design import LauraDesignScraperAdapter
from inventory_sync.domain import StockLevel, VendorProductId
from inventory_sync.log import get

from tests.test_supplier import SEEDED_STOCK, SupplierContract


BASE_URL = "https://vendor.test"


def _jsonld_html(
    sku: str,
    availability: str = "InStock",
    price: float | int | str | None = 89,
    currency: str = "ILS",
    name: str = "Test Product",
    image: str | list | dict | None = "https://vendor.test/pub/media/img.jpg",
) -> str:
    payload: dict = {
        "@context": "https://schema.org/",
        "@type": "Product",
        "sku": sku,
        "name": name,
        "offers": {
            "@type": "Offer",
            "availability": f"https://schema.org/{availability}"
            if not availability.startswith("http")
            else availability,
            "priceCurrency": currency,
        },
    }
    if price is not None:
        payload["offers"]["price"] = price
    if image is not None:
        payload["image"] = image
    return f"""<!DOCTYPE html>
<html><head>
<title>{name}</title>
<script type="application/ld+json">{json.dumps(payload)}</script>
</head><body></body></html>"""


def _make_adapter(handler) -> LauraDesignScraperAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url=BASE_URL)
    return LauraDesignScraperAdapter(client=client, logger=get("test"), base_url=BASE_URL)


class TestFetchSnapshotHappyPath:
    def test_parses_availability_price_name_image(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/3200-118"
            return httpx.Response(200, text=_jsonld_html(
                sku="3200-118",
                availability="InStock",
                price=89,
                name="Hebrew Product",
                image="https://vendor.test/img.jpg",
            ))

        adapter = _make_adapter(handler)
        snapshots = adapter.fetch_snapshots([VendorProductId("3200-118")])

        snap = snapshots[VendorProductId("3200-118")]
        assert snap.stock_level == StockLevel(1)
        assert snap.raw_availability == "https://schema.org/InStock"
        assert snap.name == "Hebrew Product"
        assert snap.price == Decimal("89")
        assert snap.currency == "ILS"
        assert snap.image_url == "https://vendor.test/img.jpg"

    def test_out_of_stock_maps_to_stock_zero(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_jsonld_html(sku="OOS-1", availability="OutOfStock"))

        adapter = _make_adapter(handler)
        snap = adapter.fetch_snapshots([VendorProductId("OOS-1")])[VendorProductId("OOS-1")]

        assert snap.stock_level == StockLevel(0)
        assert "OutOfStock" in snap.raw_availability

    def test_image_list_takes_first(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_jsonld_html(
                sku="X", image=["https://a.test/1.jpg", "https://a.test/2.jpg"]
            ))

        adapter = _make_adapter(handler)
        snap = adapter.fetch_snapshots([VendorProductId("X")])[VendorProductId("X")]
        assert snap.image_url == "https://a.test/1.jpg"

    def test_image_imageobject_reads_url_field(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_jsonld_html(
                sku="X", image={"@type": "ImageObject", "url": "https://a.test/obj.jpg"}
            ))

        adapter = _make_adapter(handler)
        snap = adapter.fetch_snapshots([VendorProductId("X")])[VendorProductId("X")]
        assert snap.image_url == "https://a.test/obj.jpg"


class TestFetchSnapshotErrors:
    def test_404_returns_no_snapshot(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="not found")

        adapter = _make_adapter(handler)
        snapshots = adapter.fetch_snapshots([VendorProductId("MISSING")])
        assert snapshots == {}

    def test_non_200_non_404_returns_no_snapshot(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        adapter = _make_adapter(handler)
        snapshots = adapter.fetch_snapshots([VendorProductId("X")])
        assert snapshots == {}

    def test_html_without_jsonld_returns_no_snapshot(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><body>no structured data</body></html>")

        adapter = _make_adapter(handler)
        snapshots = adapter.fetch_snapshots([VendorProductId("X")])
        assert snapshots == {}

    def test_malformed_jsonld_is_tolerated(self):
        bad_html = """<html><head>
        <script type="application/ld+json">{not json}</script>
        <script type="application/ld+json">{"@type": "Product", "sku": "X", "offers": {"availability": "https://schema.org/InStock"}}</script>
        </head></html>"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=bad_html)

        adapter = _make_adapter(handler)
        snap = adapter.fetch_snapshots([VendorProductId("X")])[VendorProductId("X")]
        assert snap.stock_level == StockLevel(1)

    def test_network_exception_returns_no_snapshot(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("nope")

        adapter = _make_adapter(handler)
        snapshots = adapter.fetch_snapshots([VendorProductId("X")])
        assert snapshots == {}


class TestFetchStockProjection:
    """fetch_stock() is the SupplierSource interface method — narrow view over snapshots."""

    def test_returns_stock_levels_only(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=_jsonld_html(
                sku=request.url.path.lstrip("/"), availability="InStock"
            ))

        adapter = _make_adapter(handler)
        stock = adapter.fetch_stock([VendorProductId("A"), VendorProductId("B")])
        assert stock == {VendorProductId("A"): StockLevel(1), VendorProductId("B"): StockLevel(1)}


class TestLauraDesignSatisfiesSupplierContract(SupplierContract):
    """Re-run the SupplierSource contract tests against the Laura adapter.

    This is the proof that swapping InMemorySupplier for the real adapter
    in the engine will work — exactly what the pluggability principle demands.
    """

    @pytest.fixture
    def supplier(self) -> LauraDesignScraperAdapter:
        def handler(request: httpx.Request) -> httpx.Response:
            vid = request.url.path.lstrip("/")
            try:
                level = SEEDED_STOCK[VendorProductId(vid)]
            except KeyError:
                return httpx.Response(404, text="not found")
            availability = "InStock" if level.value > 0 else "OutOfStock"
            return httpx.Response(200, text=_jsonld_html(sku=vid, availability=availability))

        return _make_adapter(handler)
