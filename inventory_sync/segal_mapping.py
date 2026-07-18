"""Map a SegalProduct into a create-ready ProductDraft (PRD §2–§4).

Owns: the category → product_type/collection map (§3, 6 in-scope categories),
the tab-label → metafield routing (§4, by label not position), HTML → rich_text
conversion, and the field assembly. See tests/test_segal_mapping.py.
"""
from __future__ import annotations

import html as _html
import json

from bs4 import BeautifulSoup

from inventory_sync.domain import SKU, Metafield, ProductDraft, VariantSpec
from inventory_sync.log import Logger, get
from inventory_sync.segal_source import SegalProduct, SegalTab

VENDOR = "segal | סגל"

# The 6 in-scope Segal categories → their WC Store API ids (ingest iterates these).
INGEST_CATEGORIES: dict[str, int] = {
    "beds": 37,
    "beds-and-toddler": 361,
    "dresser": 58,
    "soft-close-dresser": 352,
    "closet": 49,
    "storage-segal-baby": 227,
}

# category slug → (product_type, collection names). PRD §3. Deferred categories
# (mattress/xlbabycrib/learning-tower/carpet/rugs) are intentionally absent — no
# collection mapping yet, so they are skipped, not guessed (PRD §10).
CATEGORY_MAP: dict[str, tuple[str, tuple[str, ...]]] = {
    "beds": ("מיטות תינוק", ("מיטות תינוק",)),
    "beds-and-toddler": ("מיטת מעבר", ("מיטות מעבר",)),
    "dresser": ("שידות", ("שידות החתלה",)),
    "soft-close-dresser": ("שידות", ("שידות החתלה",)),
    "closet": ("ארונות", ("ארונות לחדרי ילדים",)),
    "storage-segal-baby": ("אחסון", ("אחסון", "אחסון ואביזרים")),
}

# Bed-like categories use the dedicated theme template; everything else the generic one (§2).
_BEDS_LIKE = {"beds", "beds-and-toddler", "xlbabycrib"}

RICH_TEXT = "rich_text_field"
WARRANTY_PREFIX = "אחריות רחבה 5 שנים"
# Max Baby fixed delivery/returns text (owner-supplied). Handles furniture explicitly.
DELIVERY_BOILERPLATE_LINES: tuple[str, ...] = (
    "משלוח עד הבית",
    "✓ שליח עד הבית חינם בהזמנה מעל 499 ₪ (לא כולל ריהוט)",
    "✓ מתחת ל-499 ₪ עלות המשלוח הינה 29 ₪",
    "✓ אספקת ההזמנה עד 7 ימי עסקים (לא כולל ריהוט)",
    "✓ אספקת הזמנה של ריהוט עד 14 ימי עסקים",
    "החלפות והחזרות",
    "✓ החלפת מוצר שנרכש באתר, ניתן לבצע עד 30 יום מיום קבלת המשלוח",
    "✓ החזרת פריטים תוך 14 ימים מיום קבלת המשלוח",
    "✓ החלפות או החזרות יתבצעו דרך יצירת קשר עם שירות הלקוחות שלנו במייל maxbabyonline@gmail.com ובהצגת חשבונית, שם ינחו אתכם על התהליך.",
    "✗ אין אפשרות להחלפות/החזרות בסניף המותג.",
)


def matched_category(product: SegalProduct) -> str | None:
    """First of the product's category slugs that is an in-scope mapped category."""
    for slug in product.category_slugs:
        if slug in CATEGORY_MAP:
            return slug
    return None


def product_type_for(product: SegalProduct) -> str | None:
    slug = matched_category(product)
    return CATEGORY_MAP[slug][0] if slug else None


def collections_for(product: SegalProduct) -> tuple[str, ...]:
    slug = matched_category(product)
    return CATEGORY_MAP[slug][1] if slug else ()


