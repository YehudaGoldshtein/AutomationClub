"""Contract tests for SupplierSource. Every implementation must pass these."""
from __future__ import annotations

import pytest

from inventory_sync.domain import StockLevel, VendorProductId
from inventory_sync.fakes import InMemorySupplier
from inventory_sync.interfaces import SupplierSource


SEEDED_STOCK: dict[VendorProductId, StockLevel] = {
    VendorProductId("V1"): StockLevel(5),
    VendorProductId("V2"): StockLevel(0),
    VendorProductId("V3"): StockLevel(10),
}


class SupplierContract:
    """Mix into a concrete test class and provide the `supplier` fixture seeded with SEEDED_STOCK."""

    @pytest.fixture
    def supplier(self) -> SupplierSource:
        raise NotImplementedError("provide a `supplier` fixture in the subclass")

    def test_fetch_known_ids_preserves_in_stock_vs_oos(self, supplier: SupplierSource):
        """Contract: in-stock stays positive, out-of-stock stays zero.

        Exact stock counts are NOT part of the contract because some adapters
        (HTML scrapers reading Schema.org availability) are inherently binary.
        Exact-count adapters are free to preserve precision as an extension.
        """
        result = supplier.fetch_stock([VendorProductId("V1"), VendorProductId("V2")])
        assert result[VendorProductId("V1")].value > 0
        assert result[VendorProductId("V2")] == StockLevel(0)

    def test_fetch_unknown_id_is_omitted(self, supplier: SupplierSource):
        result = supplier.fetch_stock([VendorProductId("V1"), VendorProductId("UNKNOWN")])
        assert VendorProductId("V1") in result
        assert VendorProductId("UNKNOWN") not in result

    def test_fetch_empty_returns_empty(self, supplier: SupplierSource):
        result = supplier.fetch_stock([])
        assert result == {}

    def test_fetch_preserves_zero_stock(self, supplier: SupplierSource):
        result = supplier.fetch_stock([VendorProductId("V2")])
        assert result[VendorProductId("V2")] == StockLevel(0)


class TestInMemorySupplier(SupplierContract):
    @pytest.fixture
    def supplier(self) -> SupplierSource:
        return InMemorySupplier(stock=dict(SEEDED_STOCK))
