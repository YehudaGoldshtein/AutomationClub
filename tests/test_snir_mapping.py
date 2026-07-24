"""Tests for Snir mapping: id routing, precedence, field assembly, constants."""
from __future__ import annotations

import json
from decimal import Decimal

from inventory_sync.log import get
from inventory_sync.snir_mapping import (
    DEFAULT_STOCK_QTY,
    VENDOR,
    WARRANTY_TEXT,
    collections_for,
    is_importable,
    is_studio_boutique,
    route,
    shares_variant_sku,
    to_product_draft,
)
from inventory_sync.snir_source import SnirProduct, SnirTab

LOG = get("test")


def _p(category_ids=(126,), name="מיטת תינוק רוני", sku="bed-1",
       short="<p>גוף</p>", desc="<p>תיאור מלא</p>", price=Decimal("1690"),
       in_stock=True, images=("http://img/1.jpg",), tabs=()) -> SnirProduct:
    return SnirProduct(
        sku=sku, name=name, short_description_html=short, description_html=desc,
        price=price, in_stock=in_stock, image_urls=images,
        category_ids=tuple(category_ids), permalink=f"http://snir/p/{sku}/", tabs=tabs,
    )


class TestSharesVariantSku:
    def test_variable_with_2plus_variations_is_flagged(self):
        p = SnirProduct(
            sku="var-1", name="x", short_description_html="", description_html="",
            price=Decimal("100"), in_stock=True, image_urls=(), category_ids=(126,),
            permalink="", wc_type="variable", variation_count=3)
        assert shares_variant_sku(p) is True

    def test_single_variation_and_simple_are_not_flagged(self):
        base = dict(sku="s", name="x", short_description_html="", description_html="",
                    price=Decimal("100"), in_stock=True, image_urls=(), category_ids=(126,),
                    permalink="")
        assert shares_variant_sku(SnirProduct(**base, wc_type="variable", variation_count=1)) is False
        assert shares_variant_sku(SnirProduct(**base, wc_type="simple", variation_count=0)) is False


class TestRouting:
    def test_room_wins_precedence(self):
        # product in both a room (118) and a dresser (125) → room wins.
        r = route((125, 118, 138))
        assert r.product_type == "חדרי תינוקות"

    def test_beds_get_furniture_beds_template(self):
        r = route((126,))
        assert r.product_type == "מיטות תינוק" and r.template_suffix == "furniture-beds"

    def test_other_furniture_gets_generic_template(self):
        assert route((125,)).template_suffix == "furniture-product-page"

    def test_142_closets_route_via_128(self):
        # snir-kids closets are also in 128 → ארונות (not rooms; they're not in a room cat).
        r = route((142, 128))
        assert r.product_type == "ארונות לחדרי ילדים"

    def test_142_only_is_kids_bed(self):
        # only in 142 (+ marketing) → beds fallback.
        r = route((142, 138))
        assert r.product_type == "מיטות תינוק" and r.template_suffix == "furniture-beds"

    def test_non_furniture_empty_type_no_template(self):
        r = route((130,))  # strollers
        assert r.product_type == "" and r.template_suffix is None
        assert r.collection_title == "טיולונים"

    def test_excluded_only_returns_none(self):
        assert route((129,)) is None           # spare parts only
        assert route((420, 138)) is None        # accessories + marketing only
        assert route((138, 119)) is None        # marketing only

    def test_furniture_tagged_spare_parts_still_imports(self):
        # a bed also tagged spare-parts (129) is still imported as a bed.
        assert route((126, 129)).product_type == "מיטות תינוק"

    def test_is_importable(self):
        assert is_importable(_p((126,))) is True
        assert is_importable(_p((129,))) is False


class TestDraftFields:
    def test_core_fields(self):
        d = to_product_draft(_p(), LOG)
        assert d.vendor == VENDOR
        assert d.status == "draft"
        assert d.product_type == "מיטות תינוק"
        assert d.template_suffix == "furniture-beds"
        assert d.body_html == "<p>גוף</p>"           # from short_description
        assert d.variants[0].price == Decimal("1690")
        assert d.variants[0].inventory_quantity == DEFAULT_STOCK_QTY

    def test_title_suffix_appended(self):
        d = to_product_draft(_p(name="מזרן"), LOG, title_suffix="130/70")
        assert d.title == "מזרן 130/70"

    def test_collections_for(self):
        assert collections_for(_p((125,))) == ("שידות החתלה",)
        assert collections_for(_p((129,))) == ()

    def _mf(self, draft, ns, key):
        return next((m for m in draft.metafields if m.namespace == ns and m.key == key), None)

    def test_description_goes_to_view_productss(self):
        d = to_product_draft(_p(desc="<p>שורה א</p><p>שורה ב</p>"), LOG)
        mf = self._mf(d, "custom", "view_productss")
        values = [c["children"][0]["value"] for c in json.loads(mf.value)["children"]]
        assert values == ["שורה א", "שורה ב"]

    def test_tech_details_goes_to_infoo(self):
        tabs = (SnirTab("tech_details", "רוחב 100<br>חומר MDF"),
                SnirTab("description", "<p>ignore</p>"))
        mf = self._mf(to_product_draft(_p(tabs=tabs), LOG), "custom", "infoo")
        values = [c["children"][0]["value"] for c in json.loads(mf.value)["children"]]
        assert values == ["רוחב 100", "חומר MDF"]

    def test_missing_tech_details_omits_infoo(self):
        assert self._mf(to_product_draft(_p(tabs=()), LOG), "custom", "infoo") is None

    def test_warranty_constant(self):
        mf = self._mf(to_product_draft(_p(), LOG), "custom", "securingg")
        values = [c["children"][0]["value"] for c in json.loads(mf.value)["children"]]
        assert values == [WARRANTY_TEXT]

    def test_supplier_reference_metafields(self):
        d = to_product_draft(_p(sku="cl001", price=Decimal("990")), LOG)
        assert self._mf(d, "supplier", "sku").value == "cl001"
        assert self._mf(d, "supplier", "price").value == "990"
        assert self._mf(d, "supplier", "url").value.endswith("/cl001/")


class TestStudioBoutiqueDelivery:
    def _delivery_lines(self, draft):
        mf = next(m for m in draft.metafields if m.key == "delivery")
        return [c["children"][0]["value"] for c in json.loads(mf.value)["children"]]

    def test_regular_delivery_has_no_price_block(self):
        lines = self._delivery_lines(to_product_draft(_p(name="מיטה רגילה"), LOG))
        assert not any("מחירון" in ln for ln in lines)

    def test_studio_boutique_detected_and_injected(self):
        p = _p(name="שידה כנרת STUDIO BOUTIQUE COLLECTION")
        assert is_studio_boutique(p) is True
        lines = self._delivery_lines(to_product_draft(p, LOG))
        assert any("מחירון הובלה והרכבה קולקציה סטודיו בוטיק" in ln for ln in lines)
        # block injected before the final "read more" line
        assert "לקבלת פירוט" in lines[-1]

    def test_studio_boutique_detected_via_description(self):
        assert is_studio_boutique(_p(name="שידה", desc="<p>קולקציית סטודיו בוטיק</p>")) is True
