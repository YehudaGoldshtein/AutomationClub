"""Snir binding for the unified pass (stock sync + onboard new, one run).

Snir's cheap list is the single Store-API product listing; the expensive
enrichment is the per-product page tab scrape, which the unified pass calls ONLY
for new SKUs. All fetching runs through the WAF-solving PlaywrightClient inside
the adapter — invisible here; we just reuse the adapter + mapping.

Unifying catalog + stock sync (owner decision): one pass lists the catalog once
and, per product, either stock-syncs it (already in the store) or onboards it
(new, in-scope, in-stock). `supplier_pass.unified_pass` enforces the
cross-supplier OOS gate — a new product that is out of stock at source is NOT
onboarded (created later once back in stock) — and skip-existing. This binding
adds Snir's review flags: no-collection / no-image / no-price, and MULTI_VARIANT
for the shared-SKU variable products (onboarded single-variant on the parent SKU
per Decision B; flagged so the owner reconciles).

See supplier_pass.unified_pass and tests/test_snir_pass.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from inventory_sync import review_reasons
from inventory_sync.adapters.snir_baby import SnirStoreApiAdapter, _to_snapshot
from inventory_sync.domain import ProductDraft, VendorProductSnapshot
from inventory_sync.log import Logger, get
from inventory_sync.snir_mapping import (
    collections_for,
    is_importable,
    shares_variant_sku,
    to_product_draft,
)
from inventory_sync.snir_source import SnirProduct, parse_api_product


@dataclass
class SnirUnifiedSource:
    """Adapts SnirStoreApiAdapter + snir_mapping to the UnifiedSource protocol."""

    adapter: SnirStoreApiAdapter
    logger: Logger = field(default_factory=lambda: get("snir.pass"))

    def list_catalog(self) -> list[SnirProduct]:
        """Cheap: list all products (no tab scrape), first-SKU-wins dedup.

        Dedup here is the scan-level "SKU already taken -> skip" rule; the first
        product to claim a SKU wins, later duplicates are dropped.
        """
        out: list[SnirProduct] = []
        seen: set[str] = set()
        for data in self.adapter.list_products():
            p = parse_api_product(data)
            if p.sku and p.sku not in seen:
                seen.add(p.sku)
                out.append(p)
        return out

    def sku(self, item: SnirProduct) -> str:
        return item.sku

    def in_stock(self, item: SnirProduct) -> bool:
        return item.in_stock

    def is_importable(self, item: SnirProduct) -> bool:
        return is_importable(item)

    def snapshot(self, item: SnirProduct) -> VendorProductSnapshot:
        return _to_snapshot(item)  # binary availability (Snir exposes no count)

    def enrich_to_draft(self, item: SnirProduct) -> ProductDraft:
        """Expensive: scrape the product page tabs, then map (new items only)."""
        tabs = self.adapter.fetch_tabs(item.permalink)
        return to_product_draft(replace(item, tabs=tabs), self.logger)

    def collections_for(self, item: SnirProduct) -> tuple[str, ...]:
        return collections_for(item)

    def needs_review_reason(self, item: SnirProduct, draft: ProductDraft) -> str | None:
        return review_reasons.join(
            review_reasons.NO_COLLECTION if not collections_for(item) else None,
            review_reasons.NO_IMAGE if not draft.image_urls else None,
            review_reasons.NO_PRICE if item.price is None else None,
            review_reasons.MULTI_VARIANT if shares_variant_sku(item) else None,
        )

    def link_new(self, created, store, logger) -> int:
        return 0  # Snir has no color-sibling linking (unlike Bambino)
