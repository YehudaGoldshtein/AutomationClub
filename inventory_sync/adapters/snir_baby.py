"""Snir Baby supplier adapter — WooCommerce Store API behind a WAF (PRD §0).

Hybrid source (like Segal): the Store API gives the structured product fields,
the product page HTML gives the one tab we still need (`tech_details`). Unlike
Segal, Snir lists *all* products from one endpoint (category routing happens in
snir_mapping, by id) and stock is binary.

  GET /wp-json/wc/store/v1/products?per_page=&page=   (paginate all products)
  GET <permalink>  -> .woocommerce-Tabs-panel--tech_details

The adapter is transport-agnostic: `client` is anything with an httpx-shaped
`.get(url, params=)`. In production that is `browser_fetch.PlaywrightClient`,
which solves the WAF JS-challenge and serves same-origin GETs; in tests it is a
plain `httpx.Client` on a MockTransport (no browser, no network).

WAF economy: Snir's WAF is volume/behaviour-gated, so `fetch_products` only
GETs a product page for products that are actually in scope — importable
(routable category) *and* carrying a SKU (our identity key). Out-of-scope and
SKU-less products are dropped without a page fetch. See tests/test_snir_adapter.py.
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from typing import Iterable, Protocol

from inventory_sync.domain import VendorProductId, VendorProductSnapshot
from inventory_sync.log import Logger, get
from inventory_sync.snir_mapping import is_importable
from inventory_sync.snir_source import (
    SnirProduct,
    SnirTab,
    parse_api_product,
    parse_tabs,
)

_STORE_API = "/wp-json/wc/store/v1/products"


class SupportsGet(Protocol):
    """The httpx.Client slice the adapter needs (httpx.Client / PlaywrightClient)."""
    def get(self, url: str, params: dict | None = None): ...


def _to_snapshot(p: SnirProduct) -> VendorProductSnapshot:
    """SnirProduct -> VendorProductSnapshot. Stock is binary (no count): out of
    stock -> count 0 / unavailable; in stock -> available with an unknown count
    (None), never a fabricated quantity."""
    return VendorProductSnapshot(
        vendor_product_id=VendorProductId(p.sku),
        is_available=p.in_stock,
        stock_count=0 if not p.in_stock else None,
        name=_html.unescape(p.name) or None,
        price=p.price,
        image_url=p.image_urls[0] if p.image_urls else None,
    )


@dataclass
class SnirStoreApiAdapter:
    client: SupportsGet
    logger: Logger = field(default_factory=lambda: get("adapters.snir_baby"))
    base_url: str = "https://www.snir-bebe.com"
    per_page: int = 100

    def list_products(self) -> list[dict]:
        """Paginate the Store API across all products; return raw product dicts.

        Stops on the first partial/empty page. A failed page returns what was
        collected so far (never aborts the whole ingest for one bad page). A page
        that comes back as a challenge (non-JSON) is treated as a failed page.
        """
        out: list[dict] = []
        page = 1
        while True:
            try:
                resp = self.client.get(
                    f"{self.base_url}{_STORE_API}",
                    params={"per_page": self.per_page, "page": page},
                )
            except Exception:
                self.logger.exception("products_fetch_failed", page=page)
                break
            if resp.status_code != 200:
                self.logger.warning("products_bad_status", page=page, status=resp.status_code)
                break
            try:
                batch = resp.json()
            except Exception:
                # Non-JSON (WAF challenge slipped through, or malformed) — stop here.
                self.logger.warning("products_non_json", page=page)
                break
            if not batch:
                break
            out.extend(batch)
            if len(batch) < self.per_page:
                break
            page += 1
        self.logger.info("products_listed", count=len(out))
        return out

    def fetch_tabs(self, permalink: str) -> tuple[SnirTab, ...]:
        """GET the product page and parse its .woocommerce-Tabs-panel tabs.
        Empty on any failure or a challenge (non-JSON is fine here — it's HTML —
        but a challenge page simply has no product panels, so it parses to ())."""
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

    def fetch_products(self) -> list[SnirProduct]:
        """List all products, then fetch + attach tabs for the in-scope ones.

        In scope = importable (routable category) AND has a SKU. A multi-variation
        product that reuses one SKU across its variants is kept and onboarded as a
        single-variant draft on the parent SKU (owner rule: "add the first variant,
        skip the additional" — our mapping already emits one variant per parent SKU).

        The only dedup here is by SKU within a scan: the first product to claim a
        SKU is emitted; a later product whose SKU is already taken is skipped rather
        than duplicated (owner rule). All skips happen *before* any page fetch to
        spare the WAF. (Store-side skip-existing is the ingest's job, Phase 3.)
        """
        raw = self.list_products()
        products: list[SnirProduct] = []
        seen: set[str] = set()
        skipped = skipped_dupe = 0
        for data in raw:
            stub = parse_api_product(data)  # no tabs yet — just for the scope checks
            if not stub.sku or not is_importable(stub):
                skipped += 1
                continue
            if stub.sku in seen:
                self.logger.warning("skip_duplicate_sku", sku=stub.sku)
                skipped_dupe += 1
                continue
            seen.add(stub.sku)
            tabs = self.fetch_tabs(stub.permalink)
            products.append(parse_api_product(data, tabs))
        self.logger.info("products_fetched", in_scope=len(products), skipped=skipped,
                         skipped_duplicate_sku=skipped_dupe)
        return products

    def fetch_all(self) -> Iterable[SnirProduct]:
        """Yield in-scope products (dedup / skip-existing is the caller's job)."""
        yield from self.fetch_products()

    # --- SupplierSource (stock sync for existing products) ---

    def fetch_snapshots(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, VendorProductSnapshot]:
        """Binary availability for the requested SKUs, from the Store API only.

        Lists all products (no per-product tab fetch — stock sync must never hit
        product pages) and returns snapshots just for the requested ids. A
        requested SKU not found in Snir's catalog is omitted (the engine treats
        it as vendor-missing — a product Snir removed).
        """
        wanted = {str(i) for i in ids}
        out: dict[VendorProductId, VendorProductSnapshot] = {}
        for data in self.list_products():
            p = parse_api_product(data)
            if p.sku in wanted and VendorProductId(p.sku) not in out:
                out[VendorProductId(p.sku)] = _to_snapshot(p)
        self.logger.info("snir_snapshots_fetched", requested=len(wanted), returned=len(out))
        return out
