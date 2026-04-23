"""SQL-backed shared vendor snapshot cache.

Global (multi-customer) cache: one row per (vendor_name, vendor_product_id)
holds the latest VendorProductSnapshot along with its fetch timestamp.
Callers gate freshness via TTL in code — see inventory_sync.vendor_scan.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from inventory_sync.domain import VendorProductSnapshot
from inventory_sync.log import Logger, get
from inventory_sync.persistence.schema import metadata, vendor_snapshot_cache


@dataclass
class SqlVendorSnapshotCache:
    engine: Engine
    logger: Logger = field(default_factory=lambda: get("persistence.vendor_snapshot_cache"))

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def get_fresh(
        self,
        vendor_name: str,
        ids: Iterable[str],
        ttl_minutes: int,
        now: datetime | None = None,
    ) -> dict[str, VendorProductSnapshot]:
        """Return cached snapshots whose fetched_at is within TTL. Stale rows are excluded."""
        id_list = list(ids)
        if not id_list:
            return {}
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=ttl_minutes)
        with Session(self.engine) as session:
            rows = session.execute(
                select(vendor_snapshot_cache).where(
                    vendor_snapshot_cache.c.vendor_name == vendor_name,
                    vendor_snapshot_cache.c.vendor_product_id.in_(id_list),
                    vendor_snapshot_cache.c.fetched_at >= cutoff,
                )
            ).all()
        return {r.vendor_product_id: _row_to_snapshot(r) for r in rows}

    def upsert_many(
        self,
        vendor_name: str,
        snapshots: dict[str, VendorProductSnapshot],
        now: datetime | None = None,
    ) -> None:
        if not snapshots:
            return
        now = now or datetime.now(timezone.utc)
        rows = []
        for vid, snap in snapshots.items():
            rows.append({
                "vendor_name": vendor_name,
                "vendor_product_id": vid,
                "fetched_at": now,
                "is_available": snap.is_available,
                "stock_count": snap.stock_count,
                "raw_availability": snap.raw_availability,
                "name": snap.name,
                "price": snap.price,
                "currency": snap.currency,
                "image_url": snap.image_url,
            })
        dialect = self.engine.dialect.name
        if dialect == "postgresql":
            stmt = pg_insert(vendor_snapshot_cache).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    vendor_snapshot_cache.c.vendor_name,
                    vendor_snapshot_cache.c.vendor_product_id,
                ],
                set_={
                    "fetched_at": stmt.excluded.fetched_at,
                    "is_available": stmt.excluded.is_available,
                    "stock_count": stmt.excluded.stock_count,
                    "raw_availability": stmt.excluded.raw_availability,
                    "name": stmt.excluded.name,
                    "price": stmt.excluded.price,
                    "currency": stmt.excluded.currency,
                    "image_url": stmt.excluded.image_url,
                },
            )
        else:
            stmt = sqlite_insert(vendor_snapshot_cache).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    vendor_snapshot_cache.c.vendor_name,
                    vendor_snapshot_cache.c.vendor_product_id,
                ],
                set_={
                    "fetched_at": stmt.excluded.fetched_at,
                    "is_available": stmt.excluded.is_available,
                    "stock_count": stmt.excluded.stock_count,
                    "raw_availability": stmt.excluded.raw_availability,
                    "name": stmt.excluded.name,
                    "price": stmt.excluded.price,
                    "currency": stmt.excluded.currency,
                    "image_url": stmt.excluded.image_url,
                },
            )
        with Session(self.engine) as session:
            with session.begin():
                session.execute(stmt)


def _row_to_snapshot(row) -> VendorProductSnapshot:
    return VendorProductSnapshot(
        vendor_product_id=row.vendor_product_id,
        is_available=row.is_available,
        stock_count=row.stock_count,
        raw_availability=row.raw_availability,
        name=row.name,
        price=Decimal(row.price) if row.price is not None else None,
        currency=row.currency,
        image_url=row.image_url,
    )
