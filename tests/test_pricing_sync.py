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
    tag_writes: list = field(default_factory=list)

    def update_variant_price(self, sku, price, compare_at):
        self.writes.append((str(sku), price, compare_at))

    def set_product_tags(self, store_product_id, tags):
        self.tag_writes.append((str(store_product_id), set(tags)))


def _p(sku, price, compare_at=None, spid="1", tags=()):
    return Product(sku=SKU(sku), vendor_product_id=VendorProductId(sku), stock=StockLevel(1),
                   published=True, store_product_id=spid, tags=tuple(tags),
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


class _RecLog:
    """Captures structured log events so we can assert on their fields."""
    def __init__(self): self.events = []
    def info(self, event, **kw): self.events.append((event, dict(kw)))
    def warning(self, event, **kw): self.events.append((event, dict(kw)))
    def bind(self, **kw): return self


class TestAuditLog:
    def test_price_update_records_old_and_new(self):
        rec = _RecLog()
        reconcile_prices(FakeStore(), [_p("A", "100")], {"A": _t("90", "100")}, rec)
        upd = [kw for e, kw in rec.events if e == "price_update"]
        assert upd, "expected a price_update event"
        assert upd[0]["was"] == "100" and upd[0]["price"] == "90"
        assert upd[0]["compare_at"] == "100"
        assert upd[0]["pct"] == -10   # % change → radical moves are one Axiom filter

    def test_price_update_pct_none_when_no_previous(self):
        rec = _RecLog()
        reconcile_prices(FakeStore(), [_p("A", None)], {"A": _t("500")}, rec)
        upd = [kw for e, kw in rec.events if e == "price_update"][0]
        assert upd["was"] is None and upd["pct"] is None


class TestTagSync:
    def test_sale_start_adds_tags(self):
        store, s = _run([_p("A", "800", spid="7", tags=("segal",))], {"A": _t("80", "100")})
        assert s.tags_updated == 1
        assert store.tag_writes == [("7", {"segal", "supplier-sale", "sale-20"})]

    def test_sale_end_removes_tags_keeps_others(self):
        store, s = _run([_p("A", "100", spid="7", tags=("segal", "supplier-sale", "sale-20"))],
                        {"A": _t("100")})
        assert store.tag_writes == [("7", {"segal"})]

    def test_no_tag_change_writes_nothing(self):
        store, s = _run([_p("A", "100", spid="7", tags=("segal",))], {"A": _t("100")})
        assert s.tags_updated == 0 and store.tag_writes == []

    def test_dry_run_does_not_write_tags(self):
        store, s = _run([_p("A", "800", spid="7", tags=("segal",))], {"A": _t("80", "100")},
                        dry_run=True)
        assert s.tags_would_update == 1 and store.tag_writes == []
