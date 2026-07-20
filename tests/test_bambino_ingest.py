"""Tests for the Bambino ingest driver (Phase 3).

Skip (existing/OOS/uncategorized), create per color group, backfill related_
products among siblings. Error-isolated + dry-run.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import sqlalchemy

from inventory_sync.adapters.shopify import ShopifyError
from inventory_sync.bambino_ingest import ingest_bambino
from inventory_sync.bambino_source import BambinoProduct
from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.fakes import InMemoryStore
from inventory_sync.log import get
from inventory_sync.persistence.store_product_store import SqlStoreProductStore

C = "maxbaby"
LOG = get("test")
TODAY = date(2026, 7, 20)


def _p(sku, *, brand="Graco", types=(28,), stock=5, price="399",
       group=None, is_main=True, color="שחור", images=("http://img/1.jpg",)) -> BambinoProduct:
    return BambinoProduct(
        id=int(sku[-6:]) if sku[-6:].isdigit() else 1,
        catalog_number=sku, title="עגלת", name=f"N{sku}", color=color, brand=brand,
        description_html="<p>d</p>", specifications_html="<ul><li>x</li></ul>",
        price=Decimal(price) if price is not None else None, quantity=stock, barcode="b",
        image_urls=images, type_ids=tuple(types), type_names=(),
        is_main_color=is_main, main_color_product_id=group,
        age_from=0, age_to=12, weight="", height="", width="", length="",
        standard="", isofix="", video_urls=(), product_manual="",
        related_product_ids=(), discount=None, meta_title="", meta_description="",
    )


class FakeSource:
    def __init__(self, products, warranties=None):
        self._products = products
        self._warranties = warranties or {}

    def fetch_all_products(self):
        return list(self._products)

    def warranties(self):
        return dict(self._warranties)


def _stores(existing=None):
    store = InMemoryStore(products=existing or [])
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    ps = SqlStoreProductStore(engine=engine, logger=get("test"))
    ps.create_schema()
    return store, ps


def _run(products, store, ps, **kw):
    return ingest_bambino(FakeSource(products), store, ps, C, LOG, today=TODAY, **kw)


class TestCreate:
    def test_creates_draft_and_writes_pending(self):
        store, ps = _stores()
        s = _run([_p("100000001")], store, ps)
        assert s.created == 1
        assert SKU("100000001") in {p.sku for p in store.list_products()}
        pending = ps.list_pending(C)
        assert {r.sku for r in pending} == {"100000001"}
        assert pending[0].status == "draft"

    def test_attaches_brand_and_category_collections(self):
        store, ps = _stores()
        _run([_p("100000001", brand="Graco", types=(28,))], store, ps)
        # brand collection + category collection = 2 collects
        assert len(store.collects) == 2

    def test_sets_real_stock(self):
        store, ps = _stores()
        _run([_p("100000001", stock=7)], store, ps)
        assert store.get(SKU("100000001")).stock == StockLevel(7)

    def test_needs_review_when_no_price_or_no_image(self):
        store, ps = _stores()
        _run([_p("100000001", price=None), _p("100000002", images=())], store, ps)
        review = {r.sku: (r.needs_review, r.needs_review_reason) for r in ps.list_pending(C)}
        assert review["100000001"] == (True, "no_price")
        assert review["100000002"] == (True, "no_image")

    def test_no_review_reason_when_clean(self):
        store, ps = _stores()
        _run([_p("100000009")], store, ps)
        rec = ps.list_pending(C)[0]
        assert rec.needs_review is False and rec.needs_review_reason is None


class TestSkips:
    def test_skips_existing_sku(self):
        existing = [Product(sku=SKU("100000001"), vendor_product_id=VendorProductId("100000001"),
                            stock=StockLevel(1), published=True, store_product_id="1")]
        store, ps = _stores(existing)
        s = _run([_p("100000001")], store, ps)
        assert s.created == 0 and s.skipped_existing == 1

    def test_skips_out_of_stock(self):
        store, ps = _stores()
        s = _run([_p("100000001", stock=0)], store, ps)
        assert s.created == 0 and s.skipped_oos == 1
        assert store.list_products() == []

    def test_skips_uncategorized(self):
        # only Signature(37)/feeding(21)/hygiene(42) → not onboarded
        store, ps = _stores()
        s = _run([_p("100000001", types=(37,)), _p("100000002", types=(21,))], store, ps)
        assert s.created == 0 and s.skipped_uncategorized == 2

    def test_in_stock_created_alongside_oos_and_uncategorized(self):
        store, ps = _stores()
        s = _run([_p("100000001", stock=5), _p("100000002", stock=0),
                  _p("100000003", types=(37,))], store, ps)
        assert s.created == 1 and s.skipped_oos == 1 and s.skipped_uncategorized == 1


class TestColorGrouping:
    def test_related_products_backfilled_among_siblings(self):
        store, ps = _stores()
        # a group of 3 colors: main (700) + two variants pointing at it
        main = _p("100000700", group=None, is_main=True, color="שחור")
        v1 = _p("100000701", group=700, is_main=False, color="אדום")
        v2 = _p("100000702", group=700, is_main=False, color="כחול")
        s = _run([main, v1, v2], store, ps)
        assert s.created == 3 and s.linked == 3
        # each product references its two siblings (by GID)
        writes = {spid: mfs for spid, mfs in store.metafield_writes}
        assert len(writes) == 3
        for spid, mfs in writes.items():
            mf = mfs[0]
            assert mf.namespace == "custom" and mf.key == "related_products"
            gids = json.loads(mf.value)
            assert len(gids) == 2 and spid not in [g.split("/")[-1] for g in gids]
            assert all(g.startswith("gid://shopify/Product/") for g in gids)

    def test_singleton_group_gets_no_related(self):
        store, ps = _stores()
        s = _run([_p("100000001")], store, ps)
        assert s.created == 1 and s.linked == 0
        assert store.metafield_writes == []

    def test_oos_sibling_excluded_from_group(self):
        store, ps = _stores()
        main = _p("100000700", group=None, is_main=True)
        v_oos = _p("100000701", group=700, is_main=False, stock=0)
        v_ok = _p("100000702", group=700, is_main=False)
        s = _run([main, v_oos, v_ok], store, ps)
        # only 2 created → linked to each other; OOS one deferred
        assert s.created == 2 and s.skipped_oos == 1 and s.linked == 2


class TestDryRun:
    def test_counts_without_writing(self):
        store, ps = _stores()
        s = _run([_p("100000700", group=None, is_main=True),
                  _p("100000701", group=700, is_main=False)], store, ps, dry_run=True)
        assert s.would_create == 2 and s.created == 0
        assert store.list_products() == []
        assert store.metafield_writes == []
        assert ps.list_pending(C) == []


class TestErrorIsolation:
    def test_one_bad_product_does_not_fail_run(self):
        class FlakyStore(InMemoryStore):
            def create_product(self, draft):
                if draft.variants[0].sku == SKU("100000002"):
                    raise ShopifyError("boom")
                return super().create_product(draft)

        store = FlakyStore()
        _, ps = _stores()
        s = ingest_bambino(FakeSource([_p("100000001"), _p("100000002", images=())]),
                           store, ps, C, LOG, today=TODAY)
        assert s.created == 1 and s.errors == 1

    def test_bad_image_salvaged_without_images(self):
        class NoImageStore(InMemoryStore):
            def create_product(self, draft):
                if draft.image_urls:
                    raise ShopifyError("422 image")
                return super().create_product(draft)

        store = NoImageStore()
        _, ps = _stores()
        s = ingest_bambino(FakeSource([_p("100000001")]), store, ps, C, LOG, today=TODAY)
        assert s.created == 1 and s.errors == 0
