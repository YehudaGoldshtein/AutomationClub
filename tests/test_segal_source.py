"""Failing-first tests for the Segal source layer (Phase 0).

Two pure parsers, no I/O:
  - parse_api_product: WooCommerce Store API product dict -> SegalProduct
  - parse_tabs: product-page HTML -> tuple[SegalTab] (label + inner html per tab)

Built against the real shapes captured in reports/segal-sample-product*.json and
the #more-info tab markup on the live product pages.
"""
from __future__ import annotations

from decimal import Decimal

from inventory_sync.segal_source import (
    SegalProduct,
    SegalTab,
    parse_api_product,
    parse_tabs,
)


def _api(**over) -> dict:
    base = {
        "sku": "2025102600130",
        "name": "מעקה מעבר &#8211; אגוז",
        "description": "<p>מעקה מעבר למיטת תינוק</p>",
        "on_sale": False,
        "prices": {
            "price": "299",
            "regular_price": "299",
            "sale_price": "299",
            "currency_code": "ILS",
            "currency_minor_unit": 0,
        },
        "images": [{"src": "http://img/1.jpg"}, {"src": "http://img/2.jpg"}],
        "categories": [{"slug": "beds"}, {"slug": "segal-baby"}],
        "permalink": "http://segal/product/1/",
        "is_in_stock": True,
        "stock_availability": {"text": "231 במלאי", "class": "in-stock"},
        "add_to_cart": {"minimum": 1, "maximum": 231},
    }
    base.update(over)
    return base


class TestParseApiProduct:
    def test_core_fields(self):
        p = parse_api_product(_api())
        assert isinstance(p, SegalProduct)
        assert p.sku == "2025102600130"
        assert p.name == "מעקה מעבר &#8211; אגוז"  # raw; entity-decode happens in mapping
        assert p.description_html == "<p>מעקה מעבר למיטת תינוק</p>"
        assert p.permalink == "http://segal/product/1/"
        assert p.image_urls == ("http://img/1.jpg", "http://img/2.jpg")
        assert p.category_slugs == ("beds", "segal-baby")

    def test_price_uses_regular_price_and_minor_unit(self):
        assert parse_api_product(_api()).price == Decimal("299")
        p2 = parse_api_product(_api(prices={
            "regular_price": "41000", "sale_price": "39000",
            "currency_code": "USD", "currency_minor_unit": 2,
        }))
        assert p2.price == Decimal("410.00")
        assert p2.sale_price == Decimal("390.00")

    def test_in_stock_quantity_from_add_to_cart_maximum(self):
        p = parse_api_product(_api())
        assert p.in_stock is True
        assert p.stock_qty == 231

    def test_out_of_stock_is_zero_not_maximum(self):
        # out-of-stock products still report add_to_cart.maximum=1 — must be 0.
        p = parse_api_product(_api(
            is_in_stock=False,
            stock_availability={"text": "המלאי אזל", "class": "out-of-stock"},
            add_to_cart={"minimum": 1, "maximum": 1},
        ))
        assert p.in_stock is False
        assert p.stock_qty == 0

    def test_missing_optional_fields_dont_crash(self):
        p = parse_api_product({
            "sku": "X-1", "name": "n", "prices": {"regular_price": "10", "currency_minor_unit": 0},
            "is_in_stock": False,
        })
        assert p.sku == "X-1"
        assert p.image_urls == ()
        assert p.category_slugs == ()
        assert p.description_html == ""
        assert p.tabs == ()


_HTML = """
<html><body>
<section id="more-info" class="product-more-info">
  <ul class="nav nav-tabs">
    <li><a class="nav-link active" href="#tab-1">מידע כללי</a></li>
    <li><a class="nav-link" href="#tab-2">  פרטים טכניים  </a></li>
    <li><a class="nav-link" href="#tab-3">Greenguard</a></li>
  </ul>
  <div class="tab-content">
    <div class="tab-pane" id="tab-1"><p>תיאור כללי של המוצר</p></div>
    <div class="tab-pane" id="tab-2"><p>מידות: 125 ס"מ</p><p>חומר: עץ בוק</p></div>
    <div class="tab-pane" id="tab-3"><p>תו תקן ירוק</p></div>
  </div>
</section>
</body></html>
"""


class TestParseTabs:
    def test_extracts_label_and_content_in_order(self):
        tabs = parse_tabs(_HTML)
        assert [t.label for t in tabs] == ["מידע כללי", "פרטים טכניים", "Greenguard"]
        assert isinstance(tabs[0], SegalTab)

    def test_label_whitespace_stripped(self):
        tabs = parse_tabs(_HTML)
        assert tabs[1].label == "פרטים טכניים"  # source had leading/trailing spaces

    def test_pane_content_matched_to_its_label(self):
        by_label = {t.label: t.html for t in parse_tabs(_HTML)}
        assert "125" in by_label["פרטים טכניים"]
        assert "עץ בוק" in by_label["פרטים טכניים"]
        assert "תיאור כללי" in by_label["מידע כללי"]
        assert "תו תקן" in by_label["Greenguard"]

    def test_no_more_info_section_returns_empty(self):
        assert parse_tabs("<html><body><p>nothing</p></body></html>") == ()
