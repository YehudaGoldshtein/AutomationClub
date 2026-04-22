"""Tests for the domain types — invariants and construction rules."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from inventory_sync.domain import (
    SKU,
    ChangeKind,
    Product,
    StockChange,
    StockLevel,
    SyncError,
    SyncRun,
    VendorProductId,
    VendorProductSnapshot,
)


class TestStockLevel:
    def test_zero_is_out_of_stock(self):
        assert StockLevel(0).is_out_of_stock is True

    def test_positive_is_in_stock(self):
        assert StockLevel(5).is_out_of_stock is False

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="negative"):
            StockLevel(-1)

    def test_is_frozen(self):
        sl = StockLevel(5)
        with pytest.raises(FrozenInstanceError):
            sl.value = 10  # type: ignore[misc]

    def test_equality_by_value(self):
        assert StockLevel(3) == StockLevel(3)
        assert StockLevel(3) != StockLevel(4)


class TestProduct:
    def test_constructs(self):
        p = Product(
            sku=SKU("ABC-001"),
            vendor_product_id=VendorProductId("V-123"),
            stock=StockLevel(7),
            published=True,
        )
        assert p.sku == "ABC-001"
        assert p.vendor_product_id == "V-123"
        assert p.stock.value == 7
        assert p.published is True


class TestStockChange:
    def test_set_stock_requires_new_stock(self):
        with pytest.raises(ValueError, match="SET_STOCK"):
            StockChange(sku=SKU("X"), kind=ChangeKind.SET_STOCK)

    def test_set_stock_happy_path(self):
        ch = StockChange(sku=SKU("X"), kind=ChangeKind.SET_STOCK, new_stock=StockLevel(3))
        assert ch.new_stock == StockLevel(3)
        assert ch.kind is ChangeKind.SET_STOCK

    def test_unpublish_rejects_new_stock(self):
        with pytest.raises(ValueError, match="unpublish"):
            StockChange(sku=SKU("X"), kind=ChangeKind.UNPUBLISH, new_stock=StockLevel(0))

    def test_unpublish_happy_path(self):
        ch = StockChange(sku=SKU("X"), kind=ChangeKind.UNPUBLISH, reason="vendor OOS")
        assert ch.new_stock is None
        assert ch.reason == "vendor OOS"

    def test_republish_rejects_new_stock(self):
        with pytest.raises(ValueError, match="republish"):
            StockChange(sku=SKU("X"), kind=ChangeKind.REPUBLISH, new_stock=StockLevel(1))

    def test_republish_happy_path(self):
        ch = StockChange(sku=SKU("X"), kind=ChangeKind.REPUBLISH)
        assert ch.kind is ChangeKind.REPUBLISH
        assert ch.new_stock is None


class TestSyncRun:
    def test_starts_empty_and_incomplete(self):
        run = SyncRun()
        assert run.finished_at is None
        assert run.duration_seconds is None
        assert run.items_checked == 0
        assert run.changes_planned == []
        assert run.changes_applied == []
        assert run.errors == []

    def test_has_unique_run_id(self):
        a = SyncRun()
        b = SyncRun()
        assert a.run_id != b.run_id

    def test_finish_sets_timestamp_and_duration(self):
        run = SyncRun()
        run.finish()
        assert run.finished_at is not None
        assert run.finished_at >= run.started_at
        assert run.duration_seconds is not None
        assert run.duration_seconds >= 0

    def test_can_accumulate_changes_and_errors(self):
        run = SyncRun()
        ch = StockChange(sku=SKU("X"), kind=ChangeKind.UNPUBLISH)
        run.changes_planned.append(ch)
        run.errors.append(SyncError(message="vendor unreachable"))
        assert len(run.changes_planned) == 1
        assert len(run.errors) == 1


class TestSyncError:
    def test_defaults(self):
        err = SyncError(message="boom")
        assert err.sku is None
        assert err.when is not None

    def test_with_sku(self):
        err = SyncError(message="scrape failed", sku=SKU("X-1"))
        assert err.sku == "X-1"


class TestVendorProductSnapshot:
    def test_binary_in_stock(self):
        s = VendorProductSnapshot(
            vendor_product_id=VendorProductId("V1"),
            is_available=True,
            stock_count=None,
        )
        assert s.is_available is True
        assert s.stock_count is None

    def test_exact_count_with_zero_is_not_available(self):
        s = VendorProductSnapshot(
            vendor_product_id=VendorProductId("V1"),
            is_available=False,
            stock_count=0,
        )
        assert s.is_available is False
        assert s.stock_count == 0

    def test_negative_stock_count_raises(self):
        with pytest.raises(ValueError, match="negative"):
            VendorProductSnapshot(
                vendor_product_id=VendorProductId("V1"),
                is_available=False,
                stock_count=-1,
            )

    def test_contradiction_zero_count_but_available_raises(self):
        with pytest.raises(ValueError, match="inconsistent"):
            VendorProductSnapshot(
                vendor_product_id=VendorProductId("V1"),
                is_available=True,
                stock_count=0,
            )

    def test_contradiction_positive_count_but_not_available_raises(self):
        with pytest.raises(ValueError, match="inconsistent"):
            VendorProductSnapshot(
                vendor_product_id=VendorProductId("V1"),
                is_available=False,
                stock_count=5,
            )

    def test_binary_only_with_any_is_available_value_ok(self):
        """stock_count=None means 'we don't know exactly' — any is_available is valid."""
        VendorProductSnapshot(vendor_product_id=VendorProductId("V1"), is_available=True)
        VendorProductSnapshot(vendor_product_id=VendorProductId("V1"), is_available=False)
