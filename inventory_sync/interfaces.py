"""Protocols for every external seam. Concrete implementations live in adapters/."""
from __future__ import annotations

from typing import Iterable, Protocol

from inventory_sync.domain import (
    SKU,
    Product,
    StockChange,
    StockLevel,
    VendorProductId,
)


class StorePlatform(Protocol):
    """E-commerce store read/write (Shopify, Woo, etc.)."""

    def list_products(self) -> list[Product]: ...
    def update_stock(self, sku: SKU, stock: StockLevel) -> None: ...
    def unpublish(self, sku: SKU) -> None: ...
    def republish(self, sku: SKU) -> None: ...


class SupplierSource(Protocol):
    """Vendor stock source (scraper, REST, CSV, email-parsed feed, etc.)."""

    def fetch_stock(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, StockLevel]: ...


class NotificationChannel(Protocol):
    """A single notification delivery path (email, WhatsApp, SMS, etc.)."""

    def send(self, subject: str, body: str) -> None: ...


class StockPolicy(Protocol):
    """Decides what StockChanges are needed given the store's view and the vendor's current stock."""

    def decide(
        self, product: Product, vendor_stock: StockLevel
    ) -> list[StockChange]: ...
