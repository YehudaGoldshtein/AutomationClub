"""Failing-first tests for store_products lifecycle (Phase 0 of Laura upload).

Pins the pending → approve → activate flow that lets ingest create draft products
and the dashboard confirm them, stored as columns on store_products (no new table).

Key invariant under test: the regular per-sync upsert_many must NOT clobber the
lifecycle columns — that's the whole reason a separate table is unnecessary.
"""
from __future__ import annotations

import pytest
import sqlalchemy

from sqlalchemy import text as sa_text

from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.log import get
from inventory_sync.persistence.migrations import add_store_products_lifecycle_columns
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

    def test_needs_review_reason_round_trips(self, store):
        store.write_pending(C, [_pending("N-1", pid="900", needs_review=True,
                                         needs_review_reason="no_price,no_image")])
        assert store.get(C, "N-1").needs_review_reason == "no_price,no_image"

    def test_needs_review_reason_defaults_none(self, store):
        store.write_pending(C, [_pending("N-2", pid="901")])
        assert store.get(C, "N-2").needs_review_reason is None

    def test_vendor_round_trips(self, store):
        store.write_pending(C, [_pending("N-3", pid="902", vendor="segal | סגל")])
        assert store.get(C, "N-3").vendor == "segal | סגל"


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


class TestMissingAtSource:
    """Flag/clear the dashboard's `missing_at_source` boolean, unified per product."""

    def test_flag_inserts_tracking_row_when_absent(self, store):
        # An existing store product we never onboarded (no row yet) → insert one, flagged.
        store.flag_missing_at_source(C, "700", sku="X-1", title="t",
                                     vendor="שניר | snir", published=True)
        rec = store.get(C, "X-1")
        assert rec is not None
        assert rec.missing_at_source is True
        assert rec.status == "active"      # it is live in the store
        assert rec.vendor == "שניר | snir"
        assert rec.needs_review is False   # missing-at-source is its own flag, not review-reason

    def test_flag_covers_all_variants_of_a_product(self, store):
        # Two variants share one store_product_id → one flag call flags both rows.
        store.write_pending(C, [_pending("V-1", pid="901"), _pending("V-2", pid="901")])
        store.flag_missing_at_source(C, "901")
        assert store.get(C, "V-1").missing_at_source is True
        assert store.get(C, "V-2").missing_at_source is True

    def test_flag_does_not_touch_existing_review_reason(self, store):
        store.write_pending(C, [_pending("D-1", pid="901", needs_review=True,
                                         needs_review_reason="no_image")])
        store.flag_missing_at_source(C, "901")
        rec = store.get(C, "D-1")
        assert rec.missing_at_source is True
        assert rec.needs_review_reason == "no_image"   # untouched
        assert rec.status == "draft"                   # existing row's status is NOT reset

    def test_flag_is_idempotent(self, store):
        store.flag_missing_at_source(C, "700", sku="X-1")
        store.flag_missing_at_source(C, "700", sku="X-1")
        assert store.get(C, "X-1").missing_at_source is True

    def test_clear_unsets_all_variants(self, store):
        store.write_pending(C, [_pending("V-1", pid="901"), _pending("V-2", pid="901")])
        store.flag_missing_at_source(C, "901")
        store.clear_missing_at_source(C, "901")
        assert store.get(C, "V-1").missing_at_source is False
        assert store.get(C, "V-2").missing_at_source is False

    def test_clear_leaves_other_review_reasons_intact(self, store):
        store.write_pending(C, [_pending("D-1", pid="901", needs_review=True,
                                         needs_review_reason="no_image")])
        store.flag_missing_at_source(C, "901")
        store.clear_missing_at_source(C, "901")
        rec = store.get(C, "D-1")
        assert rec.missing_at_source is False
        assert rec.needs_review_reason == "no_image" and rec.needs_review is True

    def test_clear_on_unknown_or_unflagged_is_noop(self, store):
        store.clear_missing_at_source(C, "NOPE")            # no rows → no error
        store.write_pending(C, [_pending("D-2", pid="902")])
        store.clear_missing_at_source(C, "902")             # not flagged → unchanged
        assert store.get(C, "D-2").missing_at_source is False


