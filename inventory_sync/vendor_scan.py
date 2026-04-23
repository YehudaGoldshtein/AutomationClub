"""Shared vendor-side scanning — TTL-gated, customer-agnostic.

One vendor, many customers, one cache. vendor_scan_pass reads the shared
snapshot cache first and only hits the vendor network for stale or missing
ids. This is what makes two customers sharing a vendor effectively free
on the second customer's sync.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Protocol

from inventory_sync.domain import VendorProductId, VendorProductSnapshot
from inventory_sync.log import Logger


class _VendorSnapshotCache(Protocol):
    def get_fresh(
        self,
        vendor_name: str,
        ids: Iterable[str],
        ttl_minutes: int,
        now: datetime | None = None,
    ) -> dict[str, VendorProductSnapshot]: ...

    def upsert_many(
        self,
        vendor_name: str,
        snapshots: dict[str, VendorProductSnapshot],
        now: datetime | None = None,
    ) -> None: ...


class _Supplier(Protocol):
    def fetch_snapshots(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, VendorProductSnapshot]: ...


def vendor_scan_pass(
    *,
    vendor_name: str,
    supplier: _Supplier,
    ids_needed: Iterable[VendorProductId],
    cache: _VendorSnapshotCache,
    ttl_minutes: int,
    logger: Logger,
    now: datetime | None = None,
) -> dict[VendorProductId, VendorProductSnapshot]:
    """Return snapshots for `ids_needed`, using cache hits where fresh and fetching the rest.

    - Cache HIT (fresh within TTL): returned as-is, no network.
    - Cache MISS or STALE: fetched from vendor, written to cache, returned.
    - Vendor 404 / missing in fetch result: simply absent from the returned dict.
    """
    now = now or datetime.now(timezone.utc)
    id_set = {str(i) for i in ids_needed}
    log = logger.bind(vendor=vendor_name)

    cached = cache.get_fresh(vendor_name, id_set, ttl_minutes=ttl_minutes, now=now)
    stale_ids = id_set - set(cached.keys())
    log.info(
        "vendor_scan_start",
        requested=len(id_set),
        cache_hits=len(cached),
        stale=len(stale_ids),
        ttl_minutes=ttl_minutes,
    )

    fresh: dict[VendorProductId, VendorProductSnapshot] = {}
    if stale_ids:
        fresh = supplier.fetch_snapshots(stale_ids)
        if fresh:
            cache.upsert_many(vendor_name, {str(k): v for k, v in fresh.items()}, now=now)

    merged = {**cached, **fresh}
    log.info(
        "vendor_scan_complete",
        returned=len(merged),
        cache_hits=len(cached),
        network_fetches=len(fresh),
    )
    return merged


@dataclass
class CachedSupplier:
    """Drop-in supplier that routes fetch_snapshots through vendor_scan_pass.

    Lets the existing orchestrator (which takes one `supplier` object) pick
    up cache-gated fetches transparently. fetch_catalog_skus passes through
    to the inner supplier (sitemap fetches are cheap and change shape often).
    """
    inner: _Supplier
    cache: _VendorSnapshotCache
    vendor_name: str
    ttl_minutes: int
    logger: Logger

    def fetch_catalog_skus(self) -> set[str]:
        return self.inner.fetch_catalog_skus()

    def fetch_snapshots(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, VendorProductSnapshot]:
        return vendor_scan_pass(
            vendor_name=self.vendor_name,
            supplier=self.inner,
            ids_needed=ids,
            cache=self.cache,
            ttl_minutes=self.ttl_minutes,
            logger=self.logger,
        )
