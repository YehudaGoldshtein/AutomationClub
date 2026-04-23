"""Bench: old-path vs new-path HTTP-request count and wall time.

Simulates the Laura fetch with a MockTransport returning a ~constant synthetic
delay per request (no real Laura load). Compares:
  - OLD: fetch every Max-Baby SKU from Laura, some 404
  - NEW: fetch sitemap once, pre-filter, fetch only the intersection

Not a correctness test — it's a number-producing script so we can see the
concrete savings at different scales.
"""
from __future__ import annotations

import time

import httpx

from inventory_sync.adapters.laura_design import LauraDesignScraperAdapter, parse_laura_sitemap
from inventory_sync.domain import VendorProductId
from inventory_sync.log import get


SIMULATED_LATENCY_S = 0.05  # ~50ms per fetch; fast enough to finish in seconds, realistic shape.


def _build_sitemap_xml(skus: list[str]) -> str:
    urls = "".join(f"<url><loc>https://www.laura-design.net/{s}</loc></url>" for s in skus)
    return f'<?xml version="1.0"?><urlset>{urls}</urlset>'


def _product_html(sku: str, available: bool = True) -> str:
    import json as _json
    payload = {
        "@context": "https://schema.org/",
        "@type": "Product",
        "sku": sku,
        "name": f"Product {sku}",
        "offers": {
            "@type": "Offer",
            "availability": "https://schema.org/InStock" if available else "https://schema.org/OutOfStock",
            "price": 99,
            "priceCurrency": "ILS",
        },
    }
    return f'<html><head><script type="application/ld+json">{_json.dumps(payload)}</script></head></html>'


def _make_fake_laura(vendor_skus: set[str], sitemap_skus: set[str]):
    """Build an httpx.MockTransport that simulates Laura:
    - /sitemap.xml -> all sitemap SKUs
    - /<sku> with sku in sitemap -> 200 + JSON-LD
    - /<sku> else -> 404
    Each call sleeps SIMULATED_LATENCY_S.
    """
    sitemap_xml = _build_sitemap_xml(sorted(sitemap_skus))
    counts = {"sitemap_fetches": 0, "product_fetches_ok": 0, "product_fetches_404": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(SIMULATED_LATENCY_S)
        path = request.url.path
        if path.endswith("/sitemap.xml"):
            counts["sitemap_fetches"] += 1
            return httpx.Response(200, text=sitemap_xml)
        sku = path.lstrip("/")
        if sku in sitemap_skus:
            counts["product_fetches_ok"] += 1
            return httpx.Response(200, text=_product_html(sku))
        counts["product_fetches_404"] += 1
        return httpx.Response(404, text="not found")

    return handler, counts


def run_old_path(vendor_skus: set[str], sitemap_skus: set[str], max_workers: int) -> tuple[float, dict]:
    handler, counts = _make_fake_laura(vendor_skus, sitemap_skus)
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://www.laura-design.net")
    adapter = LauraDesignScraperAdapter(client=client, logger=get("bench"), max_workers=max_workers)

    t0 = time.perf_counter()
    adapter.fetch_snapshots([VendorProductId(s) for s in vendor_skus])
    elapsed = time.perf_counter() - t0
    return elapsed, counts


def run_new_path(vendor_skus: set[str], sitemap_skus: set[str], max_workers: int) -> tuple[float, dict]:
    """Simulate: fetch sitemap -> parse -> filter -> fetch intersection only."""
    handler, counts = _make_fake_laura(vendor_skus, sitemap_skus)
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://www.laura-design.net")
    adapter = LauraDesignScraperAdapter(client=client, logger=get("bench"), max_workers=max_workers)

    t0 = time.perf_counter()
    # 1. fetch sitemap
    resp = client.get("/sitemap.xml")
    catalog = parse_laura_sitemap(resp.text)
    # 2. pre-filter
    to_fetch = {s for s in vendor_skus if s in catalog}
    # 3. fetch only intersection
    adapter.fetch_snapshots([VendorProductId(s) for s in to_fetch])
    elapsed = time.perf_counter() - t0
    return elapsed, counts


def main() -> None:
    # Scenario shapes. Each: (n_vendor, sitemap_coverage_pct)
    scenarios = [
        ("current Max Baby", 817, 649 / 817),   # today's real proportions
        ("larger single store", 5_000, 0.80),
        ("giant store", 20_000, 0.80),
    ]

    print(f"{'scenario':<25} {'vendor skus':>12} {'sitemap coverage':>18}  {'old path':>14}  {'new path':>14}  {'speedup':>10}  {'requests saved':>16}")
    print("-" * 135)

    def _sku(i: int) -> str:
        # Laura-shape SKU: NNNN-NNN
        return f"{1000 + (i // 1000):04d}-{i % 1000:03d}"

    for label, n_vendor, coverage in scenarios:
        vendor_skus = {_sku(i) for i in range(n_vendor)}
        n_covered = int(n_vendor * coverage)
        sitemap_skus = {_sku(i) for i in range(n_covered)}
        # extras that live in Laura's sitemap but aren't tagged to Max Baby
        sitemap_skus |= {_sku(100_000 + i) for i in range(n_covered // 4)}

        old_elapsed, old_counts = run_old_path(vendor_skus, sitemap_skus, max_workers=4)
        new_elapsed, new_counts = run_new_path(vendor_skus, sitemap_skus, max_workers=4)

        old_requests = old_counts["product_fetches_ok"] + old_counts["product_fetches_404"]
        new_requests = (new_counts["sitemap_fetches"] + new_counts["product_fetches_ok"] + new_counts["product_fetches_404"])
        speedup = old_elapsed / new_elapsed if new_elapsed > 0 else float("inf")
        saved = old_requests - new_requests

        print(f"{label:<25} {n_vendor:>12,} {coverage:>17.0%}  {old_elapsed:>12.2f}s  {new_elapsed:>12.2f}s  {speedup:>9.1f}x  {saved:>16,}")


if __name__ == "__main__":
    main()
