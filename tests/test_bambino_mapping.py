"""Tests for Bambino mapping: routing, discount, field/metafield assembly."""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

from inventory_sync.bambino_mapping import (
    FALLBACK_WARRANTY_LINES,
    TEMPLATE,
    build_title,
    collections_for,
    html_to_rich_text,
    is_importable,
    route,
    to_product_draft,
    vendor_for,
)
from inventory_sync.bambino_source import BambinoDiscount, BambinoProduct

WARRANTIES = {
    "Joie": "<p><strong>אחריות</strong></p>\r\n<p>שנתיים מיום הרכישה.</p>",
    "Graco": "<p>שנה אחריות.</p>",
}


def _p(**kw) -> BambinoProduct:
    base = dict(
        id=100, catalog_number="110104360", title="עגלת", name="Myavo",
        color="שחור Black", brand="Graco", description_html="<p>תיאור מלא</p>",
        specifications_html="<ul><li>תקן <strong>ECE R129</strong></li><li>קל משקל</li></ul>",
        price=Decimal("399"), quantity=5, barcode="729000",
        image_urls=("http://img/1.jpg",), type_ids=(28,), type_names=("טיולונים",),
        is_main_color=True, main_color_product_id=None,
        age_from=0, age_to=48, weight="5.8", height="101", width="52", length="88",
        standard="ISIZE", isofix="included",
        video_urls=("https://youtube.com/watch?v=x",), product_manual="http://cdn/m.pdf",
        related_product_ids=(), discount=None, meta_title="", meta_description="",
    )
    base.update(kw)
    return BambinoProduct(**base)


class TestRouting:
    def test_first_mapped_type_wins(self):
        assert route((28,)) == "טיולונים"

    def test_signature_and_unmapped_are_skipped_in_precedence(self):
        # 37 (Signature), 21/42 (unmapped) are ignored; the real type wins.
        assert route((37, 28)) == "טיולונים"
        assert route((21, 20)) == "סל קל"

    def test_new_collections(self):
        assert route((23,)) == "כסאות בטיחות"   # §5.2 new
        assert route((38,)) == "מנשאים"
        assert route((134,)) == "מגדל למידה"

    def test_only_unmapped_or_signature_routes_to_none(self):
        assert route((37,)) is None      # Signature only
        assert route((21,)) is None      # feeding accessories only
        assert route((42, 37)) is None   # hygiene + Signature only
        assert route(()) is None

    def test_is_importable(self):
        assert is_importable(_p(type_ids=(28,))) is True
        assert is_importable(_p(type_ids=(37,))) is False


class TestVendorAndCollections:
    def test_big_three_lowercased(self):
        assert vendor_for("Joie") == "joie"
        assert vendor_for("Infanti") == "infanti"
        assert vendor_for("Graco") == "graco"

    def test_other_brands_kept(self):
        assert vendor_for("Bumprider") == "Bumprider"
        assert vendor_for("Mastela") == "Mastela"

    def test_collections_are_brand_plus_category(self):
        assert collections_for(_p(brand="Graco", type_ids=(28,))) == ("Graco", "טיולונים")

    def test_collections_brand_only_when_uncategorized(self):
        # (only importable products are ingested, but the fn is defensive)
        assert collections_for(_p(brand="Nuna", type_ids=(37,))) == ("Nuna",)


class TestTitle:
    def test_main_color_no_suffix(self):
        assert build_title(_p(title="עגלת", name="Myavo", is_main_color=True)) == "עגלת Myavo"

    def test_color_variant_gets_suffix(self):
        t = build_title(_p(title="עגלת", name="Myavo", is_main_color=False, color="אדום Red"))
        assert t == "עגלת Myavo - אדום Red"


class TestDiscount:
    def test_active_discount_sets_price_and_compare_at(self):
        d = BambinoDiscount(amount=Decimal("349"), start_date=date(2026, 7, 1),
                            end_date=date(2026, 7, 31))
        draft = to_product_draft(_p(price=Decimal("399"), discount=d), WARRANTIES,
                                 today=date(2026, 7, 20))
        v = draft.variants[0]
        assert v.price == Decimal("349")
        assert v.compare_at_price == Decimal("399")

    def test_inactive_discount_keeps_regular_price(self):
        d = BambinoDiscount(amount=Decimal("349"), start_date=date(2026, 8, 1), end_date=None)
        draft = to_product_draft(_p(price=Decimal("399"), discount=d), WARRANTIES,
                                 today=date(2026, 7, 20))
        v = draft.variants[0]
        assert v.price == Decimal("399") and v.compare_at_price is None

    def test_no_discount(self):
        draft = to_product_draft(_p(price=Decimal("399"), discount=None), WARRANTIES,
                                 today=date(2026, 7, 20))
        assert draft.variants[0].compare_at_price is None


