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
from inventory_sync.reconcile import ReconcileSummary, reconcile_approved_drafts

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
