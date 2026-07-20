"""Bambino ingest: turn the master feed into net-new draft products (PRD §2–§9).

Wires the master-feed adapter (adapters.bambino) + mapping (bambino_mapping) +
the store create seams + store_products pending rows. Mirrors segal_ingest, plus
two Bambino-specific steps:

  - color grouping (§4): every color is its own product, then `custom.related_
    products` is backfilled with the sibling GIDs once the group is created;
  - skips: existing SKUs, OOS-at-source (§1 / cross-supplier rule — quantity 0),
    and uncategorized products (only Signature/feeding/hygiene types).

See tests/test_bambino_ingest.py.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date

from inventory_sync import review_reasons
from inventory_sync.bambino_mapping import collections_for, is_importable, to_product_draft
from inventory_sync.bambino_source import BambinoProduct
from inventory_sync.domain import Metafield, StockLevel
from inventory_sync.persistence.store_product_store import NewStoreProduct


@dataclass
class BambinoIngestSummary:
    created: int = 0
    skipped_existing: int = 0
    skipped_oos: int = 0
    skipped_uncategorized: int = 0
    linked: int = 0            # products that got related_products backfilled
    errors: int = 0
    would_create: int = 0
    dry_run: bool = False
    created_skus: list[str] = field(default_factory=list)


def _related_metafield(sibling_store_ids: list[str]) -> Metafield:
    gids = [f"gid://shopify/Product/{i}" for i in sibling_store_ids]
    return Metafield("custom", "related_products", "list.product_reference",
                     json.dumps(gids, ensure_ascii=False))


def _create_one(store, product_store, customer_id, product, draft, review_reason, logger) -> str:
    """Create one product, attach collections, set stock, record pending. Returns id.

    Product is created FIRST so a failed create leaves no orphan collection.
    `review_reason` is a review_reasons code (or None); needs_review = reason set.
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
            sku=product.catalog_number,
            store_product_id=created.store_product_id,
            title=draft.title,
            is_new_collection=is_new_collection,
            needs_review=review_reason is not None,
            needs_review_reason=review_reason,
        )
    ])
    logger.info("bambino_ingest_created", title=draft.title, sku=product.catalog_number,
                store_product_id=created.store_product_id, brand=product.brand,
                stock=draft.variants[0].inventory_quantity,
                is_new_collection=is_new_collection, needs_review_reason=review_reason)
    return created.store_product_id


def _create_with_salvage(store, product_store, customer_id, product, draft, review_reason,
                         logger, summary) -> str | None:
    """Create a product; on failure, retry once without images (the usual 422).

    Returns the store_product_id on success, or None on hard failure (counted).
    """
    try:
        return _create_one(store, product_store, customer_id, product, draft, review_reason, logger)
    except Exception as first_err:
        if draft.image_urls:
            try:
                spid = _create_one(store, product_store, customer_id, product,
                                   replace(draft, image_urls=()),
                                   review_reasons.IMAGE_REJECTED, logger)
                logger.warning("bambino_ingest_created_without_image",
                               sku=product.catalog_number, error=str(first_err)[:200])
                return spid
            except Exception as retry_err:
                first_err = retry_err
        logger.error("bambino_ingest_create_failed", sku=product.catalog_number,
                     brand=product.brand, error=str(first_err)[:200])
        summary.errors += 1
        return None


def _backfill_related(store, created: list[tuple[BambinoProduct, str]], logger, summary) -> None:
    """Link each created color to its siblings via custom.related_products (§4)."""
    ids = [spid for _, spid in created]
    for product, spid in created:
        siblings = [i for i in ids if i != spid]
        if not siblings:
            continue
        try:
            store.set_product_metafields(spid, [_related_metafield(siblings)])
            summary.linked += 1
            logger.info("bambino_ingest_related_linked", sku=product.catalog_number,
                        store_product_id=spid, siblings=len(siblings))
        except Exception as e:
            logger.error("bambino_ingest_related_failed", sku=product.catalog_number,
                         error=str(e)[:200])
            summary.errors += 1


def ingest_bambino(source, store, product_store, customer_id: str, logger,
                   dry_run: bool = False, today: date | None = None) -> BambinoIngestSummary:
    """Fetch the master feed, skip (existing/OOS/uncategorized), create by color group."""
    summary = BambinoIngestSummary(dry_run=dry_run)
    day = today or date.today()
    products = source.fetch_all_products()
    warranties = source.warranties()
    existing_skus = {str(p.sku) for p in store.list_products()}
    seen: set[str] = set()

    # Pass 1 — select onboard-eligible products, deduped, grouped by color model.
    groups: dict[int, list[BambinoProduct]] = defaultdict(list)
    for product in products:
        sku = product.catalog_number
        if not sku:
            logger.warning("bambino_ingest_no_sku", brand=product.brand)
            continue
        if sku in seen:
            continue
        seen.add(sku)

        if sku in existing_skus:
            summary.skipped_existing += 1
            logger.info("bambino_ingest_skip_existing", sku=sku)
            continue
        if not is_importable(product):
            # Only Signature/feeding/hygiene types → not onboarded (owner decision).
            summary.skipped_uncategorized += 1
            logger.info("bambino_ingest_skip_uncategorized", sku=sku, types=list(product.type_ids))
            continue
        # Cross-supplier rule: OOS at source is not onboarded as a net-new draft.
        if not product.in_stock:
            summary.skipped_oos += 1
            logger.info("bambino_ingest_skip_oos", sku=sku)
            continue
        groups[product.group_id].append(product)

    # Pass 2 — create each group, then backfill related_products among siblings.
    for members in groups.values():
        created: list[tuple[BambinoProduct, str]] = []
        for product in members:
            review_reason = review_reasons.join(
                review_reasons.NO_PRICE if product.price is None else None,
                review_reasons.NO_IMAGE if not product.image_urls else None,
            )
            if dry_run:
                summary.would_create += 1
                logger.info("bambino_ingest_would_create", sku=product.catalog_number,
                            brand=product.brand, needs_review_reason=review_reason)
                continue
            draft = to_product_draft(product, warranties, today=day, logger=logger)
            spid = _create_with_salvage(store, product_store, customer_id, product, draft,
                                        review_reason, logger, summary)
            if spid is not None:
                summary.created += 1
                summary.created_skus.append(product.catalog_number)
                created.append((product, spid))
        if not dry_run and len(created) > 1:
            _backfill_related(store, created, logger, summary)

    logger.info("bambino_ingest_summary", customer_id=customer_id, created=summary.created,
                skipped_existing=summary.skipped_existing, skipped_oos=summary.skipped_oos,
                skipped_uncategorized=summary.skipped_uncategorized, linked=summary.linked,
                errors=summary.errors, would_create=summary.would_create, dry_run=dry_run)
    return summary
