"""End-to-end tests for SyncEngine using in-memory fakes."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from inventory_sync.domain import (
    SKU,
    ChangeKind,
    Product,
    StockLevel,
    VendorProductId,
    VendorProductSnapshot,
)
from inventory_sync.engine import SyncEngine
from inventory_sync.fakes import InMemoryStore, InMemorySupplier
from inventory_sync.log import Logger, configure
from inventory_sync.policies import DefaultStockPolicy


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def log(log_dir: Path) -> Logger:
    return configure(log_dir=log_dir)


def _engine(store, supplier, log: Logger) -> SyncEngine:
    return SyncEngine(store=store, supplier=supplier, policy=DefaultStockPolicy(), logger=log)


def _snap(vid: str, available: bool, count: int | None = None) -> VendorProductSnapshot:
    return VendorProductSnapshot(
        vendor_product_id=VendorProductId(vid),
        is_available=available,
        stock_count=count,
    )


class TestHappyPaths:
    def test_no_changes_when_binary_vendor_agrees_with_positive_store(self, log: Logger):
        store = InMemoryStore(products=[
            Product(SKU("X"), VendorProductId("V"), StockLevel(5), published=True),
        ])
        supplier = InMemorySupplier(snapshots={VendorProductId("V"): _snap("V", True)})

        run = _engine(store, supplier, log).run()

        assert run.items_checked == 1
        assert run.changes_applied == []
        assert run.errors == []
        assert run.finished_at is not None

    def test_binary_oos_sets_stock_to_zero(self, log: Logger):
        store = InMemoryStore(products=[
            Product(SKU("X"), VendorProductId("V"), StockLevel(5), published=True),
        ])
        supplier = InMemorySupplier(snapshots={VendorProductId("V"): _snap("V", False)})

        run = _engine(store, supplier, log).run()

        kinds = {c.kind for c in run.changes_applied}
        assert kinds == {ChangeKind.SET_STOCK}
        p = store.get(SKU("X"))
        assert p.stock == StockLevel(0)
        assert p.published is True  # UNPUBLISH is NOT auto-emitted in v0.1

    def test_binary_back_in_stock_from_zero_sets_one(self, log: Logger):
        store = InMemoryStore(products=[
            Product(SKU("X"), VendorProductId("V"), StockLevel(0), published=True),
        ])
        supplier = InMemorySupplier(snapshots={VendorProductId("V"): _snap("V", True)})

        run = _engine(store, supplier, log).run()

        assert len(run.changes_applied) == 1
        assert store.get(SKU("X")).stock == StockLevel(1)

    def test_exact_count_syncs_to_exact_number(self, log: Logger):
        store = InMemoryStore(products=[
            Product(SKU("X"), VendorProductId("V"), StockLevel(5), published=True),
        ])
        supplier = InMemorySupplier(snapshots={VendorProductId("V"): _snap("V", True, 42)})

        run = _engine(store, supplier, log).run()

        assert store.get(SKU("X")).stock == StockLevel(42)

    def test_mixed_catalog_applies_only_needed_changes(self, log: Logger):
        store = InMemoryStore(products=[
            Product(SKU("A"), VendorProductId("VA"), StockLevel(3), published=True),
            Product(SKU("B"), VendorProductId("VB"), StockLevel(5), published=True),
            Product(SKU("C"), VendorProductId("VC"), StockLevel(0), published=True),
        ])
        supplier = InMemorySupplier(snapshots={
            VendorProductId("VA"): _snap("VA", True),   # binary, store has 3 -> no change
            VendorProductId("VB"): _snap("VB", False),  # OOS -> set 0
            VendorProductId("VC"): _snap("VC", True),   # binary, store 0 -> set 1
        })

        run = _engine(store, supplier, log).run()

        assert run.items_checked == 3
        assert run.errors == []
        assert store.get(SKU("A")).stock == StockLevel(3)
        assert store.get(SKU("B")).stock == StockLevel(0)
        assert store.get(SKU("C")).stock == StockLevel(1)


class TestPartialFailures:
    def test_vendor_missing_product_is_recorded_others_still_sync(self, log: Logger):
        store = InMemoryStore(products=[
            Product(SKU("A"), VendorProductId("VA"), StockLevel(3), published=True),
            Product(SKU("B"), VendorProductId("VB-missing"), StockLevel(5), published=True),
        ])
        supplier = InMemorySupplier(snapshots={
            VendorProductId("VA"): _snap("VA", True, 10),
        })

        run = _engine(store, supplier, log).run()

        assert len(run.errors) == 1
        assert run.errors[0].sku == SKU("B")
        assert store.get(SKU("A")).stock == StockLevel(10)
        assert store.get(SKU("B")).stock == StockLevel(5)

    def test_single_change_apply_failure_continues_other_products(self, log: Logger):
        class FlakeyStore(InMemoryStore):
            def update_stock(self, sku, stock):
                if sku == SKU("A"):
                    raise RuntimeError("simulated flake")
                super().update_stock(sku, stock)

        store = FlakeyStore(products=[
            Product(SKU("A"), VendorProductId("VA"), StockLevel(3), published=True),
            Product(SKU("B"), VendorProductId("VB"), StockLevel(5), published=True),
        ])
        supplier = InMemorySupplier(snapshots={
            VendorProductId("VA"): _snap("VA", True, 10),
            VendorProductId("VB"): _snap("VB", True, 20),
        })

        run = _engine(store, supplier, log).run()

        assert any(e.sku == SKU("A") for e in run.errors)
        assert store.get(SKU("B")).stock == StockLevel(20)


class TestCatastrophicFailures:
    def test_supplier_unreachable_aborts_without_touching_store(self, log: Logger):
        class BrokenSupplier:
            def fetch_snapshots(self, ids):
                raise ConnectionError("vendor unreachable")

        store = InMemoryStore(products=[
            Product(SKU("A"), VendorProductId("VA"), StockLevel(3), published=True),
        ])

        run = SyncEngine(
            store=store, supplier=BrokenSupplier(), policy=DefaultStockPolicy(), logger=log
        ).run()

        assert len(run.errors) == 1
        assert run.changes_applied == []
        assert store.get(SKU("A")).stock == StockLevel(3)
        assert run.finished_at is not None

    def test_store_unreachable_aborts_run(self, log: Logger):
        class BrokenStore:
            def list_products(self):
                raise ConnectionError("store down")
            def update_stock(self, sku, stock): ...
            def unpublish(self, sku): ...
            def republish(self, sku): ...

        run = SyncEngine(
            store=BrokenStore(),
            supplier=InMemorySupplier(snapshots={}),
            policy=DefaultStockPolicy(),
            logger=log,
        ).run()

        assert len(run.errors) == 1
        assert run.changes_applied == []
        assert run.finished_at is not None


class TestLogging:
    def test_every_log_line_carries_run_id(self, log: Logger, log_dir: Path):
        store = InMemoryStore(products=[
            Product(SKU("X"), VendorProductId("V"), StockLevel(5), published=True),
        ])
        supplier = InMemorySupplier(snapshots={VendorProductId("V"): _snap("V", False)})

        run = _engine(store, supplier, log).run()

        lines = (log_dir / "inventory_sync.log").read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(l) for l in lines]

        event_names = {e["event"] for e in events}
        assert "sync_start" in event_names
        assert "sync_complete" in event_names
        assert "change_applied" in event_names

        for e in events:
            if e["logger"] == "inventory_sync":
                assert e.get("run_id") == run.run_id
