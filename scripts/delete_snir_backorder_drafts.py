"""One-off: delete the 8 backorder drafts wrongly created by the snir-pass wet run.

Snir marks restock-pending items is_in_stock=true + is_on_backorder=true; the
pass created them before the backorder fix (0932a85). This removes exactly the
intersection of (created by run 30116494055) AND (is_on_backorder now) — the
Shopify draft AND its store_products rows. Safety: only deletes rows still in
`draft` status (skips anything that was approved/activated meanwhile).
"""
from __future__ import annotations

from inventory_sync.config import load as load_config
from inventory_sync.log import get
from inventory_sync.__main__ import _build_shopify_adapter, _build_store_product_store

CUSTOMER = "maxbaby"

# (store_product_id, sku) — backorder ∩ created-by-wet-run (verified 2026-07-24)
TARGETS = [
    ("9385826418942", "mit-0001-1"),
    ("9385827860734", "ARISA-NAD-1-2"),
    ("9385832841470", "4565434567654"),
    ("9385833005310", "444114425"),
    ("9385833070846", "25857525"),
    ("9385833988350", "5654344"),
    ("9385834086654", "346776"),
    ("9385834152190", "R13"),
]


def main() -> None:
    log = get("cleanup.snir_backorder")
    cfg = load_config()
    store = _build_shopify_adapter(cfg, log, vendor_filter=None)
    product_store = _build_store_product_store(cfg, log)

    deleted = 0
    skipped = 0
    for spid, sku in TARGETS:
        rec = product_store.get(CUSTOMER, sku)
        if rec is None:
            log.warning("cleanup_row_missing", sku=sku, store_product_id=spid)
            skipped += 1
            continue
        if rec.status != "draft":
            log.warning("cleanup_skip_not_draft", sku=sku, store_product_id=spid, status=rec.status)
            skipped += 1
            continue
        store.delete_product(spid)              # remove the Shopify draft
        product_store.delete_products(CUSTOMER, spid)  # remove its store_products rows
        log.info("cleanup_deleted", sku=sku, store_product_id=spid)
        deleted += 1

    log.info("cleanup_summary", deleted=deleted, skipped=skipped, targets=len(TARGETS))
    print(f"DELETED={deleted} SKIPPED={skipped} TARGETS={len(TARGETS)}")


if __name__ == "__main__":
    main()
