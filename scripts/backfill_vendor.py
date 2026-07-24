"""One-off: backfill store_products.vendor from the live Shopify vendor field.

Existing rows (Bambino/Segal/Snir, and anything predating the vendor column)
have vendor=NULL. This reads the authoritative vendor off each live Shopify
product and fills it in — only where currently NULL (never clobbers a set
value). Enables dashboard grouping + full coverage for MISSING_AT_SOURCE
flagging. Idempotent; safe to re-run. Logs everything.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import update
from sqlalchemy.orm import Session

from inventory_sync.config import load as load_config
from inventory_sync.log import get
from inventory_sync.persistence.schema import store_products
from inventory_sync.__main__ import _build_shopify_adapter, _build_store_product_store

CUSTOMER = "maxbaby"


def main() -> None:
    log = get("backfill.vendor")
    cfg = load_config()
    store = _build_shopify_adapter(cfg, log, vendor_filter=None)   # all vendors
    ps = _build_store_product_store(cfg, log)

    products = store.list_products()
    sku_vendor = {str(p.sku): p.vendor for p in products if p.vendor}
    log.info("backfill_loaded", store_products=len(products), skus_with_vendor=len(sku_vendor))

    updated = 0
    by_vendor: Counter = Counter()
    with Session(ps.engine) as session:
        with session.begin():
            for sku, vendor in sku_vendor.items():
                res = session.execute(
                    update(store_products)
                    .where(
                        store_products.c.customer_id == CUSTOMER,
                        store_products.c.sku == sku,
                        store_products.c.vendor.is_(None),   # only fill NULLs
                    )
                    .values(vendor=vendor)
                )
                n = res.rowcount or 0
                if n:
                    updated += n
                    by_vendor[vendor] += n

    log.info("backfill_done", rows_updated=updated, vendors=dict(by_vendor))
    print(f"VENDOR_BACKFILL rows_updated={updated}")
    for v, n in sorted(by_vendor.items(), key=lambda kv: -kv[1]):
        print(f"  {n:4d}  {v}")


if __name__ == "__main__":
    main()
