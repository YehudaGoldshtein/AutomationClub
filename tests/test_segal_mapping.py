"""Failing-first tests for Segal mapping (Phase 1).

Covers: category → product_type/collections/template, tab-label → metafield
routing (discard + log unknown), HTML → rich_text JSON, entity decode, and the
full SegalProduct → ProductDraft assembly.
"""
from __future__ import annotations

import json
from decimal import Decimal

from inventory_sync.domain import SKU, Metafield
from inventory_sync.segal_source import SegalProduct, SegalTab
from inventory_sync.segal_mapping import (
    VENDOR,
    collections_for,
    decode_entities,
    html_to_rich_text,
    matched_category,
    product_type_for,
    route_tab,
    tabs_to_metafields,
    template_suffix_for,
    to_product_draft,
)


class _RecLogger:
    def __init__(self):
        self.events = []

    def info(self, event, **kw):
        self.events.append((event, kw))

    warning = info
    error = info
    exception = info

    def bind(self, **kw):
        return self


def _product(slug="beds", **over) -> SegalProduct:
    base = dict(
        sku="3040170863",
        name="שידת קוורץ &#8211; לבן",
        description_html="<p>שידה יפה</p>",
        price=Decimal("2198"),
        sale_price=Decimal("2198"),
        on_sale=False,
        image_urls=("http://img/1.jpg",),
        category_slugs=(slug, "segal-baby"),
        permalink="http://segal/product/1/",
        in_stock=True,
        stock_qty=5,
        tabs=(),
    )
    base.update(over)
    return SegalProduct(**base)


class TestCategoryMapping:
    def test_beds(self):
        p = _product(slug="beds")
        assert matched_category(p) == "beds"
        assert product_type_for(p) == "מיטות תינוק"
        assert collections_for(p) == ("מיטות תינוק",)
        assert template_suffix_for(p) == "furniture-beds"

    def test_dresser(self):
        p = _product(slug="dresser")
        assert product_type_for(p) == "שידות"
        assert collections_for(p) == ("שידות החתלה",)
        assert template_suffix_for(p) == "furniture-product-page"

    def test_soft_close_dresser_same_as_dresser(self):
        assert product_type_for(_product(slug="soft-close-dresser")) == "שידות"

    def test_storage_maps_to_two_collections(self):
        assert collections_for(_product(slug="storage-segal-baby")) == ("אחסון", "אחסון ואביזרים")

    def test_beds_and_toddler(self):
        p = _product(slug="beds-and-toddler")
        assert collections_for(p) == ("מיטות מעבר",)
        assert template_suffix_for(p) == "furniture-beds"

    def test_unmapped_category_returns_none(self):
        p = _product(slug="mattress")  # deferred, not in the map
        assert matched_category(p) is None
        assert collections_for(p) == ()


class TestRouteTab:
    def test_known_labels(self):
        assert route_tab("מידע כללי") == ("custom", "infoo")
        assert route_tab("פרטים טכניים") == ("custom", "view_productss")
        assert route_tab("פרטים טכניים, ניקוי ואזהרה") == ("custom", "view_productss")

    def test_greenguard_and_warranty_go_to_securingg(self):
        assert route_tab("Greenguard") == ("custom", "securingg")
        assert route_tab("greenguard") == ("custom", "securingg")
        assert route_tab("אחריות מורחבת") == ("custom", "securingg")

    def test_whitespace_tolerated(self):
        assert route_tab("  מידע כללי  ") == ("custom", "infoo")

    def test_unknown_label_returns_none(self):
        assert route_tab("טכנולוגיית Cloud – טריקה שקטה") is None
        assert route_tab("100 לילות ניסיון") is None
        assert route_tab("הוראות הרכבה") is None


