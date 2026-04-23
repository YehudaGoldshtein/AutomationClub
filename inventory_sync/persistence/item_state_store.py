"""SQL-backed ItemStateStore. Replace-set semantics per (customer_id, vendor_name, state_key).

Rows in `item_state` exist only for SKUs currently active. A companion
`item_state_seeded` table records whether we've ever set the state for a
(customer_id, vendor_name, state_key) triple — distinguishes "first run"
from "observed empty."
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import Engine, delete, insert, select
from sqlalchemy.orm import Session

from inventory_sync.log import Logger, get
from inventory_sync.persistence.schema import (
    item_state,
    item_state_seeded,
    metadata,
)


@dataclass
class SqlItemStateStore:
    engine: Engine
    logger: Logger = field(default_factory=lambda: get("persistence.item_state_store"))

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def get_active_skus(self, customer_id: str, vendor_name: str, state_key: str) -> set[str]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(item_state.c.sku).where(
                    item_state.c.customer_id == customer_id,
                    item_state.c.vendor_name == vendor_name,
                    item_state.c.state_key == state_key,
                )
            ).all()
        return {r[0] for r in rows}

    def set_active(
        self, customer_id: str, vendor_name: str, state_key: str, skus: set[str]
    ) -> None:
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session:
            with session.begin():
                session.execute(
                    delete(item_state).where(
                        item_state.c.customer_id == customer_id,
                        item_state.c.vendor_name == vendor_name,
                        item_state.c.state_key == state_key,
                    )
                )
                if skus:
                    session.execute(
                        insert(item_state),
                        [
                            {
                                "customer_id": customer_id,
                                "vendor_name": vendor_name,
                                "state_key": state_key,
                                "sku": sku,
                                "updated_at": now,
                            }
                            for sku in skus
                        ],
                    )
                existing = session.execute(
                    select(item_state_seeded).where(
                        item_state_seeded.c.customer_id == customer_id,
                        item_state_seeded.c.vendor_name == vendor_name,
                        item_state_seeded.c.state_key == state_key,
                    )
                ).one_or_none()
                if existing is None:
                    session.execute(insert(item_state_seeded).values(
                        customer_id=customer_id,
                        vendor_name=vendor_name,
                        state_key=state_key,
                        first_seeded_at=now,
                    ))

    def is_seeded(self, customer_id: str, vendor_name: str, state_key: str) -> bool:
        with Session(self.engine) as session:
            row = session.execute(
                select(item_state_seeded.c.vendor_name).where(
                    item_state_seeded.c.customer_id == customer_id,
                    item_state_seeded.c.vendor_name == vendor_name,
                    item_state_seeded.c.state_key == state_key,
                )
            ).one_or_none()
        return row is not None
