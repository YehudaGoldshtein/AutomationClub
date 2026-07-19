"""Segal ingest core: pull the in-scope categories and create net-new drafts.

Wires the Store API source (adapters.segal_baby) + mapping (segal_mapping) + the
store create seams + store_products pending rows. See tests/test_segal_ingest.py.

Skip-dominant like Laura: most SKUs already exist; only net-new ones are created.
Segal products are simple (1 variant) and dedup is by SKU across categories.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from inventory_sync.domain import SKU, StockLevel
from inventory_sync.segal_mapping import (
    INGEST_CATEGORIES,
    collections_for,
    to_product_draft,
)
from inventory_sync.persistence.store_product_store import NewStoreProduct


@dataclass
class IngestSummary:
    created: int = 0
    skipped_existing: int = 0
    skipped_oos: int = 0
    errors: int = 0
    would_create: int = 0
    dry_run: bool = False
    created_skus: list[str] = field(default_factory=list)


def _create_and_record(store, product_store, customer_id, product, draft, needs_review, logger) -> None:
    """Create one product, attach its mapped collections, set stock, record pending.

    Product is created FIRST so a failed create leaves no orphan collection.
    """
    created = store.create_product(draft)
    is_new_collection = False
    for name in collections_for(product):
        ref = store.ensure_collection(name)
        store.add_to_collection(created.store_product_id, ref.id)
        is_new_collection = is_new_collection or ref.created

    for v in draft.variants:
        if v.inventory_quantity is not None:
            store.update_stock(v.sku, StockLevel(v.inventory_quantity))

    product_store.write_pending(customer_id, [
        NewStoreProduct(
            sku=product.sku,
            store_product_id=created.store_product_id,
            title=draft.title,
            is_new_collection=is_new_collection,
            needs_review=needs_review,
        )
    ])
    logger.info("segal_ingest_created", title=draft.title, sku=product.sku,
                store_product_id=created.store_product_id,
                stock=draft.variants[0].inventory_quantity,
                is_new_collection=is_new_collection, needs_review=needs_review)


def ingest_segal(source, store, product_store, customer_id: str, logger,
                 dry_run: bool = False, category_ids: dict[str, int] | None = None) -> IngestSummary:
    """Fetch in-scope categories, dedup by SKU, skip existing, create new drafts."""
    summary = IngestSummary(dry_run=dry_run)
    categories = category_ids if category_ids is not None else INGEST_CATEGORIES
    existing_skus = {str(p.sku) for p in store.list_products()}
    seen: set[str] = set()

    for slug, cat_id in categories.items():
        for product in source.fetch_products(cat_id):
            sku = product.sku
            if not sku:
                logger.warning("segal_ingest_no_sku", category=slug)
                continue
            if sku in seen:
                continue  # same product listed in multiple categories
            seen.add(sku)

            if sku in existing_skus:
                summary.skipped_existing += 1
                logger.info("segal_ingest_skip_existing", sku=sku, category=slug)
                continue

            # Cross-supplier rule: a product OOS at source is not onboarded as a
            # net-new draft — it will be created on a later run once back in stock.
            if not product.in_stock:
                summary.skipped_oos += 1
                logger.info("segal_ingest_skip_oos", sku=sku, category=slug)
                continue

            needs_review = not collections_for(product) or not product.image_urls

            if dry_run:
                summary.would_create += 1
                logger.info("segal_ingest_would_create", sku=sku, category=slug,
                            needs_review=needs_review)
                continue

            draft = to_product_draft(product, logger)
            try:
                _create_and_record(store, product_store, customer_id, product, draft,
                                   needs_review, logger)
            except Exception as first_err:
                # A bad image URL is the common Shopify 422 — salvage without images.
                if draft.image_urls:
                    try:
                        _create_and_record(store, product_store, customer_id, product,
                                           replace(draft, image_urls=()), True, logger)
                        summary.created += 1
                        summary.created_skus.append(sku)
                        logger.warning("segal_ingest_created_without_image", sku=sku,
                                       error=str(first_err)[:200])
                        continue
                    except Exception as retry_err:
                        first_err = retry_err
                logger.error("segal_ingest_create_failed", sku=sku, category=slug,
                             error=str(first_err)[:200])
                summary.errors += 1
                continue
            summary.created += 1
            summary.created_skus.append(sku)

    logger.info("segal_ingest_summary", customer_id=customer_id, created=summary.created,
                skipped_existing=summary.skipped_existing, skipped_oos=summary.skipped_oos,
                errors=summary.errors, would_create=summary.would_create, dry_run=dry_run)
    return summary
