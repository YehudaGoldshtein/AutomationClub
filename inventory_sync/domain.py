"""Pure domain types for the sync engine. No I/O, no vendor/store terminology."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import NewType
from uuid import uuid4


SKU = NewType("SKU", str)
VendorProductId = NewType("VendorProductId", str)


@dataclass(frozen=True)
class StockLevel:
    value: int

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError(f"StockLevel cannot be negative: {self.value}")

    @property
    def is_out_of_stock(self) -> bool:
        return self.value == 0


@dataclass(frozen=True)
class Product:
    """A syncable unit in the store — typically a Shopify variant, each with its own SKU, stock, and vendor mapping.

    `handle`, `title`, and `store_product_id` are store-side identity we carry
    through so the dashboard can build deep links back to the customer's
    storefront / admin. Optional for backward-compat with fakes that don't
    know the originating platform's identifiers.
    """
    sku: SKU
    vendor_product_id: VendorProductId
    stock: StockLevel
    published: bool
    handle: str | None = None
    title: str | None = None
    store_product_id: str | None = None


class ChangeKind(str, Enum):
    SET_STOCK = "set_stock"
    UNPUBLISH = "unpublish"
    REPUBLISH = "republish"


@dataclass(frozen=True)
class StockChange:
    """A change the sync engine plans to apply in the store."""
    sku: SKU
    kind: ChangeKind
    new_stock: StockLevel | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.kind is ChangeKind.SET_STOCK and self.new_stock is None:
            raise ValueError("SET_STOCK change requires a new_stock value")
        if self.kind in (ChangeKind.UNPUBLISH, ChangeKind.REPUBLISH) and self.new_stock is not None:
            raise ValueError(f"{self.kind.value} change must not carry a new_stock value")


@dataclass(frozen=True)
class VendorProductSnapshot:
    """Snapshot of a vendor product captured during one fetch.

    Two fields carry the stock signal:
      - `is_available`: every vendor tells us this (binary in/out).
      - `stock_count`: exact count when the vendor provides one; None when only
        binary availability is known.

    Invariants enforced at construction keep these consistent (e.g., stock_count=0
    with is_available=True is rejected).
    """
    vendor_product_id: VendorProductId
    is_available: bool
    stock_count: int | None = None
    raw_availability: str | None = None
    name: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    image_url: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if self.stock_count is not None:
            if self.stock_count < 0:
                raise ValueError(f"stock_count cannot be negative: {self.stock_count}")
            if self.stock_count == 0 and self.is_available:
                raise ValueError("inconsistent: stock_count=0 but is_available=True")
            if self.stock_count > 0 and not self.is_available:
                raise ValueError("inconsistent: stock_count>0 but is_available=False")


@dataclass(frozen=True)
class SyncError:
    message: str
    sku: SKU | None = None
    when: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SyncRun:
    """One sync execution. The engine appends to it as work progresses."""
    run_id: str = field(default_factory=lambda: uuid4().hex[:12])
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    items_checked: int = 0
    changes_planned: list[StockChange] = field(default_factory=list)
    changes_applied: list[StockChange] = field(default_factory=list)
    errors: list[SyncError] = field(default_factory=list)
    vendor_missing: list[SKU] = field(default_factory=list)  # vendor returned no snapshot for these SKUs

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc)

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()
