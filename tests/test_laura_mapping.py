"""Failing-first tests for ProductGroup → ProductDraft mapping (PRD §1, §4, Appendix A).

Covers the body_html template, family → sub-category collection lookup, and the
full mapping of a grouped product into a create-ready ProductDraft (vendor const,
product_type/tags = family, price per variant, size option, draft status).
"""
from __future__ import annotations

from decimal import Decimal

from inventory_sync.domain import SKU
from inventory_sync.laura_mapping import (
    CATEGORY_COLLECTION_ID,
    VENDOR,
    build_body_html,
    subcategory_collection,
    to_product_draft,
)
from inventory_sync.laura_upload import LauraRow, ProductGroup, Variant, group_products


class TestBodyHtml:
    def test_injects_title_and_text_rtl(self):
        html = build_body_html("שם המוצר", "תיאור ארוך של המוצר")
        assert 'dir="rtl"' in html
        assert "<h3" in html and "שם המוצר" in html
        assert "<p" in html and "תיאור ארוך של המוצר" in html

    def test_empty_text_omits_paragraph(self):
        html = build_body_html("שם המוצר", None)
        assert "שם המוצר" in html
        assert "<p" not in html  # PRD §4: empty טקסט -> no <p>

    def test_empty_string_text_omits_paragraph(self):
        assert "<p" not in build_body_html("שם", "")


class TestSubcategoryCollection:
    def test_known_families_map_to_collections(self):
        assert subcategory_collection("בגד גוף") == "בגדי גוף לתינוק"
        assert subcategory_collection("חולצה") == "אופנה"
        assert subcategory_collection("מכנס") == "מכנסיים ורגליות לתינוק"
        assert subcategory_collection("שמיכי") == "שמיכי לתינוק"
        assert subcategory_collection("בובות") == "שונות"

    def test_unknown_family_returns_none(self):
        assert subcategory_collection("משפחה שלא קיימת") is None

    def test_strips_whitespace_before_lookup(self):
        # The file has hidden leading/trailing spaces (' חולצה', 'וילונות ', ...).
        assert subcategory_collection(" חולצה ") == "אופנה"
        assert subcategory_collection("וילונות ") == "חדר תינוק"
        assert subcategory_collection("מוצרי האכלה ") == "מוצרי האכלה"

    def test_combined_sheet_family_is_one_exact_key(self):
        assert subcategory_collection("סדינים מטר/מעבר") == "סדינים למיטת תינוק"
        assert subcategory_collection("סל כביסה-סל אחסון") == "אחסון ואביזרים"

    def test_plural_blanket_family_keys(self):
        assert subcategory_collection("שמיכות טטרא") == "שמיכות לתינוק"
        assert subcategory_collection("שמיכת פוך") == "שמיכות לתינוק"

    def test_formerly_inferred_families_now_mapped(self):
        # All 82 families approved by the owner (FINAL map) — previously-⚠️ ones map now.
        assert subcategory_collection("שמיכות סרוגות") == "שמיכות לתינוק"
        assert subcategory_collection("סינר בנדנה") == "סינרים"
        assert subcategory_collection("סל אחסון") == "אחסון ואביזרים"
        assert subcategory_collection("אוהל טיפי") == "חדר תינוק"


class TestConstants:
    def test_category_collection_id_is_the_textile_collection(self):
        assert CATEGORY_COLLECTION_ID == "477920559358"

    def test_vendor_is_laura(self):
        assert VENDOR == "לורה סוויסרה | laura swisra"


class TestToProductDraft:
    def _group(self) -> ProductGroup:
        return ProductGroup(
            title="בגד גוף לבן",
            family="בגד גוף",
            variants=(
                Variant(size="NB", sku="A-1", barcode="111", price=Decimal("99")),
                Variant(size="0-3", sku="A-2", barcode="222", price=Decimal("99")),
            ),
            image_urls=("http://img/1.jpg",),
            body_text="כותנה אורגנית",
        )

    def test_static_fields(self):
        d = to_product_draft(self._group())
        assert d.vendor == VENDOR
        assert d.product_type == "בגד גוף"
        assert d.tags == "בגד גוף"
        assert d.option_name == "מידה"
        assert d.status == "draft"
        assert d.image_urls == ("http://img/1.jpg",)

    def test_body_html_built_from_title_and_text(self):
        d = to_product_draft(self._group())
        assert "בגד גוף לבן" in d.body_html
        assert "כותנה אורגנית" in d.body_html

    def test_variants_carry_size_price_barcode(self):
        d = to_product_draft(self._group())
        by_sku = {v.sku: v for v in d.variants}
        assert set(by_sku) == {SKU("A-1"), SKU("A-2")}
        assert by_sku[SKU("A-1")].option_value == "NB"
        assert by_sku[SKU("A-1")].price == Decimal("99")
        assert by_sku[SKU("A-1")].barcode == "111"

    def test_variants_are_created_tracked(self):
        # Created tracked (stock 0) so the hourly scrape can write real stock.
        d = to_product_draft(self._group())
        assert all(v.track_inventory for v in d.variants)


class TestPipeline:
    """group_products → to_product_draft carries text + price end to end."""

    def test_grouped_then_mapped(self):
        rows = [
            LauraRow(sku="A-1", description="בגד גוף לבן NB", family="בגד גוף",
                     text="כותנה אורגנית", recommended_price=Decimal("99")),
            LauraRow(sku="A-2", description="בגד גוף לבן 0-3", family="בגד גוף",
                     text="כותנה אורגנית", recommended_price=Decimal("99")),
        ]
        [group] = group_products(rows)
        d = to_product_draft(group)
        assert d.title == "בגד גוף לבן"
        assert "כותנה אורגנית" in d.body_html      # body_text populated by group_products
        assert len(d.variants) == 2
        assert all(v.price == Decimal("99") for v in d.variants)
