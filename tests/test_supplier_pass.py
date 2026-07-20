"""Tests for the generic unified pass (stock sync + onboard new, one run)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import sqlalchemy

from inventory_sync.adapters.shopify import ShopifyError
from inventory_sync.domain import (
    SKU,
    Product,
    ProductDraft,
    StockLevel,
    VariantSpec,
    VendorProductId,
    VendorProductSnapshot,
)
from inventory_sync.fakes import InMemoryStore
from inventory_sync.log import get
from inventory_sync.persistence.store_product_store import SqlStoreProductStore
from inventory_sync.policies import DefaultStockPolicy
from inventory_sync.supplier_pass import unified_pass

C = "maxbaby"
LOG = get("test")


@dataclass
class Item:
    sku: str
    in_stock: bool = True
    importable: bool = True
    stock_count: int = 5
    images: tuple = ("http://img/1.jpg",)
    collections: tuple = ("Coll",)


@dataclass
class FakeSource:
    items: list
    enriched: list = field(default_factory=list)   # SKUs that got expensive enrichment
    linked_calls: list = field(default_factory=list)

    def list_catalog(self):
        return list(self.items)

    def sku(self, it):
        return it.sku

    def in_stock(self, it):
        return it.in_stock

    def is_importable(self, it):
        return it.importable

    def snapshot(self, it):
        return VendorProductSnapshot(
            vendor_product_id=VendorProductId(it.sku),
            is_available=it.in_stock,
            stock_count=it.stock_count if it.in_stock else 0,
        )

    def enrich_to_draft(self, it):
        self.enriched.append(it.sku)  # track: only new items should be enriched
        return ProductDraft(
            title=f"P {it.sku}", body_html="<p>d</p>", vendor="v", product_type="", tags="",
            variants=(VariantSpec(SKU(it.sku), price=Decimal("100"),
                                  inventory_quantity=it.stock_count),),
            image_urls=it.images, status="draft",
        )

    def collections_for(self, it):
        return it.collections

    def needs_review(self, it, draft):
        return not draft.image_urls or not it.collections

    def link_new(self, created, store, logger):
        self.linked_calls.append([sku for _, spid in [] ] or [it.sku for it, _ in created])
        return len(created)


def _stores(existing=None):
    store = InMemoryStore(products=existing or [])
    eng = sqlalchemy.create_engine("sqlite:///:memory:")
    ps = SqlStoreProductStore(engine=eng, logger=get("test"))
    ps.create_schema()
    return store, ps


def _run(items, store, ps, **kw):
    return unified_pass(FakeSource(items), store, ps, DefaultStockPolicy(), C, LOG, **kw)


class TestOnboardNew:
    def test_creates_new_in_stock_importable(self):
        store, ps = _stores()
        s = _run([Item("N-1")], store, ps)
        assert s.created == 1
        assert SKU("N-1") in {p.sku for p in store.list_products()}
        assert ps.list_pending(C)[0].status == "draft"

    def test_skips_oos_and_uncategorized(self):
        store, ps = _stores()
        s = _run([Item("A"), Item("B", in_stock=False), Item("C", importable=False)], store, ps)
        assert s.created == 1 and s.skipped_oos == 1 and s.skipped_uncategorized == 1

    def test_needs_review_propagates(self):
        store, ps = _stores()
        _run([Item("N-1", images=())], store, ps)
        assert ps.list_pending(C)[0].needs_review is True


class TestStockSyncExisting:
    def test_existing_product_gets_stock_synced_not_recreated(self):
        existing = [Product(sku=SKU("E-1"), vendor_product_id=VendorProductId("E-1"),
                            stock=StockLevel(1), published=True, store_product_id="1")]
        store, ps = _stores(existing)
        src = FakeSource([Item("E-1", stock_count=9)])
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 0                      # not re-created
        assert s.stock_changes_applied == 1        # stock updated
        assert store.get(SKU("E-1")).stock == StockLevel(9)
        assert "E-1" not in src.enriched           # existing item never enriched (cheap tick)

    def test_only_new_items_enriched(self):
        existing = [Product(sku=SKU("E-1"), vendor_product_id=VendorProductId("E-1"),
                            stock=StockLevel(5), published=True, store_product_id="1")]
        store, ps = _stores(existing)
        src = FakeSource([Item("E-1", stock_count=5), Item("N-1")])
        unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert src.enriched == ["N-1"]             # E-1 (existing) skipped enrichment


class TestLinkAndNotify:
    def test_link_new_called_with_created(self):
        store, ps = _stores()
        src = FakeSource([Item("N-1"), Item("N-2")])
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.linked == 2 and len(src.linked_calls) == 1

    def test_on_new_drafts_callback_fires(self):
        store, ps = _stores()
        got = []
        unified_pass(FakeSource([Item("N-1")]), store, ps, DefaultStockPolicy(), C, LOG,
                     on_new_drafts=lambda skus: got.append(list(skus)))
        assert got == [["N-1"]]

    def test_callback_not_fired_when_no_new(self):
        store, ps = _stores()
        got = []
        unified_pass(FakeSource([]), store, ps, DefaultStockPolicy(), C, LOG,
                     on_new_drafts=lambda skus: got.append(skus))
        assert got == []


class TestDryRun:
    def test_counts_without_creating(self):
        store, ps = _stores()
        s = _run([Item("N-1"), Item("N-2")], store, ps, dry_run=True)
        assert s.would_create == 2 and s.created == 0
        assert store.list_products() == []
        assert ps.list_pending(C) == []


class TestErrorIsolation:
    def test_create_error_isolated(self):
        class Flaky(InMemoryStore):
            def create_product(self, draft):
                if draft.variants[0].sku == SKU("BAD"):
                    raise ShopifyError("boom")
                return super().create_product(draft)
        store = Flaky()
        _, ps = _stores()
        s = unified_pass(FakeSource([Item("OK"), Item("BAD", images=())]),
                         store, ps, DefaultStockPolicy(), C, LOG)
        assert s.created == 1 and s.create_errors == 1
