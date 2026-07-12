"""SQL-backed StoreProductStore — per-(customer_id, sku) store-side metadata.

Populated each sync from `StorePlatform.list_products()`. Used by the dashboard
to build storefront / admin deep links. Read-only from the sync engine's
perspective; writes happen once per sync pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import Engine
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from inventory_sync.domain import Product
from inventory_sync.log import Logger, get
from inventory_sync.persistence.schema import metadata, store_products


@dataclass(frozen=True)
class NewStoreProduct:
    """A freshly-created draft product to record after ingest creates it in the store.

    One instance per variant SKU; variants of the same product share store_product_id.
    """
    sku: str
    store_product_id: str
    handle: str | None = None
    title: str | None = None
    is_new_collection: bool = False
    needs_review: bool = False


@dataclass(frozen=True)
class StoreProductRecord:
    """A row read back from store_products, including lifecycle state."""
    customer_id: str
    sku: str
    handle: str | None
    title: str | None
    store_product_id: str | None
    status: str
    approved: bool
    approved_at: datetime | None
    is_new_collection: bool
    needs_review: bool
    updated_at: datetime


@dataclass
class SqlStoreProductStore:
    engine: Engine
    logger: Logger = field(default_factory=lambda: get("persistence.store_product_store"))

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def upsert_many(self, customer_id: str, products: Iterable[Product]) -> None:
        now = datetime.now(timezone.utc)
        rows = [
            {
                "customer_id": customer_id,
                "sku": str(p.sku),
                "handle": p.handle,
                "title": p.title,
                "store_product_id": p.store_product_id,
                "updated_at": now,
            }
            for p in products
            if p.sku  # defensive; SKU is always truthy in practice
        ]
        if not rows:
            return
        dialect = self.engine.dialect.name
        if dialect == "postgresql":
            stmt = pg_insert(store_products).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[store_products.c.customer_id, store_products.c.sku],
                set_={
                    "handle": stmt.excluded.handle,
                    "title": stmt.excluded.title,
                    "store_product_id": stmt.excluded.store_product_id,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        else:
            stmt = sqlite_insert(store_products).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[store_products.c.customer_id, store_products.c.sku],
                set_={
                    "handle": stmt.excluded.handle,
                    "title": stmt.excluded.title,
                    "store_product_id": stmt.excluded.store_product_id,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        with Session(self.engine) as session:
            with session.begin():
                session.execute(stmt)

    # --- lifecycle / pending-review flow (SCAFFOLD — see tests) ---

    def get(self, customer_id: str, sku: str) -> StoreProductRecord | None:
        raise NotImplementedError

    def write_pending(self, customer_id: str, items: Iterable[NewStoreProduct]) -> None:
        """Record newly-created draft products: status=draft, approved=false."""
        raise NotImplementedError

    def list_pending(self, customer_id: str) -> list[StoreProductRecord]:
        """Rows awaiting confirmation: status=draft AND approved=false."""
        raise NotImplementedError

    def list_approved_drafts(self, customer_id: str) -> list[StoreProductRecord]:
        """Confirmed-but-not-yet-live rows: status=draft AND approved=true."""
        raise NotImplementedError

    def mark_approved(self, customer_id: str, store_product_id: str) -> None:
        """Dashboard confirm: set approved=true + approved_at for all rows of the product."""
        raise NotImplementedError

    def mark_active(self, customer_id: str, store_product_id: str) -> None:
        """Sync activation: flip status=active for all rows of the product."""
        raise NotImplementedError
