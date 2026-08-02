"""reconcile_prices — apply supplier prices to the store, writing only what changed.

Folded into the sync passes (not a separate job): the pass already fetched the
catalog and matched store products, so this just decides per product and writes
the handful that changed. Write-avoidance keeps us off Shopify's 2 req/s limit;
the >60% guard blocks a bad match from writing a wildly wrong price.

Pure of transport: `store` only needs `update_variant_price(sku, price, compare_at)`;
`targets` is {sku: TargetPrice} the caller built from the supplier data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from inventory_sync.pricing import PriceAction, TargetPrice, decide_price, reconcile_tags


@dataclass
class PriceSyncSummary:
    checked: int = 0                 # matched products with a target price
    unchanged: int = 0               # NOOP — already correct (the common case)
    updated: int = 0                 # WRITE applied
    would_update: int = 0            # WRITE planned (dry-run)
    blocked: int = 0                 # change exceeded the >60% guard — not written
    skipped_unmatched: int = 0       # store product with no supplier target
    tags_updated: int = 0            # products whose sale tags changed
    tags_would_update: int = 0
    dry_run: bool = False
    blocked_skus: list[str] = field(default_factory=list)


def reconcile_prices(store, store_products, targets: dict[str, TargetPrice], logger,
                     *, dry_run: bool = False) -> PriceSyncSummary:
    """Set each matched product's price to its supplier target — only when changed."""
    summary = PriceSyncSummary(dry_run=dry_run)
    for p in store_products:
        target = targets.get(str(p.sku))
        if target is None:
            summary.skipped_unmatched += 1
            continue
        summary.checked += 1
        action = decide_price(p.price, p.compare_at_price, target)
        if action is PriceAction.NOOP:
            summary.unchanged += 1
        elif action is PriceAction.BLOCKED:
            summary.blocked += 1
            summary.blocked_skus.append(str(p.sku))
            logger.warning("price_change_blocked", sku=str(p.sku),
                           current=str(p.price), target=str(target.price))
        else:  # WRITE
            if dry_run:
                summary.would_update += 1
            else:
                store.update_variant_price(p.sku, target.price, target.compare_at)
                summary.updated += 1
            # Full before→after audit (ships to Axiom in prod). `pct` makes radical
            # moves a one-line filter: price_update where abs(pct) > N.
            pct = (int(round(float((target.price - p.price) / p.price * 100)))
                   if p.price not in (None, 0) else None)
            logger.info("price_update", sku=str(p.sku),
                        was=str(p.price) if p.price is not None else None,
                        price=str(target.price), pct=pct,
                        was_compare_at=str(p.compare_at_price) if p.compare_at_price is not None else None,
                        compare_at=str(target.compare_at) if target.compare_at is not None else None,
                        dry_run=dry_run)
    # --- product-level sale tags (supplier-sale / sale-<pct>) drive the collection ---
    # Grouped by product: a product is on sale if any of its variants is; tags are
    # added when it goes on sale and removed when it ends (other tags preserved).
    by_product: dict[str, dict] = {}
    for p in store_products:
        if targets.get(str(p.sku)) is None or not p.store_product_id:
            continue
        g = by_product.setdefault(p.store_product_id, {"tags": set(p.tags), "rep": targets[str(p.sku)]})
        if targets[str(p.sku)].compare_at is not None:
            g["rep"] = targets[str(p.sku)]   # prefer an on-sale variant as representative
    for spid, g in by_product.items():
        desired = reconcile_tags(g["tags"], g["rep"])
        if desired == g["tags"]:
            continue
        if dry_run:
            summary.tags_would_update += 1
        else:
            store.set_product_tags(spid, desired)
            summary.tags_updated += 1
        logger.info("product_tags_reconciled", store_product_id=spid,
                    tags=sorted(desired), dry_run=dry_run)

    logger.info("price_sync_summary", checked=summary.checked, unchanged=summary.unchanged,
                updated=summary.updated, would_update=summary.would_update,
                blocked=summary.blocked, skipped_unmatched=summary.skipped_unmatched,
                dry_run=dry_run)
    return summary
