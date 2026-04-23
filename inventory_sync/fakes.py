"""In-memory implementations of every interface. For tests and local runs."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Iterable

from inventory_sync.customers import Customer
from inventory_sync.domain import (
    SKU,
    Product,
    StockLevel,
    SyncRun,
    VendorProductId,
    VendorProductSnapshot,
)


class InMemoryStore:
    def __init__(self, products: list[Product] | None = None):
        self._products: dict[SKU, Product] = {p.sku: p for p in (products or [])}

    def list_products(self) -> list[Product]:
        return list(self._products.values())

    def update_stock(self, sku: SKU, stock: StockLevel) -> None:
        self._products[sku] = replace(self._products[sku], stock=stock)

    def unpublish(self, sku: SKU) -> None:
        self._products[sku] = replace(self._products[sku], published=False)

    def republish(self, sku: SKU) -> None:
        self._products[sku] = replace(self._products[sku], published=True)

    def get(self, sku: SKU) -> Product:
        return self._products[sku]


class InMemorySupplier:
    def __init__(
        self, snapshots: dict[VendorProductId, VendorProductSnapshot] | None = None
    ):
        self._snapshots: dict[VendorProductId, VendorProductSnapshot] = dict(snapshots or {})

    def fetch_snapshots(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, VendorProductSnapshot]:
        return {vid: self._snapshots[vid] for vid in ids if vid in self._snapshots}

    def set_snapshot(self, snapshot: VendorProductSnapshot) -> None:
        self._snapshots[snapshot.vendor_product_id] = snapshot


class InMemoryNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))


class InMemorySyncRunStore:
    def __init__(self) -> None:
        self._runs: dict[str, SyncRun] = {}

    def save(self, run: SyncRun) -> None:
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> SyncRun | None:
        return self._runs.get(run_id)

    def list_recent(self, limit: int = 20) -> list[SyncRun]:
        ordered = sorted(
            self._runs.values(), key=lambda r: r.started_at, reverse=True
        )
        return ordered[:limit]


class InMemoryItemStateStore:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], set[str]] = {}
        self._seeded: set[tuple[str, str]] = set()

    def get_active_skus(self, vendor_name: str, state_key: str) -> set[str]:
        return set(self._active.get((vendor_name, state_key), set()))

    def set_active(self, vendor_name: str, state_key: str, skus: set[str]) -> None:
        self._active[(vendor_name, state_key)] = set(skus)
        self._seeded.add((vendor_name, state_key))

    def is_seeded(self, vendor_name: str, state_key: str) -> bool:
        return (vendor_name, state_key) in self._seeded


class InMemoryCustomerRepository:
    def __init__(self) -> None:
        self._by_id: dict[str, Customer] = {}

    def get(self, customer_id: str) -> Customer | None:
        return self._by_id.get(customer_id)

    def list_all(self) -> list[Customer]:
        return sorted(self._by_id.values(), key=lambda c: c.id)

    def list_due(self, now: datetime | None = None) -> list[Customer]:
        now = now or datetime.now(timezone.utc)
        out: list[Customer] = []
        for c in self.list_all():
            if c.last_synced_at is None:
                out.append(c)
                continue
            if c.last_synced_at + timedelta(minutes=c.sync_interval_minutes) <= now:
                out.append(c)
        return out

    def upsert(self, customer: Customer) -> None:
        existing = self._by_id.get(customer.id)
        if existing is not None:
            customer = Customer(
                id=customer.id,
                display_name=customer.display_name,
                sync_interval_minutes=customer.sync_interval_minutes,
                last_synced_at=existing.last_synced_at,
                store=customer.store,
                vendors=customer.vendors,
                notifications=customer.notifications,
            )
        self._by_id[customer.id] = customer

    def mark_synced(self, customer_id: str, when: datetime | None = None) -> None:
        when = when or datetime.now(timezone.utc)
        c = self._by_id[customer_id]
        self._by_id[customer_id] = Customer(
            id=c.id,
            display_name=c.display_name,
            sync_interval_minutes=c.sync_interval_minutes,
            last_synced_at=when,
            store=c.store,
            vendors=c.vendors,
            notifications=c.notifications,
        )


class InMemoryVendorSnapshotCache:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], tuple[datetime, VendorProductSnapshot]] = {}

    def get_fresh(
        self,
        vendor_name: str,
        ids: Iterable[str],
        ttl_minutes: int,
        now: datetime | None = None,
    ) -> dict[str, VendorProductSnapshot]:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=ttl_minutes)
        out: dict[str, VendorProductSnapshot] = {}
        for vid in ids:
            entry = self._rows.get((vendor_name, vid))
            if entry is None:
                continue
            fetched_at, snap = entry
            if fetched_at >= cutoff:
                out[vid] = snap
        return out

    def upsert_many(
        self,
        vendor_name: str,
        snapshots: dict[str, VendorProductSnapshot],
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now(timezone.utc)
        for vid, snap in snapshots.items():
            self._rows[(vendor_name, vid)] = (now, snap)
