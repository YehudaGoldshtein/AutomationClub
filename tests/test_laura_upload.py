"""Failing-first tests for Laura Excel → product grouping (PRD §2).

Pins the size-extraction + product-grouping rules that turn supplier rows into
grouped products (color = product, size = variant). Pure logic, no I/O.

Target module `inventory_sync.laura_upload` does not exist yet — these are the
spec. Rules under test (PRD-laura-product-upload.md §2):
  - size token may sit anywhere in `תיאור פריט`, not only the end;
  - clothing size (NB/XS/0-3…) is ALWAYS a variant, even if it appears once;
  - metric size (34*44, 75/100…) is a variant ONLY if the same base title has
    ≥2 sizes; a lone metric size stays in the title (single-variant product);
  - "ס\"מ" is NOT a reliable signal — grouping is;
  - supplier typos normalize: 6-3 → 3-6, 3-0 → 0-3.
"""
from __future__ import annotations

from decimal import Decimal

from inventory_sync.laura_upload import (
    LauraRow,
    ProductGroup,
    extract_size,
    group_products,
    normalize_size,
)


def _row(sku: str, description: str, family: str = "משפחה", price: str = "100") -> LauraRow:
    return LauraRow(
        sku=sku,
        description=description,
        family=family,
        barcode=None,
        text=None,
        image_url=None,
        recommended_price=Decimal(price),
    )


def _titles(groups: list[ProductGroup]) -> set[str]:
    return {g.title for g in groups}


def _by_title(groups: list[ProductGroup], title: str) -> ProductGroup:
    [g] = [g for g in groups if g.title == title]
    return g


class TestExtractSize:
    """The pure string split: description -> (title without size, size, kind)."""

    def test_clothing_size_at_end(self):
        ex = extract_size("מכנסיים סרוגים חום XS")
        assert ex.title == "מכנסיים סרוגים חום"
        assert ex.size == "XS"
        assert ex.kind == "clothing"

    def test_clothing_size_in_middle(self):
        # PRD §2: size is NB, sitting in the MIDDLE of the string.
        ex = extract_size("אוברול קצר עם קשירה NB טרי ניוד")
        assert ex.title == "אוברול קצר עם קשירה טרי ניוד"
        assert ex.size == "NB"
        assert ex.kind == "clothing"

    def test_metric_size_with_cm_suffix_is_stripped(self):
        # Metric token + the adjacent "ס\"מ" both come out of the title.
        ex = extract_size('ציפית לכרית 34*44 ס"מ אופוואיט COZY')
        assert ex.title == "ציפית לכרית אופוואיט COZY"
        assert ex.size == "34*44"
        assert ex.kind == "metric"

    def test_no_size_token_returns_full_title(self):
        ex = extract_size("סט מצעים למיטת תינוק נקודות רקום לבן")
        assert ex.title == "סט מצעים למיטת תינוק נקודות רקום לבן"
        assert ex.size is None
        assert ex.kind is None

    def test_collapses_whitespace_left_by_removed_size(self):
        ex = extract_size("אוברול קצר עם קשירה NB טרי ניוד")
        assert "  " not in ex.title


class TestNormalizeSize:
    """Supplier typo normalization (PRD §2.1)."""

    def test_6_3_normalizes_to_3_6(self):
        assert normalize_size("6-3") == "3-6"

    def test_3_0_normalizes_to_0_3(self):
        assert normalize_size("3-0") == "0-3"

    def test_valid_size_unchanged(self):
        assert normalize_size("9-12") == "9-12"
        assert normalize_size("XS") == "XS"


class TestGroupingClothing:
    def test_two_sizes_same_base_become_one_product(self):
        groups = group_products([
            _row("A-1", "אוברול קצר עם קשירה NB טרי ניוד"),
            _row("A-2", "אוברול קצר עם קשירה 0-3 טרי ניוד"),
        ])
        assert len(groups) == 1
        g = groups[0]
        assert g.title == "אוברול קצר עם קשירה טרי ניוד"
        assert {v.size for v in g.variants} == {"NB", "0-3"}
        assert {v.sku for v in g.variants} == {"A-1", "A-2"}

    def test_single_clothing_size_is_still_a_variant(self):
        # Clothing size is ALWAYS a variant, even alone.
        groups = group_products([_row("B-1", "מכנסיים סרוגים חום XS")])
        assert len(groups) == 1
        g = groups[0]
        assert g.title == "מכנסיים סרוגים חום"
        assert [v.size for v in g.variants] == ["XS"]

    def test_different_colors_are_different_products(self):
        groups = group_products([
            _row("C-1", "בגד גוף לבן NB"),
            _row("C-2", "בגד גוף בז' NB"),
        ])
        assert _titles(groups) == {"בגד גוף לבן", "בגד גוף בז'"}
        assert all(len(g.variants) == 1 for g in groups)


class TestGroupingMetric:
    def test_lone_metric_size_stays_in_title_single_variant(self):
        # PRD §2 rule 4: a metric size with only ONE occurrence stays in the name;
        # the product is single-variant (size option = None).
        desc = 'ציפית לכרית 34*44 ס"מ אופוואיט COZY'
        groups = group_products([_row("D-1", desc)])
        assert len(groups) == 1
        g = groups[0]
        assert g.title == desc
        assert [v.size for v in g.variants] == [None]

    def test_two_metric_sizes_same_base_become_variants(self):
        # ≥2 metric sizes on the same base -> real variants, size stripped from title.
        groups = group_products([
            _row("E-1", 'ציפית לכרית 34*44 ס"מ אופוואיט COZY'),
            _row("E-2", 'ציפית לכרית 70*50 ס"מ אופוואיט COZY'),
        ])
        assert len(groups) == 1
        g = groups[0]
        assert g.title == "ציפית לכרית אופוואיט COZY"
        assert {v.size for v in g.variants} == {"34*44", "70*50"}


class TestGroupingNoSize:
    def test_no_size_product_is_single_variant_full_title(self):
        desc = "סט מצעים למיטת תינוק נקודות רקום לבן"
        groups = group_products([_row("F-1", desc)])
        assert len(groups) == 1
        g = groups[0]
        assert g.title == desc
        assert [v.size for v in g.variants] == [None]

    def test_family_carried_onto_group(self):
        groups = group_products([_row("G-1", "בובה", family="בובות")])
        assert groups[0].family == "בובות"
