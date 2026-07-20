"""Tests for the Segal binding of the unified pass.

Uses a fake Segal adapter (category listing + tab scrape) to prove: new products
are enriched (tab scrape) + created; existing products are stock-synced WITHOUT a
tab scrape (the efficiency guarantee).
"""
from __future__ import annotations

import sqlalchemy

from inventory_sync.domain import SKU, Product, ProductDraft, StockLevel, VendorProductId
from inventory_sync.fakes import InMemoryStore
from inventory_sync.log import get
from inventory_sync.persistence.store_product_store import SqlStoreProductStore
from inventory_sync.policies import DefaultStockPolicy
from inventory_sync.segal_pass import SegalUnifiedSource
from inventory_sync.segal_source import SegalTab
from inventory_sync.supplier_pass import unified_pass

C = "maxbaby"
LOG = get("test")


def _prod(sku, stock=5, slug="dresser"):
    return {
        "sku": sku, "name": f"מוצר {sku}", "description": "<p>desc</p>",
        "prices": {"regular_price": "2198", "sale_price": "2198", "currency_minor_unit": 0},
        "on_sale": False, "images": [{"src": f"http://img/{sku}.jpg"}],
        "categories": [{"slug": slug}, {"slug": "segal-baby"}],
        "permalink": f"http://segal/p/{sku}/",
        "is_in_stock": stock > 0, "add_to_cart": {"maximum": stock},
    }


class FakeSegalAdapter:
    def __init__(self, by_cat):
        self.by_cat = by_cat
        self.tab_calls: list[str] = []

    def list_category_products(self, cat_id):
        return list(self.by_cat.get(cat_id, []))

    def fetch_tabs(self, permalink):
        self.tab_calls.append(permalink)
        return (SegalTab("מידע כללי", "<p>כללי</p>"),
                SegalTab("פרטים טכניים", "<p>מידות: 125</p>"))


def _stores(existing=None):
    store = InMemoryStore(products=existing or [])
    eng = sqlalchemy.create_engine("sqlite:///:memory:")
    ps = SqlStoreProductStore(engine=eng, logger=get("test"))
    ps.create_schema()
    return store, ps


def _source(by_cat, cats):
    return SegalUnifiedSource(adapter=FakeSegalAdapter(by_cat), logger=LOG, category_ids=cats)


class TestSegalUnifiedPass:
    def test_new_product_enriched_and_created(self):
        store, ps = _stores()
        src = _source({58: [_prod("D-1")]}, {"dresser": 58})
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 1
        assert SKU("D-1") in {p.sku for p in store.list_products()}
        assert src.adapter.tab_calls == ["http://segal/p/D-1/"]   # enriched exactly once

    def test_existing_product_synced_without_tab_scrape(self):
        existing = [Product(sku=SKU("D-1"), vendor_product_id=VendorProductId("D-1"),
                            stock=StockLevel(1), published=True, store_product_id="1")]
        store, ps = _stores(existing)
        src = _source({58: [_prod("D-1", stock=9)]}, {"dresser": 58})
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 0
        assert s.stock_changes_applied == 1
        assert store.get(SKU("D-1")).stock == StockLevel(9)
        assert src.adapter.tab_calls == []       # existing product NOT scraped

    def test_oos_new_product_skipped(self):
        store, ps = _stores()
        src = _source({58: [_prod("D-1", stock=0)]}, {"dresser": 58})
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 0 and s.skipped_oos == 1
        assert src.adapter.tab_calls == []       # OOS never scraped either

    def test_uncategorized_new_product_skipped(self):
        store, ps = _stores()
        src = _source({99: [_prod("X-1", slug="not-a-mapped-category")]}, {"unknown": 99})
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 0 and s.skipped_uncategorized == 1

    def test_mixed_run(self):
        existing = [Product(sku=SKU("D-1"), vendor_product_id=VendorProductId("D-1"),
                            stock=StockLevel(2), published=True, store_product_id="1")]
        store, ps = _stores(existing)
        src = _source({58: [_prod("D-1", stock=7), _prod("D-2", stock=3),
                            _prod("D-3", stock=0)]}, {"dresser": 58})
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.stock_changes_applied == 1      # D-1 resynced
        assert s.created == 1                     # D-2 new+in-stock
        assert s.skipped_oos == 1                 # D-3 new+OOS
        assert src.adapter.tab_calls == ["http://segal/p/D-2/"]  # only the new in-stock one
