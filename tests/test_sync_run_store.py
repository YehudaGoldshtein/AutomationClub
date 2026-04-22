"""Contract tests for SyncRunStore.

Runs against both InMemorySyncRunStore and SqlSyncRunStore (sqlite:///:memory:).
Both must behave identically per the contract.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy

from inventory_sync.domain import (
    SKU,
    ChangeKind,
    StockChange,
    StockLevel,
    SyncError,
    SyncRun,
)
from inventory_sync.fakes import InMemorySyncRunStore
from inventory_sync.interfaces import SyncRunStore
from inventory_sync.log import get
from inventory_sync.persistence.sync_run_store import SqlSyncRunStore


def _sample_run(run_id: str = "abc123", minutes_ago: int = 0) -> SyncRun:
    started = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    finished = started + timedelta(seconds=12)
    run = SyncRun(
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        items_checked=817,
    )
    applied = [
        StockChange(SKU("2800-253"), ChangeKind.SET_STOCK, StockLevel(1), "vendor back in stock"),
        StockChange(SKU("1306-028"), ChangeKind.SET_STOCK, StockLevel(1), "vendor back in stock"),
    ]
    run.changes_planned = list(applied)
    run.changes_applied = list(applied)
    run.errors = [
        SyncError(message="simulated flake", sku=SKU("BROKEN-1")),
    ]
    run.vendor_missing = [SKU("MISSING-1"), SKU("MISSING-2")]
    return run


class SyncRunStoreContract:
    """Every SyncRunStore implementation must pass these."""

    @pytest.fixture
    def store(self) -> SyncRunStore:
        raise NotImplementedError("provide a `store` fixture in the subclass")

    def test_save_then_get(self, store: SyncRunStore):
        run = _sample_run("abc123")
        store.save(run)
        loaded = store.get("abc123")
        assert loaded is not None
        assert loaded.run_id == "abc123"
        assert loaded.items_checked == 817
        assert len(loaded.changes_planned) == 2
        assert len(loaded.changes_applied) == 2
        assert len(loaded.errors) == 1

    def test_get_unknown_returns_none(self, store: SyncRunStore):
        assert store.get("does-not-exist") is None

    def test_list_recent_is_empty_initially(self, store: SyncRunStore):
        assert store.list_recent() == []

    def test_list_recent_orders_by_started_at_desc(self, store: SyncRunStore):
        old = _sample_run("run-old", minutes_ago=60)
        new = _sample_run("run-new", minutes_ago=1)
        store.save(old)
        store.save(new)
        recent = store.list_recent(limit=10)
        assert [r.run_id for r in recent] == ["run-new", "run-old"]

    def test_list_recent_honors_limit(self, store: SyncRunStore):
        for i in range(5):
            store.save(_sample_run(f"run-{i}", minutes_ago=i))
        assert len(store.list_recent(limit=3)) == 3

    def test_save_overwrites_existing_run(self, store: SyncRunStore):
        original = _sample_run("abc123")
        store.save(original)

        updated = _sample_run("abc123")
        updated.items_checked = 999
        updated.changes_applied.append(
            StockChange(SKU("NEW-1"), ChangeKind.SET_STOCK, StockLevel(2), "new change")
        )
        updated.changes_planned.append(updated.changes_applied[-1])
        store.save(updated)

        loaded = store.get("abc123")
        assert loaded is not None
        assert loaded.items_checked == 999
        assert len(loaded.changes_applied) == 3

    def test_preserves_change_kind_and_stock_level(self, store: SyncRunStore):
        run = _sample_run("abc")
        store.save(run)
        loaded = store.get("abc")
        assert loaded is not None
        kinds = {c.kind for c in loaded.changes_applied}
        assert ChangeKind.SET_STOCK in kinds
        stocks = {c.new_stock for c in loaded.changes_applied}
        assert StockLevel(1) in stocks

    def test_preserves_errors(self, store: SyncRunStore):
        run = _sample_run("abc")
        store.save(run)
        loaded = store.get("abc")
        assert loaded is not None
        assert loaded.errors[0].message == "simulated flake"
        assert loaded.errors[0].sku == SKU("BROKEN-1")


class TestInMemorySyncRunStore(SyncRunStoreContract):
    @pytest.fixture
    def store(self) -> SyncRunStore:
        return InMemorySyncRunStore()


class TestSqlSyncRunStore(SyncRunStoreContract):
    @pytest.fixture
    def store(self) -> SyncRunStore:
        # sqlite in-memory per test — fully isolated, no cleanup needed.
        engine = sqlalchemy.create_engine("sqlite:///:memory:")
        sql = SqlSyncRunStore(engine=engine, logger=get("test"))
        sql.create_schema()
        return sql
