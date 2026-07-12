"""Contract tests for StorePlatform. Every implementation must pass these."""
from __future__ import annotations

from decimal import Decimal

import pytest

from inventory_sync.domain import (
    SKU,
    ProductDraft,
    Product,
    StockLevel,
    VariantSpec,
    VendorProductId,
)
from inventory_sync.fakes import InMemoryStore
from inventory_sync.interfaces import StorePlatform


def _draft(title: str, variants: list[VariantSpec], status: str = "draft") -> ProductDraft:
    return ProductDraft(
        title=title,
        body_html="<p>x</p>",
        vendor="לורה סוויסרה | laura swisra",
        product_type="בגד גוף",
        tags="בגד גוף",
        variants=tuple(variants),
        image_urls=(),
        status=status,
    )


SEEDED_PRODUCTS: list[Product] = [
    Product(SKU("ABC-001"), VendorProductId("V1"), StockLevel(5), published=True),
    Product(SKU("ABC-002"), VendorProductId("V2"), StockLevel(0), published=False),
    Product(SKU("ABC-003"), VendorProductId("V3"), StockLevel(10), published=True),
]


class StoreContract:
    """Mix into a concrete test class and provide the `store` fixture seeded with SEEDED_PRODUCTS."""

    @pytest.fixture
    def store(self) -> StorePlatform:
        raise NotImplementedError("provide a `store` fixture in the subclass")

    def test_list_products_returns_all_seeded_skus(self, store: StorePlatform):
        skus = {p.sku for p in store.list_products()}
        assert SKU("ABC-001") in skus
        assert SKU("ABC-002") in skus
        assert SKU("ABC-003") in skus

    def test_list_products_preserves_stock_and_published(self, store: StorePlatform):
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-001")].stock == StockLevel(5)
        assert by_sku[SKU("ABC-001")].published is True
        assert by_sku[SKU("ABC-002")].stock == StockLevel(0)
        assert by_sku[SKU("ABC-002")].published is False

    def test_list_products_includes_vendor_mapping(self, store: StorePlatform):
        """Every returned product must have a vendor_product_id populated.

        The exact representation (SKU equality, metafield, tag lookup) is
        adapter-specific and not part of the StorePlatform contract.
        """
        for p in store.list_products():
            assert p.vendor_product_id
            assert isinstance(p.vendor_product_id, str)

    def test_update_stock_persists(self, store: StorePlatform):
        store.update_stock(SKU("ABC-001"), StockLevel(42))
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-001")].stock == StockLevel(42)

    def test_update_stock_preserves_other_fields(self, store: StorePlatform):
        before = {p.sku: p for p in store.list_products()}[SKU("ABC-001")]
        store.update_stock(SKU("ABC-001"), StockLevel(42))
        after = {p.sku: p for p in store.list_products()}[SKU("ABC-001")]
        assert after.vendor_product_id == before.vendor_product_id
        assert after.published == before.published

    def test_unpublish_sets_published_false(self, store: StorePlatform):
        store.unpublish(SKU("ABC-001"))
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-001")].published is False

    def test_republish_sets_published_true(self, store: StorePlatform):
        store.republish(SKU("ABC-002"))
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-002")].published is True

    def test_unpublish_preserves_stock(self, store: StorePlatform):
        store.unpublish(SKU("ABC-001"))
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("ABC-001")].stock == StockLevel(5)

    # --- net-new product creation (Laura upload) ---

    def test_create_product_appears_in_list(self, store: StorePlatform):
        store.create_product(_draft("חדש", [VariantSpec(SKU("NEW-1"), price=Decimal("99"))]))
        assert SKU("NEW-1") in {p.sku for p in store.list_products()}

    def test_create_product_returns_store_product_id(self, store: StorePlatform):
        created = store.create_product(_draft("חדש", [VariantSpec(SKU("NEW-1"))]))
        assert created.store_product_id
        assert SKU("NEW-1") in created.variant_ids_by_sku

    def test_created_draft_is_unpublished(self, store: StorePlatform):
        store.create_product(_draft("חדש", [VariantSpec(SKU("NEW-1"))], status="draft"))
        by_sku = {p.sku: p for p in store.list_products()}
        assert by_sku[SKU("NEW-1")].published is False

    def test_create_multi_variant_lists_all_skus(self, store: StorePlatform):
        created = store.create_product(_draft("סט", [
            VariantSpec(SKU("NEW-1"), option_value="NB"),
            VariantSpec(SKU("NEW-2"), option_value="0-3"),
        ]))
        skus = {p.sku for p in store.list_products()}
        assert {SKU("NEW-1"), SKU("NEW-2")} <= skus
        assert set(created.variant_ids_by_sku) == {SKU("NEW-1"), SKU("NEW-2")}

    # --- collections ---

    def test_ensure_collection_creates_when_missing(self, store: StorePlatform):
        ref = store.ensure_collection("אופנה")
        assert ref.created is True
        assert ref.id

    def test_ensure_collection_idempotent(self, store: StorePlatform):
        first = store.ensure_collection("אופנה")
        again = store.ensure_collection("אופנה")
        assert again.created is False
        assert again.id == first.id

    def test_add_to_collection_succeeds(self, store: StorePlatform):
        created = store.create_product(_draft("חדש", [VariantSpec(SKU("NEW-1"))]))
        ref = store.ensure_collection("אופנה")
        store.add_to_collection(created.store_product_id, ref.id)  # must not raise


class TestInMemoryStore(StoreContract):
    @pytest.fixture
    def store(self) -> StorePlatform:
        return InMemoryStore(products=list(SEEDED_PRODUCTS))
