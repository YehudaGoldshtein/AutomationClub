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
    Numeric,
    String,
    Table,
    Text,
)


metadata = MetaData()


sync_runs = Table(
    "sync_runs", metadata,
    Column("run_id", String, primary_key=True),
    Column("customer_id", String, nullable=False),
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
Index("ix_sync_runs_customer_id", sync_runs.c.customer_id)


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
    Column("customer_id", String, nullable=False),
    Column("vendor_name", String, nullable=False),
    Column("state_key", String, nullable=False),
    Column("sku", String, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    # Composite PK enforces uniqueness per (customer, vendor, state_key, sku).
    # Two customers can carry the same SKU from the same vendor without collision.
    # Rows exist only for SKUs CURRENTLY active in that state.
    # Absent rows = not active. No is_active bool needed.
    schema=None,
)
from sqlalchemy import PrimaryKeyConstraint as _PK  # noqa: E402
item_state.append_constraint(
    _PK(item_state.c.customer_id, item_state.c.vendor_name, item_state.c.state_key, item_state.c.sku)
)


item_state_seeded = Table(
    "item_state_seeded", metadata,
    Column("customer_id", String, nullable=False),
    Column("vendor_name", String, nullable=False),
    Column("state_key", String, nullable=False),
    Column("first_seeded_at", DateTime(timezone=True), nullable=False),
)

item_state_seeded.append_constraint(
    _PK(item_state_seeded.c.customer_id, item_state_seeded.c.vendor_name, item_state_seeded.c.state_key)
)


# --- Multi-tenant customer registry ---

customers = Table(
    "customers", metadata,
    Column("id", String, primary_key=True),             # short slug, e.g. "maxbaby"
    Column("display_name", String, nullable=False),
    Column("sync_interval_minutes", Integer, nullable=False, default=60),
    Column("last_synced_at", DateTime(timezone=True), nullable=True),
    # Full customer config (store platform, vendors, notification routing, recipients).
    # Secrets are NOT stored here — resolved from env per deployment.
    Column("config_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

Index("ix_customers_last_synced_at", customers.c.last_synced_at)


# --- Shared vendor snapshot cache ---
# Global: one row per (vendor, product). Many customers can read the same row.
# TTL gate is applied in code (vendor_scan_pass), not in the schema.

vendor_snapshot_cache = Table(
    "vendor_snapshot_cache", metadata,
    Column("vendor_name", String, nullable=False),
    Column("vendor_product_id", String, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("is_available", Boolean, nullable=False),
    Column("stock_count", Integer, nullable=True),
    Column("raw_availability", String, nullable=True),
    Column("name", String, nullable=True),
    Column("price", Numeric(12, 2), nullable=True),
    Column("currency", String, nullable=True),
    Column("image_url", String, nullable=True),
)

vendor_snapshot_cache.append_constraint(
    _PK(vendor_snapshot_cache.c.vendor_name, vendor_snapshot_cache.c.vendor_product_id)
)
Index("ix_vendor_snapshot_cache_fetched_at", vendor_snapshot_cache.c.fetched_at)


# --- Per-customer store product metadata ---
# Keyed by (customer_id, sku). Populated each sync from the store adapter so
# the dashboard can build storefront / admin deep links without hitting the
# store API.

store_products = Table(
    "store_products", metadata,
    Column("customer_id", String, nullable=False),
    Column("sku", String, nullable=False),
    Column("handle", String, nullable=True),
    Column("title", String, nullable=True),
    Column("store_product_id", String, nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

store_products.append_constraint(_PK(store_products.c.customer_id, store_products.c.sku))
