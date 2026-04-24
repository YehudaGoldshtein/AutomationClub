"""Sync orchestrator — the glue that runs one sync pass end to end.

Flow:
  1. Fetch vendor catalog (e.g., Laura sitemap) — cheap pre-filter.
  2. Fetch store's list of products; split into "vendor-has-it" vs "vendor-dropped-it".
  3. Fetch detail snapshots only for vendor-has-it (skip wasted 404s).
  4. Run SyncEngine on that slice to apply stock changes.
  5. Compute the `unarchive_candidate` set from the results (archived in store, available at vendor).
  6. Compare to stored state → delta.
  7. Dispatch aggregated summary message IF the delta is non-empty or the run had errors.
  8. On success, persist the new state. Persist the SyncRun.

Pluggable end to end — takes interfaces, not concrete adapters. Tested with fakes.
"""
from __future__ import annotations

from typing import Iterable, Protocol

from inventory_sync.deltas import compute_delta
from inventory_sync.domain import (
    SKU,
    Product,
    SyncRun,
    VendorProductId,
    VendorProductSnapshot,
)
from inventory_sync.engine import SyncEngine
from inventory_sync.interfaces import (
    ItemStateStore,
    StockPolicy,
    StorePlatform,
    SupplierSource,
    SyncRunStore,
)
from inventory_sync.log import Logger
from inventory_sync.notifications import EVENT_SYNC_SUMMARY, Notifier, PreviewNotifier


STATE_KEY_UNARCHIVE_CANDIDATE = "unarchive_candidate"


class _CatalogAwareSupplier(Protocol):
    """SupplierSource plus fetch_catalog_skus — Laura adapter matches this shape."""

    def fetch_catalog_skus(self) -> set[str]: ...
    def fetch_snapshots(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, VendorProductSnapshot]: ...


class _StoreProductStore(Protocol):
    def upsert_many(self, customer_id: str, products) -> None: ...


def run_sync_pass(
    *,
    store: StorePlatform,
    supplier: _CatalogAwareSupplier,
    policy: StockPolicy,
    notifier: Notifier | PreviewNotifier,
    item_state_store: ItemStateStore,
    sync_run_store: SyncRunStore,
    logger: Logger,
    vendor_name: str,
    customer_id: str,
    store_display_name: str = "your store",
    store_product_store: _StoreProductStore | None = None,
) -> SyncRun:
    log = logger.bind(customer_id=customer_id, vendor=vendor_name)
    log.info("sync_pass_start")

    # 1. Fetch vendor catalog.
    catalog = supplier.fetch_catalog_skus()
    log.info("catalog_loaded", sku_count=len(catalog))

    # 2. Fetch store catalog.
    try:
        all_products = store.list_products()
    except Exception as e:
        log.exception("store_list_failed")
        return _abort_empty_run(sync_run_store, log, customer_id=customer_id, message=f"store.list_products failed: {e}")
    log.info("store_products_loaded", count=len(all_products))

    # Persist per-customer store-side metadata (handle/title/product_id) so the
    # dashboard can build deep links without re-hitting the store API.
    if store_product_store is not None:
        try:
            store_product_store.upsert_many(customer_id, all_products)
        except Exception:
            log.exception("store_product_upsert_failed")

    # 3. Pre-filter: only fetch detail for products the vendor still carries.
    in_catalog_products = [p for p in all_products if p.vendor_product_id in catalog]
    out_of_catalog_products = [p for p in all_products if p.vendor_product_id not in catalog]
    log.info(
        "catalog_filter_applied",
        in_catalog=len(in_catalog_products),
        out_of_catalog=len(out_of_catalog_products),
    )

    # 4. Fetch detail snapshots for the filtered set.
    try:
        snapshots = supplier.fetch_snapshots(
            [p.vendor_product_id for p in in_catalog_products]
        )
    except Exception as e:
        log.exception("supplier_fetch_failed")
        return _abort_empty_run(sync_run_store, log, customer_id=customer_id, message=f"supplier.fetch_snapshots failed: {e}")

    # 5. Run the stock-sync engine on the filtered slice.
    engine = SyncEngine(store=store, supplier=supplier, policy=policy, logger=logger)
    run = engine.run_with_data(in_catalog_products, snapshots)
    # Out-of-catalog products were never in the run. Record them as vendor_missing.
    run.vendor_missing.extend(p.sku for p in out_of_catalog_products)

    # 6. Compute current unarchive-candidate set and delta vs stored.
    current = _compute_unarchive_candidates(all_products, snapshots)
    stored = item_state_store.get_active_skus(customer_id, vendor_name, STATE_KEY_UNARCHIVE_CANDIDATE)
    added, removed = compute_delta(current=current, stored=stored)

    is_first_run = not item_state_store.is_seeded(customer_id, vendor_name, STATE_KEY_UNARCHIVE_CANDIDATE)

    log.info(
        "delta_computed",
        current_count=len(current),
        stored_count=len(stored),
        newly_active=len(added),
        newly_inactive=len(removed),
        first_run=is_first_run,
    )

    # 7. Build + dispatch summary message if there's anything worth saying.
    subject, body = _build_summary_message(
        run=run, current=current, added=added, removed=removed,
        out_of_catalog_skus={p.sku for p in out_of_catalog_products},
        is_first_run=is_first_run,
        store_display_name=store_display_name,
    )
    if subject is not None:
        notifier.dispatch(EVENT_SYNC_SUMMARY, subject, body)

    # 8. Persist new state + run.
    item_state_store.set_active(customer_id, vendor_name, STATE_KEY_UNARCHIVE_CANDIDATE, current)
    try:
        sync_run_store.save(run, customer_id=customer_id)
    except Exception:
        log.exception("sync_run_persist_failed", run_id=run.run_id)

    log.info("sync_pass_complete", run_id=run.run_id, dispatched=subject is not None)
    return run


