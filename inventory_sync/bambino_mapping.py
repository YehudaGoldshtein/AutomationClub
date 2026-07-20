"""Map a BambinoProduct into a create-ready ProductDraft (PRD §2–§8).

Owns: brand→vendor, the `types`-id → collection routing (§5, incl. the 3 new
collections + Signature/unmapped skips), discount → price/compare_at (§2), the
HTML → rich_text conversion (§3, lists + bold preserved), and field assembly.

Bambino specifics vs Segal:
  - one master feed, no scrape; every color is a separate product (§4);
  - `product_type` is always empty, `template_suffix` is always `bambino` (§2, §6);
  - `custom.infoo` = structured attrs; `custom.view_productss` = specifications;
  - warranty is per-brand (§7); delivery reuses the textile boilerplate (§8);
  - vendor is per-brand — Bambino spans 9 vendors under one feed.

See tests/test_bambino_mapping.py.
"""
from __future__ import annotations

import html as _html
import json
import re
from datetime import date

from bs4 import BeautifulSoup, NavigableString, Tag

from inventory_sync import store_content
from inventory_sync.bambino_source import BambinoProduct
from inventory_sync.domain import SKU, Metafield, ProductDraft, VariantSpec
from inventory_sync.log import Logger, get

RICH_TEXT = "rich_text_field"
TEMPLATE = "bambino"  # every Bambino product uses templates/product.bambino.json (§6)

# --- brand → vendor (§2): the three big brands lowercase to match the store's
# existing vendor tags; everyone else keeps their display name.
_LOWERCASE_VENDORS = {"Joie", "Infanti", "Graco"}


def vendor_for(brand: str) -> str:
    return brand.lower() if brand in _LOWERCASE_VENDORS else brand


def brand_collection(brand: str) -> str:
    """Brand collection title (§5). Display name as-is; QA against live titles."""
    return brand


# --- types id → collection title (§5.1 existing + §5.2 the 3 new collections).
# Signature (37) and the unmapped feeding/hygiene types (21, 42) are absent on
# purpose → a product whose only types are those routes to None and is skipped
# (owner decision 2026-07-20; §5.3 for Signature).
_TYPE_COLLECTION: dict[int, str] = {
    28: "טיולונים",
    18: "עגלות תינוק",
    33: "עגלות תאומים", 56: "עגלות תאומים",
    27: "אקססוריז לעגלה/טיולון", 30: "אקססוריז לעגלה/טיולון", 32: "אקססוריז לעגלה/טיולון",
    20: "סל קל",
    25: "כסאות אוכל",
    29: "נדנדה לתינוק",
    34: "טרמפולינה לתינוק",
    35: "הליכונים לתינוק",
    72: "בימבה", 24: "בימבה",
    59: "תלת אופן",
    73: "אופני איזון",
    55: "צעצועים", 19: "צעצועים", 47: "צעצועים", 66: "צעצועים", 67: "צעצועים",
    76: "צעצועים", 68: "צעצועים",
    75: "מזרן ומשטח פעילות",
    69: "אמבטיות ואביזריהם", 43: "אמבטיות ואביזריהם",
    60: "לולים ועריסות", 31: "לולים ועריסות", 26: "לולים ועריסות",
    61: "מיטות תינוק",
    62: "מיטות מעבר",
    36: "שערי בטיחות",
    52: "אביזרי בטיחות", 74: "אביזרי בטיחות",
    65: "תיקים", 44: "תיקים", 58: "תיקים",
    64: "שולחנות כיסאות ספות",
    70: "סירים וישבנונים",
    # §5.2 — new collections (ensure_collection creates them on first use)
    23: "כסאות בטיחות", 22: "כסאות בטיחות", 46: "כסאות בטיחות", 54: "כסאות בטיחות",
    152: "כסאות בטיחות", 45: "כסאות בטיחות",
    38: "מנשאים",
    134: "מגדל למידה",
}

# Structured-attribute labels for custom.infoo (§3). Isolated as constants — the
# feed gives no units/labels, so these are house choices to QA against the theme.
_ISOFIX_LABELS = {"included": "כלול", "optional": "אופציונלי", "without": "ללא"}

