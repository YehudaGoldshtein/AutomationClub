"""Protocols for every external seam. Concrete implementations live in adapters/."""
from __future__ import annotations

from typing import Iterable, Protocol

from inventory_sync.domain import (
    SKU,
    Product,
    StockChange,
    StockLevel,
    SyncRun,
    VendorProductId,
    VendorProductSnapshot,
)


class StorePlatform(Protocol):
    """E-commerce store read/write (Shopify, Woo, etc.)."""

    def list_products(self) -> list[Product]: ...
    def update_stock(self, sku: SKU, stock: StockLevel) -> None: ...
    def unpublish(self, sku: SKU) -> None: ...
    def republish(self, sku: SKU) -> None: ...


class SupplierSource(Protocol):
    """Vendor product source (scraper, REST, CSV, email-parsed feed, etc.).

    Returns rich snapshots so each adapter can expose whatever fidelity its
    source supports — binary availability, exact counts, price, name, etc. —
    without being forced into a lossy projection.
    """

    def fetch_snapshots(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, VendorProductSnapshot]: ...


class NotificationChannel(Protocol):
    """A single notification delivery path (email, WhatsApp, SMS, etc.)."""

    def send(self, subject: str, body: str) -> None: ...


class StockPolicy(Protocol):
    """Decides what StockChanges are needed given the store's view and the vendor's snapshot."""

    def decide(
        self, product: Product, snapshot: VendorProductSnapshot
    ) -> list[StockChange]: ...


class SyncRunStore(Protocol):
    """Persistence for sync-run history. Backend-agnostic: sqlite dev, postgres prod."""

    def save(self, run: SyncRun, customer_id: str) -> None: ...
    def get(self, run_id: str) -> SyncRun | None: ...
    def list_recent(self, limit: int = 20) -> list[SyncRun]: ...


class ItemStateStore(Protocol):
    """Per-`(customer_id, vendor_name, state_key, sku)` state tracking.

    Used by the sync engine to detect transitions — a SKU's membership in a
    state like 'unarchive_candidate' going from absent to present (or vice
    versa) between runs.

    `is_seeded` distinguishes 'never observed this state_key yet' (first run)
    from 'observed and found nothing active' (legitimate empty). Scoped per
    customer so two tenants carrying the same SKU from the same vendor do
    not collide.
    """

    def get_active_skus(self, customer_id: str, vendor_name: str, state_key: str) -> set[str]: ...
    def set_active(
        self, customer_id: str, vendor_name: str, state_key: str, skus: set[str]
    ) -> None: ...
    def is_seeded(self, customer_id: str, vendor_name: str, state_key: str) -> bool: ...
