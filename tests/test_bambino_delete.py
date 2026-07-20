"""Tests for the Bambino legacy-product delete (Phase 4).

Dry-run by default; catalog-guarded; error-isolated.
"""
from __future__ import annotations

from inventory_sync.bambino_delete import (
    TARGET_VENDORS,
    delete_existing_bambino_brands,
)
from inventory_sync.log import get

LOG = get("test")


class FakeCleanupStore:
    """Minimal store: product_ids_by_vendor + delete_product."""

    def __init__(self, products):
        self._products = products      # list[{id,title,vendor,skus}]
        self.deleted: list[str] = []
        self.raise_on: set[str] = set()

    def product_ids_by_vendor(self, vendors):
        return [p for p in self._products if p["vendor"] in set(vendors)]

    def delete_product(self, store_product_id):
        if store_product_id in self.raise_on:
            raise RuntimeError("boom")
        self.deleted.append(store_product_id)


def _legacy():
    return [
        {"id": "1", "title": "old joie", "vendor": "joie", "skus": ["10152"]},
        {"id": "2", "title": "old graco", "vendor": "GRACO", "skus": ["ZEST"]},
        {"id": "3", "title": "old bambino", "vendor": "BAMBINO", "skus": ["ABC"]},
    ]


class TestDryRunDefault:
    def test_dry_run_deletes_nothing(self):
        store = FakeCleanupStore(_legacy())
        s = delete_existing_bambino_brands(store, LOG)  # confirm defaults False
        assert s.confirmed is False
        assert s.found == 3 and s.deleted == 0
        assert store.deleted == []
        assert {t["id"] for t in s.targets} == {"1", "2", "3"}


class TestConfirmedDelete:
    def test_confirm_deletes_all_targets(self):
        store = FakeCleanupStore(_legacy())
        s = delete_existing_bambino_brands(store, LOG, confirm=True)
        assert s.deleted == 3
        assert set(store.deleted) == {"1", "2", "3"}

    def test_error_isolated(self):
        store = FakeCleanupStore(_legacy())
        store.raise_on = {"2"}
        s = delete_existing_bambino_brands(store, LOG, confirm=True)
        assert s.deleted == 2 and s.errors == 1


class TestCatalogGuard:
    def test_protects_products_whose_sku_is_a_catalog_item(self):
        # a legacy row that somehow carries a live catalogNumber must NOT be deleted
        products = _legacy() + [
            {"id": "9", "title": "fresh import", "vendor": "joie", "skus": ["110104360"]},
        ]
        store = FakeCleanupStore(products)
        s = delete_existing_bambino_brands(store, LOG, confirm=True,
                                           protect_skus={"110104360"})
        assert s.protected == 1
        assert "9" not in store.deleted
        assert s.deleted == 3


class TestTargetVendors:
    def test_default_target_vendors(self):
        assert TARGET_VENDORS == ("infanti", "joie", "graco", "GRACO", "BAMBINO")

    def test_only_target_vendors_touched(self):
        products = _legacy() + [
            {"id": "8", "title": "segal", "vendor": "segal | סגל", "skus": ["S-1"]},
        ]
        store = FakeCleanupStore(products)
        s = delete_existing_bambino_brands(store, LOG, confirm=True)
        assert "8" not in store.deleted and s.deleted == 3
