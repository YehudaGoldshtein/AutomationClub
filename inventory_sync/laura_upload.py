"""Laura Excel → product grouping (PRD §2). SCAFFOLD — not yet implemented.

Types + signatures exist so the test suite imports and runs (tests fail red on
NotImplementedError, not on a collection error). Implement the bodies to turn
the failing tests in tests/test_laura_upload.py green.

Grouping rules (PRD-laura-product-upload.md §2):
  - size token may appear anywhere in `תיאור פריט`, not only at the end;
  - clothing size (NB/XS/0-3…) is ALWAYS a variant, even alone;
  - metric size (34*44, 75/100…) is a variant only if the same base title has
    ≥2 sizes; a lone metric size stays in the title (single-variant product);
  - "ס\"מ" is not a reliable signal — grouping is;
  - typos normalize: 6-3 → 3-6, 3-0 → 0-3.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class LauraRow:
    """One content row from the supplier xlsx (Sheet1)."""
    sku: str                              # מקט
    description: str                      # תיאור פריט (raw; size may be embedded)
    family: str                           # תאור משפחה
    barcode: str | None = None            # ברקוד
    text: str | None = None               # טקסט
    image_url: str | None = None          # link -קישור לתמונה
    recommended_price: Decimal | None = None  # מחיר מומלץ


@dataclass(frozen=True)
class SizeExtraction:
    """Result of splitting a description into title + size."""
    title: str
    size: str | None          # normalized size, or None if no size token found
    kind: str | None          # "clothing" | "metric" | None


@dataclass(frozen=True)
class Variant:
    size: str | None
    sku: str
    barcode: str | None = None
    price: Decimal | None = None


@dataclass(frozen=True)
class ProductGroup:
    """A color = one product; its sizes = variants under option 'מידה'."""
    title: str
    family: str
    variants: tuple[Variant, ...]
    image_urls: tuple[str, ...] = ()
    needs_review: bool = False


def normalize_size(token: str) -> str:
    """Map supplier size typos to canonical form (6-3 → 3-6, 3-0 → 0-3)."""
    raise NotImplementedError


def extract_size(description: str) -> SizeExtraction:
    """Split a `תיאור פריט` into (title without size, size, kind)."""
    raise NotImplementedError


def group_products(rows: list[LauraRow]) -> list[ProductGroup]:
    """Group rows into products (color = product, size = variant) per PRD §2."""
    raise NotImplementedError