class TestUnarchiveCandidate:
    """`unarchive_candidate` mirrors each vendor pass's candidate set onto
    store_products so the dashboard lists candidates like missing_at_source.
    Replace-set within a scope: flag the set, clear scope rows no longer in it —
    but never touch rows outside the scope (so vendors don't clobber each other)."""

    def test_defaults_false(self, store):
        store.upsert_many(C, [_product("A-1")])
        assert store.get(C, "A-1").unarchive_candidate is False

    def test_sets_flag_for_candidate_skus(self, store):
        store.upsert_many(C, [_product("A-1"), _product("A-2")])
        store.set_unarchive_candidates(C, {"A-1"}, {"A-1", "A-2"})
        assert store.get(C, "A-1").unarchive_candidate is True
        assert store.get(C, "A-2").unarchive_candidate is False

    def test_clears_skus_no_longer_candidates(self, store):
        store.upsert_many(C, [_product("A-1"), _product("A-2")])
        store.set_unarchive_candidates(C, {"A-1", "A-2"}, {"A-1", "A-2"})
        store.set_unarchive_candidates(C, {"A-1"}, {"A-1", "A-2"})    # A-2 dropped off
        assert store.get(C, "A-1").unarchive_candidate is True
        assert store.get(C, "A-2").unarchive_candidate is False

    def test_empty_set_clears_within_scope(self, store):
        store.upsert_many(C, [_product("A-1")])
        store.set_unarchive_candidates(C, {"A-1"}, {"A-1"})
        store.set_unarchive_candidates(C, set(), {"A-1"})            # nothing is a candidate now
        assert store.get(C, "A-1").unarchive_candidate is False

    def test_other_vendor_scope_is_not_clobbered(self, store):
        # Vendor A flags A-1; vendor B then runs with its OWN scope {B-1} — A-1 must survive.
        store.upsert_many(C, [_product("A-1"), _product("B-1")])
        store.set_unarchive_candidates(C, {"A-1"}, {"A-1"})          # vendor A pass
        store.set_unarchive_candidates(C, {"B-1"}, {"B-1"})          # vendor B pass
        assert store.get(C, "A-1").unarchive_candidate is True       # NOT cleared by B
        assert store.get(C, "B-1").unarchive_candidate is True

    def test_scoped_per_customer(self, store):
        store.upsert_many(C, [_product("A-1")])
        store.upsert_many(OTHER, [_product("A-1")])
        store.set_unarchive_candidates(C, {"A-1"}, {"A-1"})          # must not touch OTHER
        assert store.get(C, "A-1").unarchive_candidate is True
        assert store.get(OTHER, "A-1").unarchive_candidate is False

    def test_does_not_touch_other_lifecycle_fields(self, store):
        store.write_pending(C, [_pending("D-1", pid="901", needs_review=True,
                                         needs_review_reason="no_image")])
        store.set_unarchive_candidates(C, {"D-1"}, {"D-1"})
        rec = store.get(C, "D-1")
        assert rec.unarchive_candidate is True
        assert rec.status == "draft" and rec.needs_review_reason == "no_image"


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


class TestMigration:
    """The migration that upgrades an EXISTING (pre-lifecycle) store_products table."""

    def _legacy_engine(self):
        engine = sqlalchemy.create_engine("sqlite:///:memory:")
        with engine.begin() as conn:
            conn.execute(sa_text(
                "CREATE TABLE store_products ("
                "customer_id TEXT NOT NULL, sku TEXT NOT NULL, handle TEXT, title TEXT, "
                "store_product_id TEXT, updated_at TIMESTAMP NOT NULL, "
                "PRIMARY KEY (customer_id, sku))"
            ))
            conn.execute(sa_text(
                "INSERT INTO store_products "
                "(customer_id, sku, handle, title, store_product_id, updated_at) "
                "VALUES ('maxbaby', 'OLD-1', 'h', 't', '100', '2026-01-01 00:00:00')"
            ))
        return engine

    def test_adds_columns_and_backfills_existing_rows_active(self):
        engine = self._legacy_engine()
        added = add_store_products_lifecycle_columns(engine)
        assert set(added) == {"status", "approved", "approved_at", "is_new_collection",
                              "needs_review", "needs_review_reason", "vendor", "missing_at_source",
                              "unarchive_candidate"}
        rec = SqlStoreProductStore(engine=engine, logger=get("test")).get("maxbaby", "OLD-1")
        assert rec.status == "active"   # pre-existing live products are not swept into review
        assert rec.approved is True

    def test_idempotent_second_run_is_noop(self):
        engine = self._legacy_engine()
        add_store_products_lifecycle_columns(engine)
        assert add_store_products_lifecycle_columns(engine) == []

    def test_noop_when_table_absent(self):
        engine = sqlalchemy.create_engine("sqlite:///:memory:")
        assert add_store_products_lifecycle_columns(engine) == []
