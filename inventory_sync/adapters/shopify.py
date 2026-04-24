"""Shopify Admin API adapter. StorePlatform implementation for v0.1.

Variant-level sync: each Shopify variant becomes one Product in our domain,
with `sku == vendor_product_id` (direct mapping used by Max Baby / Laura).

The adapter expects an httpx.Client pre-configured with the full API base URL
(e.g., https://<shop>.myshopify.com/admin/api/2024-10) and the
X-Shopify-Access-Token header. The caller is responsible for auth / version.

State: on list_products() we cache per-SKU variant refs (inventory_item_id,
product_id) so update_stock / unpublish / republish don't need to re-scan
the catalog. First mutation also lazily resolves and caches the primary
location_id (via /locations.json).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

import httpx

from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.log import Logger, get


class ShopifyError(Exception):
    pass


@dataclass(frozen=True)
class _VariantRef:
    inventory_item_id: int
    product_id: int
    variant_id: int


@dataclass
class ShopifyAdapter:
    client: httpx.Client
    logger: Logger = field(default_factory=lambda: get("adapters.shopify"))
    vendor_filter: str | None = None
    page_size: int = 250

    _variant_by_sku: dict[SKU, _VariantRef] = field(default_factory=dict, init=False, repr=False)
    _location_id: int | None = field(default=None, init=False, repr=False)

    # --- StorePlatform ---

    def list_products(self) -> list[Product]:
        self._variant_by_sku.clear()
        out: list[Product] = []

        for sp in self._paginated_products():
            product_id = sp["id"]
            handle = sp.get("handle") or None
            title = sp.get("title") or None
            published = sp.get("status", "active") == "active"
            for variant in sp.get("variants", []):
                sku_raw = variant.get("sku")
                if not sku_raw:
                    continue
                sku = SKU(sku_raw)
                self._variant_by_sku[sku] = _VariantRef(
                    inventory_item_id=variant["inventory_item_id"],
                    product_id=product_id,
                    variant_id=variant["id"],
                )
                qty = variant.get("inventory_quantity") or 0
                out.append(
                    Product(
                        sku=sku,
                        vendor_product_id=VendorProductId(sku_raw),
                        stock=StockLevel(max(0, qty)),
                        published=published,
                        handle=handle,
                        title=title,
                        store_product_id=str(product_id),
                    )
                )

        self.logger.info(
            "list_products_complete",
            count=len(out),
            cached_variants=len(self._variant_by_sku),
        )
        return out

    def update_stock(self, sku: SKU, stock: StockLevel) -> None:
        ref = self._require_ref(sku)
        location_id = self._ensure_location()
        log = self.logger.bind(sku=sku, inventory_item_id=ref.inventory_item_id)

        resp = self.client.post(
            "/inventory_levels/set.json",
            json={
                "location_id": location_id,
                "inventory_item_id": ref.inventory_item_id,
                "available": stock.value,
            },
        )
        if resp.status_code not in (200, 201):
            log.error("inventory_set_failed", status=resp.status_code, body=resp.text[:200])
            raise ShopifyError(
                f"inventory_levels/set.json {resp.status_code}: {resp.text[:200]}"
            )
        log.info("stock_updated", available=stock.value)

    def unpublish(self, sku: SKU) -> None:
        self._set_product_status(sku, "archived")

    def republish(self, sku: SKU) -> None:
        self._set_product_status(sku, "active")

    # --- private ---

    def _set_product_status(self, sku: SKU, status: str) -> None:
        ref = self._require_ref(sku)
        log = self.logger.bind(sku=sku, product_id=ref.product_id, status=status)
        resp = self.client.put(
            f"/products/{ref.product_id}.json",
            json={"product": {"id": ref.product_id, "status": status}},
        )
        if resp.status_code not in (200, 201):
            log.error("product_status_update_failed", status=resp.status_code, body=resp.text[:200])
            raise ShopifyError(
                f"products/{ref.product_id}.json {resp.status_code}: {resp.text[:200]}"
            )
        log.info("product_status_updated")

    def _require_ref(self, sku: SKU) -> _VariantRef:
        ref = self._variant_by_sku.get(sku)
        if ref is None:
            raise ShopifyError(
                f"no variant cached for sku={sku!r}; call list_products() first"
            )
        return ref

    def _ensure_location(self) -> int:
        if self._location_id is not None:
            return self._location_id
        resp = self.client.get("/locations.json")
        if resp.status_code != 200:
            raise ShopifyError(f"locations.json {resp.status_code}: {resp.text[:200]}")
        locations = resp.json().get("locations", [])
        if not locations:
            raise ShopifyError("no locations returned by Shopify")
        self._location_id = locations[0]["id"]
        self.logger.info("location_resolved", location_id=self._location_id)
        return self._location_id

    def _paginated_products(self) -> Iterator[dict]:
        params: dict = {"limit": self.page_size}
        if self.vendor_filter:
            params["vendor"] = self.vendor_filter

        while True:
            resp = self.client.get("/products.json", params=params)
            if resp.status_code != 200:
                raise ShopifyError(f"products.json {resp.status_code}: {resp.text[:200]}")
            body = resp.json()
            for p in body.get("products", []):
                yield p

            next_info = _next_page_info(resp.headers.get("link", ""))
            if not next_info:
                break
            params = {"limit": self.page_size, "page_info": next_info}


_LINK_NEXT_RE = re.compile(r'<[^>]*[?&]page_info=([^>&]+)[^>]*>;\s*rel="next"')


def _next_page_info(link_header: str) -> str | None:
    if not link_header:
        return None
    m = _LINK_NEXT_RE.search(link_header)
    return m.group(1) if m else None
