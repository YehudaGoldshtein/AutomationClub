"""Bench: how fast does parse_laura_sitemap scale?

Generates synthetic sitemaps of various sizes and measures parse time.
Establishes that parsing is O(N) and cheap even at very large N.
"""
from __future__ import annotations

import time

from inventory_sync.adapters.laura_design import parse_laura_sitemap


def _build_sitemap(n_products: int) -> str:
    urls = [
        f"<url><loc>https://www.laura-design.net/{1000 + i // 1000:04d}-{i % 1000:03d}</loc></url>"
        for i in range(n_products)
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset>' + "".join(urls) + '</urlset>'
    )


def main() -> None:
    print(f"{'N products':>12}  {'XML size':>12}  {'parse time':>12}  {'SKUs found':>12}")
    print("-" * 56)
    for n in [1_000, 5_000, 10_000, 50_000, 100_000]:
        xml = _build_sitemap(n)
        t0 = time.perf_counter()
        skus = parse_laura_sitemap(xml)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        size_kb = len(xml) / 1024
        print(f"{n:>12,}  {size_kb:>10,.0f}KB  {elapsed_ms:>9,.1f}ms  {len(skus):>12,}")


if __name__ == "__main__":
    main()
