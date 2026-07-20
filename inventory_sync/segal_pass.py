"""Segal binding for the unified pass (stock sync + onboard new, one run).

Segal's cheap list is the category listings; the expensive enrichment is the
per-product page tab scrape, which the unified pass calls ONLY for new SKUs.
Reuses the existing adapter + mapping — no new fetch/parse logic.

See supplier_pass.unified_pass and tests/test_segal_pass.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from inventory_sync.adapters.segal_baby import SegalBabyStoreApiAdapter, _to_snapshot
from inventory_sync.domain import ProductDraft, VendorProductSnapshot
from inventory_sync.log import Logger, get
from inventory_sync.segal_mapping import (
    INGEST_CATEGORIES,
    collections_for,
    matched_category,
    to_product_draft,
)
from inventory_sync.segal_source import SegalProduct, parse_api_product


@dataclass
class SegalUnifiedSource:
    """Adapts SegalBabyStoreApiAdapter + segal_mapping to the UnifiedSource protocol."""

    adapter: SegalBabyStoreApiAdapter
    logger: Logger = field(default_factory=lambda: get("segal.pass"))
    category_ids: dict[str, int] = field(default_factory=lambda: dict(INGEST_CATEGORIES))

    def list_catalog(self) -> list[SegalProduct]:
        """Cheap: list the in-scope categories (no tab scrape), deduped by SKU."""
        out: list[SegalProduct] = []
        seen: set[str] = set()
        for cat_id in self.category_ids.values():
            for data in self.adapter.list_category_products(cat_id):
                p = parse_api_product(data)
                if p.sku and p.sku not in seen:
                    seen.add(p.sku)
                    out.append(p)
        return out

    def sku(self, item: SegalProduct) -> str:
        return item.sku

    def in_stock(self, item: SegalProduct) -> bool:
        return item.in_stock

    def is_importable(self, item: SegalProduct) -> bool:
        return matched_category(item) is not None

    def snapshot(self, item: SegalProduct) -> VendorProductSnapshot:
        return _to_snapshot(item)

    def enrich_to_draft(self, item: SegalProduct) -> ProductDraft:
        """Expensive: scrape the product page tabs, then map (new items only)."""
        tabs = self.adapter.fetch_tabs(item.permalink)
        return to_product_draft(replace(item, tabs=tabs), self.logger)

    def collections_for(self, item: SegalProduct) -> tuple[str, ...]:
        return collections_for(item)

    def needs_review(self, item: SegalProduct, draft: ProductDraft) -> bool:
        return not draft.image_urls or not collections_for(item)

    def link_new(self, created, store, logger) -> int:
        return 0  # Segal has no color-sibling linking (unlike Bambino)