# Warranty for brands with no `websites` row (Bambino/Mastela/Nuna/RycoBaby/
# Safety1st) — the feed's Bambino warranty is empty, so PRD §7's "fall back to
# Bambino" can't be honored. Owner-supplied text (2026-07-20).
FALLBACK_WARRANTY_LINES: tuple[str, ...] = (
    "אחריות",
    "כל המוצרים מגיעים עם שנה אחריות יצרן מיום הרכישה (בהצגת חשבונית מקורית), לא כולל ריפוד.",
    "האחריות מכסה פגמי ייצור, ואינה כוללת בלאי טבעי, ריפוד, שימוש לא תקין, "
    "או נזק שנגרם עקב הובלה/הרכבה עצמית.",
)


def route(type_ids) -> str | None:
    """First of a product's types that maps to a collection (§5), else None.

    None means "no importable category" (only Signature/feeding/hygiene) → the
    product is skipped, not onboarded.
    """
    for tid in type_ids:
        title = _TYPE_COLLECTION.get(tid)
        if title:
            return title
    return None


def is_importable(product: BambinoProduct) -> bool:
    return route(product.type_ids) is not None


def collections_for(product: BambinoProduct) -> tuple[str, ...]:
    """Brand collection + category collection (§5), skipping any blanks."""
    out = [brand_collection(product.brand)]
    category = route(product.type_ids)
    if category:
        out.append(category)
    return tuple(t for t in out if t)


def decode_entities(text: str) -> str:
    return _html.unescape(text or "").strip()


def _collapse(text: str) -> str:
    """Collapse runs of whitespace (incl. newlines) to single spaces; no strip."""
    return re.sub(r"\s+", " ", text or "")


# --- HTML → Shopify rich_text tree (§3): <ul>/<ol> → list, <p> → paragraph,
# <strong>/<b> → bold text. Faithful enough for specifications + warranty HTML.

def _inline_runs(node, bold: bool = False) -> list[tuple[str, bool]]:
    runs: list[tuple[str, bool]] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            text = _collapse(str(child))
            if text:
                runs.append((text, bold))
        elif isinstance(child, Tag):
            if child.name == "br":
                continue
            runs.extend(_inline_runs(child, bold or child.name in ("strong", "b")))
    return runs


def _text_nodes(runs: list[tuple[str, bool]]) -> list[dict]:
    """Merge same-bold runs, trim outer whitespace, drop empties → text nodes."""
    merged: list[list] = []
    for value, bold in runs:
        if merged and merged[-1][1] == bold:
            merged[-1][0] += value
        else:
            merged.append([value, bold])
    if merged:
        merged[0][0] = merged[0][0].lstrip()
        merged[-1][0] = merged[-1][0].rstrip()
    nodes: list[dict] = []
    for value, bold in merged:
        if not value:
            continue
        node = {"type": "text", "value": value}
        if bold:
            node["bold"] = True
        nodes.append(node)
    return nodes


def _block_nodes(container) -> list[dict]:
    out: list[dict] = []
    for el in container.children:
        if isinstance(el, NavigableString):
            nodes = _text_nodes([(_collapse(str(el)), False)])
            if nodes:
                out.append({"type": "paragraph", "children": nodes})
            continue
        if not isinstance(el, Tag):
            continue
        if el.name in ("ul", "ol"):
            items = []
            for li in el.find_all("li", recursive=False):
                nodes = _text_nodes(_inline_runs(li))
                if nodes:
                    items.append({"type": "list-item", "children": nodes})
            if items:
                out.append({
                    "type": "list",
                    "listType": "ordered" if el.name == "ol" else "unordered",
                    "children": items,
                })
        elif el.name == "br":
            continue
        else:  # <p>, <div>, inline wrappers, etc. → a paragraph
            nodes = _text_nodes(_inline_runs(el))
            if nodes:
                out.append({"type": "paragraph", "children": nodes})
    return out


def html_to_rich_text(html: str) -> str:
    """HTML fragment → Shopify rich_text_field JSON (lists + bold preserved)."""
    soup = BeautifulSoup(html or "", "lxml")
    root = soup.body or soup
    children = _block_nodes(root)
    if not children:
        text = _collapse(soup.get_text()).strip()
        children = [{"type": "paragraph", "children": [{"type": "text", "value": text}]}] if text else []
    return json.dumps({"type": "root", "children": children}, ensure_ascii=False)


def _rich_text_paragraphs(lines) -> str:
    return store_content.rich_text_paragraphs(tuple(lines))


def _plain_text(html: str) -> str:
    return _collapse(BeautifulSoup(html or "", "lxml").get_text()).strip()


