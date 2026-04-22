"""Tests for the archive audit."""
from __future__ import annotations

from decimal import Decimal

import pytest

from inventory_sync.audit import (
    AuditFinding,
    find_archived_but_available,
    format_archived_but_available_message,
)
from inventory_sync.domain import (
    SKU,
    Product,
    StockLevel,
    VendorProductId,
    VendorProductSnapshot,
)
from inventory_sync.fakes import InMemoryStore, InMemorySupplier
from inventory_sync.log import Logger, configure


@pytest.fixture
def log(tmp_path) -> Logger:
    return configure(log_dir=tmp_path / "logs")


def _snap(vid: str, available: bool, **extra) -> VendorProductSnapshot:
    return VendorProductSnapshot(
        vendor_product_id=VendorProductId(vid),
        is_available=available,
        **extra,
    )


class TestFindArchivedButAvailable:
    def test_returns_products_archived_but_available(self, log: Logger):
        store = InMemoryStore(products=[
            Product(SKU("A"), VendorProductId("VA"), StockLevel(0), published=False),
            Product(SKU("B"), VendorProductId("VB"), StockLevel(5), published=True),
            Product(SKU("C"), VendorProductId("VC"), StockLevel(0), published=False),
        ])
        supplier = InMemorySupplier(snapshots={
            VendorProductId("VA"): _snap("VA", True),   # archived + available => FINDING
            VendorProductId("VB"): _snap("VB", True),   # active + available => skip (not archived)
            VendorProductId("VC"): _snap("VC", False),  # archived + OOS => skip (correctly archived)
        })

        findings = find_archived_but_available(store=store, supplier=supplier, logger=log)
        assert {f.product.sku for f in findings} == {SKU("A")}

    def test_empty_when_nothing_archived(self, log: Logger):
        store = InMemoryStore(products=[
            Product(SKU("A"), VendorProductId("VA"), StockLevel(5), published=True),
        ])
        supplier = InMemorySupplier(snapshots={VendorProductId("VA"): _snap("VA", True)})

        findings = find_archived_but_available(store=store, supplier=supplier, logger=log)
        assert findings == []

    def test_empty_when_all_archived_are_oos(self, log: Logger):
        store = InMemoryStore(products=[
            Product(SKU("A"), VendorProductId("VA"), StockLevel(0), published=False),
        ])
        supplier = InMemorySupplier(snapshots={VendorProductId("VA"): _snap("VA", False)})

        findings = find_archived_but_available(store=store, supplier=supplier, logger=log)
        assert findings == []

    def test_supplier_missing_product_is_treated_as_unavailable(self, log: Logger):
        """If the supplier returns no snapshot (404), we can't claim availability — skip it."""
        store = InMemoryStore(products=[
            Product(SKU("A"), VendorProductId("VA"), StockLevel(0), published=False),
        ])
        supplier = InMemorySupplier(snapshots={})  # 404 on everything

        findings = find_archived_but_available(store=store, supplier=supplier, logger=log)
        assert findings == []


class TestFormatMessage:
    def test_no_findings_reports_clean(self):
        subject, body = format_archived_but_available_message([], store_name="Max Baby")
        assert "Archive audit" in subject
        assert "Nothing to unarchive" in body

    def test_with_findings_includes_count_and_skus(self):
        findings = [
            AuditFinding(
                product=Product(SKU("3200-118"), VendorProductId("3200-118"), StockLevel(0), published=False),
                snapshot=_snap("3200-118", True, price=Decimal("89"), currency="ILS", name="Stroller pad"),
            ),
            AuditFinding(
                product=Product(SKU("1603-135"), VendorProductId("1603-135"), StockLevel(0), published=False),
                snapshot=_snap("1603-135", True, price=Decimal("69"), currency="ILS"),
            ),
        ]
        subject, body = format_archived_but_available_message(findings, store_name="Max Baby")
        assert "Archive audit" in subject
        assert "2 archived" in body
        assert "3200-118" in body
        assert "89 ILS" in body
        assert "Stroller pad" in body
        assert "1603-135" in body
        assert "69 ILS" in body

    def test_price_strips_trailing_zeros(self):
        """Decimal('39.5000') should render as '39.5 ILS', not '39.5000 ILS'."""
        findings = [
            AuditFinding(
                product=Product(SKU("A"), VendorProductId("A"), StockLevel(0), published=False),
                snapshot=_snap("A", True, price=Decimal("39.5000"), currency="ILS"),
            ),
            AuditFinding(
                product=Product(SKU("B"), VendorProductId("B"), StockLevel(0), published=False),
                snapshot=_snap("B", True, price=Decimal("44.5000"), currency="ILS"),
            ),
            AuditFinding(
                product=Product(SKU("C"), VendorProductId("C"), StockLevel(0), published=False),
                snapshot=_snap("C", True, price=Decimal("223.2000"), currency="ILS"),
            ),
        ]
        _, body = format_archived_but_available_message(findings)
        assert "39.5 ILS" in body and "39.5000" not in body
        assert "44.5 ILS" in body and "44.5000" not in body
        assert "223.2 ILS" in body and "223.2000" not in body

    def test_price_keeps_integer_clean(self):
        """Decimal('89') should render as '89 ILS', not '89.00 ILS'."""
        findings = [
            AuditFinding(
                product=Product(SKU("A"), VendorProductId("A"), StockLevel(0), published=False),
                snapshot=_snap("A", True, price=Decimal("89"), currency="ILS"),
            ),
        ]
        _, body = format_archived_but_available_message(findings)
        assert "89 ILS" in body and "89.00" not in body

    def test_price_keeps_two_decimals_when_needed(self):
        findings = [
            AuditFinding(
                product=Product(SKU("A"), VendorProductId("A"), StockLevel(0), published=False),
                snapshot=_snap("A", True, price=Decimal("89.99"), currency="ILS"),
            ),
        ]
        _, body = format_archived_but_available_message(findings)
        assert "89.99 ILS" in body

    def test_findings_without_optional_fields_still_format(self):
        """Price, currency, and name are all optional on the snapshot."""
        findings = [
            AuditFinding(
                product=Product(SKU("X-1"), VendorProductId("X-1"), StockLevel(0), published=False),
                snapshot=_snap("X-1", True),
            ),
        ]
        _, body = format_archived_but_available_message(findings)
        assert "X-1" in body