class TestDraftFields:
    def _draft(self, **kw):
        return to_product_draft(_p(**kw), WARRANTIES, today=date(2026, 7, 20))

    def test_core_fields(self):
        d = self._draft(brand="Graco", type_ids=(28,))
        assert d.vendor == "graco"
        assert d.product_type == "" and d.tags == ""
        assert d.template_suffix == TEMPLATE
        assert d.status == "draft"
        assert d.body_html == "<p>תיאור מלא</p>"       # description → body_html
        assert d.variants[0].sku == "110104360"
        assert d.variants[0].barcode == "729000"
        assert d.variants[0].inventory_quantity == 5

    def _mf(self, draft, ns, key):
        return next((m for m in draft.metafields if m.namespace == ns and m.key == key), None)

    def test_infoo_structured_attributes(self):
        mf = self._mf(self._draft(), "custom", "infoo")
        vals = [c["children"][0]["value"] for c in json.loads(mf.value)["children"]]
        assert "גיל מומלץ: 0-48 חודשים" in vals
        assert 'משקל: 5.8 ק"ג' in vals
        assert 'מידות (גובה×רוחב×אורך): 101×52×88 ס"מ' in vals
        assert "תקן: ISIZE" in vals
        assert "מערכת איזופיקס: כלול" in vals

    def test_infoo_omitted_when_no_attributes(self):
        d = self._draft(age_from=None, age_to=None, weight="", height="", width="",
                        length="", standard="", isofix="")
        assert self._mf(d, "custom", "infoo") is None

    def test_view_productss_from_specifications_as_list(self):
        mf = self._mf(self._draft(), "custom", "view_productss")
        doc = json.loads(mf.value)
        lst = doc["children"][0]
        assert lst["type"] == "list" and lst["listType"] == "unordered"
        # first list-item keeps the bold run
        item0 = lst["children"][0]["children"]
        assert any(n.get("bold") and "ECE R129" in n["value"] for n in item0)

    def test_securingg_per_brand(self):
        mf = self._mf(self._draft(brand="Joie", type_ids=(28,)), "custom", "securingg")
        text = " ".join(c["children"][0]["value"] for c in json.loads(mf.value)["children"]
                        if c["children"])
        assert "שנתיים" in text

    def test_securingg_fallback_for_siteless_brand(self):
        mf = self._mf(self._draft(brand="Mastela", type_ids=(20,)), "custom", "securingg")
        vals = [c["children"][0]["value"] for c in json.loads(mf.value)["children"]]
        assert vals == list(FALLBACK_WARRANTY_LINES)

    def test_delivery_constant(self):
        from inventory_sync import store_content
        mf = self._mf(self._draft(), "custom", "delivery")
        vals = [c["children"][0]["value"] for c in json.loads(mf.value)["children"]]
        assert vals == list(store_content.TEXTILE_DELIVERY_LINES)

    def test_videos_and_manual(self):
        d = self._draft()
        assert json.loads(self._mf(d, "custom", "videos").value) == ["https://youtube.com/watch?v=x"]
        assert self._mf(d, "custom", "manual").value == "http://cdn/m.pdf"

    def test_videos_manual_omitted_when_absent(self):
        d = self._draft(video_urls=(), product_manual="")
        assert self._mf(d, "custom", "videos") is None
        assert self._mf(d, "custom", "manual") is None

    def test_seo_fallback_to_title_and_description(self):
        d = self._draft(title="עגלת", name="Myavo")
        assert self._mf(d, "global", "title_tag").value == "עגלת Myavo"
        assert self._mf(d, "global", "description_tag").value == "תיאור מלא"

    def test_supplier_reference(self):
        d = self._draft(catalog_number="110104360", price=Decimal("399"))
        assert self._mf(d, "supplier", "sku").value == "110104360"
        assert self._mf(d, "supplier", "price").value == "399"


class TestHtmlToRichText:
    def test_paragraphs_and_bold(self):
        doc = json.loads(html_to_rich_text("<p>שלום <strong>עולם</strong></p>"))
        para = doc["children"][0]
        assert para["type"] == "paragraph"
        assert para["children"] == [
            {"type": "text", "value": "שלום "},
            {"type": "text", "value": "עולם", "bold": True},
        ]

    def test_list_items(self):
        doc = json.loads(html_to_rich_text("<ul><li>א</li><li>ב</li></ul>"))
        lst = doc["children"][0]
        assert lst["type"] == "list"
        assert [li["children"][0]["value"] for li in lst["children"]] == ["א", "ב"]

    def test_plain_text_becomes_one_paragraph(self):
        doc = json.loads(html_to_rich_text("סתם טקסט"))
        assert doc["children"][0]["children"][0]["value"] == "סתם טקסט"

    def test_empty(self):
        assert json.loads(html_to_rich_text(""))["children"] == []
