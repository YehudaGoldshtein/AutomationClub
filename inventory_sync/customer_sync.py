"""Customer-level sync entry point.

Composes the shared vendor-snapshot cache + the existing orchestrator
(run_sync_pass) + a Customer record into a single call. One customer, one
vendor per Phase A; multi-vendor comes in Phase B.

Secrets (Shopify token, WhatsApp bridge token, email API key) still come
from env — this module only consumes already-built pluggables.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from inventory_sync.customers import Customer
from inventory_sync.domain import SyncRun
from inventory_sync.interfaces import (
    ItemStateStore,
    StockPolicy,
    StorePlatform,
    SyncRunStore,
)
from inventory_sync.log import Logger
from inventory_sync.notifications import Notifier, PreviewNotifier
from inventory_sync.orchestrator import run_sync_pass
from inventory_sync.vendor_scan import CachedSupplier


class _CatalogAwareSupplier(Protocol):
    def fetch_catalog_skus(self) -> set[str]: ...
    def fetch_snapshots(self, ids): ...


class _VendorCache(Protocol):
    def get_fresh(self, vendor_name, ids, ttl_minutes, now=None): ...
    def upsert_many(self, vendor_name, snapshots, now=None): ...


class _CustomerRepo(Protocol):
    def mark_synced(self, customer_id: str, when: datetime | None = None) -> None: ...


class _StoreProductStore(Protocol):
    def upsert_many(self, customer_id: str, products) -> None: ...


def customer_sync_pass(
    *,
    customer: Customer,
    store: StorePlatform,
    supplier: _CatalogAwareSupplier,
    cache: _VendorCache,
    policy: StockPolicy,
    notifier: Notifier | PreviewNotifier,
    item_state_store: ItemStateStore,
    sync_run_store: SyncRunStore,
    customer_repo: _CustomerRepo | None,
    logger: Logger,
    ttl_minutes: int | None = None,
    store_product_store: _StoreProductStore | None = None,
) -> SyncRun:
    """Run one sync pass for a customer; vendor fetches go through the shared cache.

    `ttl_minutes` defaults to min(customer.sync_interval_minutes, 60) — cache
    freshness matches the cadence so each customer sees data <= one interval old.
    """
    if not customer.vendors:
        raise ValueError(f"customer {customer.id!r} has no vendors configured")
    binding = customer.vendors[0]
    vendor_name = binding.name
    ttl = ttl_minutes if ttl_minutes is not None else min(customer.sync_interval_minutes, 60)

    log = logger.bind(customer_id=customer.id, vendor=vendor_name)
    log.info(
        "customer_sync_start",
        sync_interval_minutes=customer.sync_interval_minutes,
        ttl_minutes=ttl,
    )

    cached_supplier = CachedSupplier(
        inner=supplier,
        cache=cache,
        vendor_name=vendor_name,
        ttl_minutes=ttl,
        logger=log,
    )

    run = run_sync_pass(
        store=store,
        supplier=cached_supplier,
        policy=policy,
        notifier=notifier,
        item_state_store=item_state_store,
        sync_run_store=sync_run_store,
        logger=log,
        vendor_name=vendor_name,
        customer_id=customer.id,
        store_display_name=customer.store.display_name or customer.display_name,
        store_product_store=store_product_store,
    )

    if customer_repo is not None:
        customer_repo.mark_synced(customer.id, when=datetime.now(timezone.utc))
    log.info("customer_sync_complete", run_id=run.run_id)
    return run
