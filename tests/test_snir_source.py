"""Pure-parser tests for the Snir source layer (Phase 0)."""
from __future__ import annotations

from decimal import Decimal

from inventory_sync.snir_source import parse_api_product, parse_tabs, tab_html

# Trimmed real-shape Store API product (Snir): whole-unit price, binary stock.
API = {
    "id": 11433,
    "sku": "sh-asenta",
    "name": "שידת אחסנה קריסטל",
    "short_description": "<p>תיאור קצר</p>",
    "description": "<p>תיאור מלא</p>",
    "prices": {"regular_price": "1690", "currency_minor_unit": 0, "currency_code": "ILS"},
    "is_in_stock": True,
    "add_to_cart": {"maximum": 9999},
    "images": [{"src": "http://img/a.jpg"}, {"src": "http://img/b.jpg"}],
    "categories": [{"id": 125, "name": "שידות אחסנה"}, {"id": 138, "name": "MIX AND MATCH"}],
    "permalink": "https://www.snir-bebe.com/product/sh-asenta/",
}


class TestParseApiProduct:
    def test_maps_core_fields(self):
        p = parse_api_product(API)
        assert p.sku == "sh-asenta"
        assert p.name == "שידת אחסנה קריסטל"
        assert p.short_description_html == "<p>תיאור קצר</p>"
        assert p.description_html == "<p>תיאור מלא</p>"
        assert p.permalink.endswith("/sh-asenta/")

    def test_price_whole_units_when_minor_unit_zero(self):
        # minor_unit=0 → 1690 stays 1690 (not 16.90).
        assert parse_api_product(API).price == Decimal("1690")

    def test_price_scales_when_minor_unit_set(self):
        data = {**API, "prices": {"regular_price": "1690", "currency_minor_unit": 2}}
        assert parse_api_product(data).price == Decimal("16.90")

    def test_stock_is_binary(self):
        assert parse_api_product(API).in_stock is True
        assert parse_api_product({**API, "is_in_stock": False}).in_stock is False

    def test_category_ids_and_images(self):
        p = parse_api_product(API)
        assert p.category_ids == (125, 138)
        assert p.image_urls == ("http://img/a.jpg", "http://img/b.jpg")

    def test_type_and_variation_count(self):
        # absent → safe defaults; present → captured from the list object.
        assert parse_api_product(API).wc_type == "" and parse_api_product(API).variation_count == 0
        p = parse_api_product({**API, "type": "variable", "variations": [{"id": 1}, {"id": 2}]})
        assert p.wc_type == "variable" and p.variation_count == 2

    def test_missing_fields_are_safe(self):
        p = parse_api_product({"id": 1})
        assert p.sku == "" and p.price is None and p.category_ids == () and p.tabs == ()


TABS_HTML = """
<div class="woocommerce-Tabs-panel woocommerce-Tabs-panel--tech_details" id="tab-x">
  מידה כללית: רוחב 100<br>חומר גלם: MDF
</div>
<div class="woocommerce-Tabs-panel woocommerce-Tabs-panel--description">
  <p>תיאור פריט</p>
</div>
<div class="woocommerce-Tabs-panel woocommerce-Tabs-panel--oc_theme_product_tab_2">
  אחריות
</div>
"""


class TestParseTabs:
    def test_parses_panels_by_name(self):
        tabs = parse_tabs(TABS_HTML)
        labels = {t.label for t in tabs}
        assert labels == {"tech_details", "description", "oc_theme_product_tab_2"}

    def test_tab_html_extracts_tech_details(self):
        tabs = parse_tabs(TABS_HTML)
        td = tab_html(tabs, "tech_details")
        assert "רוחב 100" in td and "MDF" in td

    def test_missing_tab_returns_empty(self):
        assert tab_html(parse_tabs("<div>nothing</div>"), "tech_details") == ""
        assert parse_tabs("") == ()
