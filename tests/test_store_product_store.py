"""Failing-first tests for store_products lifecycle (Phase 0 of Laura upload).

Pins the pending → approve → activate flow that lets ingest create draft products
and the dashboard confirm them, stored as columns on store_products (no new table).

Key invariant under test: the regular per-sync upsert_many must NOT clobber the
lifecycle columns — that's the whole reason a separate table is unnecessary.
"""
from __future__ import annotations

import pytest
import sqlalchemy

from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.log import get
from inventory_sync.persistence.store_product_store import (
    NewStoreProduct,
    SqlStoreProductStore,
)

C = "maxbaby"
OTHER = "other"


def _store() -> SqlStoreProductStore:
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    store = SqlStoreProductStore(engine=engine, logger=get("test"))
    store.create_schema()
    return store


@pytest.fixture
def store() -> SqlStoreProductStore:
    return _store()


def _product(sku: str, handle: str = "h", title: str = "t", pid: str = "100") -> Product:
    return Product(
        sku=SKU(sku),
        vendor_product_id=VendorProductId(sku),
        stock=StockLevel(1),
        published=True,
        handle=handle,
        title=title,
        store_product_id=pid,
    )


def _pending(sku: str, pid: str, **kw) -> NewStoreProduct:
    return NewStoreProduct(sku=sku, store_product_id=pid, handle="h", title="t", **kw)


class TestUpsertDefaults:
    def test_sync_upserted_product_is_active_and_approved(self, store):
        """A product discovered by the normal sync is a live product: active + approved."""
        store.upsert_many(C, [_product("A-1")])
        rec = store.get(C, "A-1")
        assert rec is not None
        assert rec.status == "active"
        assert rec.approved is True

    def test_get_returns_none_for_unknown_sku(self, store):
        assert store.get(C, "NOPE") is None


class TestWritePending:
    def test_pending_product_is_draft_unapproved(self, store):
        store.write_pending(C, [_pending("N-1", pid="900")])
        rec = store.get(C, "N-1")
        assert rec.status == "draft"
        assert rec.approved is False
        assert rec.approved_at is None

    def test_flags_are_persisted(self, store):
        store.write_pending(C, [_pending("N-1", pid="900", is_new_collection=True, needs_review=True)])
        rec = store.get(C, "N-1")
        assert rec.is_new_collection is True
        assert rec.needs_review is True


class TestNonClobberInvariant:
    """The load-bearing property: hourly upsert_many refreshes metadata only."""

    def test_upsert_does_not_reset_draft_or_approval(self, store):
        store.write_pending(C, [_pending("N-1", pid="900")])
        # Next hourly sync sees the draft in list_products and refreshes its metadata.
        store.upsert_many(C, [_product("N-1", handle="new-handle", title="New Title", pid="900")])
        rec = store.get(C, "N-1")
        assert rec.status == "draft"        # NOT reset to active
        assert rec.approved is False        # NOT reset to approved
        assert rec.handle == "new-handle"   # metadata DID refresh
        assert rec.title == "New Title"


class TestListPending:
    def test_lists_only_unapproved_drafts(self, store):
        store.write_pending(C, [_pending("D-1", pid="901"), _pending("D-2", pid="902")])
        store.upsert_many(C, [_product("A-1")])  # active, not pending
        skus = {r.sku for r in store.list_pending(C)}
        assert skus == {"D-1", "D-2"}

    def test_scoped_to_customer(self, store):
        store.write_pending(C, [_pending("D-1", pid="901")])
        store.write_pending(OTHER, [_pending("D-9", pid="999")])
        assert {r.sku for r in store.list_pending(C)} == {"D-1"}


class TestApproveThenActivate:
    def test_approve_moves_from_pending_to_approved_drafts(self, store):
        store.write_pending(C, [_pending("D-1", pid="901")])
        store.mark_approved(C, "901")
        assert store.list_pending(C) == []
        approved = store.list_approved_drafts(C)
        assert {r.sku for r in approved} == {"D-1"}
        assert approved[0].approved is True
        assert approved[0].approved_at is not None

    def test_approve_covers_all_variants_of_a_product(self, store):
        # Two size variants share one store_product_id.
        store.write_pending(C, [_pending("D-1", pid="901"), _pending("D-2", pid="901")])
        store.mark_approved(C, "901")
        assert {r.sku for r in store.list_approved_drafts(C)} == {"D-1", "D-2"}

    def test_activate_flips_status(self, store):
        store.write_pending(C, [_pending("D-1", pid="901")])
        store.mark_approved(C, "901")
        store.mark_active(C, "901")
        rec = store.get(C, "D-1")
        assert rec.status == "active"
        assert store.list_approved_drafts(C) == []

    def test_approve_is_customer_scoped(self, store):
        store.write_pending(C, [_pending("D-1", pid="901")])
        store.write_pending(OTHER, [_pending("D-9", pid="901")])  # same pid, different tenant
        store.mark_approved(C, "901")
        assert store.get(OTHER, "D-9").approved is False
