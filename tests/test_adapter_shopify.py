"""Tests for ShopifyAdapter.

Uses httpx.MockTransport + a small stateful in-memory Shopify fake so we can
drive the full list -> update -> read back flow without live API calls.
Inherits StoreContract to prove the adapter satisfies the same behavior as
InMemoryStore.
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from inventory_sync.adapters.shopify import ShopifyAdapter, ShopifyError
from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.log import get

from tests.test_store import SEEDED_PRODUCTS, StoreContract


# ---------- in-memory Shopify fake ----------

class _FakeShopifyApi:
    """Stateful fake of the subset of Shopify Admin API our adapter uses."""

    def __init__(self, products: list[dict], location_id: int = 999):
        self.products: dict[int, dict] = {p["id"]: p for p in products}
        self.location_id = location_id
        self.inventory: dict[int, int] = {}
        for p in products:
            for v in p.get("variants", []):
                self.inventory[v["inventory_item_id"]] = v.get("inventory_quantity", 0)
        self.request_log: list[tuple[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.request_log.append((method, path))

        if method == "GET" and path.endswith("/products.json"):
            return self._products_json(request)
        if method == "GET" and path.endswith("/locations.json"):
            return httpx.Response(
                200, json={"locations": [{"id": self.location_id, "name": "Primary"}]}
            )
        if method == "POST" and path.endswith("/inventory_levels/set.json"):
            body = request.content.decode() if request.content else "{}"
            import json as _json
            data = _json.loads(body)
            self.inventory[data["inventory_item_id"]] = data["available"]
            for p in self.products.values():
                for v in p.get("variants", []):
                    if v["inventory_item_id"] == data["inventory_item_id"]:
                        v["inventory_quantity"] = data["available"]
            return httpx.Response(200, json={"inventory_level": data})
        if method == "PUT" and "/products/" in path and path.endswith(".json"):
            product_id_str = path.rsplit("/", 1)[-1].replace(".json", "")
            try:
                product_id = int(product_id_str)
            except ValueError:
                return httpx.Response(404, text=f"bad product id: {product_id_str}")
            import json as _json
            data = _json.loads(request.content.decode())
            new_status = data["product"]["status"]
            if product_id in self.products:
                self.products[product_id]["status"] = new_status
            return httpx.Response(200, json={"product": self.products.get(product_id, {})})

        return httpx.Response(404, text=f"unhandled {method} {path}")

    def _products_json(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        limit = int(params.get("limit", 250))
        vendor = params.get("vendor")
        page_info = params.get("page_info")

        all_products = list(self.products.values())
        if vendor is not None:
            all_products = [p for p in all_products if p.get("vendor") == vendor]

        start = int(page_info) if page_info else 0
        page = all_products[start : start + limit]
        next_start = start + limit

        headers = {}
        if next_start < len(all_products):
            headers["link"] = (
                f'<https://shop.test/admin/api/2024-10/products.json?'
                f'limit={limit}&page_info={next_start}>; rel="next"'
            )

        return httpx.Response(200, json={"products": page}, headers=headers)


def _mk_variant(variant_id: int, inventory_item_id: int, sku: str, qty: int) -> dict:
    return {
        "id": variant_id,
        "inventory_item_id": inventory_item_id,
        "sku": sku,
        "inventory_quantity": qty,
    }


def _mk_product(
    product_id: int,
    variants: list[dict],
    status: str = "active",
    vendor: str | None = None,
) -> dict:
    return {
        "id": product_id,
        "status": status,
        "vendor": vendor,
        "variants": variants,
    }


def _make_adapter(fake: _FakeShopifyApi, **kwargs: Any) -> ShopifyAdapter:
    transport = httpx.MockTransport(fake.handle)
    client = httpx.Client(transport=transport, base_url="https://shop.test/admin/api/2024-10")
    return ShopifyAdapter(client=client, logger=get("test"), **kwargs)


# ---------- unit tests ----------

class TestListProducts:
    def test_flattens_variants_into_products(self):
        fake = _FakeShopifyApi([
            _mk_product(100, [
                _mk_variant(10, 1001, "A-1", 5),
                _mk_variant(11, 1002, "A-2", 0),
            ]),
        ])
        adapter = _make_adapter(fake)

        products = adapter.list_products()

        assert {p.sku for p in products} == {SKU("A-1"), SKU("A-2")}
        by_sku = {p.sku: p for p in products}
        assert by_sku[SKU("A-1")].stock == StockLevel(5)
        assert by_sku[SKU("A-2")].stock == StockLevel(0)

    def test_maps_sku_to_vendor_product_id_directly(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "2800-253", 0)])])
        adapter = _make_adapter(fake)

        products = adapter.list_products()
        assert products[0].vendor_product_id == VendorProductId("2800-253")

    def test_status_active_is_published(self):
        fake = _FakeShopifyApi([
            _mk_product(1, [_mk_variant(10, 100, "X", 0)], status="active"),
            _mk_product(2, [_mk_variant(20, 200, "Y", 0)], status="archived"),
        ])
        adapter = _make_adapter(fake)

        by_sku = {p.sku: p for p in adapter.list_products()}
        assert by_sku[SKU("X")].published is True
        assert by_sku[SKU("Y")].published is False

    def test_skips_variants_without_sku(self):
        fake = _FakeShopifyApi([
            _mk_product(1, [
                _mk_variant(10, 100, "HAS-SKU", 1),
                _mk_variant(11, 101, "", 1),
            ]),
        ])
        adapter = _make_adapter(fake)
        skus = [p.sku for p in adapter.list_products()]
        assert skus == [SKU("HAS-SKU")]

    def test_negative_inventory_coerced_to_zero(self):
        """Shopify allows negative inventory_quantity (oversold). Our domain disallows it."""
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", -3)])])
        adapter = _make_adapter(fake)
        assert adapter.list_products()[0].stock == StockLevel(0)

    def test_vendor_filter_is_sent_in_query(self):
        fake = _FakeShopifyApi([
            _mk_product(1, [_mk_variant(10, 100, "X", 1)], vendor="laura"),
            _mk_product(2, [_mk_variant(20, 200, "Y", 1)], vendor="other"),
        ])
        adapter = _make_adapter(fake, vendor_filter="laura")

        products = adapter.list_products()
        assert {p.sku for p in products} == {SKU("X")}

    def test_pagination_follows_link_header(self):
        variants = [
            _mk_variant(10 + i, 1000 + i, f"SKU-{i:03d}", i)
            for i in range(25)
        ]
        products = [_mk_product(i, [variants[i]]) for i in range(25)]
        fake = _FakeShopifyApi(products)
        adapter = _make_adapter(fake, page_size=10)

        all_products = adapter.list_products()
        assert len(all_products) == 25

    def test_populates_variant_cache_for_subsequent_mutations(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)])])
        adapter = _make_adapter(fake)
        adapter.list_products()
        adapter.update_stock(SKU("X"), StockLevel(3))
        assert fake.inventory[100] == 3


class TestUpdateStock:
    def test_writes_inventory_level_set(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)])])
        adapter = _make_adapter(fake)
        adapter.list_products()

        adapter.update_stock(SKU("X"), StockLevel(42))

        assert fake.inventory[100] == 42
        methods = [m for (m, p) in fake.request_log if p.endswith("/inventory_levels/set.json")]
        assert methods == ["POST"]

    def test_resolves_location_lazily_and_caches(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)])])
        adapter = _make_adapter(fake)
        adapter.list_products()

        adapter.update_stock(SKU("X"), StockLevel(1))
        adapter.update_stock(SKU("X"), StockLevel(2))

        location_calls = [p for (m, p) in fake.request_log if p.endswith("/locations.json")]
        assert len(location_calls) == 1

    def test_unknown_sku_raises(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)])])
        adapter = _make_adapter(fake)
        adapter.list_products()

        with pytest.raises(ShopifyError, match="no variant cached"):
            adapter.update_stock(SKU("NOT-CACHED"), StockLevel(1))


class TestPublishStatus:
    def test_unpublish_sets_status_archived(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)], status="active")])
        adapter = _make_adapter(fake)
        adapter.list_products()

        adapter.unpublish(SKU("X"))

        assert fake.products[1]["status"] == "archived"

    def test_republish_sets_status_active(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)], status="archived")])
        adapter = _make_adapter(fake)
        adapter.list_products()

        adapter.republish(SKU("X"))

        assert fake.products[1]["status"] == "active"

    def test_unpublish_unknown_sku_raises(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)])])
        adapter = _make_adapter(fake)
        adapter.list_products()

        with pytest.raises(ShopifyError):
            adapter.unpublish(SKU("NOPE"))


class TestShopifySatisfiesStoreContract(StoreContract):
    """Re-run the StorePlatform contract tests against the Shopify adapter.

    Each SEEDED_PRODUCT becomes a single-variant Shopify product so the
    contract's assertions about SKU, stock, published, and mutations all hold.
    """

    @pytest.fixture
    def store(self) -> ShopifyAdapter:
        shopify_products = [
            _mk_product(
                product_id=(i + 1) * 1000,
                status="active" if sp.published else "archived",
                variants=[
                    _mk_variant(
                        variant_id=(i + 1) * 100,
                        inventory_item_id=(i + 1) * 10,
                        sku=sp.sku,
                        qty=sp.stock.value,
                    )
                ],
            )
            for i, sp in enumerate(SEEDED_PRODUCTS)
        ]
        fake = _FakeShopifyApi(shopify_products)
        adapter = _make_adapter(fake)
        adapter.list_products()  # prime cache
        return adapter
