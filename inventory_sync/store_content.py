"""Store-wide content shared across suppliers (Max Baby).

Delivery/returns boilerplate that every ingested product carries in its
`custom.delivery` metafield. It is **per-category**, not one text for all:
  - textile (Laura) has one text;
  - furniture (Segal) has a different one.
"""
from __future__ import annotations

import json

from inventory_sync.domain import Metafield

# Textile / Laura — owner-supplied fixed shipping + returns text.
TEXTILE_DELIVERY_LINES: tuple[str, ...] = (
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

# Furniture / Segal — TODO(owner): real furniture delivery text (to be pasted).
FURNITURE_DELIVERY_LINES: tuple[str, ...] = (
    "משלוחים והחזרות — נוסח הרהיטים יתעדכן בקרוב.",
)

RICH_TEXT = "rich_text_field"


def rich_text_paragraphs(lines: tuple[str, ...] | list[str]) -> str:
    """Shopify rich_text_field JSON — one paragraph per line."""
    root = {
        "type": "root",
        "children": [
            {"type": "paragraph", "children": [{"type": "text", "value": line}]}
            for line in lines
        ],
    }
    return json.dumps(root, ensure_ascii=False)


def _delivery(lines: tuple[str, ...]) -> Metafield:
    return Metafield("custom", "delivery", RICH_TEXT, rich_text_paragraphs(lines))


def textile_delivery_metafield() -> Metafield:
    """custom.delivery for textile products (Laura)."""
    return _delivery(TEXTILE_DELIVERY_LINES)


def furniture_delivery_metafield() -> Metafield:
    """custom.delivery for furniture products (Segal). Placeholder until owner supplies text."""
    return _delivery(FURNITURE_DELIVERY_LINES)
