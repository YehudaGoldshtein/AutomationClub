"""Unified supplier pass: stock-sync existing products + onboard new ones, in one run.

For API-source suppliers (Bambino, Segal, Snir) stock and catalog come from the
same place, so a single pass can do both each tick:

  1. list the catalog once (cheap);
  2. for SKUs already in the store -> stock sync (exact count via the policy);
  3. for SKUs not in the store, importable, in-stock -> enrich (expensive, e.g.
     Segal's per-product tab scrape) + create as a DRAFT (approval-gated);
  4. optionally link/notify.

The expensive per-item enrichment runs ONLY for genuinely-new products, so a
steady-state tick (no new items) costs about the same as a plain stock sync.

A supplier plugs in via the `UnifiedSource` protocol; see segal_pass.py. This is
the shared core — one engine, many suppliers (ARCHITECTURE: pluggable seams).
See tests/test_supplier_pass.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Callable, Protocol

from inventory_sync.domain import (
    SKU,
    ProductDraft,
    StockLevel,
    VendorProductId,
    VendorProductSnapshot,
)
from inventory_sync.engine import SyncEngine
from inventory_sync.persistence.store_product_store import NewStoreProduct


class UnifiedSource(Protocol):
    """What the pass needs from a supplier. Cheap list + per-item accessors; the
    expensive enrichment is deferred to `enrich_to_draft` (new items only)."""

    def list_catalog(self) -> list: ...          # cheap catalog list (with stock)
    def sku(self, item) -> str: ...
    def in_stock(self, item) -> bool: ...
    def is_importable(self, item) -> bool: ...
    def snapshot(self, item) -> VendorProductSnapshot: ...   # existing-product stock sync
    def enrich_to_draft(self, item) -> ProductDraft: ...     # expensive; new items only
    def collections_for(self, item) -> tuple[str, ...]: ...
    def needs_review(self, item, draft: ProductDraft) -> bool: ...
    def link_new(self, created: list[tuple[object, str]], store, logger) -> int: ...


@dataclass
class UnifiedPassSummary:
    # stock sync (existing products)
    items_checked: int = 0
    stock_changes_applied: int = 0
    stock_errors: int = 0
    # onboarding (new products)
    created: int = 0
    skipped_oos: int = 0
    skipped_uncategorized: int = 0
    linked: int = 0
    create_errors: int = 0
    would_create: int = 0
    dry_run: bool = False
    new_skus: list[str] = field(default_factory=list)


def _create_and_record(source, store, product_store, customer_id, item, draft, needs_review, logger) -> str:
    """Create one draft, attach collections, set stock, record pending. Returns id."""
    created = store.create_product(draft)
    is_new_collection = False
    for name in source.collections_for(item):
        ref = store.ensure_collection(name)
        store.add_to_collection(created.store_product_id, ref.id)
        is_new_collection = is_new_collection or ref.created
    for v in draft.variants:
        if v.inventory_quantity is not None:
            store.update_stock(v.sku, StockLevel(v.inventory_quantity))
    product_store.write_pending(customer_id, [
        NewStoreProduct(
            sku=source.sku(item),
            store_product_id=created.store_product_id,
            title=draft.title,
            is_new_collection=is_new_collection,
            needs_review=needs_review,
        )
    ])
    logger.info("unified_pass_created", title=draft.title, sku=source.sku(item),
                store_product_id=created.store_product_id, needs_review=needs_review)
    return created.store_product_id


def _create_with_salvage(source, store, product_store, customer_id, item, draft, needs_review,
                         logger, summary) -> str | None:
    """Create; on failure retry once without images (the usual 422). None on hard fail."""
    try:
        return _create_and_record(source, store, product_store, customer_id, item, draft,
                                  needs_review, logger)
    except Exception as first_err:
        if draft.image_urls:
            try:
                spid = _create_and_record(source, store, product_store, customer_id, item,
                                          replace(draft, image_urls=()), True, logger)
                logger.warning("unified_pass_created_without_image", sku=source.sku(item),
                               error=str(first_err)[:200])
                return spid
            except Exception as retry_err:
                first_err = retry_err
        logger.error("unified_pass_create_failed", sku=source.sku(item), error=str(first_err)[:200])
        summary.create_errors += 1
        return None


def unified_pass(source: UnifiedSource, store, product_store, policy, customer_id: str, logger,
                 *, dry_run: bool = False, today: date | None = None,
                 on_new_drafts: Callable[[list[str]], None] | None = None) -> UnifiedPassSummary:
    """One pass: stock-sync existing supplier products + onboard new in-stock ones."""
    summary = UnifiedPassSummary(dry_run=dry_run)
    _ = today or date.today()

    items = source.list_catalog()
    store_products = store.list_products()
    existing = {str(p.sku) for p in store_products}
    listed_skus = {source.sku(it) for it in items if source.sku(it)}

    # --- 1. stock sync for products already in the store ---
    snapshots: dict[VendorProductId, VendorProductSnapshot] = {}
    for it in items:
        s = source.sku(it)
        if s and s in existing:
            snapshots[VendorProductId(s)] = source.snapshot(it)
    targets = [p for p in store_products if str(p.sku) in listed_skus]
    run = SyncEngine(store=store, supplier=source, policy=policy, logger=logger).run_with_data(
        targets, snapshots)
    summary.items_checked = run.items_checked
    summary.stock_changes_applied = len(run.changes_applied)
    summary.stock_errors = len(run.errors)

    # --- 2. onboard new products (not yet in the store) ---
    created: list[tuple[object, str]] = []
    seen: set[str] = set()
    for it in items:
        s = source.sku(it)
        if not s or s in seen:
            continue
        seen.add(s)
        if s in existing:
            continue  # handled by stock sync above
        if not source.is_importable(it):
            summary.skipped_uncategorized += 1
            logger.info("unified_pass_skip_uncategorized", sku=s)
            continue
        if not source.in_stock(it):
            summary.skipped_oos += 1  # cross-supplier OOS rule
            logger.info("unified_pass_skip_oos", sku=s)
            continue
        if dry_run:
            summary.would_create += 1
            logger.info("unified_pass_would_create", sku=s)
            continue
        draft = source.enrich_to_draft(it)
        needs_review = source.needs_review(it, draft)
        spid = _create_with_salvage(source, store, product_store, customer_id, it, draft,
                                    needs_review, logger, summary)
        if spid is not None:
            summary.created += 1
            summary.new_skus.append(s)
            created.append((it, spid))

    if created:
        summary.linked = source.link_new(created, store, logger)
        if on_new_drafts is not None:
            on_new_drafts(summary.new_skus)

    logger.info("unified_pass_summary", customer_id=customer_id,
                items_checked=summary.items_checked,
                stock_changes_applied=summary.stock_changes_applied,
                stock_errors=summary.stock_errors, created=summary.created,
                skipped_oos=summary.skipped_oos,
                skipped_uncategorized=summary.skipped_uncategorized,
                linked=summary.linked, create_errors=summary.create_errors,
                would_create=summary.would_create, dry_run=dry_run)
    return summary
