"""Tests for the shared missing-at-source reconciliation helper."""
from __future__ import annotations

import sqlalchemy

from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.log import get
from inventory_sync.missing_source import reconcile_missing_at_source
from inventory_sync.persistence.store_product_store import SqlStoreProductStore

C = "maxbaby"
SEG = "segal | סגל"
LOG = get("test")


def _ps() -> SqlStoreProductStore:
    ps = SqlStoreProductStore(engine=sqlalchemy.create_engine("sqlite:///:memory:"), logger=LOG)
    ps.create_schema()
    return ps


def _p(sku, spid, vendor=SEG, published=True):
    return Product(sku=SKU(sku), vendor_product_id=VendorProductId(sku), stock=StockLevel(1),
                   published=published, store_product_id=spid, vendor=vendor)


def test_flags_only_products_absent_from_full_catalog():
    ps = _ps()
    store_products = [_p("IN-1", "1"), _p("GONE-1", "2")]
    n = reconcile_missing_at_source(ps, store_products, {"IN-1"}, [SEG], C, LOG)
    assert n == 1
    assert ps.get(C, "GONE-1").missing_at_source is True
    assert ps.get(C, "IN-1") is None  # present → not flagged (and no row invented)


def test_full_catalog_prevents_false_positive():
    # sku 446 is in the FULL catalog (a 'room') even if not in the ingest subset.
    ps = _ps()
    n = reconcile_missing_at_source(ps, [_p("446", "9")], {"446", "IN-1"}, [SEG], C, LOG)
    assert n == 0
    assert ps.get(C, "446") is None


def test_unified_per_product_multivariant_counts_once():
    from inventory_sync.persistence.store_product_store import NewStoreProduct
    ps = _ps()
    ps.write_pending(C, [NewStoreProduct(sku="V-1", store_product_id="5", vendor=SEG),
                         NewStoreProduct(sku="V-2", store_product_id="5", vendor=SEG)])
    variants = [_p("V-1", "5"), _p("V-2", "5")]  # same store_product_id
    n = reconcile_missing_at_source(ps, variants, {"OTHER"}, [SEG], C, LOG)
    assert n == 1  # one product, not two
    assert ps.get(C, "V-1").missing_at_source is True
    assert ps.get(C, "V-2").missing_at_source is True


def test_vendor_scoped_ignores_other_suppliers():
    ps = _ps()
    store_products = [_p("L-1", "1", vendor="laura | לורה")]  # not owned by Segal
    n = reconcile_missing_at_source(ps, store_products, set(), [SEG], C, LOG)
    assert n == 0
    assert ps.get(C, "L-1") is None


def test_clears_when_back_in_catalog():
    ps = _ps()
    ps.flag_missing_at_source(C, "2", sku="BACK-1", vendor=SEG)
    reconcile_missing_at_source(ps, [_p("BACK-1", "2")], {"BACK-1"}, [SEG], C, LOG)
    assert ps.get(C, "BACK-1").missing_at_source is False


def test_dry_run_counts_but_writes_nothing():
    ps = _ps()
    n = reconcile_missing_at_source(ps, [_p("GONE-1", "2")], set(), [SEG], C, LOG, dry_run=True)
    assert n == 1
    assert ps.get(C, "GONE-1") is None  # nothing written


def test_no_owned_vendors_is_noop():
    ps = _ps()
    n = reconcile_missing_at_source(ps, [_p("GONE-1", "2")], set(), [], C, LOG)
    assert n == 0