class TestRichText:
    def test_paragraph_per_line_valid_json(self):
        rt = html_to_rich_text("<p>שורה א</p><p>שורה ב</p>")
        doc = json.loads(rt)
        assert doc["type"] == "root"
        values = [c["children"][0]["value"] for c in doc["children"]]
        assert values == ["שורה א", "שורה ב"]

    def test_br_splits_lines(self):
        rt = html_to_rich_text("<p>מידות: 125<br>חומר: עץ בוק</p>")
        values = [c["children"][0]["value"] for c in json.loads(rt)["children"]]
        assert values == ["מידות: 125", "חומר: עץ בוק"]

    def test_bold_flattened_to_text(self):
        rt = html_to_rich_text("<p><strong>כותרת</strong> טקסט</p>")
        values = [c["children"][0]["value"] for c in json.loads(rt)["children"]]
        assert values == ["כותרת טקסט"]


class TestDecodeEntities:
    def test_decodes_html_entities(self):
        assert decode_entities("שידת קוורץ &#8211; לבן") == "שידת קוורץ – לבן"
        assert decode_entities("א &amp; ב") == "א & ב"


class TestTabsToMetafields:
    def test_maps_known_tabs(self):
        tabs = (
            SegalTab("מידע כללי", "<p>כללי</p>"),
            SegalTab("פרטים טכניים", "<p>מידות: 125</p>"),
        )
        mfs = tabs_to_metafields(tabs, _RecLogger())
        by_key = {(m.namespace, m.key): m for m in mfs}
        assert ("custom", "infoo") in by_key
        assert ("custom", "view_productss") in by_key
        assert by_key[("custom", "infoo")].type == "rich_text_field"
        assert "כללי" in by_key[("custom", "infoo")].value

    def test_greenguard_prefixed_with_warranty(self):
        mfs = tabs_to_metafields((SegalTab("Greenguard", "<p>תו תקן</p>"),), _RecLogger())
        [mf] = [m for m in mfs if m.key == "securingg"]
        values = [c["children"][0]["value"] for c in json.loads(mf.value)["children"]]
        assert values[0].startswith("אחריות רחבה 5 שנים")
        assert "תו תקן" in values

    def test_unknown_tab_discarded_and_logged(self):
        log = _RecLogger()
        mfs = tabs_to_metafields((SegalTab("100 לילות ניסיון", "<p>x</p>"),), log)
        assert mfs == ()
        assert any(e == "tab_discarded" for e, _ in log.events)
        discard = next(kw for e, kw in log.events if e == "tab_discarded")
        assert discard.get("label") == "100 לילות ניסיון"


class TestToProductDraft:
    def test_core_fields(self):
        p = _product(slug="dresser")
        d = to_product_draft(p, _RecLogger())
        assert d.title == "שידת קוורץ – לבן"  # entity-decoded
        assert d.body_html == "<p>שידה יפה</p>"
        assert d.vendor == VENDOR
        assert d.product_type == "שידות"
        assert d.status == "draft"
        assert d.template_suffix == "furniture-product-page"

    def test_single_variant_with_price_and_stock(self):
        d = to_product_draft(_product(stock_qty=5, price=Decimal("2198")), _RecLogger())
        assert len(d.variants) == 1
        v = d.variants[0]
        assert v.sku == SKU("3040170863")
        assert v.option_value is None       # simple product, no size option
        assert v.price == Decimal("2198")
        assert v.inventory_quantity == 5

    def test_metafields_include_tabs_supplier_and_seo(self):
        p = _product(slug="dresser", tabs=(SegalTab("מידע כללי", "<p>כללי</p>"),))
        d = to_product_draft(p, _RecLogger())
        keys = {(m.namespace, m.key) for m in d.metafields}
        assert ("custom", "infoo") in keys          # from tab
        assert ("custom", "delivery") in keys        # boilerplate
        assert ("global", "title_tag") in keys       # SEO ← name
        assert ("global", "description_tag") in keys # SEO ← description
        assert ("supplier", "url") in keys           # permalink anchor
        assert ("supplier", "price") in keys
