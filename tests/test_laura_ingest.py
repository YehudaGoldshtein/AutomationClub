"""Failing-first tests for the Laura ingest core (Phase 3).

Two units:
  - parse_laura_xlsx: xlsx bytes -> list[LauraRow] (header-based column mapping)
  - ingest_products: group -> detect-new (skip existing) -> create draft +
    collections -> write_pending, with dry-run and needs_review flagging.
"""
from __future__ import annotations

import io
from decimal import Decimal

import openpyxl
import pytest
import sqlalchemy

from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.fakes import InMemoryStore
from inventory_sync.laura_mapping import CATEGORY_COLLECTION_ID
from inventory_sync.laura_ingest import IngestSummary, ingest_products, parse_laura_xlsx
from inventory_sync.laura_upload import LauraRow
from inventory_sync.log import get
from inventory_sync.persistence.store_product_store import SqlStoreProductStore

HEADERS = ["מקט", "ברקוד", "תיאור פריט", "מלרי זמין", "חדש",
           "תאור משפחה", "מחיר במחירון בסיס", "מחיר מומלץ", "טקסט", "link  -קישור לתמונה"]


def _xlsx(headers: list, data_rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in data_rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestParseXlsx:
    def test_parses_row_fields(self):
        data = _xlsx(HEADERS, [
            ["1000-1", 7290000000001, "בגד גוף לבן NB", "במלאי", None,
             "בגד גוף", 100, 199, "כותנה אורגנית", "http://img/1.jpg"],
        ])
        [row] = parse_laura_xlsx(data)
        assert row.sku == "1000-1"
        assert row.description == "בגד גוף לבן NB"
        assert row.family == "בגד גוף"
        assert row.text == "כותנה אורגנית"
        assert row.image_url == "http://img/1.jpg"
        assert row.recommended_price == Decimal("199")
        assert row.barcode == "7290000000001"  # coerced to str

    def test_header_order_independent(self):
        headers = ["תאור משפחה", "מקט", "מחיר מומלץ", "תיאור פריט"]
        data = _xlsx(headers, [["בגד גוף", "1000-2", 250, "חולצה כחול"]])
        [row] = parse_laura_xlsx(data)
        assert row.sku == "1000-2"
        assert row.family == "בגד גוף"
        assert row.recommended_price == Decimal("250")

    def test_skips_rows_without_sku(self):
        data = _xlsx(HEADERS, [
            ["1000-1", None, "א", None, None, "בגד גוף", None, 10, None, None],
            [None, None, "ריק", None, None, "בגד גוף", None, 10, None, None],
        ])
        assert [r.sku for r in parse_laura_xlsx(data)] == ["1000-1"]

    def test_missing_optional_columns_ok(self):
        headers = ["מקט", "תיאור פריט", "תאור משפחה"]
        data = _xlsx(headers, [["1000-3", "מוצר", "בגד גוף"]])
        [row] = parse_laura_xlsx(data)
        assert row.text is None
        assert row.image_url is None
        assert row.recommended_price is None


# --- ingest_products ---

def _stores(existing: list[Product] | None = None):
    store = InMemoryStore(products=existing or [])
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    ps = SqlStoreProductStore(engine=engine, logger=get("test"))
    ps.create_schema()
    return store, ps


def _row(sku, desc="בגד גוף לבן NB", family="בגד גוף",
         text="כותנה", image="http://img", price="199") -> LauraRow:
    return LauraRow(
        sku=sku, description=desc, family=family,
        barcode="b" + sku, text=text, image_url=image,
        recommended_price=Decimal(price) if price is not None else None,
    )


C = "maxbaby"
LOG = get("test")


class TestIngestCreate:
    def test_creates_new_product_and_writes_pending_draft(self):
        store, ps = _stores()
        summary = ingest_products([_row("N-1")], store, ps, C, LOG)
        assert summary.created == 1
        assert SKU("N-1") in {p.sku for p in store.list_products()}
        pending = ps.list_pending(C)
        assert {r.sku for r in pending} == {"N-1"}
        assert pending[0].status == "draft"

    def test_attaches_category_and_subcategory_collections(self):
        store, ps = _stores()
        ingest_products([_row("N-1")], store, ps, C, LOG)
        collection_ids = {cid for (_pid, cid) in store.collects}
        assert CATEGORY_COLLECTION_ID in collection_ids
        assert len(store.collects) == 2  # category + subcategory
        assert ps.list_pending(C)[0].is_new_collection is True  # subcategory freshly created

    def test_new_collection_flag_only_first_time(self):
        store, ps = _stores()
        ingest_products([_row("N-1", desc="בגד גוף לבן NB"),
                         _row("N-2", desc="בגד גוף שחור NB")], store, ps, C, LOG)
        flags = {r.sku: r.is_new_collection for r in ps.list_pending(C)}
        assert sum(flags.values()) == 1  # only the first product to hit the collection


class TestIngestSkip:
    def test_skips_existing_sku(self):
        existing = [Product(SKU("N-1"), VendorProductId("N-1"), StockLevel(1),
                            published=True, title="בגד גוף לבן")]
        store, ps = _stores(existing)
        summary = ingest_products([_row("N-1")], store, ps, C, LOG)
        assert summary.created == 0
        assert summary.skipped_existing == 1
        assert ps.list_pending(C) == []

    def test_duplicate_title_flagged_not_created(self):
        # New SKU, but its computed title collides with an existing product.
        existing = [Product(SKU("OLD-1"), VendorProductId("OLD-1"), StockLevel(1),
                            published=True, title="בגד גוף לבן")]
        store, ps = _stores(existing)
        summary = ingest_products([_row("N-9")], store, ps, C, LOG)
        assert summary.created == 0
        assert summary.flagged_review == 1
        assert SKU("N-9") not in {p.sku for p in store.list_products()}


class TestIngestNeedsReview:
    def test_unknown_family_flags_review_and_skips_subcategory(self):
        store, ps = _stores()
        ingest_products([_row("N-1", family="משפחה לא ידועה")], store, ps, C, LOG)
        pending = ps.list_pending(C)
        assert pending[0].needs_review is True
        assert len(store.collects) == 1  # category only; no subcategory resolved

    def test_missing_image_flags_review(self):
        store, ps = _stores()
        ingest_products([_row("N-1", image=None)], store, ps, C, LOG)
        assert ps.list_pending(C)[0].needs_review is True

    def test_missing_text_flags_review(self):
        store, ps = _stores()
        ingest_products([_row("N-1", text=None)], store, ps, C, LOG)
        assert ps.list_pending(C)[0].needs_review is True


class TestIngestDryRun:
    def test_dry_run_makes_no_writes(self):
        store, ps = _stores()
        summary = ingest_products([_row("N-1")], store, ps, C, LOG, dry_run=True)
        assert store.list_products() == []
        assert ps.list_pending(C) == []
        assert summary.would_create == 1
        assert summary.created == 0
        assert summary.dry_run is True
