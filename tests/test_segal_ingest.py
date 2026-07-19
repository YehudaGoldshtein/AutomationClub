"""Failing-first tests for the Segal ingest driver (Phase 3).

Iterate the in-scope categories, dedup by SKU, skip existing, create net-new
drafts (+ collections + real stock + metafields), record pending. Error-isolated
+ dry-run, mirroring laura_ingest's shape but for single-variant products.
"""
from __future__ import annotations

from decimal import Decimal

import sqlalchemy

from inventory_sync.adapters.shopify import ShopifyError
from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.fakes import InMemoryStore
from inventory_sync.log import get
from inventory_sync.persistence.store_product_store import SqlStoreProductStore
from inventory_sync.segal_ingest import ingest_segal
from inventory_sync.segal_source import SegalProduct, SegalTab

C = "maxbaby"
LOG = get("test")


def _sp(sku, slug="dresser", stock=5, image=("http://img/1.jpg",), tabs=()) -> SegalProduct:
    return SegalProduct(
        sku=sku, name=f"מוצר {sku}", description_html="<p>d</p>",
        price=Decimal("2198"), sale_price=Decimal("2198"), on_sale=False,
        image_urls=image, category_slugs=(slug, "segal-baby"),
        permalink=f"http://segal/p/{sku}/", in_stock=stock > 0,
        stock_qty=stock, tabs=tabs,
    )


class FakeSource:
    """Stands in for SegalBabyStoreApiAdapter: category_id -> list[SegalProduct]."""

    def __init__(self, by_category: dict[int, list[SegalProduct]]):
        self.by_category = by_category

    def fetch_products(self, category_id: int) -> list[SegalProduct]:
        return list(self.by_category.get(category_id, []))


def _stores(existing=None):
    store = InMemoryStore(products=existing or [])
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    ps = SqlStoreProductStore(engine=engine, logger=get("test"))
    ps.create_schema()
    return store, ps


class TestCreate:
    def test_creates_draft_and_writes_pending(self):
        store, ps = _stores()
        src = FakeSource({58: [_sp("D-1")]})
        summary = ingest_segal(src, store, ps, C, LOG, category_ids={"dresser": 58})
        assert summary.created == 1
        assert SKU("D-1") in {p.sku for p in store.list_products()}
        pending = ps.list_pending(C)
        assert {r.sku for r in pending} == {"D-1"}
        assert pending[0].status == "draft"

    def test_attaches_mapped_collection(self):
        store, ps = _stores()
        src = FakeSource({58: [_sp("D-1", slug="dresser")]})
        ingest_segal(src, store, ps, C, LOG, category_ids={"dresser": 58})
        # dresser -> single collection "שידות החתלה"
        assert len(store.collects) == 1
        assert ps.list_pending(C)[0].is_new_collection is True

    def test_storage_attaches_two_collections(self):
        store, ps = _stores()
        src = FakeSource({227: [_sp("S-1", slug="storage-segal-baby")]})
        ingest_segal(src, store, ps, C, LOG, category_ids={"storage-segal-baby": 227})
        assert len(store.collects) == 2

    def test_sets_real_initial_stock(self):
        store, ps = _stores()
        src = FakeSource({58: [_sp("D-1", stock=42)]})
        ingest_segal(src, store, ps, C, LOG, category_ids={"dresser": 58})
        assert store.get(SKU("D-1")).stock.value == 42


class TestDedup:
    def test_same_sku_across_categories_created_once(self):
        store, ps = _stores()
        dup = _sp("DUP-1", slug="dresser")
        src = FakeSource({58: [dup], 352: [dup]})
        summary = ingest_segal(src, store, ps, C, LOG,
                               category_ids={"dresser": 58, "soft-close-dresser": 352})
        assert summary.created == 1
        assert len([p for p in store.list_products() if p.sku == SKU("DUP-1")]) == 1


