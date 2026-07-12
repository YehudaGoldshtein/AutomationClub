"""CLI: add store_products lifecycle columns to the configured database.

Usage:  python -m scripts.migrate_store_products_lifecycle
Reads DATABASE_URL from the environment (falls back to the local sqlite dev DB).
Idempotent — safe to run repeatedly.
"""
from __future__ import annotations

import os

import sqlalchemy

from inventory_sync.persistence.migrations import add_store_products_lifecycle_columns


def main() -> None:
    url = os.environ.get("DATABASE_URL") or "sqlite:///inventory_sync.db"
    engine = sqlalchemy.create_engine(url, future=True)
    added = add_store_products_lifecycle_columns(engine)
    print(f"store_products migration: added {added or 'nothing (already up to date)'}")


if __name__ == "__main__":
    main()
