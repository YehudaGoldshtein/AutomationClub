"""Laura Excel → product grouping (PRD §2).

Turns supplier rows into grouped products: color = product, size = variant under
option "מידה". See tests/test_laura_upload.py for the pinned behavior.

Grouping rules (PRD-laura-product-upload.md §2):
  - size token may appear anywhere in `תיאור פריט`, not only at the end;
  - clothing size (NB/XS/0-3…) is ALWAYS a variant, even alone;
  - metric size (34*44, 75/100…) is a variant only if the same base title has
    ≥2 sizes; a lone metric size stays in the title (single-variant product);
  - "ס\"מ" is not a reliable signal — grouping is;
  - typos normalize: 6-3 → 3-6, 3-0 → 0-3.
"""
from __future__ import annotations

import re
from collections import OrderedDict
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
    body_text: str | None = None      # first non-empty `טקסט` among the grouped rows
    needs_review: bool = False


# --- Size lexicon (PRD §2.1) ---
# Alpha clothing sizes are exactly NB / XS; every other clothing size is a
# month-range (0-3, 6-9M, …), matched structurally below.
_CLOTHING_ALPHA = {"NB", "XS"}
# The "ס\"מ" suffix that trails a metric size — several quote glyphs seen in data.
_CM_TOKENS = {'ס"מ', "ס״מ", "ס”מ", "סמ"}
_TYPO_MAP = {"6-3": "3-6", "3-0": "0-3"}
_RANGE_RE = re.compile(r"^(\d+)-(\d+)(M?)$")


def normalize_size(token: str) -> str:
    """Map supplier size typos to canonical form (6-3 → 3-6, 3-0 → 0-3)."""
    return _TYPO_MAP.get(token, token)


def _size_kind(token: str) -> str | None:
    """Classify a token as a clothing size, a metric size, or not a size.

    Clothing vs metric (PRD §2.2): a numeric range whose both ends are ≤24 is
    months (clothing); metric sizes use '*' or '/' separators or larger ranges.
    "ס\"מ" is deliberately NOT used as a signal — it self-contradicts in the data.
    """
    t = normalize_size(token)
    if t in _CLOTHING_ALPHA:
        return "clothing"
    if "*" in t or "/" in t:
        return "metric"
    m = _RANGE_RE.match(t)
    if m:
        if m.group(3) == "M":
            return "clothing"
        low, high = int(m.group(1)), int(m.group(2))
        return "clothing" if (low <= 24 and high <= 24) else "metric"
    return None


def extract_size(description: str) -> SizeExtraction:
    """Split a `תיאור פריט` into (title without size, size, kind).

    Removes the size token wherever it sits, plus an adjacent "ס\"מ" marker, and
    collapses the whitespace it left behind.
    """
    tokens = description.split()
    for i, tok in enumerate(tokens):
        kind = _size_kind(tok)
        if kind is None:
            continue
        remaining = tokens[:i] + tokens[i + 1:]
        # A "ס\"מ" that trailed the size is now at position i in `remaining`.
        if i < len(remaining) and remaining[i] in _CM_TOKENS:
            remaining = remaining[:i] + remaining[i + 1:]
        return SizeExtraction(title=" ".join(remaining), size=normalize_size(tok), kind=kind)
    return SizeExtraction(title=description, size=None, kind=None)


def _variant(row: LauraRow, size: str | None) -> Variant:
    return Variant(size=size, sku=row.sku, barcode=row.barcode, price=row.recommended_price)


def _make_group(title: str, members: list[tuple[LauraRow, SizeExtraction]], variants: tuple[Variant, ...]) -> ProductGroup:
    family = members[0][0].family
    # De-duped, order-preserving image list.
    images = tuple(OrderedDict.fromkeys(r.image_url for r, _ in members if r.image_url))
    return ProductGroup(title=title, family=family, variants=variants, image_urls=images)


def group_products(rows: list[LauraRow]) -> list[ProductGroup]:
    """Group rows into products (color = product, size = variant) per PRD §2."""
    buckets: "OrderedDict[str, list[tuple[LauraRow, SizeExtraction]]]" = OrderedDict()
    for row in rows:
        ex = extract_size(row.description)
        buckets.setdefault(ex.title, []).append((row, ex))

    groups: list[ProductGroup] = []
    for title, members in buckets.items():
        if len(members) >= 2:
            # Same base with ≥2 sizes → real variants (clothing or metric).
            variants = tuple(_variant(row, ex.size) for row, ex in members)
            groups.append(_make_group(title, members, variants))
            continue

        row, ex = members[0]
        if ex.kind == "clothing":
            # Clothing size is always a variant, even alone.
            groups.append(_make_group(title, members, (_variant(row, ex.size),)))
        else:
            # Lone metric size (or no size) → size stays in the name; single variant.
            groups.append(_make_group(row.description, members, (_variant(row, None),)))
    return groups
