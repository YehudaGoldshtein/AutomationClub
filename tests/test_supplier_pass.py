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
    price: int | None = None   # supplier target price (None = no price target)


@dataclass
class FakeSource:
    items: list
    enriched: list = field(default_factory=list)   # SKUs that got expensive enrichment
    linked_calls: list = field(default_factory=list)
    owned: tuple = ()                               # vendor tags this supplier owns

    def owned_vendors(self):
        return self.owned

    def catalog_skus(self):
        # full catalog = every item's sku (tests don't distinguish ingest subset)
        return {it.sku for it in self.items if it.sku}

    def price_target(self, it):
        from inventory_sync.pricing import resolve_target
        if it.price is None:
            return None
        return resolve_target(Decimal(str(it.price)), None)

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

    def needs_review_reason(self, it, draft):
        from inventory_sync import review_reasons
        return review_reasons.join(
            review_reasons.NO_COLLECTION if not it.collections else None,
            review_reasons.NO_IMAGE if not draft.image_urls else None,
        )

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


SNIR = "שניר | snir"


def _owned_product(sku, spid, vendor=SNIR, published=True):
    return Product(sku=SKU(sku), vendor_product_id=VendorProductId(sku),
                   stock=StockLevel(1), published=published, store_product_id=spid, vendor=vendor)


class TestMissingAtSource:
    """Existing store products that vanished from the supplier source get flagged."""

    def test_flags_owned_product_absent_from_catalog(self):
        existing = [_owned_product("GONE-1", "10"),
                    _owned_product("OTHER-1", "11", vendor="laura | לורה")]
        store, ps = _stores(existing)
        src = FakeSource([Item("KEEP-1")], owned=(SNIR,))   # catalog has KEEP-1 only
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert s.flagged_missing == 1
        assert ps.get(C, "GONE-1").missing_at_source is True
        assert ps.get(C, "OTHER-1") is None                 # different vendor → untouched

    def test_clears_flag_when_back_in_catalog(self):
        store, ps = _stores([_owned_product("BACK-1", "10")])
        ps.flag_missing_at_source(C, "10", sku="BACK-1", vendor=SNIR)
        src = FakeSource([Item("BACK-1")], owned=(SNIR,))   # now present again
        unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG)
        assert ps.get(C, "BACK-1").missing_at_source is False

    def test_no_owned_vendors_means_no_flagging(self):
        store, ps = _stores([_owned_product("GONE-1", "10")])
        s = _run([Item("KEEP-1")], store, ps)               # FakeSource.owned defaults ()
        assert s.flagged_missing == 0
        assert ps.get(C, "GONE-1") is None

    def test_dry_run_counts_but_does_not_write(self):
        store, ps = _stores([_owned_product("GONE-1", "10")])
        src = FakeSource([Item("KEEP-1")], owned=(SNIR,))
        s = unified_pass(src, store, ps, DefaultStockPolicy(), C, LOG, dry_run=True)
        assert s.flagged_missing == 1
        assert ps.get(C, "GONE-1") is None                  # no write on dry run


def _priced(sku, price, spid, compare_at=None):
    return Product(sku=SKU(sku), vendor_product_id=VendorProductId(sku), stock=StockLevel(1),
                   published=True, store_product_id=spid, price=Decimal(str(price)),
                   compare_at_price=Decimal(str(compare_at)) if compare_at is not None else None)


class TestPriceSync:
    """Price sync folded into the pass — off by default, gated by sync_prices."""

    def test_off_by_default(self):
        store, ps = _stores([_priced("E-1", 100, "1")])
        s = unified_pass(FakeSource([Item("E-1", price=90)]), store, ps,
                         DefaultStockPolicy(), C, LOG)
        assert s.prices_updated == 0
        assert store.get(SKU("E-1")).price == Decimal("100")     # untouched

    def test_updates_changed_price_when_enabled(self):
        store, ps = _stores([_priced("E-1", 100, "1")])
        s = unified_pass(FakeSource([Item("E-1", price=90)]), store, ps,
                         DefaultStockPolicy(), C, LOG, sync_prices=True)
        assert s.prices_updated == 1
        assert store.get(SKU("E-1")).price == Decimal("90")

    def test_dry_run_plans_but_does_not_write(self):
        store, ps = _stores([_priced("E-1", 100, "1")])
        s = unified_pass(FakeSource([Item("E-1", price=90)]), store, ps,
                         DefaultStockPolicy(), C, LOG, sync_prices=True, dry_run=True)
        assert s.prices_would_update == 1 and s.prices_updated == 0
        assert store.get(SKU("E-1")).price == Decimal("100")


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
