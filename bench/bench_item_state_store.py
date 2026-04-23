"""Bench: ItemStateStore operations at scale.

Measures set_active + get_active_skus latency as the stored set grows.
Runs against sqlite :memory: (dev baseline) and a file-backed sqlite
(closer to prod-lite). Skips postgres — needs an external DB.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import sqlalchemy

from inventory_sync.log import get
from inventory_sync.persistence.item_state_store import SqlItemStateStore


def _time_ms(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000


def bench_backend(backend_name: str, url: str) -> None:
    engine = sqlalchemy.create_engine(url, future=True)
    try:
        store = SqlItemStateStore(engine=engine, logger=get("bench"))
        store.create_schema()

        print(f"\n== {backend_name} ({url}) ==")
        print(f"{'N SKUs':>10}  {'set_active':>12}  {'get_active':>12}")
        print("-" * 40)
        for n in [100, 1_000, 10_000, 100_000]:
            skus = {f"SKU-{i:07d}" for i in range(n)}
            set_ms = _time_ms(lambda: store.set_active("laura", "oos", skus))
            get_ms = _time_ms(lambda: store.get_active_skus("laura", "oos"))
            print(f"{n:>10,}  {set_ms:>10,.1f}ms  {get_ms:>10,.1f}ms")
    finally:
        engine.dispose()  # release Windows file handle before temp dir cleanup


def main() -> None:
    bench_backend("sqlite in-memory", "sqlite:///:memory:")

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "bench.db"
        bench_backend("sqlite file", f"sqlite:///{db_path}")


if __name__ == "__main__":
    main()