def template_suffix_for(product: SegalProduct) -> str:
    slug = matched_category(product)
    return "furniture-beds" if slug in _BEDS_LIKE else "furniture-product-page"


def route_tab(label: str) -> tuple[str, str] | None:
    """Tab label → (namespace, key), or None to discard. By label, not position (§4).

    The label deliberately does not match the field's display name — mismatch is
    intended (e.g. Greenguard, a safety cert, → the assurance/הבטחה field).
    """
    lbl = (label or "").strip()
    if lbl == "מידע כללי":
        return ("custom", "infoo")
    if lbl.startswith("פרטים טכניים"):
        return ("custom", "view_productss")
    if "greenguard" in lbl.casefold() or "אחריות" in lbl:
        return ("custom", "securingg")
    return None


def decode_entities(text: str) -> str:
    return _html.unescape(text or "").strip()


def _lines(html: str) -> list[str]:
    """Flatten HTML to text lines: one per <p>/<br>, tags (incl. <strong>) stripped."""
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


def _rich_text_doc(lines: list[str]) -> str:
    root = {
        "type": "root",
        "children": [
            {"type": "paragraph", "children": [{"type": "text", "value": line}]}
            for line in lines
        ],
    }
    return json.dumps(root, ensure_ascii=False)


def html_to_rich_text(html: str) -> str:
    """HTML block → Shopify rich_text_field JSON string, one paragraph per line (§4)."""
    return _rich_text_doc(_lines(html))


def tabs_to_metafields(
    tabs: tuple[SegalTab, ...], logger: Logger
) -> tuple[Metafield, ...]:
    """Route each tab to its metafield by label; discard + log unknown labels (§4)."""
    out: list[Metafield] = []
    for tab in tabs:
        route = route_tab(tab.label)
        if route is None:
            logger.info("tab_discarded", label=tab.label)
            continue
        namespace, key = route
        lines = _lines(tab.html)
        if key == "securingg":
            lines = [WARRANTY_PREFIX, *lines]  # §4: warranty prefix
        out.append(Metafield(namespace, key, RICH_TEXT, _rich_text_doc(lines)))
    return tuple(out)


def _plain_text(html: str) -> str:
    return " ".join(_lines(html))


def to_product_draft(product: SegalProduct, logger: Logger | None = None) -> ProductDraft:
    """Build a draft product from a SegalProduct (API fields + scraped tabs)."""
    log = logger or get("segal.mapping")
    title = decode_entities(product.name)

    metafields: list[Metafield] = list(tabs_to_metafields(product.tabs, log))
    metafields.append(Metafield("custom", "delivery", RICH_TEXT,
                                _rich_text_doc(list(DELIVERY_BOILERPLATE_LINES))))
    metafields.append(Metafield("global", "title_tag", "single_line_text_field", title))
    metafields.append(Metafield("global", "description_tag", "multi_line_text_field",
                                _plain_text(product.description_html)))
    # supplier.* — internal reference / sync anchors (§2, §4)
    if product.price is not None:
        metafields.append(Metafield("supplier", "price", "number_decimal", str(product.price)))
    metafields.append(Metafield("supplier", "sku", "single_line_text_field", product.sku))
    metafields.append(Metafield("supplier", "url", "url", product.permalink))
    if product.stock_qty is not None:
        metafields.append(Metafield("supplier", "stock_qty", "number_integer", str(product.stock_qty)))

    variant = VariantSpec(
        sku=SKU(product.sku),
        option_value=None,          # simple product — no size option
        price=product.price,
        inventory_quantity=product.stock_qty,
    )
    return ProductDraft(
        title=title,
        body_html=product.description_html,
        vendor=VENDOR,
        product_type=product_type_for(product) or "",
        tags=product_type_for(product) or "",
        variants=(variant,),
        image_urls=product.image_urls,
        status="draft",
        metafields=tuple(metafields),
        template_suffix=template_suffix_for(product),
    )
