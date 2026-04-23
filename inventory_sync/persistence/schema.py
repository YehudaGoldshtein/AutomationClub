"""SQLAlchemy Core table definitions for the persistence layer.

Single MetaData object so all tables live together. New tables for other
concerns (notification_sends, users, etc.) should be added here.
"""
from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)


metadata = MetaData()


sync_runs = Table(
    "sync_runs", metadata,
    Column("run_id", String, primary_key=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("items_checked", Integer, nullable=False, default=0),
    Column("changes_planned_count", Integer, nullable=False, default=0),
    Column("changes_applied_count", Integer, nullable=False, default=0),
    Column("errors_count", Integer, nullable=False, default=0),
    Column("vendor_missing_count", Integer, nullable=False, default=0),
    Column("duration_seconds", Float, nullable=True),
)

Index("ix_sync_runs_started_at", sync_runs.c.started_at)


sync_run_changes = Table(
    "sync_run_changes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("sync_runs.run_id"), nullable=False),
    Column("sku", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("new_stock", Integer, nullable=True),
    Column("reason", String, nullable=True),
    Column("applied", Boolean, nullable=False, default=False),
)

Index("ix_sync_run_changes_run_id", sync_run_changes.c.run_id)
Index("ix_sync_run_changes_sku", sync_run_changes.c.sku)


sync_run_errors = Table(
    "sync_run_errors", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, ForeignKey("sync_runs.run_id"), nullable=False),
    Column("sku", String, nullable=True),
    Column("message", String, nullable=False),
    Column("when_at", DateTime(timezone=True), nullable=False),
)

Index("ix_sync_run_errors_run_id", sync_run_errors.c.run_id)


# --- Per-item state tracking (drives delta-based notifications) ---

item_state = Table(
    "item_state", metadata,
    Column("vendor_name", String, nullable=False),
    Column("state_key", String, nullable=False),
    Column("sku", String, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    # Composite PK enforces uniqueness per (vendor, state_key, sku).
    # Rows exist only for SKUs CURRENTLY active in that state.
    # Absent rows = not active. No is_active bool needed.
    # Multi-vendor / future multi-customer: add customer_name column here.
    schema=None,
)
from sqlalchemy import PrimaryKeyConstraint as _PK  # noqa: E402
item_state.append_constraint(_PK(item_state.c.vendor_name, item_state.c.state_key, item_state.c.sku))


item_state_seeded = Table(
    "item_state_seeded", metadata,
    Column("vendor_name", String, nullable=False),
    Column("state_key", String, nullable=False),
    Column("first_seeded_at", DateTime(timezone=True), nullable=False),
)

item_state_seeded.append_constraint(_PK(item_state_seeded.c.vendor_name, item_state_seeded.c.state_key))
