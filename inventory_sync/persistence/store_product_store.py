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
    vendor: str | None = None
    is_new_collection: bool = False
    needs_review: bool = False
    needs_review_reason: str | None = None  # review_reasons code(s), comma-joined


@dataclass(frozen=True)
class StoreProductRecord:
    """A row read back from store_products, including lifecycle state."""
    customer_id: str
    sku: str
    handle: str | None
    title: str | None
    store_product_id: str | None
    vendor: str | None
    status: str
    approved: bool
    approved_at: datetime | None
    is_new_collection: bool
    needs_review: bool
    needs_review_reason: str | None
    missing_at_source: bool
    unarchive_candidate: bool
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
                "vendor": p.vendor,
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
                    "vendor": stmt.excluded.vendor,
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
                    "vendor": stmt.excluded.vendor,
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
                "vendor": it.vendor,
                "status": "draft",
                "approved": False,
                "approved_at": None,
                "is_new_collection": it.is_new_collection,
                "needs_review": it.needs_review,
                "needs_review_reason": it.needs_review_reason,
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
                "needs_review_reason": stmt.excluded.needs_review_reason,
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

    def mark_unarchive_requested(self, customer_id: str, store_product_id: str) -> None:
        """Dashboard 'unarchive': mark for republish (reconcile sets it live in the store)."""
        self._update_product(customer_id, store_product_id, {"status": "unarchive_requested"})
        self.logger.info("store_product_unarchive_requested",
                         customer_id=customer_id, store_product_id=store_product_id)

    def list_rejected(self, customer_id: str) -> list[StoreProductRecord]:
        """Rows the user ignored: status=rejected (awaiting deletion)."""
        return self._list_by_status(customer_id, "rejected")

    def list_unarchive_requested(self, customer_id: str) -> list[StoreProductRecord]:
        """Rows the user asked to unarchive: status=unarchive_requested (awaiting republish)."""
        return self._list_by_status(customer_id, "unarchive_requested")

    def _list_by_status(self, customer_id: str, status: str) -> list[StoreProductRecord]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(store_products).where(
                    store_products.c.customer_id == customer_id,
                    store_products.c.status == status,
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

    def flag_missing_at_source(self, customer_id: str, store_product_id: str, *,
                               sku: str | None = None, title: str | None = None,
                               vendor: str | None = None, published: bool = True) -> None:
        """Flag a whole store product (all its variant rows) as missing at source.

        Unified per product (store_product_id) — a multi-variant product is flagged
        once, not per variant. If the product isn't tracked yet (a manual product we
        never onboarded), insert a tracking row keyed by `sku` so the dashboard can
        surface it. Flag-only (never delete); preserves status/approval/review reason.
        """
        now = datetime.now(timezone.utc)
        updated = self._set_missing_by_product(customer_id, store_product_id, True, now)
        if updated == 0 and sku is not None:
            status = "active" if published else "draft"
            insert = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
            stmt = insert(store_products).values({
                "customer_id": customer_id, "sku": sku, "store_product_id": store_product_id,
                "handle": None, "title": title, "vendor": vendor,
                "status": status, "approved": status == "active",
                "approved_at": now if status == "active" else None,
                "is_new_collection": False, "needs_review": False,
                "needs_review_reason": None, "missing_at_source": True, "updated_at": now,
            }).on_conflict_do_update(
                index_elements=[store_products.c.customer_id, store_products.c.sku],
                set_={"missing_at_source": True, "updated_at": now},
            )
            with Session(self.engine) as session:
                with session.begin():
                    session.execute(stmt)
        self.logger.info("store_product_flagged_missing", customer_id=customer_id,
                         store_product_id=store_product_id)

    def clear_missing_at_source(self, customer_id: str, store_product_id: str) -> None:
        """Clear the flag on all rows of a product (back in the catalog). No-op if none set."""
        n = self._set_missing_by_product(customer_id, store_product_id, False,
                                         datetime.now(timezone.utc), only_flagged=True)
        if n:
            self.logger.info("store_product_missing_cleared", customer_id=customer_id,
                             store_product_id=store_product_id)

    def set_unarchive_candidates(self, customer_id: str, candidate_skus, scope_skus) -> None:
        """Mirror one vendor pass's unarchive-candidate set onto store_products.

        Replace-set *within scope_skus* (the SKUs that pass examined): flag the
        candidates, clear any previously-flagged SKU in scope that dropped out.
        Rows OUTSIDE the scope are never touched, so a Laura pass and a Bambino pass
        don't clobber each other's flags. Only rows whose flag actually changes are
        written, so the dashboard's `updated_at` isn't churned every run.
        """
        scope = {str(s) for s in scope_skus}
        cand = {str(s) for s in candidate_skus} & scope
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            with session.begin():
                # Currently-flagged rows are few; pull them and decide clears in Python
                # so every UPDATE uses a small IN-list (no huge scope IN-clause).
                flagged = {
                    r[0] for r in session.execute(
                        select(store_products.c.sku).where(
                            store_products.c.customer_id == customer_id,
                            store_products.c.unarchive_candidate.is_(True),
                        )
                    ).all()
                }
                to_clear = [s for s in flagged if s in scope and s not in cand]
                to_set = [s for s in cand if s not in flagged]
                if to_clear:
                    session.execute(
                        update(store_products).where(
                            store_products.c.customer_id == customer_id,
                            store_products.c.sku.in_(to_clear),
                        ).values(unarchive_candidate=False, updated_at=now)
                    )
                if to_set:
                    session.execute(
                        update(store_products).where(
                            store_products.c.customer_id == customer_id,
                            store_products.c.sku.in_(to_set),
                        ).values(unarchive_candidate=True, updated_at=now)
                    )

    def _set_missing_by_product(self, customer_id: str, store_product_id: str, value: bool,
                                now: datetime, only_flagged: bool = False) -> int:
        """Set missing_at_source on every row of a product; returns rows affected."""
        stmt = update(store_products).where(
            store_products.c.customer_id == customer_id,
            store_products.c.store_product_id == store_product_id,
        )
        if only_flagged:
            stmt = stmt.where(store_products.c.missing_at_source.is_(True))
        stmt = stmt.values(missing_at_source=value, updated_at=now)
        with Session(self.engine) as session:
            with session.begin():
                res = session.execute(stmt)
        return res.rowcount or 0

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
        vendor=row["vendor"],
        status=row["status"],
        approved=bool(row["approved"]),
        approved_at=row["approved_at"],
        is_new_collection=bool(row["is_new_collection"]),
        needs_review=bool(row["needs_review"]),
        needs_review_reason=row["needs_review_reason"],
        missing_at_source=bool(row["missing_at_source"]),
        unarchive_candidate=bool(row["unarchive_candidate"]),
        updated_at=row["updated_at"],
    )
