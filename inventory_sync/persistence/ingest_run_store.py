"""SQL-backed ingest-run status — a frontend-trackable outcome for each upload.

The dashboard generates a serial (`run_ref`) at upload time, passes it into the
workflow dispatch, and polls this store to learn the async ingest's outcome:
`running` → `success` | `rejected` | `error`, with a reason and counts. This needs
no GitHub run-id or Actions polling — the frontend already knows its own serial.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import Engine, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from inventory_sync.log import Logger, get
from inventory_sync.persistence.schema import ingest_runs, metadata


@dataclass(frozen=True)
class IngestRunRecord:
    run_ref: str
    customer_id: str
    blob_url: str | None
    status: str                 # running | success | rejected | error
    reason: str | None
    created: int | None
    archived: int | None
    skipped_existing: int | None
    errors: int | None
    started_at: datetime
    finished_at: datetime | None


def _to_record(row) -> IngestRunRecord:
    return IngestRunRecord(
        run_ref=row["run_ref"], customer_id=row["customer_id"], blob_url=row["blob_url"],
        status=row["status"], reason=row["reason"], created=row["created"],
        archived=row["archived"], skipped_existing=row["skipped_existing"],
        errors=row["errors"], started_at=row["started_at"], finished_at=row["finished_at"],
    )


@dataclass
class SqlIngestRunStore:
    engine: Engine
    logger: Logger = field(default_factory=lambda: get("persistence.ingest_run_store"))

    def create_schema(self) -> None:
        metadata.create_all(self.engine)

    def start(self, run_ref: str, customer_id: str, blob_url: str | None) -> None:
        """Mark a run as `running` (upsert — a retry with the same serial resets it)."""
        insert = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
        stmt = insert(ingest_runs).values(
            run_ref=run_ref, customer_id=customer_id, blob_url=blob_url,
            status="running", reason=None, created=None, archived=None,
            skipped_existing=None, errors=None,
            started_at=datetime.now(timezone.utc), finished_at=None,
        ).on_conflict_do_update(
            index_elements=[ingest_runs.c.run_ref],
            set_={
                "customer_id": customer_id, "blob_url": blob_url, "status": "running",
                "reason": None, "created": None, "archived": None,
                "skipped_existing": None, "errors": None,
                "started_at": datetime.now(timezone.utc), "finished_at": None,
            },
        )
        with Session(self.engine) as session:
            with session.begin():
                session.execute(stmt)

    def finish_success(self, run_ref: str, *, created: int, archived: int,
                       skipped_existing: int, errors: int) -> None:
        self._finish(run_ref, status="success", created=created, archived=archived,
                     skipped_existing=skipped_existing, errors=errors)

    def finish_rejected(self, run_ref: str, reason: str) -> None:
        self._finish(run_ref, status="rejected", reason=reason)

    def finish_error(self, run_ref: str, reason: str) -> None:
        self._finish(run_ref, status="error", reason=reason)

    def _finish(self, run_ref: str, *, status: str, reason: str | None = None,
                created: int | None = None, archived: int | None = None,
                skipped_existing: int | None = None, errors: int | None = None) -> None:
        values = {"status": status, "finished_at": datetime.now(timezone.utc)}
        if reason is not None:
            values["reason"] = reason
        if created is not None:
            values.update(created=created, archived=archived,
                          skipped_existing=skipped_existing, errors=errors)
        with Session(self.engine) as session:
            with session.begin():
                session.execute(
                    update(ingest_runs).where(ingest_runs.c.run_ref == run_ref).values(**values)
                )
        self.logger.info("ingest_run_finished", run_ref=run_ref, status=status)

    def get(self, run_ref: str) -> IngestRunRecord | None:
        with Session(self.engine) as session:
            row = session.execute(
                select(ingest_runs).where(ingest_runs.c.run_ref == run_ref)
            ).mappings().first()
        return _to_record(row) if row else None
