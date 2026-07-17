"""Segal Baby supplier adapter — WooCommerce Store API (public, read-only).

Hybrid source (PRD §0): the Store API gives structured product fields; the
product page HTML gives the tab content (metafields). One list call per category
page + one HTML GET per product for its tabs.

  GET /wp-json/wc/store/v1/products?category=<id>&per_page=&page=
  GET <permalink>  -> #more-info tabs

See tests/test_segal_adapter.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import httpx

from inventory_sync.domain import VendorProductId, VendorProductSnapshot
from inventory_sync.log import Logger, get
from inventory_sync.segal_mapping import INGEST_CATEGORIES
from inventory_sync.segal_source import (
    SegalProduct,
    SegalTab,
    parse_api_product,
    parse_tabs,
)

_STORE_API = "/wp-json/wc/store/v1/products"


def _to_snapshot(p: SegalProduct) -> VendorProductSnapshot:
    """SegalProduct -> VendorProductSnapshot (stock signal only; no tabs needed).

    Keeps the domain invariant consistent: out of stock -> count 0 / unavailable;
    in stock with a positive count -> exact count; in stock with an unknown/zero
    count -> binary-available (count None).
    """
    if not p.in_stock:
        stock_count = 0
    elif p.stock_qty and p.stock_qty > 0:
        stock_count = p.stock_qty
    else:
        stock_count = None
    return VendorProductSnapshot(
        vendor_product_id=VendorProductId(p.sku),
        is_available=p.in_stock,
        stock_count=stock_count,
        name=p.name or None,
        price=p.price,
        image_url=p.image_urls[0] if p.image_urls else None,
    )


@dataclass
class SegalBabyStoreApiAdapter:
    client: httpx.Client
    logger: Logger = field(default_factory=lambda: get("adapters.segal_baby"))
    base_url: str = "https://www.segalbaby.co.il"
    per_page: int = 100
    category_ids: tuple[int, ...] = field(
        default_factory=lambda: tuple(INGEST_CATEGORIES.values())
    )

    def list_category_products(self, category_id: int) -> list[dict]:
        """Paginate the Store API for one category; return raw product dicts.

        Stops on the first partial/empty page. A failed page returns what was
        collected so far (never aborts the whole ingest for one bad page).
        """
        out: list[dict] = []
        page = 1
        while True:
            try:
                resp = self.client.get(
                    f"{self.base_url}{_STORE_API}",
                    params={"category": category_id, "per_page": self.per_page, "page": page},
                )
            except Exception:
                self.logger.exception("category_fetch_failed", category_id=category_id, page=page)
                break
            if resp.status_code != 200:
                self.logger.warning("category_bad_status", category_id=category_id,
                                    page=page, status=resp.status_code)
                break
            batch = resp.json()
            if not batch:
                break
            out.extend(batch)
            if len(batch) < self.per_page:
                break
            page += 1
        self.logger.info("category_listed", category_id=category_id, count=len(out))
        return out

    def fetch_tabs(self, permalink: str) -> tuple[SegalTab, ...]:
        """GET the product page and parse its #more-info tabs. Empty on any failure."""
        if not permalink:
            return ()
        try:
            resp = self.client.get(permalink)
        except Exception:
            self.logger.exception("tab_fetch_failed", permalink=permalink)
            return ()
        if resp.status_code != 200:
            self.logger.warning("tab_bad_status", permalink=permalink, status=resp.status_code)
            return ()
        return parse_tabs(resp.text)

    def fetch_products(self, category_id: int) -> list[SegalProduct]:
        """List a category, then fetch + attach each product's tabs (1 GET/product)."""
        raw = self.list_category_products(category_id)
        products: list[SegalProduct] = []
        for data in raw:
            tabs = self.fetch_tabs(data.get("permalink") or "")
            products.append(parse_api_product(data, tabs))
        return products

    def fetch_all(self, category_ids: Iterable[int]) -> Iterable[SegalProduct]:
        """Yield products across categories (dedup is the caller's job)."""
        for cid in category_ids:
            yield from self.fetch_products(cid)

    # --- SupplierSource (stock sync for existing products) ---

    def fetch_snapshots(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, VendorProductSnapshot]:
        """Availability + exact quantity for the requested SKUs, from the Store API.

        Lists the in-scope categories (no per-product tab fetch — stock only) and
        returns snapshots just for the requested ids. A requested SKU not found in
        Segal's catalog is omitted (the engine treats it as vendor-missing).
        """
        wanted = {str(i) for i in ids}
        out: dict[VendorProductId, VendorProductSnapshot] = {}
        for cid in self.category_ids:
            for data in self.list_category_products(cid):
                p = parse_api_product(data)
                if p.sku in wanted and VendorProductId(p.sku) not in out:
                    out[VendorProductId(p.sku)] = _to_snapshot(p)
        self.logger.info("segal_snapshots_fetched", requested=len(wanted), returned=len(out))
        return out
