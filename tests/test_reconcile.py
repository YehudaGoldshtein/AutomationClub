"""Failing-first tests for activation reconcile (Phase 4).

After the dashboard approves a draft (approved=true), the sync job flips it live:
list_approved_drafts → republish (Shopify status=active) → mark_active. Per product
(all variants share one store_product_id); errors on one product don't abort the rest.
"""
from __future__ import annotations

import sqlalchemy

from inventory_sync.domain import SKU, ProductDraft, VariantSpec
from inventory_sync.fakes import InMemoryStore
from inventory_sync.log import get
from inventory_sync.persistence.store_product_store import NewStoreProduct, SqlStoreProductStore
from inventory_sync.reconcile import (
    RejectSummary,
    ReconcileSummary,
    UnarchiveSummary,
    reconcile_approved_drafts,
    reconcile_rejected_drafts,
    reconcile_unarchive_requests,
)

C = "maxbaby"
LOG = get("test")


def _stores():
    store = InMemoryStore(products=[])
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    ps = SqlStoreProductStore(engine=engine, logger=get("test"))
    ps.create_schema()
    return store, ps


def _create_draft(store, ps, skus, title="מוצר", approve=True) -> str:
    variants = tuple(VariantSpec(SKU(s)) for s in skus)
    created = store.create_product(ProductDraft(
        title=title, body_html="", vendor="v", product_type="t", tags="t",
        variants=variants, status="draft",
    ))
    ps.write_pending(C, [
        NewStoreProduct(sku=s, store_product_id=created.store_product_id, title=title)
        for s in skus
    ])
    if approve:
        ps.mark_approved(C, created.store_product_id)
    return created.store_product_id


class TestReconcile:
    def test_activates_approved_draft(self):
        store, ps = _stores()
        _create_draft(store, ps, ["D-1"])
        summary = reconcile_approved_drafts(store, ps, C, LOG)
        assert summary.activated == 1
        assert store.get(SKU("D-1")).published is True
        assert ps.get(C, "D-1").status == "active"
        assert ps.list_approved_drafts(C) == []

    def test_leaves_unapproved_drafts_alone(self):
        store, ps = _stores()
        _create_draft(store, ps, ["D-1"], approve=False)
        summary = reconcile_approved_drafts(store, ps, C, LOG)
        assert summary.activated == 0
        assert store.get(SKU("D-1")).published is False
        assert ps.get(C, "D-1").status == "draft"

    def test_multi_variant_product_activated_once(self):
        store, ps = _stores()
        _create_draft(store, ps, ["D-1", "D-2"], title="סט")
        summary = reconcile_approved_drafts(store, ps, C, LOG)
        assert summary.activated == 1  # one product, not two rows
        assert store.get(SKU("D-1")).published is True
        assert {ps.get(C, s).status for s in ("D-1", "D-2")} == {"active"}

    def test_no_approved_is_noop(self):
        store, ps = _stores()
        summary = reconcile_approved_drafts(store, ps, C, LOG)
        assert summary == ReconcileSummary()

    def test_error_on_one_product_is_isolated(self):
        store, ps = _stores()
        # Approved row whose product isn't in the store → republish will raise.
        ps.write_pending(C, [NewStoreProduct(sku="GHOST-1", store_product_id="99999", title="רפאים")])
        ps.mark_approved(C, "99999")
        summary = reconcile_approved_drafts(store, ps, C, LOG)
        assert summary.activated == 0
        assert summary.errors == 1
        assert ps.get(C, "GHOST-1").status == "draft"  # not marked active on failure


class TestRejectReconcile:
    def test_rejected_product_is_deleted(self):
        store, ps = _stores()
        pid = _create_draft(store, ps, ["D-1"], approve=False)
        ps.mark_rejected(C, pid)
        summary = reconcile_rejected_drafts(store, ps, C, LOG)
        assert summary.deleted == 1
        assert SKU("D-1") not in {p.sku for p in store.list_products()}  # gone from store
        assert ps.get(C, "D-1") is None                                 # row gone

    def test_non_rejected_drafts_untouched(self):
        store, ps = _stores()
        _create_draft(store, ps, ["D-1"], approve=False)  # draft, not rejected
        summary = reconcile_rejected_drafts(store, ps, C, LOG)
        assert summary.deleted == 0
        assert SKU("D-1") in {p.sku for p in store.list_products()}

    def test_multi_variant_rejected_deleted_once(self):
        store, ps = _stores()
        pid = _create_draft(store, ps, ["D-1", "D-2"], approve=False)
        ps.mark_rejected(C, pid)
        summary = reconcile_rejected_drafts(store, ps, C, LOG)
        assert summary.deleted == 1
        assert {ps.get(C, s) for s in ("D-1", "D-2")} == {None}

    def test_no_rejected_is_noop(self):
        store, ps = _stores()
        assert reconcile_rejected_drafts(store, ps, C, LOG) == RejectSummary()


class TestUnarchiveReconcile:
    """Unarchive reuses store_products.status: the dashboard sets
    status='unarchive_requested'; reconcile republishes by product id and marks active."""

    def test_republishes_requested_product_and_marks_active(self):
        store, ps = _stores()
        pid = _create_draft(store, ps, ["A-1"], approve=False)  # archived/draft in store
        assert store.get(SKU("A-1")).published is False
        ps.mark_unarchive_requested(C, pid)

        summary = reconcile_unarchive_requests(store, ps, C, LOG)

        assert summary.unarchived == 1
        assert store.get(SKU("A-1")).published is True   # now live
        assert ps.get(C, "A-1").status == "active"       # lifecycle settled
        assert ps.list_unarchive_requested(C) == []      # nothing left pending

    def test_multi_variant_product_unarchived_once(self):
        store, ps = _stores()
        pid = _create_draft(store, ps, ["A-1", "A-2"], title="סט", approve=False)
        ps.mark_unarchive_requested(C, pid)

        summary = reconcile_unarchive_requests(store, ps, C, LOG)

        assert summary.unarchived == 1  # one product, not two rows
        assert store.get(SKU("A-1")).published is True
        assert store.get(SKU("A-2")).published is True
        assert {ps.get(C, s).status for s in ("A-1", "A-2")} == {"active"}

    def test_leaves_non_requested_alone(self):
        store, ps = _stores()
        _create_draft(store, ps, ["A-1"], approve=False)  # status=draft, not requested
        summary = reconcile_unarchive_requests(store, ps, C, LOG)
        assert summary.unarchived == 0
        assert store.get(SKU("A-1")).published is False

    def test_no_requests_is_noop(self):
        store, ps = _stores()
        assert reconcile_unarchive_requests(store, ps, C, LOG) == UnarchiveSummary()

    def test_error_isolates_and_keeps_status(self):
        store, ps = _stores()
        # Requested row whose product isn't in the store → republish_by_id raises.
        ps.write_pending(C, [NewStoreProduct(sku="GHOST-1", store_product_id="88888", title="רפאים")])
        ps.mark_unarchive_requested(C, "88888")

        summary = reconcile_unarchive_requests(store, ps, C, LOG)

        assert summary.unarchived == 0 and summary.errors == 1
        assert ps.get(C, "GHOST-1").status == "unarchive_requested"  # not marked active on failure
