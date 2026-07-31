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

from inventory_sync.pricing import PriceAction, TargetPrice, decide_price


@dataclass
class PriceSyncSummary:
    checked: int = 0                 # matched products with a target price
    unchanged: int = 0               # NOOP — already correct (the common case)
    updated: int = 0                 # WRITE applied
    would_update: int = 0            # WRITE planned (dry-run)
    blocked: int = 0                 # change exceeded the >60% guard — not written
    skipped_unmatched: int = 0       # store product with no supplier target
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
            logger.info("price_update", sku=str(p.sku), price=str(target.price),
                        compare_at=str(target.compare_at) if target.compare_at is not None else None,
                        dry_run=dry_run)
    logger.info("price_sync_summary", checked=summary.checked, unchanged=summary.unchanged,
                updated=summary.updated, would_update=summary.would_update,
                blocked=summary.blocked, skipped_unmatched=summary.skipped_unmatched,
                dry_run=dry_run)
    return summary
