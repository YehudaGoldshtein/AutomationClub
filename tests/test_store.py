"""Contract tests for StorePlatform. Every implementation must pass these."""
from __future__ import annotations

import pytest

from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.fakes import InMemoryStore
from inventory_sync.interfaces import StorePlatform


SEEDED_PRODUCTS: list[Product] = [
    Product(SKU("ABC-001"), VendorProductId("V1"), StockLevel(5), published=True),
    Product(SKU("ABC-002"), VendorProductId("V2"), StockLevel(0), published=False),
    Product(SKU("ABC-003"), VendorProductId("V3"), StockLevel(10), published=True),
]


class StoreContract:
    """Mix into a concrete test class and provide the `store` fixture seeded with SEEDED_PRODUCTS."""

    @pytest.fixture
    def store(self) -> StorePlatform:
        raise NotImplementedError("provide a `store` fixture in the subclass")

    def test_list_products_returns_all_seeded_skus(self, store: StorePlatform):
        skus = {p.sku for p in store.list_products()}
        assert SKU("ABC-001") in skus
        assert SKU("ABC-002") in skus
        assert SKU("ABC-003") in skus

    def test_list_products_preserves_stock_and_published(self, store: StorePlatform):
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-001")].stock == StockLevel(5)
        assert by_sku[SKU("ABC-001")].published is True
        assert by_sku[SKU("ABC-002")].stock == StockLevel(0)
        assert by_sku[SKU("ABC-002")].published is False

    def test_list_products_includes_vendor_mapping(self, store: StorePlatform):
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-001")].vendor_product_id == VendorProductId("V1")
        assert by_sku[SKU("ABC-003")].vendor_product_id == VendorProductId("V3")

    def test_update_stock_persists(self, store: StorePlatform):
        store.update_stock(SKU("ABC-001"), StockLevel(42))
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-001")].stock == StockLevel(42)

    def test_update_stock_preserves_other_fields(self, store: StorePlatform):
        store.update_stock(SKU("ABC-001"), StockLevel(42))
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-001")].vendor_product_id == VendorProductId("V1")
        assert by_sku[SKU("ABC-001")].published is True

    def test_unpublish_sets_published_false(self, store: StorePlatform):
        store.unpublish(SKU("ABC-001"))
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-001")].published is False

    def test_republish_sets_published_true(self, store: StorePlatform):
        store.republish(SKU("ABC-002"))
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-002")].published is True

    def test_unpublish_preserves_stock(self, store: StorePlatform):
        store.unpublish(SKU("ABC-001"))
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-001")].stock == StockLevel(5)


class TestInMemoryStore(StoreContract):
    @pytest.fixture
    def store(self) -> StorePlatform:
        return InMemoryStore(products=list(SEEDED_PRODUCTS))
