"""Shared missing-at-source reconciliation.

Flags store products a supplier has dropped from its catalog (flag-only — the
dashboard decides deletion; PRD-snir §1/§3). Two invariants that earlier bit us:

  1. Compare against the supplier's FULL catalog SKU set, NOT the onboarding
     subset. Segal only ingests 6 categories, but a live Segal product in a
     non-ingested category (e.g. 'rooms', sku 446) is NOT gone — flagging it
     against the ingest subset was a false positive.
  2. Unify per product (store_product_id): a multi-variant product is flagged
     once, not once per variant SKU.

Vendor-scoped so a Segal pass never flags a Laura/Bambino/manual product. Reused
by the unified pass (Segal/Snir) and — later — Bambino/Laura.
"""
from __future__ import annotations

from typing import Iterable


def reconcile_missing_at_source(product_store, store_products, catalog_skus: set[str],
                                owned_vendors: Iterable[str], customer_id: str, logger,
                                *, dry_run: bool = False) -> int:
    """Flag/clear missing-at-source for one supplier. Returns products flagged.

    A product (grouped by store_product_id) is "present" if ANY of its variant
    SKUs is in `catalog_skus`; otherwise the supplier dropped it -> flag. Present
    products are cleared (idempotent), so a returning product un-flags itself.
    """
    owned = set(owned_vendors)
    if not owned:
        return 0

    products: dict[str, dict] = {}
    for p in store_products:
        if p.vendor not in owned or not p.store_product_id:
            continue
        g = products.setdefault(p.store_product_id, {"present": False, "rep": p})
        if str(p.sku) in catalog_skus:
            g["present"] = True

    flagged = 0
    for spid, g in products.items():
        if g["present"]:
            if not dry_run:
                product_store.clear_missing_at_source(customer_id, spid)
            continue
        flagged += 1
        rep = g["rep"]
        logger.info("missing_at_source", store_product_id=spid, vendor=rep.vendor, sku=str(rep.sku))
        if not dry_run:
            product_store.flag_missing_at_source(
                customer_id, spid, sku=str(rep.sku), title=rep.title,
                vendor=rep.vendor, published=rep.published,
            )
    return flagged
