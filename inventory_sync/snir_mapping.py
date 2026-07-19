"""Map a SnirProduct into a create-ready ProductDraft.

Source of truth is MAPPING-snir-categories.md (LOCKED). Highlights vs Segal:
  - categories routed by **id** with precedence ("room wins"), not slug/name;
  - `body_html` ← short_description; `custom.view_productss` ← API description;
  - the only scraped tab is `tech_details` → `custom.infoo`;
  - warranty (12mo) + delivery are **constants**; delivery gets a Studio Boutique
    price-list block for those products;
  - `template_suffix` derived from product_type; non-furniture → no template;
  - stock is binary (no counts) → created in-stock with a default quantity.

See tests/test_snir_mapping.py.
"""
from __future__ import annotations

import html as _html
import json
from dataclasses import dataclass

from bs4 import BeautifulSoup

from inventory_sync import store_content
from inventory_sync.domain import SKU, Metafield, ProductDraft, VariantSpec
from inventory_sync.log import Logger, get
from inventory_sync.snir_source import SnirProduct, tab_html

VENDOR = "שניר | snir"
RICH_TEXT = "rich_text_field"

# Constants (MAPPING §5, §8) — not from the site.
WARRANTY_TEXT = "כל המוצרים מגיעים עם 12 חודשי אחריות לפי התקנון."
STUDIO_BOUTIQUE_BLOCK: tuple[str, ...] = (
    "מחירון הובלה והרכבה קולקציה סטודיו בוטיק:",
    'שידה- 400 ש"ח',
    "מיטה- 350 ש\"ח",
    "שידה + מיטה – 600 ש\"ח",
)

TEMPLATE_BEDS = "furniture-beds"
TEMPLATE_FURNITURE = "furniture-product-page"
_BEDS_PT = "מיטות תינוק"

# In-stock products are created with this quantity (Snir exposes no count; stock is
# binary). Matches the binary restock quantity used elsewhere.
DEFAULT_STOCK_QTY = 10


@dataclass(frozen=True)
class Routing:
    product_type: str            # "" for non-furniture lines
    collection_title: str
    template_suffix: str | None  # None for non-furniture (store convention)


# Precedence-ordered routes (MAPPING §2). First whose id-set intersects wins.
# Rows 1–6 furniture; 7–10 non-furniture (empty product_type, no template).
_ROUTES: tuple[tuple[frozenset[int], Routing], ...] = (
    (frozenset({118, 137, 136, 135}), Routing("חדרי תינוקות", "חדרי תינוקות", TEMPLATE_FURNITURE)),
    (frozenset({126, 140, 139, 141}), Routing(_BEDS_PT, "מיטות תינוק", TEMPLATE_BEDS)),
    (frozenset({128}), Routing("ארונות לחדרי ילדים", "ארונות לחדרי ילדים", TEMPLATE_FURNITURE)),
    (frozenset({125, 134, 133, 132}), Routing("שידות החתלה", "שידות החתלה", TEMPLATE_FURNITURE)),
    (frozenset({120}), Routing("מזרונים לתינוק", "מזרונים לתינוק", TEMPLATE_FURNITURE)),
    (frozenset({122}), Routing("כורסאות הנקה", "כורסאות הנקה", TEMPLATE_FURNITURE)),
    (frozenset({130}), Routing("", "טיולונים", None)),
    (frozenset({117, 131}), Routing("", "עגלות תינוק", None)),
    (frozenset({121}), Routing("", "כסאות אוכל", None)),
    (frozenset({419}), Routing("", "אמבטיות ואביזריהם לתינוק", None)),
)

# 142 (snir-kids): closets there are also in 128 (caught above); products *only* in
# 142 are kids-beds → route to beds as a fallback (MAPPING §3.1).
_KIDS_ID = 142

# Not imported (owner decision, MAPPING §2). Only matters when nothing else matches:
# a bed also tagged spare-parts is still imported as a bed.
EXCLUDE_IDS = frozenset({129, 420})


def route(category_ids) -> Routing | None:
    """Route a product's category ids to product_type/collection/template.

    Returns None when the product has no importable category (only marketing or
    exclude ids) — i.e. it is ignored, not onboarded.
    """
    ids = set(category_ids)
    for route_ids, routing in _ROUTES:
        if ids & route_ids:
            return routing
    if _KIDS_ID in ids:
        return Routing(_BEDS_PT, "מיטות תינוק", TEMPLATE_BEDS)
    return None


