"""Laura Design supplier adapter.

Scrapes each product page's embedded Schema.org JSON-LD to capture availability,
price, name, and image. Binary availability only — `stock_count` stays None.

URL pattern: https://www.laura-design.net/<SKU> (SKU is the URL slug).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable

import httpx
from bs4 import BeautifulSoup

from inventory_sync.domain import (
    VendorProductId,
    VendorProductSnapshot,
)
from inventory_sync.log import Logger, get


@dataclass
class LauraDesignScraperAdapter:
    client: httpx.Client
    logger: Logger = field(default_factory=lambda: get("adapters.laura_design"))
    base_url: str = "https://www.laura-design.net"
    max_workers: int = 4  # concurrent fetches; httpx.Client is thread-safe for reads. 4 is as fast as 8 or 16 against Laura — lighter on their server, lower risk of tripping rate limits.

    def fetch_snapshots(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, VendorProductSnapshot]:
        id_list = list(ids)
        out: dict[VendorProductId, VendorProductSnapshot] = {}
        if not id_list:
            return out
        if self.max_workers <= 1:
            for vid in id_list:
                snap = self._fetch_one(vid)
                if snap is not None:
                    out[vid] = snap
            return out
        with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
            futures = {ex.submit(self._fetch_one, vid): vid for vid in id_list}
            for fut in as_completed(futures):
                vid = futures[fut]
                snap = fut.result()
                if snap is not None:
                    out[vid] = snap
        return out

    def _fetch_one(self, vid: VendorProductId) -> VendorProductSnapshot | None:
        url = f"{self.base_url}/{vid}"
        log = self.logger.bind(vendor_product_id=vid, url=url)
        try:
            resp = self.client.get(url)
        except Exception:
            log.exception("fetch_failed")
            return None

        if resp.status_code == 404:
            log.info("product_not_found", status=404)
            return None
        if resp.status_code != 200:
            log.warning("unexpected_status", status=resp.status_code)
            return None

        product = _extract_product_jsonld(resp.text)
        if product is None:
            log.warning("no_jsonld_product_found")
            return None

        offers = product.get("offers") or {}
        raw_availability = offers.get("availability")
        is_available = _is_available(raw_availability)

        snapshot = VendorProductSnapshot(
            vendor_product_id=vid,
            is_available=is_available,
            stock_count=None,  # binary-only source
            raw_availability=raw_availability,
            name=_str_or_none(product.get("name")),
            price=_to_decimal(offers.get("price")),
            currency=_str_or_none(offers.get("priceCurrency")),
            image_url=_first_image_url(product.get("image")),
        )
        log.info(
            "snapshot_fetched",
            is_available=is_available,
            has_price=snapshot.price is not None,
            has_image=snapshot.image_url is not None,
        )
        return snapshot


def _extract_product_jsonld(html: str) -> dict | None:
    """Return the first Schema.org Product JSON-LD object found in the page."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        body = (script.string or script.get_text() or "").strip()
        if not body:
            continue
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            continue
        items = parsed if isinstance(parsed, list) else [parsed]
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    return None


def _is_available(availability: str | None) -> bool:
    """Schema.org availability values: InStock, OutOfStock, Discontinued, PreOrder, etc."""
    return bool(availability and "InStock" in availability)


def _to_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _str_or_none(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _first_image_url(value) -> str | None:
    """Schema.org image can be a string, a list of strings, or an ImageObject."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, list) and value:
        return _first_image_url(value[0])
    if isinstance(value, dict):
        return _str_or_none(value.get("url") or value.get("contentUrl"))
    return None
