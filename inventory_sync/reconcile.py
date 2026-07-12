"""Activation reconcile: flip approved draft products live.

After the dashboard sets approved=true on a pending product, the tokened sync job
runs this to make it live in the store. Vendor-agnostic — it activates any
approved draft in store_products. See tests/test_reconcile.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from inventory_sync.domain import SKU


@dataclass
class ReconcileSummary:
    activated: int = 0
    errors: int = 0
    activated_product_ids: list[str] = field(default_factory=list)


@dataclass
class RejectSummary:
    deleted: int = 0
    errors: int = 0
    deleted_product_ids: list[str] = field(default_factory=list)


def reconcile_approved_drafts(store, product_store, customer_id: str, logger) -> ReconcileSummary:
    """Republish every approved draft (status=active) and mark it active in the DB."""
    approved = product_store.list_approved_drafts(customer_id)
    if not approved:
        logger.info("reconcile_none", customer_id=customer_id)
        return ReconcileSummary()

    # Prime the store's variant cache so republish() can resolve refs (no-op-cheap
    # for the fake; one list call for the real adapter when run standalone).
    store.list_products()

    # One representative SKU per product — republish acts on the whole product.
    sku_by_product: dict[str, str] = {}
    for row in approved:
        sku_by_product.setdefault(row.store_product_id, row.sku)

    summary = ReconcileSummary()
    for product_id, sku in sku_by_product.items():
        try:
            store.republish(SKU(sku))
            product_store.mark_active(customer_id, product_id)
        except Exception:
            logger.exception(
                "reconcile_activate_failed",
                customer_id=customer_id, store_product_id=product_id, sku=sku,
            )
            summary.errors += 1
            continue
        summary.activated += 1
        summary.activated_product_ids.append(product_id)
        logger.info("reconcile_activated", customer_id=customer_id, store_product_id=product_id)

    logger.info("reconcile_summary", customer_id=customer_id,
                activated=summary.activated, errors=summary.errors)
    return summary


def reconcile_rejected_drafts(store, product_store, customer_id: str, logger) -> RejectSummary:
    """Delete every rejected ('ignored') product from the store + drop its rows."""
    rejected = product_store.list_rejected(customer_id)
    if not rejected:
        logger.info("reconcile_reject_none", customer_id=customer_id)
        return RejectSummary()

    product_ids: list[str] = []
    seen: set[str] = set()
    for row in rejected:
        if row.store_product_id not in seen:
            seen.add(row.store_product_id)
            product_ids.append(row.store_product_id)

    summary = RejectSummary()
    for product_id in product_ids:
        try:
            store.delete_product(product_id)
            product_store.delete_products(customer_id, product_id)
        except Exception:
            logger.exception("reconcile_reject_failed", customer_id=customer_id, store_product_id=product_id)
            summary.errors += 1
            continue
        summary.deleted += 1
        summary.deleted_product_ids.append(product_id)
        logger.info("reconcile_rejected_deleted", customer_id=customer_id, store_product_id=product_id)

    logger.info("reconcile_reject_summary", customer_id=customer_id,
                deleted=summary.deleted, errors=summary.errors)
    return summary
