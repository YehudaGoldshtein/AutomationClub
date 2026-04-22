"""Protocols for every external seam. Concrete implementations live in adapters/."""
from __future__ import annotations

from typing import Iterable, Protocol

from inventory_sync.domain import (
    SKU,
    Product,
    StockChange,
    StockLevel,
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
