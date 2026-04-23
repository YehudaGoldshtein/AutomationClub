"""Tests for the Laura sitemap parser.

Pure parsing — no HTTP. Given sitemap XML, return the set of product SKUs.
"""
from __future__ import annotations

from inventory_sync.adapters.laura_design import parse_laura_sitemap


def _sitemap_wrapper(urls: list[str]) -> str:
    locs = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f'{locs}</urlset>'
    )


class TestParseLauraSitemap:
    def test_extracts_basic_sku_pattern(self):
        xml = _sitemap_wrapper([
            "https://www.laura-design.net/3200-118",
            "https://www.laura-design.net/1603-135",
        ])
        assert parse_laura_sitemap(xml) == {"3200-118", "1603-135"}

    def test_ignores_non_product_urls(self):
        xml = _sitemap_wrapper([
            "https://www.laura-design.net/",
            "https://www.laura-design.net/about",
            "https://www.laura-design.net/category/strollers",
            "https://www.laura-design.net/3200-118",
        ])
        assert parse_laura_sitemap(xml) == {"3200-118"}

    def test_handles_trailing_slash(self):
        xml = _sitemap_wrapper(["https://www.laura-design.net/3200-118/"])
        assert parse_laura_sitemap(xml) == {"3200-118"}

    def test_handles_query_string(self):
        xml = _sitemap_wrapper([
            "https://www.laura-design.net/3200-118?ref=home",
        ])
        assert parse_laura_sitemap(xml) == {"3200-118"}

    def test_handles_uppercase_letter_suffix(self):
        """Laura uses SKUs like '2809-021M' for modular/multi-variant codes."""
        xml = _sitemap_wrapper([
            "https://www.laura-design.net/2809-021M",
        ])
        assert parse_laura_sitemap(xml) == {"2809-021M"}

    def test_empty_sitemap_returns_empty_set(self):
        assert parse_laura_sitemap(_sitemap_wrapper([])) == set()

    def test_malformed_xml_returns_empty_set(self):
        """Graceful degradation — no crash on bad input."""
        assert parse_laura_sitemap("<not valid xml") == set()

    def test_completely_empty_string(self):
        assert parse_laura_sitemap("") == set()

    def test_deduplicates_repeated_urls(self):
        xml = _sitemap_wrapper([
            "https://www.laura-design.net/3200-118",
            "https://www.laura-design.net/3200-118",
        ])
        assert parse_laura_sitemap(xml) == {"3200-118"}

    def test_rejects_near_miss_patterns(self):
        """Something like '/32001-118' (5+3 digits) shouldn't match the 4+3 SKU shape."""
        xml = _sitemap_wrapper([
            "https://www.laura-design.net/32001-118",
            "https://www.laura-design.net/320-118",
            "https://www.laura-design.net/3200-11",
        ])
        assert parse_laura_sitemap(xml) == set()

    def test_large_realistic_mix(self):
        xml = _sitemap_wrapper([
            "https://www.laura-design.net/",
            "https://www.laura-design.net/2801-068",
            "https://www.laura-design.net/1603-138/",
            "https://www.laura-design.net/1603-135?ref=home",
            "https://www.laura-design.net/1904-020M",
            "https://www.laura-design.net/2809-021M/",
            "https://www.laura-design.net/about-us",
            "https://www.laura-design.net/collections/strollers",
            "https://www.laura-design.net/pages/shipping",
        ])
        assert parse_laura_sitemap(xml) == {
            "2801-068", "1603-138", "1603-135", "1904-020M", "2809-021M",
        }
