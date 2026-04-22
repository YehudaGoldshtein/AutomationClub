"""The sync engine. Orchestrates supplier -> policy -> store for one run.

Knows nothing about Shopify or any vendor — it speaks only the domain types
and the interfaces in `inventory_sync.interfaces`.
"""
from __future__ import annotations

from dataclasses import dataclass

from inventory_sync.domain import (
    ChangeKind,
    StockChange,
    SyncError,
    SyncRun,
)
from inventory_sync.interfaces import (
    StockPolicy,
    StorePlatform,
    SupplierSource,
)
from inventory_sync.log import Logger


@dataclass
class SyncEngine:
    store: StorePlatform
    supplier: SupplierSource
    policy: StockPolicy
    logger: Logger

    def run(self) -> SyncRun:
        run = SyncRun()
        log = self.logger.bind(run_id=run.run_id)
        log.info("sync_start")

        products = self._safe_list_products(run, log)
        if products is None:
            return self._finish(run, log, aborted=True, reason="store unreachable")

        run.items_checked = len(products)
        log.info("catalog_loaded", count=run.items_checked)

        vendor_ids = [p.vendor_product_id for p in products]
        snapshots = self._safe_fetch_snapshots(vendor_ids, run, log)
        if snapshots is None:
            return self._finish(run, log, aborted=True, reason="supplier unreachable")

        log.info(
            "vendor_snapshots_loaded",
            returned=len(snapshots),
            requested=len(vendor_ids),
        )

        for product in products:
            snapshot = snapshots.get(product.vendor_product_id)
            if snapshot is None:
                log.warning(
                    "vendor_missing_product",
                    sku=product.sku,
                    vendor_product_id=product.vendor_product_id,
                )
                run.errors.append(
                    SyncError(
                        message=f"vendor did not return snapshot for {product.vendor_product_id}",
                        sku=product.sku,
                    )
                )
                continue

            changes = self.policy.decide(product, snapshot)
            for change in changes:
                run.changes_planned.append(change)
                if self._safe_apply(change, run, log):
                    run.changes_applied.append(change)

        return self._finish(run, log, aborted=False)

    def _safe_list_products(self, run: SyncRun, log: Logger):
        try:
            return self.store.list_products()
        except Exception as e:
            log.exception("store_list_failed")
            run.errors.append(SyncError(message=f"store.list_products failed: {e}"))
            return None

    def _safe_fetch_snapshots(self, vendor_ids, run: SyncRun, log: Logger):
        try:
            return self.supplier.fetch_snapshots(vendor_ids)
        except Exception as e:
            log.exception("supplier_fetch_failed")
            run.errors.append(SyncError(message=f"supplier.fetch_snapshots failed: {e}"))
            return None

    def _safe_apply(self, change: StockChange, run: SyncRun, log: Logger) -> bool:
        try:
            self._apply(change)
        except Exception as e:
            log.exception("change_failed", sku=change.sku, kind=change.kind.value)
            run.errors.append(
                SyncError(
                    message=f"{change.kind.value} on {change.sku} failed: {e}",
                    sku=change.sku,
                )
            )
            return False
        log.info(
            "change_applied",
            sku=change.sku,
            kind=change.kind.value,
            reason=change.reason,
        )
        return True

    def _apply(self, change: StockChange) -> None:
        if change.kind is ChangeKind.SET_STOCK:
            if change.new_stock is None:
                raise ValueError(f"SET_STOCK change without new_stock: {change}")
            self.store.update_stock(change.sku, change.new_stock)
        elif change.kind is ChangeKind.UNPUBLISH:
            self.store.unpublish(change.sku)
        elif change.kind is ChangeKind.REPUBLISH:
            self.store.republish(change.sku)
        else:
            raise ValueError(f"unknown change kind: {change.kind}")

    def _finish(self, run: SyncRun, log: Logger, aborted: bool, reason: str = "") -> SyncRun:
        run.finish()
        event = "sync_aborted" if aborted else "sync_complete"
        log.info(
            event,
            items_checked=run.items_checked,
            changes_planned=len(run.changes_planned),
            changes_applied=len(run.changes_applied),
            errors=len(run.errors),
            duration_seconds=run.duration_seconds,
            reason=reason,
        )
        return run
