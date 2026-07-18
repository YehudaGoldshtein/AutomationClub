"""Map a grouped Laura product (ProductGroup) into a create-ready ProductDraft.

Owns the PRD §4 body_html template, the Appendix-A family → sub-category
collection lookup, and the field mapping. See tests/test_laura_mapping.py.
"""
from __future__ import annotations

from inventory_sync import store_content
from inventory_sync.domain import SKU, ProductDraft, VariantSpec
from inventory_sync.laura_upload import ProductGroup

VENDOR = "לורה סוויסרה | laura swisra"
# Top-level category collection — every Laura product joins it (PRD §3).
CATEGORY_COLLECTION_ID = "477920559358"
OPTION_NAME = "מידה"

# Appendix A: family (`תאור משפחה`) → sub-category collection name.
# FINAL owner-approved map (family_collection_map_FINAL.md) — all 82 families
# confirmed, 29 collections. Keys are matched after .strip() (the file carries
# hidden leading/trailing spaces on some families, e.g. ' חולצה', 'וילונות ').
COLLECTION_BY_FAMILY: dict[str, str] = {
    # אופנה
    "חולצה": "אופנה",
    "אקססוריז לשיער": "אופנה",
    "בגדי ים": "אופנה",
    "ביגוד לתינוקות": "אופנה",
    # אחסון ואביזרים
    "ארגונית": "אחסון ואביזרים",
    "סל אחסון": "אחסון ואביזרים",
    "סל כביסה-סל אחסון": "אחסון ואביזרים",
    "סלסלה": "אחסון ואביזרים",
    "שק טיטולים": "אחסון ואביזרים",
    "תיקים": "אחסון ואביזרים",
    # בגדי גוף לתינוק
    "בגד גוף": "בגדי גוף לתינוק",
    # בייבי נסט
    "בייבי נסט": "בייבי נסט",
    "משטח פעילות": "בייבי נסט",
    "פופים": "בייבי נסט",
    # גרביים לתינוקות
    "נעליים וגרביים": "גרביים לתינוקות",
    # חדר תינוק
    "אוהל טיפי": "חדר תינוק",
    "דקורציה לקיר": "חדר תינוק",
    "וילונות": "חדר תינוק",
    "כילה": "חדר תינוק",
    "כילה מתקרה": "חדר תינוק",
    "שטיח": "חדר תינוק",
    # חיתולי בד לתינוקות
    "חיתולי במבוק": "חיתולי בד לתינוקות",
    "חיתולי טטרא": "חיתולי בד לתינוקות",
    "חיתולי טריקו": "חיתולי בד לתינוקות",
    "חיתולי פלנל": "חיתולי בד לתינוקות",
    # כובע לתינוק
    "כובעים": "כובע לתינוק",
    # כריות לתינוק
    "כריות כללי": "כריות לתינוק",
    "כרית הנקה": "כריות לתינוק",
    # מגבות לתינוק
    "כפפות רחצה לפנים": "מגבות לתינוק",
    "מגבת עם כובע": "מגבות לתינוק",
    "מגבת קשירה": "מגבות לתינוק",
    # מגן ראש לתינוק
    "מגן למיטה": "מגן ראש לתינוק",
    "מגן ראש לעריסה": "מגן ראש לתינוק",
    "ציפה": "מגן ראש לתינוק",
    "ציפיות": "מגן ראש לתינוק",
    "צפי צף": "מגן ראש לתינוק",
    # מוצרי האכלה
    "מוצרי האכלה": "מוצרי האכלה",
    "מוצרי האכלה סיליקון": "מוצרי האכלה",
    # מכנסיים ורגליות לתינוק
    "מכנס": "מכנסיים ורגליות לתינוק",
    "רגליות": "מכנסיים ורגליות לתינוק",
    # משטחי החתלה
    "מזרוני שידה עם ספוג": "משטחי החתלה",
    # נחשושים
    "כרית נחשוש": "נחשושים",
    "כרית נחשוש לעגלה": "נחשושים",
    "כרית נחשוש קלועה": "נחשושים",
    # סדינים
    "סדינים לול": "סדינים ללול",
    "סדינים למיטה": "סדינים למיטת תינוק",
    "סדינים מטר/מעבר": "סדינים למיטת תינוק",
    "סדינים לעגלה": "סדינים לעגלה",
    "סדינים לעריסה": "סדינים לעריסה",
    # סטים
    "סט לול": "סט מצעים ללול ולעריסה",
    "סט עריסה": "סט מצעים ללול ולעריסה",
    "סט למיטת מטר": "סט מצעים למיטת מטר",
    "סט למיטת מעבר": "סט מצעים למיטת מעבר",
    "סט קומפלט למיטת תינוק": "סט מצעים למיטת תינוק",
    "סט בגד גוף ואוברול": "סטים לתינוקות",
    "סט בגד גוף ורגליות": "סטים לתינוקות",
    "סט חולצה ומכנס": "סטים לתינוקות",
    "סט יחיד": "סטים לתינוקות",
    # סינרים
    "סינר PVC": "סינרים",
    "סינר בנדנה": "סינרים",
    "סינר הנקה": "סינרים",
    "סינר מגבת": "סינרים",
    "סינר סיליקון": "סינרים",
    "סינר שרוול": "סינרים",
    "סינרי טטרא": "סינרים",
    # עגלה ונסיעות
    "מזרון עגלה עם חבקים": "עגלה ונסיעות",
    "עגלה וסל קל": "עגלה ונסיעות",
    "ריפודית לסלקל": "עגלה ונסיעות",
    # שונות
    "בובות": "שונות",
    # שמיכות לתינוק
    "כירבולית ושק שינה": "שמיכות לתינוק",
    "שמיכות": "שמיכות לתינוק",
    "שמיכות טטרא": "שמיכות לתינוק",
    "שמיכות טריקו": "שמיכות לתינוק",
    "שמיכות סרוגות": "שמיכות לתינוק",
    "שמיכות פיקה": "שמיכות לתינוק",
    "שמיכות פליז": "שמיכות לתינוק",
    "שמיכות פלנל": "שמיכות לתינוק",
    "שמיכות קטיפה": "שמיכות לתינוק",
    "שמיכות שרפא": "שמיכות לתינוק",
    "שמיכת פוך": "שמיכות לתינוק",
    # שמיכי לתינוק
    "שמיכי": "שמיכי לתינוק",
}