def _compute_unarchive_candidates(
    products: list[Product],
    snapshots: dict[VendorProductId, VendorProductSnapshot],
) -> set[str]:
    """A product is an unarchive candidate when: archived in store AND available at vendor."""
    out: set[str] = set()
    for p in products:
        if p.published:
            continue
        snap = snapshots.get(p.vendor_product_id)
        if snap is None:
            continue
        if snap.is_available:
            out.add(str(p.sku))
    return out


def _build_summary_message(
    *,
    run: SyncRun,
    current: set[str],
    added: set[str],
    removed: set[str],
    out_of_catalog_skus: set[SKU],
    is_first_run: bool,
    store_display_name: str,
) -> tuple[str | None, str]:
    """Return (subject, body). subject=None means 'nothing to say — skip dispatch'."""
    has_errors = len(run.errors) > 0
    has_deltas = bool(added or removed)

    # First run: dispatch a one-shot informational message with the full current state.
    if is_first_run:
        lines = [
            f"Inventory sync initial reconciliation for {store_display_name}.",
            "",
            f"Current unarchive candidates (products archived in your store that are in stock at the vendor): {len(current)}",
        ]
        if current:
            lines.append("")
            for sku in sorted(current):
                lines.append(f"  - {sku}")
        if out_of_catalog_skus:
            lines.append("")
            lines.append(f"Products no longer in vendor catalog (consider reviewing for cleanup): {len(out_of_catalog_skus)}")
        if has_errors:
            lines.append("")
            lines.append(f"Run errors: {len(run.errors)}")
        lines.append("")
        lines.append("This message won't repeat — subsequent runs only notify when things change.")
        subject = f"Inventory sync — initial reconciliation for {store_display_name}"
        return subject, "\n".join(lines)

    # Subsequent runs: only speak if deltas or errors happened.
    if not has_deltas and not has_errors:
        return None, ""

    lines = [f"Inventory sync update for {store_display_name}."]
    if added:
        lines.append("")
        lines.append(f"Newly unarchive candidates ({len(added)}):")
        for sku in sorted(added):
            lines.append(f"  + {sku}")
    if removed:
        lines.append("")
        lines.append(f"No longer unarchive candidates ({len(removed)}):")
        for sku in sorted(removed):
            lines.append(f"  - {sku}")
    if has_errors:
        lines.append("")
        lines.append(f"Run had {len(run.errors)} error(s):")
        for e in run.errors[:5]:
            lines.append(f"  - [{e.sku or '-'}] {e.message[:100]}")
        if len(run.errors) > 5:
            lines.append(f"  ... and {len(run.errors) - 5} more")
    lines.append("")
    lines.append(f"run_id={run.run_id}  items_checked={run.items_checked}  applied={len(run.changes_applied)}")

    subject = f"Inventory sync — {len(added)} new, {len(removed)} resolved"
    return subject, "\n".join(lines)


def _abort_empty_run(
    sync_run_store: SyncRunStore, log: Logger, *, customer_id: str, message: str
) -> SyncRun:
    from inventory_sync.domain import SyncError
    run = SyncRun()
    run.errors.append(SyncError(message=message))
    run.finish()
    try:
        sync_run_store.save(run, customer_id=customer_id)
    except Exception:
        log.exception("sync_run_persist_failed_on_abort", run_id=run.run_id)
    return run
