"""SQL-backed StoreProductStore — per-(customer_id, sku) store-side metadata.

Populated each sync from `StorePlatform.list_products()`. Used by the dashboard
to build storefront / admin deep links. Read-only from the sync engine's
perspective; writes happen once per sync pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import Engine, delete, select, update
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

    # --- lifecycle / pending-review flow ---

    def get(self, customer_id: str, sku: str) -> StoreProductRecord | None:
        with Session(self.engine) as session:
            row = session.execute(
                select(store_products).where(
                    store_products.c.customer_id == customer_id,
                    store_products.c.sku == sku,
                )
            ).mappings().first()
        return _to_record(row) if row else None

    def write_pending(self, customer_id: str, items: Iterable[NewStoreProduct]) -> None:
        """Record newly-created draft products: status=draft, approved=false.

        Upsert (idempotent re-ingest). On conflict, refresh metadata + flags only —
        never resets status/approved, so a re-ingest can't un-approve a pending row.
        """
        now = datetime.now(timezone.utc)
        rows = [
            {
                "customer_id": customer_id,
                "sku": it.sku,
                "handle": it.handle,
                "title": it.title,
                "store_product_id": it.store_product_id,
                "status": "draft",
                "approved": False,
                "approved_at": None,
                "is_new_collection": it.is_new_collection,
                "needs_review": it.needs_review,
                "updated_at": now,
            }
            for it in items
        ]
        if not rows:
            return
        insert = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
        stmt = insert(store_products).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=[store_products.c.customer_id, store_products.c.sku],
            set_={
                "handle": stmt.excluded.handle,
                "title": stmt.excluded.title,
                "store_product_id": stmt.excluded.store_product_id,
                "is_new_collection": stmt.excluded.is_new_collection,
                "needs_review": stmt.excluded.needs_review,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        with Session(self.engine) as session:
            with session.begin():
                session.execute(stmt)
        self.logger.info(
            "store_products_pending_written",
            customer_id=customer_id,
            count=len(rows),
            skus=[r["sku"] for r in rows],
        )

    def list_pending(self, customer_id: str) -> list[StoreProductRecord]:
        """Rows awaiting confirmation: status=draft AND approved=false."""
        return self._list_by_state(customer_id, status="draft", approved=False)

    def list_approved_drafts(self, customer_id: str) -> list[StoreProductRecord]:
        """Confirmed-but-not-yet-live rows: status=draft AND approved=true."""
        return self._list_by_state(customer_id, status="draft", approved=True)

    def _list_by_state(self, customer_id: str, status: str, approved: bool) -> list[StoreProductRecord]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(store_products).where(
                    store_products.c.customer_id == customer_id,
                    store_products.c.status == status,
                    store_products.c.approved == approved,
                )
            ).mappings().all()
        return [_to_record(r) for r in rows]

    def mark_approved(self, customer_id: str, store_product_id: str) -> None:
        """Dashboard confirm: set approved=true + approved_at for all rows of the product."""
        now = datetime.now(timezone.utc)
        self._update_product(customer_id, store_product_id, {"approved": True, "approved_at": now})
        self.logger.info("store_product_approved", customer_id=customer_id, store_product_id=store_product_id)

    def mark_active(self, customer_id: str, store_product_id: str) -> None:
        """Sync activation: flip status=active for all rows of the product."""
        self._update_product(customer_id, store_product_id, {"status": "active"})
        self.logger.info("store_product_activated", customer_id=customer_id, store_product_id=store_product_id)

    def mark_rejected(self, customer_id: str, store_product_id: str) -> None:
        """Dashboard 'ignore': mark for deletion (reconcile deletes it from the store)."""
        self._update_product(customer_id, store_product_id, {"status": "rejected"})
        self.logger.info("store_product_rejected", customer_id=customer_id, store_product_id=store_product_id)

    def list_rejected(self, customer_id: str) -> list[StoreProductRecord]:
        """Rows the user ignored: status=rejected (awaiting deletion)."""
        with Session(self.engine) as session:
            rows = session.execute(
                select(store_products).where(
                    store_products.c.customer_id == customer_id,
                    store_products.c.status == "rejected",
                )
            ).mappings().all()
        return [_to_record(r) for r in rows]

    def delete_products(self, customer_id: str, store_product_id: str) -> None:
        """Remove all rows for a product (after it's deleted from the store)."""
        with Session(self.engine) as session:
            with session.begin():
                session.execute(
                    delete(store_products).where(
                        store_products.c.customer_id == customer_id,
                        store_products.c.store_product_id == store_product_id,
                    )
                )
        self.logger.info("store_products_deleted", customer_id=customer_id, store_product_id=store_product_id)

    def _update_product(self, customer_id: str, store_product_id: str, values: dict) -> None:
        with Session(self.engine) as session:
            with session.begin():
                session.execute(
                    update(store_products)
                    .where(
                        store_products.c.customer_id == customer_id,
                        store_products.c.store_product_id == store_product_id,
                    )
                    .values(**values)
                )


def _to_record(row) -> StoreProductRecord:
    return StoreProductRecord(
        customer_id=row["customer_id"],
        sku=row["sku"],
        handle=row["handle"],
        title=row["title"],
        store_product_id=row["store_product_id"],
        status=row["status"],
        approved=bool(row["approved"]),
        approved_at=row["approved_at"],
        is_new_collection=bool(row["is_new_collection"]),
        needs_review=bool(row["needs_review"]),
        updated_at=row["updated_at"],
    )