def build_body_html(title: str, text: str | None) -> str:
    """Wrap title + `טקסט` in the fixed RTL template (PRD §4).

    Empty text → no <p> element (not an empty paragraph).
    """
    paragraph = (
        f'\n  <p style="margin-bottom: 12px;">{text}</p>' if text else ""
    )
    return (
        '<div class="laura-desc" dir="rtl" '
        'style="font-family: inherit; line-height: 1.7; color: #333;">\n'
        f'  <h3 style="font-size: 1.1em; margin-bottom: 10px; color: #222;">{title}</h3>'
        f'{paragraph}\n'
        '</div>'
    )


def subcategory_collection(family: str) -> str | None:
    """Family (`תאור משפחה`) → sub-category collection name (Appendix A), or None.

    Strips whitespace first — the source data carries hidden leading/trailing
    spaces on some families. Unknown/unconfirmed families return None so ingest
    flags needs_review instead of mis-filing.
    """
    if not family:
        return None
    return COLLECTION_BY_FAMILY.get(family.strip())


def to_product_draft(group: ProductGroup) -> ProductDraft:
    """Build a draft product from a grouped Laura product."""
    variants = tuple(
        VariantSpec(
            sku=SKU(v.sku),
            option_value=v.size,
            barcode=v.barcode,
            price=v.price,
            # Create tracked (stock 0) so the hourly scrape can write real stock —
            # an untracked variant 422s on inventory_levels/set.
            track_inventory=True,
        )
        for v in group.variants
    )
    return ProductDraft(
        title=group.title,
        body_html=build_body_html(group.title, group.body_text),
        vendor=VENDOR,
        product_type=group.family,
        tags=group.family,
        variants=variants,
        option_name=OPTION_NAME,
        image_urls=group.image_urls,
        status="draft",
        # Fixed Max Baby delivery/returns text on every product (owner: "to all").
        metafields=(store_content.delivery_metafield(),),
    )
