"""SQL-backed SyncRunStore.

Writes a SyncRun as one row in `sync_runs` plus N rows in `sync_run_changes`
and `sync_run_errors`. Reads hydrate the whole aggregate back.

Works against any SQLAlchemy-supported backend — sqlite for dev, postgres for
prod. Backend switch is pure config (DATABASE_URL).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import Engine, select, delete
from sqlalchemy.orm import Session

from inventory_sync.domain import (
    SKU,
    ChangeKind,
    StockChange,
    StockLevel,
    SyncError,
    SyncRun,
)
from inventory_sync.log import Logger, get
from inventory_sync.persistence.schema import (
    metadata,
    sync_run_changes,
    sync_run_errors,
    sync_runs,
)


@dataclass
class SqlSyncRunStore:
    engine: Engine
    logger: Logger = field(default_factory=lambda: get("persistence.sync_run_store"))

    def create_schema(self) -> None:
        """Create tables if they don't exist. Idempotent; safe to call on every startup."""
        metadata.create_all(self.engine)
        self.logger.info("schema_ready")

    def save(self, run: SyncRun) -> None:
        log = self.logger.bind(run_id=run.run_id)
        with Session(self.engine) as session:
            with session.begin():
                # Upsert sync_run row — delete + insert keeps the logic simple and backend-agnostic.
                session.execute(delete(sync_runs).where(sync_runs.c.run_id == run.run_id))
                session.execute(delete(sync_run_changes).where(sync_run_changes.c.run_id == run.run_id))
                session.execute(delete(sync_run_errors).where(sync_run_errors.c.run_id == run.run_id))
                session.execute(sync_runs.insert().values(
                    run_id=run.run_id,
                    started_at=run.started_at,
                    finished_at=run.finished_at,
                    items_checked=run.items_checked,
                    changes_planned_count=len(run.changes_planned),
                    changes_applied_count=len(run.changes_applied),
                    errors_count=len(run.errors),
                    vendor_missing_count=len(run.vendor_missing),
                    duration_seconds=run.duration_seconds,
                ))

                applied_keys = {(c.sku, c.kind, c.new_stock) for c in run.changes_applied}
                change_rows = [
                    {
                        "run_id": run.run_id,
                        "sku": str(c.sku),
                        "kind": c.kind.value,
                        "new_stock": c.new_stock.value if c.new_stock is not None else None,
                        "reason": c.reason or None,
                        "applied": (c.sku, c.kind, c.new_stock) in applied_keys,
                    }
                    for c in run.changes_planned
                ]
                if change_rows:
                    session.execute(sync_run_changes.insert(), change_rows)

                error_rows = [
                    {
                        "run_id": run.run_id,
                        "sku": str(e.sku) if e.sku is not None else None,
                        "message": e.message,
                        "when_at": e.when,
                    }
                    for e in run.errors
                ]
                if error_rows:
                    session.execute(sync_run_errors.insert(), error_rows)
        log.info("sync_run_saved",
                 changes_planned=len(run.changes_planned),
                 changes_applied=len(run.changes_applied),
                 errors=len(run.errors))

    def get(self, run_id: str) -> SyncRun | None:
        with Session(self.engine) as session:
            row = session.execute(
                select(sync_runs).where(sync_runs.c.run_id == run_id)
            ).one_or_none()
            if row is None:
                return None
            changes = list(session.execute(
                select(sync_run_changes).where(sync_run_changes.c.run_id == run_id)
            ).mappings())
            errors = list(session.execute(
                select(sync_run_errors).where(sync_run_errors.c.run_id == run_id)
            ).mappings())
        return _hydrate_run(row._mapping, changes, errors)

    def list_recent(self, limit: int = 20) -> list[SyncRun]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(sync_runs).order_by(sync_runs.c.started_at.desc()).limit(limit)
            ).all()
            out: list[SyncRun] = []
            for r in rows:
                rid = r._mapping["run_id"]
                changes = list(session.execute(
                    select(sync_run_changes).where(sync_run_changes.c.run_id == rid)
                ).mappings())
                errors = list(session.execute(
                    select(sync_run_errors).where(sync_run_errors.c.run_id == rid)
                ).mappings())
                out.append(_hydrate_run(r._mapping, changes, errors))
        return out


def _hydrate_run(row, change_rows: Iterable[dict], error_rows: Iterable[dict]) -> SyncRun:
    planned: list[StockChange] = []
    applied: list[StockChange] = []
    for cr in change_rows:
        change = StockChange(
            sku=SKU(cr["sku"]),
            kind=ChangeKind(cr["kind"]),
            new_stock=StockLevel(cr["new_stock"]) if cr["new_stock"] is not None else None,
            reason=cr["reason"] or "",
        )
        planned.append(change)
        if cr["applied"]:
            applied.append(change)
    errors = [
        SyncError(
            message=er["message"],
            sku=SKU(er["sku"]) if er["sku"] is not None else None,
            when=er["when_at"],
        )
        for er in error_rows
    ]
    return SyncRun(
        run_id=row["run_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        items_checked=row["items_checked"],
        changes_planned=planned,
        changes_applied=applied,
        errors=errors,
    )
