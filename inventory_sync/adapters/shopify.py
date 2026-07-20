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
import time
from dataclasses import dataclass, field
from typing import Iterator
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from inventory_sync.domain import (
    SKU,
    CollectionRef,
    CreatedProduct,
    Product,
    ProductDraft,
    StockLevel,
    VendorProductId,
)
from inventory_sync.log import Logger, get


class ShopifyError(Exception):
    pass


def _safe_image_url(url: str) -> str:
    """Percent-encode a media URL's path so Shopify's server-side image fetch works.

    Shopify rejects image URLs with raw non-ASCII bytes in the path ("Image URL is
    invalid") — unlike browsers/httpx, its fetcher doesn't encode them. Suppliers
    (Bambino/Segal, Hebrew filenames) serve images at such paths. Encoding the path
    yields a URL the CDN serves identically. Idempotent: `%` and `/` stay safe, so
    an already-encoded URL is unchanged and a clean ASCII URL passes through.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%"),
                       parts.query, parts.fragment))


def _retry_after_seconds(resp) -> float:
    """Seconds to wait on a 429, from the Retry-After header. Defaults to 1s, capped at 5s."""
    try:
        return min(5.0, max(0.0, float(resp.headers.get("Retry-After", "1"))))
    except (TypeError, ValueError):
        return 1.0


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
    max_rate_limit_retries: int = 5

    _variant_by_sku: dict[SKU, _VariantRef] = field(default_factory=dict, init=False, repr=False)
    _location_id: int | None = field(default=None, init=False, repr=False)
    _collection_id_by_title: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    # --- StorePlatform ---

    def list_products(self) -> list[Product]:
        self._variant_by_sku.clear()
        out: list[Product] = []

        for sp in self._paginated_products():
            product_id = sp["id"]
            handle = sp.get("handle") or None
            title = sp.get("title") or None
            vendor = sp.get("vendor") or None
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
                        vendor=vendor,
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

        resp = self._set_level(ref.inventory_item_id, location_id, stock.value, log)
        if resp.status_code in (200, 201):
            log.info("stock_updated", available=stock.value)
            return

        # Self-heal: the variant has inventory tracking disabled, so set 422s.
        # Enable tracking on the inventory item, then retry once.
        if resp.status_code == 422 and "tracking" in resp.text.lower():
            log.warning("inventory_tracking_disabled_enabling", body=resp.text[:200])
            self._enable_tracking(ref.inventory_item_id)
            resp = self._set_level(ref.inventory_item_id, location_id, stock.value, log)
            if resp.status_code in (200, 201):
                log.info("stock_updated", available=stock.value, tracking_enabled=True)
                return

        log.error("inventory_set_failed", status=resp.status_code, body=resp.text[:200])
        raise ShopifyError(
            f"inventory_levels/set.json {resp.status_code}: {resp.text[:200]}"
        )

    def _set_level(self, inventory_item_id: int, location_id: int, available: int, log=None):
        """POST inventory_levels/set, retrying on 429 (honoring Retry-After).

        Bulk syncs (e.g. Segal's first run) can exceed Shopify's ~2 req/s; a
        reactive backoff paces us instead of failing the whole run on a 429.
        """
        body = {"location_id": location_id, "inventory_item_id": inventory_item_id, "available": available}
        resp = self.client.post("/inventory_levels/set.json", json=body)
        attempt = 0
        while resp.status_code == 429 and attempt < self.max_rate_limit_retries:
            delay = _retry_after_seconds(resp)
            if log is not None:
                log.warning("rate_limited_retrying", attempt=attempt + 1, delay=delay)
            time.sleep(delay)
            resp = self.client.post("/inventory_levels/set.json", json=body)
            attempt += 1
        return resp

    def _enable_tracking(self, inventory_item_id: int) -> None:
        resp = self.client.put(
            f"/inventory_items/{inventory_item_id}.json",
            json={"inventory_item": {"id": inventory_item_id, "tracked": True}},
        )
        if resp.status_code not in (200, 201):
            raise ShopifyError(
                f"inventory_items/{inventory_item_id}.json {resp.status_code}: {resp.text[:200]}"
            )

    def unpublish(self, sku: SKU) -> None:
        self._set_product_status(sku, "archived")

    def republish(self, sku: SKU) -> None:
        self._set_product_status(sku, "active")

    # --- net-new product creation (Laura upload) ---

    def create_product(self, draft: ProductDraft) -> CreatedProduct:
        has_size_option = any(v.option_value is not None for v in draft.variants)
        variants_payload: list[dict] = []
        for v in draft.variants:
            vp: dict = {"sku": str(v.sku)}
            if v.option_value is not None:
                vp["option1"] = v.option_value
            if v.barcode is not None:
                vp["barcode"] = v.barcode
            if v.price is not None:
                vp["price"] = str(v.price)
            if v.compare_at_price is not None:
                vp["compare_at_price"] = str(v.compare_at_price)
            if v.inventory_quantity is not None or v.track_inventory:
                # Track inventory so a follow-up update_stock can set the level.
                vp["inventory_management"] = "shopify"
            variants_payload.append(vp)

        product: dict = {
            "title": draft.title,
            "body_html": draft.body_html,
            "vendor": draft.vendor,
            "product_type": draft.product_type,
            "tags": draft.tags,
            "status": draft.status,
            "variants": variants_payload,
        }
        if has_size_option:
            product["options"] = [{"name": draft.option_name}]
        if draft.image_urls:
            product["images"] = [{"src": _safe_image_url(url)} for url in draft.image_urls]
        if draft.metafields:
            product["metafields"] = [
                {"namespace": m.namespace, "key": m.key, "type": m.type, "value": m.value}
                for m in draft.metafields
            ]
        if draft.template_suffix:
            product["template_suffix"] = draft.template_suffix

        resp = self.client.post("/products.json", json={"product": product})
        if resp.status_code not in (200, 201):
            self.logger.error("product_create_failed", status=resp.status_code, body=resp.text[:200])
            raise ShopifyError(f"products.json {resp.status_code}: {resp.text[:200]}")

        created = resp.json()["product"]
        product_id = created["id"]
        variant_ids: dict[SKU, str] = {}
        for v in created.get("variants", []):
            sku_raw = v.get("sku")
            if not sku_raw:
                continue
            sku = SKU(sku_raw)
            variant_ids[sku] = str(v["id"])
            # Cache the ref so a follow-up update_stock/status needs no re-list.
            self._variant_by_sku[sku] = _VariantRef(
                inventory_item_id=v["inventory_item_id"],
                product_id=product_id,
                variant_id=v["id"],
            )
        self.logger.info("product_created", product_id=product_id, variants=len(variant_ids), status=draft.status)
        return CreatedProduct(store_product_id=str(product_id), variant_ids_by_sku=variant_ids)

    def ensure_collection(self, title: str) -> CollectionRef:
        # Cache within the run: same collection is ensured once, so N products of
        # one family don't each create a duplicate (and don't re-query).
        cached = self._collection_id_by_title.get(title)
        if cached is not None:
            self.logger.info("collection_resolved", title=title, collection_id=cached,
                             matched=True, source="cache")
            return CollectionRef(id=cached, created=False)

        # Server-side exact-title filter — the store can have hundreds of
        # collections, so an unfiltered (paginated) list would miss the match
        # and spam duplicates. `title=` returns just the one we want.
        resp = self.client.get("/custom_collections.json", params={"title": title, "limit": 250})
        if resp.status_code != 200:
            raise ShopifyError(f"custom_collections.json {resp.status_code}: {resp.text[:200]}")
        returned = resp.json().get("custom_collections", [])
        returned_titles = [c.get("title") for c in returned]
        for c in returned:
            if c.get("title") == title:
                cid = str(c["id"])
                self._collection_id_by_title[title] = cid
                self.logger.info("collection_resolved", title=title, collection_id=cid,
                                 matched=True, source="store", matched_title=c.get("title"))
                return CollectionRef(id=cid, created=False)

        # No exact match — log what the query returned so a near-miss (e.g. a
        # whitespace/spelling difference) is visible in the trail before we create.
        self.logger.info("collection_no_match", title=title, matched=False,
                         returned_count=len(returned), returned_titles=returned_titles[:10])

        resp = self.client.post("/custom_collections.json", json={"custom_collection": {"title": title}})
        if resp.status_code not in (200, 201):
            self.logger.error("collection_create_failed", status=resp.status_code, body=resp.text[:200])
            raise ShopifyError(f"custom_collections.json POST {resp.status_code}: {resp.text[:200]}")
        c = resp.json()["custom_collection"]
        cid = str(c["id"])
        self._collection_id_by_title[title] = cid
        self.logger.info("collection_created", collection_id=c["id"], title=title, matched=False)
        return CollectionRef(id=cid, created=True)

    def delete_product(self, store_product_id: str) -> None:
        resp = self.client.delete(f"/products/{store_product_id}.json")
        if resp.status_code != 200:
            self.logger.error("product_delete_failed", status=resp.status_code, body=resp.text[:200])
            raise ShopifyError(f"products/{store_product_id}.json DELETE {resp.status_code}: {resp.text[:200]}")
        self.logger.info("product_deleted", product_id=store_product_id)

    def add_to_collection(self, store_product_id: str, collection_id: str) -> None:
        resp = self.client.post(
            "/collects.json",
            json={"collect": {"product_id": int(store_product_id), "collection_id": int(collection_id)}},
        )
        if resp.status_code not in (200, 201):
            self.logger.error("collect_failed", status=resp.status_code, body=resp.text[:200])
            raise ShopifyError(f"collects.json {resp.status_code}: {resp.text[:200]}")

    def set_product_metafields(self, store_product_id: str, metafields) -> None:
        """Write metafields onto an existing product (post-create backfill).

        Used for Bambino's `custom.related_products` (§4): the sibling GIDs are
        only known once every color in a group has been created, so they can't
        be set at create time. One POST per metafield.
        """
        for m in metafields:
            resp = self.client.post(
                f"/products/{store_product_id}/metafields.json",
                json={"metafield": {"namespace": m.namespace, "key": m.key,
                                    "type": m.type, "value": m.value}},
            )
            if resp.status_code not in (200, 201):
                self.logger.error("metafield_set_failed", store_product_id=store_product_id,
                                  key=f"{m.namespace}.{m.key}", status=resp.status_code,
                                  body=resp.text[:200])
                raise ShopifyError(
                    f"products/{store_product_id}/metafields.json {resp.status_code}: {resp.text[:200]}"
                )
        self.logger.info("metafields_set", store_product_id=store_product_id, count=len(metafields))

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

    def product_ids_by_vendor(self, vendors) -> list[dict]:
        """Product-level summaries for the given vendor tags — a bulk-cleanup helper.

        Returns [{id, title, vendor, skus}] deduped across vendors. Used by the
        Bambino pre-import delete (PRD §1); carries variant SKUs so the caller can
        protect any product that is already a live catalog item.
        """
        out: list[dict] = []
        seen: set[str] = set()
        for vendor in vendors:
            for p in self._paginated_products(vendor=vendor):
                pid = str(p["id"])
                if pid in seen:
                    continue
                seen.add(pid)
                out.append({
                    "id": pid,
                    "title": p.get("title") or "",
                    "vendor": p.get("vendor") or "",
                    "skus": [v.get("sku") for v in p.get("variants", []) if v.get("sku")],
                })
        return out

    _UNSET = object()

    def _paginated_products(self, vendor=_UNSET) -> Iterator[dict]:
        params: dict = {"limit": self.page_size}
        effective_vendor = self.vendor_filter if vendor is self._UNSET else vendor
        if effective_vendor:
            params["vendor"] = effective_vendor

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