def _infoo_lines(p: BambinoProduct) -> list[str]:
    """Structured attributes → custom.infoo (מאפיינים) lines (§3)."""
    lines: list[str] = []
    if p.age_from is not None and p.age_to:
        lines.append(f"גיל מומלץ: {p.age_from}-{p.age_to} חודשים")
    elif p.age_from:
        lines.append(f"גיל מומלץ: מגיל {p.age_from} חודשים")
    if p.weight:
        lines.append(f'משקל: {p.weight} ק"ג')
    if p.height and p.width and p.length:
        lines.append(f'מידות (גובה×רוחב×אורך): {p.height}×{p.width}×{p.length} ס"מ')
    if p.standard:
        lines.append(f"תקן: {p.standard}")
    if p.isofix:
        lines.append(f"מערכת איזופיקס: {_ISOFIX_LABELS.get(p.isofix, p.isofix)}")
    return lines


def warranty_rich_text(brand: str, warranties: dict[str, str]) -> str:
    """Per-brand warranty (§7); the owner-supplied fallback for site-less brands."""
    html = warranties.get(brand)
    if html and html.strip():
        return html_to_rich_text(html)
    return _rich_text_paragraphs(FALLBACK_WARRANTY_LINES)


def _metafields(product: BambinoProduct, title: str, warranties: dict[str, str]) -> tuple[Metafield, ...]:
    out: list[Metafield] = []

    infoo = _infoo_lines(product)
    if infoo:
        out.append(Metafield("custom", "infoo", RICH_TEXT, _rich_text_paragraphs(infoo)))
    if product.specifications_html.strip():
        out.append(Metafield("custom", "view_productss", RICH_TEXT,
                             html_to_rich_text(product.specifications_html)))
    out.append(Metafield("custom", "securingg", RICH_TEXT,
                         warranty_rich_text(product.brand, warranties)))
    out.append(Metafield("custom", "delivery", RICH_TEXT,
                         store_content.rich_text_paragraphs(store_content.TEXTILE_DELIVERY_LINES)))

    if product.video_urls:
        out.append(Metafield("custom", "videos", "list.url", json.dumps(list(product.video_urls))))
    if product.product_manual:
        out.append(Metafield("custom", "manual", "url", product.product_manual))

    # SEO: metaTitle/metaDescription are always empty → fall back to title/description (§2).
    out.append(Metafield("global", "title_tag", "single_line_text_field", title))
    out.append(Metafield("global", "description_tag", "multi_line_text_field",
                         _plain_text(product.description_html)))

    # supplier.* — internal reference / sync anchors
    out.append(Metafield("supplier", "sku", "single_line_text_field", product.catalog_number))
    if product.price is not None:
        out.append(Metafield("supplier", "price", "number_decimal", str(product.price)))
    return tuple(out)


def build_title(product: BambinoProduct) -> str:
    """"{title} {name}" + " - {color}" for a non-main color variant (§2)."""
    title = decode_entities(f"{product.title} {product.name}")
    if not product.is_main_color and product.color:
        title = f"{title} - {decode_entities(product.color)}"
    return title


def to_product_draft(product: BambinoProduct, warranties: dict[str, str],
                     today: date | None = None, logger: Logger | None = None) -> ProductDraft:
    """Build a draft product from a BambinoProduct + per-brand warranties."""
    _ = logger or get("bambino.mapping")
    day = today or date.today()
    title = build_title(product)

    # Discount (§2): an active overwrite sale puts the sale price on `price` and
    # the original on compare_at; otherwise the regular price stands.
    price = product.price
    compare_at = None
    if product.discount and product.discount.active_on(day) and product.price is not None:
        price = product.discount.amount
        compare_at = product.price

    variant = VariantSpec(
        sku=SKU(product.catalog_number),
        option_value=None,                    # every color is its own product (§4)
        barcode=product.barcode or None,
        price=price,
        compare_at_price=compare_at,
        inventory_quantity=product.quantity,  # real count (OOS is skipped at ingest)
    )
    return ProductDraft(
        title=title,
        body_html=product.description_html,
        vendor=vendor_for(product.brand),
        product_type="",                      # always empty (§2, §5)
        tags="",
        variants=(variant,),
        image_urls=product.image_urls,
        status="draft",
        metafields=_metafields(product, title, warranties),
        template_suffix=TEMPLATE,
    )
