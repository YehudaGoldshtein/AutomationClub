"""Tests for DefaultStockPolicy — covers both binary and exact-count vendor signals."""
from __future__ import annotations

from inventory_sync.domain import (
    SKU,
    ChangeKind,
    Product,
    StockLevel,
    VendorProductId,
    VendorProductSnapshot,
)
from inventory_sync.policies import DefaultStockPolicy


def _product(stock: int = 5, published: bool = True) -> Product:
    return Product(
        sku=SKU("ABC-001"),
        vendor_product_id=VendorProductId("V1"),
        stock=StockLevel(stock),
        published=published,
    )


def _binary(is_available: bool) -> VendorProductSnapshot:
    return VendorProductSnapshot(
        vendor_product_id=VendorProductId("V1"),
        is_available=is_available,
        stock_count=None,
    )


def _exact(n: int) -> VendorProductSnapshot:
    return VendorProductSnapshot(
        vendor_product_id=VendorProductId("V1"),
        is_available=n > 0,
        stock_count=n,
    )


class TestBinaryOutOfStock:
    policy = DefaultStockPolicy()

    def test_sets_stock_zero_when_store_had_stock(self):
        changes = self.policy.decide(_product(stock=5), _binary(False))
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.SET_STOCK
        assert changes[0].new_stock == StockLevel(0)

    def test_no_changes_when_store_already_zero(self):
        changes = self.policy.decide(_product(stock=0), _binary(False))
        assert changes == []

    def test_does_not_emit_unpublish_automatically(self):
        """v0.1: UNPUBLISH is owner-triggered manually, never auto-emitted by policy."""
        changes = self.policy.decide(_product(stock=5, published=True), _binary(False))
        assert all(c.kind is not ChangeKind.UNPUBLISH for c in changes)


class TestBinaryInStock:
    policy = DefaultStockPolicy()

    def test_preserves_store_count_when_positive(self):
        """Binary-only vendor + store has count -> don't clobber the count."""
        changes = self.policy.decide(_product(stock=10), _binary(True))
        assert changes == []

    def test_sets_stock_to_one_when_store_was_zero(self):
        """Back-in-stock from binary-only source: at least 1."""
        changes = self.policy.decide(_product(stock=0), _binary(True))
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.SET_STOCK
        assert changes[0].new_stock == StockLevel(1)

    def test_does_not_emit_republish_automatically(self):
        changes = self.policy.decide(_product(stock=0, published=False), _binary(True))
        assert all(c.kind is not ChangeKind.REPUBLISH for c in changes)


class TestExactCount:
    policy = DefaultStockPolicy()

    def test_updates_to_exact_number_when_differs(self):
        changes = self.policy.decide(_product(stock=5), _exact(12))
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.SET_STOCK
        assert changes[0].new_stock == StockLevel(12)

    def test_no_change_when_counts_match(self):
        changes = self.policy.decide(_product(stock=5), _exact(5))
        assert changes == []

    def test_zero_count_sets_store_zero(self):
        changes = self.policy.decide(_product(stock=5), _exact(0))
        assert len(changes) == 1
        assert changes[0].new_stock == StockLevel(0)

    def test_back_in_stock_sets_exact_count(self):
        """When store is 0 and vendor reports exact count, sync to that number."""
        changes = self.policy.decide(_product(stock=0), _exact(7))
        assert len(changes) == 1
        assert changes[0].new_stock == StockLevel(7)
