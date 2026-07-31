"""Tests for reconcile_prices — the per-product price write step (write-avoidance).

Uses a fake store that records writes, so nothing touches a real price.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.log import get
from inventory_sync.pricing import TargetPrice
from inventory_sync.pricing_sync import reconcile_prices

D = Decimal
LOG = get("test")


@dataclass
class FakeStore:
    writes: list = field(default_factory=list)

    def update_variant_price(self, sku, price, compare_at):
        self.writes.append((str(sku), price, compare_at))


def _p(sku, price, compare_at=None):
    return Product(sku=SKU(sku), vendor_product_id=VendorProductId(sku), stock=StockLevel(1),
                   published=True,
                   price=D(price) if price is not None else None,
                   compare_at_price=D(compare_at) if compare_at is not None else None)


def _t(price, compare_at=None):
    return TargetPrice(price=D(price), compare_at=D(compare_at) if compare_at is not None else None)


def _run(products, targets, **kw):
    store = FakeStore()
    summary = reconcile_prices(store, products, targets, LOG, **kw)
    return store, summary


class TestReconcilePrices:
    def test_unchanged_price_writes_nothing(self):
        store, s = _run([_p("A", "100")], {"A": _t("100")})
        assert s.unchanged == 1 and s.updated == 0 and store.writes == []

    def test_changed_price_is_written(self):
        store, s = _run([_p("A", "100")], {"A": _t("90")})
        assert s.updated == 1
        assert store.writes == [("A", D("90"), None)]

    def test_sale_start_writes_compare_at(self):
        store, s = _run([_p("A", "100")], {"A": _t("80", "100")})
        assert s.updated == 1
        assert store.writes == [("A", D("80"), D("100"))]

    def test_change_over_60pct_is_blocked_not_written(self):
        store, s = _run([_p("A", "100")], {"A": _t("30")})   # -70%
        assert s.blocked == 1 and s.updated == 0 and store.writes == []
        assert s.blocked_skus == ["A"]

    def test_unmatched_product_is_skipped(self):
        store, s = _run([_p("A", "100"), _p("B", "50")], {"A": _t("100")})
        assert s.skipped_unmatched == 1 and store.writes == []

    def test_dry_run_counts_but_writes_nothing(self):
        store, s = _run([_p("A", "100")], {"A": _t("90")}, dry_run=True)
        assert s.would_update == 1 and s.updated == 0 and store.writes == []