class TestSkip:
    def test_skips_existing_sku(self):
        existing = [Product(SKU("D-1"), VendorProductId("D-1"), StockLevel(1),
                            published=True, title="מוצר D-1")]
        store, ps = _stores(existing)
        src = FakeSource({58: [_sp("D-1")]})
        summary = ingest_segal(src, store, ps, C, LOG, category_ids={"dresser": 58})
        assert summary.created == 0
        assert summary.skipped_existing == 1
        assert ps.list_pending(C) == []


class TestOutOfStock:
    def test_oos_product_is_not_onboarded(self):
        # Cross-supplier rule: a product OOS at source is not created as a new draft.
        store, ps = _stores()
        src = FakeSource({58: [_sp("OOS-1", stock=0)]})
        summary = ingest_segal(src, store, ps, C, LOG, category_ids={"dresser": 58})
        assert summary.created == 0
        assert summary.skipped_oos == 1
        assert store.list_products() == []
        assert ps.list_pending(C) == []

    def test_in_stock_still_created_alongside_oos(self):
        store, ps = _stores()
        src = FakeSource({58: [_sp("OOS-1", stock=0), _sp("GOOD-1", stock=3)]})
        summary = ingest_segal(src, store, ps, C, LOG, category_ids={"dresser": 58})
        assert summary.created == 1
        assert summary.skipped_oos == 1
        assert {p.sku for p in store.list_products()} == {SKU("GOOD-1")}

    def test_dry_run_counts_oos_skip_without_would_create(self):
        store, ps = _stores()
        src = FakeSource({58: [_sp("OOS-1", stock=0)]})
        summary = ingest_segal(src, store, ps, C, LOG,
                               category_ids={"dresser": 58}, dry_run=True)
        assert summary.would_create == 0
        assert summary.skipped_oos == 1


class TestNeedsReview:
    def test_missing_image_flags_review(self):
        store, ps = _stores()
        src = FakeSource({58: [_sp("D-1", image=())]})
        ingest_segal(src, store, ps, C, LOG, category_ids={"dresser": 58})
        assert ps.list_pending(C)[0].needs_review is True


class _FlakyStore(InMemoryStore):
    def __init__(self, fail_skus=(), fail_if_image=False, **kw):
        super().__init__(**kw)
        self.fail_skus = set(fail_skus)
        self.fail_if_image = fail_if_image

    def create_product(self, draft):
        if self.fail_if_image and draft.image_urls:
            raise ShopifyError('422: {"errors":{"product":["Image URL is invalid"]}}')
        if any(str(v.sku) in self.fail_skus for v in draft.variants):
            raise ShopifyError("boom")
        return super().create_product(draft)


class TestErrorIsolation:
    def test_one_bad_product_does_not_abort_batch(self):
        store = _FlakyStore(fail_skus={"BAD-1"})
        engine = sqlalchemy.create_engine("sqlite:///:memory:")
        ps = SqlStoreProductStore(engine=engine, logger=get("test"))
        ps.create_schema()
        src = FakeSource({58: [_sp("BAD-1"), _sp("GOOD-1")]})
        summary = ingest_segal(src, store, ps, C, LOG, category_ids={"dresser": 58})
        assert summary.errors == 1
        assert summary.created == 1
        assert SKU("GOOD-1") in {p.sku for p in store.list_products()}

    def test_invalid_image_retries_without_image_and_flags_review(self):
        store = _FlakyStore(fail_if_image=True)
        engine = sqlalchemy.create_engine("sqlite:///:memory:")
        ps = SqlStoreProductStore(engine=engine, logger=get("test"))
        ps.create_schema()
        src = FakeSource({58: [_sp("D-1")]})
        summary = ingest_segal(src, store, ps, C, LOG, category_ids={"dresser": 58})
        assert summary.created == 1
        assert ps.get(C, "D-1").needs_review is True


class TestDryRun:
    def test_dry_run_makes_no_writes(self):
        store, ps = _stores()
        src = FakeSource({58: [_sp("D-1")]})
        summary = ingest_segal(src, store, ps, C, LOG, category_ids={"dresser": 58}, dry_run=True)
        assert store.list_products() == []
        assert ps.list_pending(C) == []
        assert summary.would_create == 1
        assert summary.created == 0
        assert summary.dry_run is True