def is_importable(product: SnirProduct) -> bool:
    return route(product.category_ids) is not None


def decode_entities(text: str) -> str:
    return _html.unescape(text or "").strip()


def _lines(html: str) -> list[str]:
    """Flatten HTML to text lines: one per <p>/<br>, tags stripped."""
    soup = BeautifulSoup(html or "", "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    blocks = soup.find_all("p") or [soup]
    lines: list[str] = []
    for block in blocks:
        for raw in block.get_text().split("\n"):
            line = raw.strip()
            if line:
                lines.append(line)
    return lines


def _rich_text_doc(lines: list[str] | tuple[str, ...]) -> str:
    root = {
        "type": "root",
        "children": [
            {"type": "paragraph", "children": [{"type": "text", "value": line}]}
            for line in lines
        ],
    }
    return json.dumps(root, ensure_ascii=False)


def html_to_rich_text(html: str) -> str:
    return _rich_text_doc(_lines(html))


def _plain_text(html: str) -> str:
    return " ".join(_lines(html))


def is_studio_boutique(product: SnirProduct) -> bool:
    """Detect Studio Boutique products by name/description (MAPPING §8)."""
    name = (product.name or "").upper()
    desc = product.description_html or ""
    return "STUDIO BOUTIQUE" in name or "סטודיו בוטיק" in desc or "סטודיו בוטיק" in product.name


def delivery_lines(product: SnirProduct) -> tuple[str, ...]:
    """Furniture delivery boilerplate (= Segal's), with the Studio Boutique price
    list injected before the final "read more" line when applicable."""
    base = list(store_content.FURNITURE_DELIVERY_LINES)
    if is_studio_boutique(product):
        base = base[:-1] + list(STUDIO_BOUTIQUE_BLOCK) + base[-1:]
    return tuple(base)


def _metafields(product: SnirProduct, title: str) -> tuple[Metafield, ...]:
    out: list[Metafield] = []

    tech = tab_html(product.tabs, "tech_details")
    if _lines(tech):
        out.append(Metafield("custom", "infoo", RICH_TEXT, html_to_rich_text(tech)))
    if _lines(product.description_html):
        out.append(Metafield("custom", "view_productss", RICH_TEXT,
                             html_to_rich_text(product.description_html)))
    out.append(Metafield("custom", "securingg", RICH_TEXT, _rich_text_doc([WARRANTY_TEXT])))
    out.append(Metafield("custom", "delivery", RICH_TEXT, _rich_text_doc(delivery_lines(product))))

    out.append(Metafield("global", "title_tag", "single_line_text_field", title))
    out.append(Metafield("global", "description_tag", "multi_line_text_field",
                         _plain_text(product.description_html)))

    # supplier.* — internal reference / sync anchors
    if product.price is not None:
        out.append(Metafield("supplier", "price", "number_decimal", str(product.price)))
    out.append(Metafield("supplier", "sku", "single_line_text_field", product.sku))
    out.append(Metafield("supplier", "url", "url", product.permalink))
    return tuple(out)


def to_product_draft(product: SnirProduct, logger: Logger | None = None,
                     title_suffix: str = "") -> ProductDraft:
    """Build a draft product from a SnirProduct.

    `title_suffix` lets the ingest append a variation value (e.g. "130/70") when a
    variable product is split into separate single-variant products.
    """
    log = logger or get("snir.mapping")
    routing = route(product.category_ids) or Routing("", "", None)
    title = decode_entities(product.name)
    if title_suffix:
        title = f"{title} {title_suffix}".strip()

    variant = VariantSpec(
        sku=SKU(product.sku),
        option_value=None,                    # single-variant (splits are separate products)
        price=product.price,
        inventory_quantity=DEFAULT_STOCK_QTY,  # in-stock at ingest (OOS is not onboarded)
    )
    return ProductDraft(
        title=title,
        body_html=product.short_description_html,
        vendor=VENDOR,
        product_type=routing.product_type,
        tags=routing.product_type,
        variants=(variant,),
        image_urls=product.image_urls,
        status="draft",
        metafields=_metafields(product, title),
        template_suffix=routing.template_suffix,
    )


def collections_for(product: SnirProduct) -> tuple[str, ...]:
    routing = route(product.category_ids)
    return (routing.collection_title,) if routing and routing.collection_title else ()
