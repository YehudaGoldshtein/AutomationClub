"""Tests for vendor_scan_pass — TTL-gated shared vendor fetch."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from inventory_sync.domain import VendorProductSnapshot
from inventory_sync.fakes import InMemorySupplier, InMemoryVendorSnapshotCache
from inventory_sync.log import get
from inventory_sync.vendor_scan import vendor_scan_pass


def _snap(vid: str, avail: bool = True) -> VendorProductSnapshot:
    return VendorProductSnapshot(
        vendor_product_id=vid,
        is_available=avail,
        stock_count=None,
        raw_availability="InStock" if avail else "OutOfStock",
        name=None,
        price=Decimal("1.0"),
        currency="ILS",
        image_url=None,
    )


class CountingSupplier:
    """Wraps InMemorySupplier, counts how many ids got through to the network."""

    def __init__(self, inner: InMemorySupplier):
        self._inner = inner
        self.call_ids: list[set[str]] = []

    def fetch_snapshots(self, ids):
        ids_set = {str(i) for i in ids}
        self.call_ids.append(ids_set)
        return self._inner.fetch_snapshots(ids_set)


def _make(snapshots: dict[str, VendorProductSnapshot]):
    inner = InMemorySupplier(snapshots)
    supplier = CountingSupplier(inner)
    cache = InMemoryVendorSnapshotCache()
    return supplier, cache


def test_first_run_fetches_all_and_fills_cache():
    supplier, cache = _make({"A": _snap("A"), "B": _snap("B")})
    out = vendor_scan_pass(
        vendor_name="laura-design",
        supplier=supplier,
        ids_needed=["A", "B"],
        cache=cache,
        ttl_minutes=30,
        logger=get("test"),
    )
    assert set(out) == {"A", "B"}
    assert supplier.call_ids == [{"A", "B"}]
    # Second call with same TTL should be zero-network
    supplier.call_ids.clear()
    out2 = vendor_scan_pass(
        vendor_name="laura-design", supplier=supplier, ids_needed=["A", "B"],
        cache=cache, ttl_minutes=30, logger=get("test"),
    )
    assert set(out2) == {"A", "B"}
    assert supplier.call_ids == []


def test_partial_cache_fetches_only_missing():
    supplier, cache = _make({"A": _snap("A"), "B": _snap("B"), "C": _snap("C")})
    cache.upsert_many("laura-design", {"A": _snap("A")})

    out = vendor_scan_pass(
        vendor_name="laura-design", supplier=supplier, ids_needed=["A", "B", "C"],
        cache=cache, ttl_minutes=30, logger=get("test"),
    )
    assert set(out) == {"A", "B", "C"}
    # Only B and C must have hit the network
    assert supplier.call_ids == [{"B", "C"}]


def test_stale_cache_entries_refetched():
    supplier, cache = _make({"A": _snap("A", True)})
    past = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)
    cache.upsert_many("laura-design", {"A": _snap("A", False)}, now=past)

    # TTL = 30 min, now is 2h later → stale
    now = past + timedelta(hours=2)
    out = vendor_scan_pass(
        vendor_name="laura-design", supplier=supplier, ids_needed=["A"],
        cache=cache, ttl_minutes=30, logger=get("test"), now=now,
    )
    assert supplier.call_ids == [{"A"}]
    # Refetched value wins (True)
    assert out["A"].is_available is True


def test_vendor_404_simply_absent_from_result():
    supplier, cache = _make({"A": _snap("A")})  # no "GONE"
    out = vendor_scan_pass(
        vendor_name="laura-design", supplier=supplier, ids_needed=["A", "GONE"],
        cache=cache, ttl_minutes=30, logger=get("test"),
    )
    assert "A" in out and "GONE" not in out


def test_empty_ids_skips_fetch_entirely():
    supplier, cache = _make({"A": _snap("A")})
    out = vendor_scan_pass(
        vendor_name="laura-design", supplier=supplier, ids_needed=[],
        cache=cache, ttl_minutes=30, logger=get("test"),
    )
    assert out == {}
    assert supplier.call_ids == []


def test_two_customers_share_one_fetch():
    """Customer A triggers the fetch; Customer B reuses the cache → 0 extra network."""
    supplier, cache = _make({"A": _snap("A"), "B": _snap("B")})

    # Customer A, 30-min TTL
    vendor_scan_pass(
        vendor_name="laura-design", supplier=supplier, ids_needed=["A", "B"],
        cache=cache, ttl_minutes=30, logger=get("test"),
    )
    assert supplier.call_ids == [{"A", "B"}]
    supplier.call_ids.clear()

    # Customer B, same vendor, same TTL, same ids
    out_b = vendor_scan_pass(
        vendor_name="laura-design", supplier=supplier, ids_needed=["A", "B"],
        cache=cache, ttl_minutes=30, logger=get("test"),
    )
    assert set(out_b) == {"A", "B"}
    assert supplier.call_ids == []  # no extra hits
