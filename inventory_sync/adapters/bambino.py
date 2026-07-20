"""Bambino supplier adapter — the single master API (public, read-only).

The cleanest source of any supplier: one GET returns the whole catalog + the
per-brand policies (PRD §0). No WAF, no pagination, no per-product scrape. The
master is fetched once and memoized on the instance, so ingest (all products +
warranties) and stock sync (snapshots) share a single round-trip.

  GET https://api.bambinok.com/cache/Bambino

See tests/test_bambino_adapter.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

import httpx

from inventory_sync.bambino_source import (
    BambinoProduct,
    parse_products,
    parse_warranties,
)
from inventory_sync.domain import VendorProductId, VendorProductSnapshot
from inventory_sync.log import Logger, get

_MASTER_URL = "https://api.bambinok.com/cache/Bambino"


def _sale_price(p: BambinoProduct, day: date):
    """The effective price on `day` — the sale amount when a discount is active."""
    if p.discount and p.discount.active_on(day) and p.price is not None:
        return p.discount.amount
    return p.price


def _to_snapshot(p: BambinoProduct, day: date) -> VendorProductSnapshot:
    """BambinoProduct -> VendorProductSnapshot (exact stock; effective price).

    Quantity is an exact count, so the domain invariant is simple: >0 → available
    with that count; 0 → unavailable with count 0.
    """
    available = p.in_stock
    return VendorProductSnapshot(
        vendor_product_id=VendorProductId(p.catalog_number),
        is_available=available,
        stock_count=p.quantity if available else 0,
        name=(f"{p.title} {p.name}".strip() or None),
        price=_sale_price(p, day),
        image_url=p.image_urls[0] if p.image_urls else None,
    )


@dataclass
class BambinoApiAdapter:
    client: httpx.Client
    logger: Logger = field(default_factory=lambda: get("adapters.bambino"))
    url: str = _MASTER_URL

    _master: dict | None = field(default=None, init=False, repr=False)

    def _fetch_master(self) -> dict:
        """GET + memoize the master feed. Raises on a non-200 (a fatal run)."""
        if self._master is None:
            resp = self.client.get(self.url)
            resp.raise_for_status()
            self._master = resp.json()
            self.logger.info("bambino_master_fetched",
                             products=len(self._master.get("products") or []),
                             websites=len(self._master.get("websites") or []))
        return self._master

    # --- ingest source ---

    def fetch_all_products(self) -> list[BambinoProduct]:
        return parse_products(self._fetch_master())

    def warranties(self) -> dict[str, str]:
        return parse_warranties(self._fetch_master())

    # --- SupplierSource (stock sync for existing products) ---

    def fetch_snapshots(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, VendorProductSnapshot]:
        """Availability + exact quantity for the requested SKUs (catalogNumbers).

        A requested SKU not in Bambino's catalog is omitted (the engine treats it
        as vendor-missing). The whole catalog is one fetch, so this is cheap even
        when only a subset is requested.
        """
        wanted = {str(i) for i in ids}
        day = date.today()
        out: dict[VendorProductId, VendorProductSnapshot] = {}
        for p in self.fetch_all_products():
            if p.catalog_number and p.catalog_number in wanted:
                out[VendorProductId(p.catalog_number)] = _to_snapshot(p, day)
        self.logger.info("bambino_snapshots_fetched", requested=len(wanted), returned=len(out))
        return out
