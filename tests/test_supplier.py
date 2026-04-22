"""Contract tests for SupplierSource. Every implementation must pass these."""
from __future__ import annotations

import pytest

from inventory_sync.domain import VendorProductId, VendorProductSnapshot
from inventory_sync.fakes import InMemorySupplier
from inventory_sync.interfaces import SupplierSource


SEEDED_SNAPSHOTS: dict[VendorProductId, VendorProductSnapshot] = {
    VendorProductId("V1"): VendorProductSnapshot(
        vendor_product_id=VendorProductId("V1"),
        is_available=True,
        stock_count=None,
    ),
    VendorProductId("V2"): VendorProductSnapshot(
        vendor_product_id=VendorProductId("V2"),
        is_available=False,
        stock_count=None,
    ),
    VendorProductId("V3"): VendorProductSnapshot(
        vendor_product_id=VendorProductId("V3"),
        is_available=True,
        stock_count=None,
    ),
}


class SupplierContract:
    """Mix into a concrete test class and provide the `supplier` fixture seeded with SEEDED_SNAPSHOTS."""

    @pytest.fixture
    def supplier(self) -> SupplierSource:
        raise NotImplementedError("provide a `supplier` fixture in the subclass")

    def test_fetch_known_ids_preserves_availability(self, supplier: SupplierSource):
        """Contract: each returned snapshot's is_available matches the seeded value.

        Exact stock counts are optional — not every adapter can observe them.
        """
        result = supplier.fetch_snapshots([VendorProductId("V1"), VendorProductId("V2")])
        assert result[VendorProductId("V1")].is_available is True
        assert result[VendorProductId("V2")].is_available is False

    def test_fetch_unknown_id_is_omitted(self, supplier: SupplierSource):
        result = supplier.fetch_snapshots(
            [VendorProductId("V1"), VendorProductId("UNKNOWN")]
        )
        assert VendorProductId("V1") in result
        assert VendorProductId("UNKNOWN") not in result

    def test_fetch_empty_returns_empty(self, supplier: SupplierSource):
        result = supplier.fetch_snapshots([])
        assert result == {}

    def test_out_of_stock_snapshot_is_not_available(self, supplier: SupplierSource):
        result = supplier.fetch_snapshots([VendorProductId("V2")])
        snap = result[VendorProductId("V2")]
        assert snap.is_available is False


class TestInMemorySupplier(SupplierContract):
    @pytest.fixture
    def supplier(self) -> SupplierSource:
        return InMemorySupplier(snapshots=dict(SEEDED_SNAPSHOTS))
