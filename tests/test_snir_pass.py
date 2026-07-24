"""Tests for the Snir binding of the unified pass (catalog + stock sync, one run).

Uses a fake Snir adapter (product listing + tab scrape) to prove: new in-stock
products are enriched (tab scrape) + created; existing products are stock-synced
WITHOUT a tab scrape; the OOS gate blocks new out-of-stock products; and
shared-SKU variable products are still onboarded, flagged MULTI_VARIANT.
"""
from __future__ import annotations

import sqlalchemy

from inventory_sync import review_reasons
from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.fakes import InMemoryStore
from inventory_sync.log import get
from inventory_sync.persistence.store_product_store import SqlStoreProductStore
from inventory_sync.policies import DefaultStockPolicy
from inventory_sync.snir_pass import SnirUnifiedSource
from inventory_sync.snir_source import SnirTab, parse_api_product
from inventory_sync.supplier_pass import unified_pass

C = "maxbaby"
LOG = get("test")
_BEDS = 126           # importable
_MARKETING = 999      # not importable


def _prod(sku, cat=_BEDS, in_stock=True, price="1490", wc_type="simple", variations=0):
    return {
        "sku": sku,
        "name": f"מוצר {sku}",
        "short_description": "<p>תקציר</p>",
        "description": "<p>תיאור</p>",
        "prices": {"regular_price": price, "currency_minor_unit": 0},
        "images": [{"src": f"http://img/{sku}.jpg"}],
        "categories": [{"id": cat}],
        "permalink": f"http://snir/p/{sku}/",
        "is_in_stock": in_stock,
        "type": wc_type,
        "variations": [{"id": 1000 + i} for i in range(variations)],
    }


class FakeSnirAdapter:
    def __init__(self, products):
        self.products = products
        self.tab_calls: list[str] = []

    def list_products(self):
        return list(self.products)

    def fetch_tabs(self, permalink):
        self.tab_calls.append(permalink)
        return (SnirTab("tech_details", "<p>רוחב: 120</p>"),)


def _stores(existing=None):
    store = InMemoryStore(products=existing or [])
    eng = sqlalchemy.create_engine("sqlite:///:memory:")
    ps = SqlStoreProductStore(engine=eng, logger=get("test"))
    ps.create_schema()
    return store, ps


def _source(products):
    return SnirUnifiedSource(adapter=FakeSnirAdapter(products), logger=LOG)


class TestSnirUnifiedPass:
    def test_new_in_stock_product_enriched_and_created(self):
        store, ps = _stores()
        src = _source([_prod("bed-1")])
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 1
        assert SKU("bed-1") in {p.sku for p in store.list_products()}
        assert src.adapter.tab_calls == ["http://snir/p/bed-1/"]  # enriched exactly once

    def test_existing_product_synced_without_tab_scrape(self):
        # store shows OOS, vendor in stock (binary) -> restock; no tab scrape.
        existing = [Product(sku=SKU("bed-1"), vendor_product_id=VendorProductId("bed-1"),
                            stock=StockLevel(0), published=True, store_product_id="1")]
        store, ps = _stores(existing)
        src = _source([_prod("bed-1", in_stock=True)])
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 0
        assert s.stock_changes_applied == 1        # binary restock
        assert src.adapter.tab_calls == []         # existing product NOT scraped

    def test_oos_new_product_skipped_by_gate(self):
        store, ps = _stores()
        src = _source([_prod("bed-1", in_stock=False)])
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 0 and s.skipped_oos == 1
        assert src.adapter.tab_calls == []         # OOS never scraped

    def test_uncategorized_new_product_skipped(self):
        store, ps = _stores()
        src = _source([_prod("junk-1", cat=_MARKETING)])
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 0 and s.skipped_uncategorized == 1

    def test_multi_variant_created_and_flagged(self):
        store, ps = _stores()
        src = _source([_prod("var-1", wc_type="variable", variations=3)])
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 1                       # onboarded (single-variant), not skipped
        pending = ps.list_pending(C)
        row = next(r for r in pending if str(r.sku) == "var-1")
        assert row.needs_review is True
        assert review_reasons.MULTI_VARIANT in (row.needs_review_reason or "")

    def test_duplicate_sku_deduped_in_catalog(self):
        src = _source([_prod("dup"), _prod("dup")])
        assert [p.sku for p in src.list_catalog()] == ["dup"]  # first wins

    def test_mixed_run(self):
        existing = [Product(sku=SKU("bed-1"), vendor_product_id=VendorProductId("bed-1"),
                            stock=StockLevel(0), published=True, store_product_id="1")]
        store, ps = _stores(existing)
        src = _source([
            _prod("bed-1", in_stock=True),   # existing -> restock
            _prod("bed-2", in_stock=True),   # new + in-stock -> create
            _prod("bed-3", in_stock=False),  # new + OOS -> skip
        ])
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.stock_changes_applied == 1
        assert s.created == 1
        assert s.skipped_oos == 1
        assert src.adapter.tab_calls == ["http://snir/p/bed-2/"]  # only the new in-stock one
