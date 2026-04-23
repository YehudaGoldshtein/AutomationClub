"""Contract tests for VendorSnapshotCache."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import sqlalchemy

from inventory_sync.domain import VendorProductSnapshot
from inventory_sync.fakes import InMemoryVendorSnapshotCache
from inventory_sync.log import get
from inventory_sync.persistence.vendor_snapshot_cache import SqlVendorSnapshotCache


def _snap(vid: str, avail: bool = True) -> VendorProductSnapshot:
    return VendorProductSnapshot(
        vendor_product_id=vid,
        is_available=avail,
        stock_count=None,
        raw_availability="InStock" if avail else "OutOfStock",
        name=f"Product {vid}",
        price=Decimal("199.00"),
        currency="ILS",
        image_url=f"https://example/{vid}.jpg",
    )


class VendorSnapshotCacheContract:
    @pytest.fixture
    def cache(self):
        raise NotImplementedError

    def test_empty_cache_returns_empty(self, cache):
        assert cache.get_fresh("laura-design", ["A", "B"], ttl_minutes=30) == {}

    def test_upsert_then_get_within_ttl(self, cache):
        cache.upsert_many("laura-design", {"A": _snap("A"), "B": _snap("B", False)})
        got = cache.get_fresh("laura-design", ["A", "B"], ttl_minutes=30)
        assert set(got) == {"A", "B"}
        assert got["A"].is_available is True
        assert got["B"].is_available is False
        assert got["A"].name == "Product A"
        assert got["A"].price == Decimal("199.00")

    def test_stale_entries_excluded(self, cache):
        past = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)
        cache.upsert_many("laura-design", {"A": _snap("A")}, now=past)
        # TTL = 30 min, query "now" one hour later → must be stale
        now = past + timedelta(hours=1)
        assert cache.get_fresh("laura-design", ["A"], ttl_minutes=30, now=now) == {}

    def test_vendor_scope_isolation(self, cache):
        cache.upsert_many("laura-design", {"A": _snap("A")})
        cache.upsert_many("other-vendor", {"A": _snap("A", False)})
        laura = cache.get_fresh("laura-design", ["A"], ttl_minutes=30)
        other = cache.get_fresh("other-vendor", ["A"], ttl_minutes=30)
        assert laura["A"].is_available is True
        assert other["A"].is_available is False

    def test_upsert_overwrites_existing(self, cache):
        past = datetime(2026, 4, 23, 10, 0, 0, tzinfo=timezone.utc)
        cache.upsert_many("laura-design", {"A": _snap("A", True)}, now=past)
        now = past + timedelta(minutes=5)
        cache.upsert_many("laura-design", {"A": _snap("A", False)}, now=now)
        got = cache.get_fresh("laura-design", ["A"], ttl_minutes=30, now=now)
        assert got["A"].is_available is False

    def test_partial_hits_filter_correctly(self, cache):
        cache.upsert_many("laura-design", {"A": _snap("A")})
        got = cache.get_fresh("laura-design", ["A", "B", "C"], ttl_minutes=30)
        assert list(got) == ["A"]

    def test_empty_ids_returns_empty(self, cache):
        cache.upsert_many("laura-design", {"A": _snap("A")})
        assert cache.get_fresh("laura-design", [], ttl_minutes=30) == {}


class TestInMemoryVendorSnapshotCache(VendorSnapshotCacheContract):
    @pytest.fixture
    def cache(self):
        return InMemoryVendorSnapshotCache()


class TestSqlVendorSnapshotCache(VendorSnapshotCacheContract):
    @pytest.fixture
    def cache(self):
        engine = sqlalchemy.create_engine("sqlite:///:memory:")
        sql = SqlVendorSnapshotCache(engine=engine, logger=get("test"))
        sql.create_schema()
        return sql
