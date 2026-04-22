"""Tests for DefaultStockPolicy's decision logic."""
from __future__ import annotations

from inventory_sync.domain import (
    SKU,
    ChangeKind,
    Product,
    StockLevel,
    VendorProductId,
)
from inventory_sync.policies import DefaultStockPolicy


def _product(stock: int = 5, published: bool = True) -> Product:
    return Product(
        sku=SKU("ABC-001"),
        vendor_product_id=VendorProductId("V1"),
        stock=StockLevel(stock),
        published=published,
    )


class TestVendorOutOfStock:
    policy = DefaultStockPolicy()

    def test_sets_stock_zero_and_unpublishes(self):
        changes = self.policy.decide(_product(stock=5, published=True), StockLevel(0))
        kinds = [c.kind for c in changes]
        assert ChangeKind.SET_STOCK in kinds
        assert ChangeKind.UNPUBLISH in kinds

    def test_new_stock_is_zero(self):
        changes = self.policy.decide(_product(stock=5, published=True), StockLevel(0))
        set_stock = next(c for c in changes if c.kind is ChangeKind.SET_STOCK)
        assert set_stock.new_stock == StockLevel(0)

    def test_skips_stock_update_if_already_zero(self):
        changes = self.policy.decide(_product(stock=0, published=True), StockLevel(0))
        kinds = [c.kind for c in changes]
        assert ChangeKind.SET_STOCK not in kinds
        assert ChangeKind.UNPUBLISH in kinds

    def test_skips_unpublish_if_already_unpublished(self):
        changes = self.policy.decide(_product(stock=5, published=False), StockLevel(0))
        kinds = [c.kind for c in changes]
        assert ChangeKind.SET_STOCK in kinds
        assert ChangeKind.UNPUBLISH not in kinds

    def test_no_changes_when_state_already_reflects_oos(self):
        changes = self.policy.decide(_product(stock=0, published=False), StockLevel(0))
        assert changes == []


class TestVendorInStock:
    policy = DefaultStockPolicy()

    def test_updates_stock_when_changed(self):
        changes = self.policy.decide(_product(stock=5, published=True), StockLevel(7))
        assert len(changes) == 1
        assert changes[0].kind is ChangeKind.SET_STOCK
        assert changes[0].new_stock == StockLevel(7)

    def test_no_changes_when_matched_and_published(self):
        changes = self.policy.decide(_product(stock=5, published=True), StockLevel(5))
        assert changes == []

    def test_republishes_when_coming_back_in_stock(self):
        changes = self.policy.decide(_product(stock=0, published=False), StockLevel(3))
        kinds = [c.kind for c in changes]
        assert ChangeKind.SET_STOCK in kinds
        assert ChangeKind.REPUBLISH in kinds

    def test_republishes_only_if_was_unpublished(self):
        changes = self.policy.decide(_product(stock=5, published=True), StockLevel(5))
        kinds = [c.kind for c in changes]
        assert ChangeKind.REPUBLISH not in kinds

    def test_republish_carries_no_new_stock(self):
        changes = self.policy.decide(_product(stock=0, published=False), StockLevel(3))
        republish = next(c for c in changes if c.kind is ChangeKind.REPUBLISH)
        assert republish.new_stock is None
