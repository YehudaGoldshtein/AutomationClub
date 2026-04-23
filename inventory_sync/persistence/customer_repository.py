"""SQL-backed customer registry.

Customers are the tenants in the multi-customer sync. A CustomerRepository
reads and writes Customer domain objects, treating `customers.config_json`
as the source of truth for everything except sync cadence bookkeeping.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from inventory_sync.customers import Customer
from inventory_sync.log import Logger, get
from inventory_sync.persistence.schema import customers, metadata


@dataclass
class SqlCustomerRepository:
    engine: Engine
    logger: Logger = field(default_factory=lambda: get("persistence.customer_repository"))

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def get(self, customer_id: str) -> Customer | None:
        with Session(self.engine) as session:
            row = session.execute(
                select(customers).where(customers.c.id == customer_id)
            ).one_or_none()
        return _row_to_customer(row) if row else None

    def list_all(self) -> list[Customer]:
        with Session(self.engine) as session:
            rows = session.execute(select(customers).order_by(customers.c.id)).all()
        return [_row_to_customer(r) for r in rows]

    def list_due(self, now: datetime | None = None) -> list[Customer]:
        """Customers whose last_synced_at + sync_interval_minutes <= now (or never synced)."""
        now = now or datetime.now(timezone.utc)
        out: list[Customer] = []
        for c in self.list_all():
            if c.last_synced_at is None:
                out.append(c)
                continue
            due_at = c.last_synced_at + timedelta(minutes=c.sync_interval_minutes)
            if due_at <= now:
                out.append(c)
        return out

    def upsert(self, customer: Customer) -> None:
        now = datetime.now(timezone.utc)
        values = {
            "id": customer.id,
            "display_name": customer.display_name,
            "sync_interval_minutes": customer.sync_interval_minutes,
            "last_synced_at": customer.last_synced_at,
            "config_json": customer.to_config_json(),
            "created_at": now,
            "updated_at": now,
        }
        dialect = self.engine.dialect.name
        if dialect == "postgresql":
            stmt = pg_insert(customers).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[customers.c.id],
                set_={
                    "display_name": stmt.excluded.display_name,
                    "sync_interval_minutes": stmt.excluded.sync_interval_minutes,
                    # Don't overwrite last_synced_at on upsert — preserve bookkeeping.
                    "config_json": stmt.excluded.config_json,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        else:
            stmt = sqlite_insert(customers).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[customers.c.id],
                set_={
                    "display_name": stmt.excluded.display_name,
                    "sync_interval_minutes": stmt.excluded.sync_interval_minutes,
                    "config_json": stmt.excluded.config_json,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        with Session(self.engine) as session:
            with session.begin():
                session.execute(stmt)

    def mark_synced(self, customer_id: str, when: datetime | None = None) -> None:
        when = when or datetime.now(timezone.utc)
        with Session(self.engine) as session:
            with session.begin():
                session.execute(
                    customers.update()
                    .where(customers.c.id == customer_id)
                    .values(last_synced_at=when, updated_at=when)
                )


def _row_to_customer(row) -> Customer:
    # SQLAlchemy Row supports attribute access for column names.
    return Customer.from_row(
        id=row.id,
        display_name=row.display_name,
        sync_interval_minutes=row.sync_interval_minutes,
        last_synced_at=_ensure_utc(row.last_synced_at),
        config_json=row.config_json,
    )


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo on DateTime roundtrip; we always write UTC, so reattach on read."""
    if dt is None or dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=timezone.utc)
