"""In-memory implementations of every interface. For tests and local runs."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Iterable

from inventory_sync.customers import Customer
from inventory_sync.domain import (
    SKU,
    CollectionRef,
    CreatedProduct,
    Product,
    ProductDraft,
    StockLevel,
    SyncRun,
    VendorProductId,
    VendorProductSnapshot,
)


class InMemoryStore:
    def __init__(self, products: list[Product] | None = None):
        self._products: dict[SKU, Product] = {p.sku: p for p in (products or [])}
        self._collections: dict[str, str] = {}   # title -> collection_id
        self.collects: list[tuple[str, str]] = []  # (store_product_id, collection_id)
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return str(9000 + self._seq)

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

    # --- net-new product creation (Laura upload) ---

    def create_product(self, draft: ProductDraft) -> CreatedProduct:
        product_id = self._next_id()
        published = draft.status == "active"
        variant_ids: dict[SKU, str] = {}
        for v in draft.variants:
            variant_ids[v.sku] = self._next_id()
            self._products[v.sku] = Product(
                sku=v.sku,
                vendor_product_id=VendorProductId(str(v.sku)),
                stock=StockLevel(0),
                published=published,
                handle=None,
                title=draft.title,
                store_product_id=product_id,
            )
        return CreatedProduct(store_product_id=product_id, variant_ids_by_sku=variant_ids)

    def ensure_collection(self, title: str) -> CollectionRef:
        existing = self._collections.get(title)
        if existing is not None:
            return CollectionRef(id=existing, created=False)
        new_id = self._next_id()
        self._collections[title] = new_id
        return CollectionRef(id=new_id, created=True)

    def add_to_collection(self, store_product_id: str, collection_id: str) -> None:
        self.collects.append((store_product_id, collection_id))


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
        self._customer_of: dict[str, str] = {}

    def save(self, run: SyncRun, customer_id: str) -> None:
        self._runs[run.run_id] = run
        self._customer_of[run.run_id] = customer_id

    def get(self, run_id: str) -> SyncRun | None:
        return self._runs.get(run_id)

    def customer_of(self, run_id: str) -> str | None:
        return self._customer_of.get(run_id)

    def list_recent(self, limit: int = 20) -> list[SyncRun]:
        ordered = sorted(
            self._runs.values(), key=lambda r: r.started_at, reverse=True
        )
        return ordered[:limit]


class InMemoryItemStateStore:
    def __init__(self) -> None:
        self._active: dict[tuple[str, str, str], set[str]] = {}
        self._seeded: set[tuple[str, str, str]] = set()

    def get_active_skus(self, customer_id: str, vendor_name: str, state_key: str) -> set[str]:
        return set(self._active.get((customer_id, vendor_name, state_key), set()))

    def set_active(
        self, customer_id: str, vendor_name: str, state_key: str, skus: set[str]
    ) -> None:
        self._active[(customer_id, vendor_name, state_key)] = set(skus)
        self._seeded.add((customer_id, vendor_name, state_key))

    def is_seeded(self, customer_id: str, vendor_name: str, state_key: str) -> bool:
        return (customer_id, vendor_name, state_key) in self._seeded


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
