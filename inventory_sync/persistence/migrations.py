"""One-shot schema migrations for existing databases.

`metadata.create_all()` only creates missing *tables* — it never adds columns to
an existing one. So new columns on a live table (Neon prod, dev sqlite) need an
explicit, idempotent ALTER. Logic lives here (testable); scripts/ wraps it as a CLI.
"""
from __future__ import annotations

from sqlalchemy import Engine, inspect, text

# store_products lifecycle columns (Laura upload). Ordered; dialect-specific DDL.
# NOT NULL + DEFAULT backfills existing rows to active/approved in one statement.
_STORE_PRODUCTS_LIFECYCLE: list[tuple[str, dict[str, str]]] = [
    ("status", {"postgresql": "VARCHAR NOT NULL DEFAULT 'active'", "sqlite": "TEXT NOT NULL DEFAULT 'active'"}),
    ("approved", {"postgresql": "BOOLEAN NOT NULL DEFAULT TRUE", "sqlite": "BOOLEAN NOT NULL DEFAULT 1"}),
    ("approved_at", {"postgresql": "TIMESTAMPTZ", "sqlite": "TIMESTAMP"}),
    ("is_new_collection", {"postgresql": "BOOLEAN NOT NULL DEFAULT FALSE", "sqlite": "BOOLEAN NOT NULL DEFAULT 0"}),
    ("needs_review", {"postgresql": "BOOLEAN NOT NULL DEFAULT FALSE", "sqlite": "BOOLEAN NOT NULL DEFAULT 0"}),
    ("needs_review_reason", {"postgresql": "VARCHAR", "sqlite": "TEXT"}),  # nullable; why a draft is flagged
    ("vendor", {"postgresql": "VARCHAR", "sqlite": "TEXT"}),  # nullable; store vendor/supplier tag
]


def add_store_products_lifecycle_columns(engine: Engine) -> list[str]:
    """Add missing lifecycle columns to store_products. Returns names added.

    Idempotent: skips columns that already exist; a no-op on a fresh/up-to-date DB.
    """
    dialect = engine.dialect.name
    insp = inspect(engine)
    if "store_products" not in insp.get_table_names():
        return []  # fresh install — create_all() will build it with the columns
    existing = {c["name"] for c in insp.get_columns("store_products")}
    added: list[str] = []
    with engine.begin() as conn:
        for name, ddl_by_dialect in _STORE_PRODUCTS_LIFECYCLE:
            if name in existing:
                continue
            ddl = ddl_by_dialect[dialect]
            conn.execute(text(f"ALTER TABLE store_products ADD COLUMN {name} {ddl}"))
            added.append(name)
    return added
