"""Pure-parser tests for the Bambino source layer (Phase 0)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from inventory_sync.bambino_source import (
    parse_api_product,
    parse_products,
    parse_warranties,
)

# Trimmed real-shape master-feed product (Infanti I-Dream Base).
API = {
    "id": 190850,
    "catalogNumber": "190850000",
    "title": "בסיס איזופיקס מסתובב",
    "name": "I-Dream Base",
    "color": "שחור Black",
    "brand": "Infanti",
    "description": "שדרגו את חוויית הנסיעה עם בסיס ה-I Dream Base.",
    "specifications": "<ul>\r\n <li>תקן אירופאי מתקדם <strong>ECE R129</strong></li>\r\n</ul>",
    "price": 399,
    "quantity": 153,
    "barcode": "7290111690000",
    "images": ["https://cdn/a.jpg", "https://cdn/b.jpeg"],
    "types": [{"id": 30, "name": "בסיסים לסלקלים"}, {"id": 37, "name": "Signature"}],
    "isMainColor": True,
    "mainColorProductId": None,
    "age": {"from": 0, "to": 13},
    "weight": 5.8,
    "height": 101.6,
    "width": 52,
    "length": 88,
    "standard": "ISIZE",
    "isofix": "included",
    "video": "https://youtube.com/watch?v=aaa",
    "videos": [{"url": "https://youtube.com/watch?v=bbb", "title": "x"},
               {"url": "https://youtube.com/watch?v=aaa", "title": "dup"}],
    "productManual": "https://cdn/manual.pdf",
    "relatedProducts": [973240],
    "discount": {"type": "overwrite", "productIds": [190850], "amount": 349,
                 "startDate": "07/05/2026", "endDate": "07/31/2026"},
    "metaTitle": "",
    "metaDescription": "",
}


class TestParseApiProduct:
    def test_core_fields(self):
        p = parse_api_product(API)
        assert p.id == 190850
        assert p.catalog_number == "190850000"
        assert p.title == "בסיס איזופיקס מסתובב"
        assert p.name == "I-Dream Base"
        assert p.color == "שחור Black"
        assert p.brand == "Infanti"
        assert p.barcode == "7290111690000"
        assert p.price == Decimal("399")
        assert p.quantity == 153
        assert p.in_stock is True

    def test_price_zero_is_none(self):
        assert parse_api_product({**API, "price": 0}).price is None

    def test_out_of_stock_when_zero_quantity(self):
        p = parse_api_product({**API, "quantity": 0})
        assert p.quantity == 0 and p.in_stock is False

    def test_types_and_images(self):
        p = parse_api_product(API)
        assert p.type_ids == (30, 37)
        assert p.type_names == ("בסיסים לסלקלים", "Signature")
        assert p.image_urls == ("https://cdn/a.jpg", "https://cdn/b.jpeg")

    def test_structured_attributes(self):
        p = parse_api_product(API)
        assert (p.age_from, p.age_to) == (0, 13)
        assert p.weight == "5.8"       # float kept
        assert p.height == "101.6"
        assert p.width == "52"         # int-valued float → no trailing .0
        assert p.length == "88"
        assert p.standard == "ISIZE"
        assert p.isofix == "included"

    def test_zero_dimensions_become_empty(self):
        p = parse_api_product({**API, "weight": 0, "height": 0.0, "width": "", "length": None})
        assert p.weight == "" and p.height == "" and p.width == "" and p.length == ""

    def test_videos_merged_and_deduped(self):
        # single `video` first, then videos[].url, dropping the duplicate.
        assert parse_api_product(API).video_urls == (
            "https://youtube.com/watch?v=aaa",
            "https://youtube.com/watch?v=bbb",
        )

    def test_manual_and_related(self):
        p = parse_api_product(API)
        assert p.product_manual == "https://cdn/manual.pdf"
        assert p.related_product_ids == (973240,)

    def test_discount_parsed_with_dates(self):
        d = parse_api_product(API).discount
        assert d is not None
        assert d.amount == Decimal("349")
        assert d.start_date == date(2026, 7, 5)
        assert d.end_date == date(2026, 7, 31)

    def test_discount_ignored_when_not_overwrite(self):
        assert parse_api_product({**API, "discount": {"type": "percent", "amount": 10}}).discount is None
        assert parse_api_product({**API, "discount": None}).discount is None

    def test_missing_fields_are_safe(self):
        p = parse_api_product({"id": 1})
        assert p.catalog_number == "" and p.price is None and p.quantity == 0
        assert p.type_ids == () and p.image_urls == () and p.video_urls == ()
        assert p.discount is None and p.in_stock is False


class TestColorGrouping:
    def test_main_color_is_its_own_group(self):
        assert parse_api_product(API).group_id == 190850  # main → own id

    def test_variant_points_at_main(self):
        p = parse_api_product({**API, "id": 190851, "isMainColor": False,
                               "mainColorProductId": 190850})
        assert p.is_main_color is False and p.group_id == 190850


class TestDiscountWindow:
    def _d(self, start, end):
        return parse_api_product(
            {**API, "discount": {"type": "overwrite", "amount": 10,
                                 "startDate": start, "endDate": end}}
        ).discount

    def test_active_within_window(self):
        assert self._d("07/05/2026", "07/31/2026").active_on(date(2026, 7, 20)) is True

    def test_inactive_before_and_after(self):
        d = self._d("07/05/2026", "07/31/2026")
        assert d.active_on(date(2026, 7, 1)) is False
        assert d.active_on(date(2026, 8, 1)) is False

    def test_open_ended_dates(self):
        d = self._d(None, None)
        assert d.active_on(date(2026, 1, 1)) is True


class TestParseProductsAndWarranties:
    def test_parse_products(self):
        prods = parse_products({"products": [API, {**API, "id": 2, "catalogNumber": "2"}]})
        assert len(prods) == 2 and prods[1].catalog_number == "2"

    def test_parse_warranties_keyed_by_brand(self):
        master = {"websites": [
            {"brand": "Joie", "policies": {"warranty": "<p>שנתיים</p>"}},
            {"brand": "Graco", "policies": {"warranty": "<p>שנה</p>"}},
            {"brand": "Empty", "policies": {"warranty": "  "}},
            {"brand": "NoPol"},
        ]}
        w = parse_warranties(master)
        assert w == {"Joie": "<p>שנתיים</p>", "Graco": "<p>שנה</p>"}
