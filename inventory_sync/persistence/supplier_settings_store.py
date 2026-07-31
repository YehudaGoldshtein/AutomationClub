"""Per-customer, per-supplier sync toggle — the dashboard-controlled on/off flag.

The orchestrator reads this to decide which suppliers to run each tick; the
dashboard writes it (turn a supplier off while it's pulled from the site). A
missing row means ENABLED, so the default is on and no backfill is needed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from inventory_sync.log import Logger, get
from inventory_sync.persistence.schema import supplier_settings

# The suppliers the orchestrator knows about (stable keys used by workflows).
SUPPLIERS = ("laura", "segal", "bambino", "snir")


@dataclass
class SqlSupplierSettingsStore:
    engine: Engine
    logger: Logger = field(default_factory=lambda: get("persistence.supplier_settings"))

    def create_schema(self) -> None:
        # Create ONLY this table (checkfirst = IF NOT EXISTS). Avoids
        # metadata.create_all's full reflection, which can stall the orchestrator
        # preflight on a cold Neon connection.
        supplier_settings.create(self.engine, checkfirst=True)

    def is_enabled(self, customer_id: str, supplier: str) -> bool:
        """True unless a row explicitly disables it (missing row = enabled)."""
        with Session(self.engine) as s:
            row = s.execute(
                select(supplier_settings.c.enabled).where(
                    supplier_settings.c.customer_id == customer_id,
                    supplier_settings.c.supplier == supplier,
                )
            ).first()
        return True if row is None else bool(row[0])

    def enabled_map(self, customer_id: str, suppliers=SUPPLIERS) -> dict[str, bool]:
        """{supplier: enabled} for the given suppliers; missing rows default to True."""
        with Session(self.engine) as s:
            rows = s.execute(
                select(supplier_settings.c.supplier, supplier_settings.c.enabled).where(
                    supplier_settings.c.customer_id == customer_id,
                )
            ).all()
        stored = {r[0]: bool(r[1]) for r in rows}
        return {sup: stored.get(sup, True) for sup in suppliers}

    def set_enabled(self, customer_id: str, supplier: str, enabled: bool) -> None:
        """Upsert the flag (used by the CLI toggle + tests; the dashboard writes directly)."""
        now = datetime.now(timezone.utc)
        insert = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
        stmt = insert(supplier_settings).values(
            customer_id=customer_id, supplier=supplier, enabled=enabled, updated_at=now,
        ).on_conflict_do_update(
            index_elements=[supplier_settings.c.customer_id, supplier_settings.c.supplier],
            set_={"enabled": enabled, "updated_at": now},
        )
        with Session(self.engine) as s:
            with s.begin():
                s.execute(stmt)
        self.logger.info("supplier_setting_updated", customer_id=customer_id,
                         supplier=supplier, enabled=enabled)
