"""SQL-backed unarchive-request queue — the dashboard→backend intent for one-click unarchive.

The dashboard writes an intent row when the user clicks "unarchive" on a candidate
(archived in the store, in stock at the vendor). The reconcile job drains it:
republish the product (status→active) then delete the row. Present row == pending.

Keyed by (customer_id, store_product_id): the click acts on a whole product, and an
archived product may have no store_products row, so we can't key on sku.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import Engine, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from inventory_sync.log import Logger, get
from inventory_sync.persistence.schema import metadata, unarchive_requests


@dataclass
class SqlUnarchiveRequestStore:
    engine: Engine
    logger: Logger = field(default_factory=lambda: get("persistence.unarchive_request_store"))

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def add(self, customer_id: str, store_product_id: str) -> None:
        """Record an unarchive intent. Idempotent — a repeated click is a no-op."""
        insert = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
        stmt = insert(unarchive_requests).values(
            customer_id=customer_id, store_product_id=store_product_id,
            requested_at=datetime.now(timezone.utc),
        ).on_conflict_do_nothing(
            index_elements=[unarchive_requests.c.customer_id, unarchive_requests.c.store_product_id]
        )
        with Session(self.engine) as session:
            with session.begin():
                session.execute(stmt)
        self.logger.info("unarchive_requested", customer_id=customer_id, store_product_id=store_product_id)

    def list_pending(self, customer_id: str) -> list[str]:
        """Store product ids awaiting unarchive for this customer."""
        with Session(self.engine) as session:
            rows = session.execute(
                select(unarchive_requests.c.store_product_id).where(
                    unarchive_requests.c.customer_id == customer_id
                )
            ).all()
        return [r[0] for r in rows]

    def mark_done(self, customer_id: str, store_product_id: str) -> None:
        """Drop the intent row once the product has been republished."""
        with Session(self.engine) as session:
            with session.begin():
                session.execute(
                    delete(unarchive_requests).where(
                        unarchive_requests.c.customer_id == customer_id,
                        unarchive_requests.c.store_product_id == store_product_id,
                    )
                )
