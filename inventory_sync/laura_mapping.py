"""Map a grouped Laura product (ProductGroup) into a create-ready ProductDraft.

Owns the PRD §4 body_html template, the Appendix-A family → sub-category
collection lookup, and the field mapping. See tests/test_laura_mapping.py.
"""
from __future__ import annotations

from inventory_sync.domain import SKU, ProductDraft, VariantSpec
from inventory_sync.laura_upload import ProductGroup

VENDOR = "לורה סוויסרה | laura swisra"
# Top-level category collection — every Laura product joins it (PRD §3).
CATEGORY_COLLECTION_ID = "477920559358"
OPTION_NAME = "מידה"

# Appendix A: family (`תאור משפחה`) → sub-category collection name.
# NOTE: family strings should be verified against the live file; unknown families
# return None so the ingest flags needs_review rather than mis-filing.
COLLECTION_BY_FAMILY: dict[str, str] = {
    # אופנה
    "ביגוד לתינוקות": "אופנה",
    "חולצה": "אופנה",
    "בגדי ים": "אופנה",
    "אקססוריז לשיער": "אופנה",
    # מכנסיים ורגליות
    "מכנס": "מכנסיים ורגליות לתינוק",
    "רגליות": "מכנסיים ורגליות לתינוק",
    # בגדי גוף
    "בגד גוף": "בגדי גוף לתינוק",
    # כובעים
    "כובעים": "כובע לתינוק",
    # גרביים
    "נעליים וגרביים": "גרביים לתינוקות",
    # חיתולי בד
    "חיתולי במבוק": "חיתולי בד לתינוקות",
    "חיתולי טטרא": "חיתולי בד לתינוקות",
    "חיתולי טריקו": "חיתולי בד לתינוקות",
    "חיתולי פלנל": "חיתולי בד לתינוקות",
    # מגבות
    "מגבת עם כובע": "מגבות לתינוק",
    "מגבת קשירה": "מגבות לתינוק",
    # סינרים
    "סינר PVC": "סינרים",
    "סינר הנקה": "סינרים",
    "סינר מגבת": "סינרים",
    "סינר סיליקון": "סינרים",
    "סינר שרוול": "סינרים",
    # שמיכות
    "שמיכת טטרא": "שמיכות לתינוק",
    "שמיכת טריקו": "שמיכות לתינוק",
    "שמיכת פליז": "שמיכות לתינוק",
    "שמיכת פלנל": "שמיכות לתינוק",
    "שמיכת קטיפה": "שמיכות לתינוק",
    "שמיכת פוך": "שמיכות לתינוק",
    # שמיכי
    "שמיכי": "שמיכי לתינוק",
    # כריות
    "כריות כללי": "כריות לתינוק",
    "כרית הנקה": "כריות לתינוק",
    # נחשושים
    "כרית נחשוש": "נחשושים",
    "כרית נחשוש קלועה": "נחשושים",
    # מגן ראש
    "מגן למיטה": "מגן ראש לתינוק",
    "מגן ראש לעריסה": "מגן ראש לתינוק",
    "ציפה": "מגן ראש לתינוק",
    "ציפיות": "מגן ראש לתינוק",
    "צפי צף": "מגן ראש לתינוק",
    # סדינים
    "סדינים למיטה": "סדינים למיטת תינוק",
    "סדינים מטר": "סדינים למיטת תינוק",
    "סדינים מעבר": "סדינים למיטת תינוק",
    "סדינים לול": "סדינים ללול",
    "סדינים לעגלה": "סדינים לעגלה",
    "סדינים לעריסה": "סדינים לעריסה",
    # סטים
    "סט קומפלט למיטת תינוק": "סט מצעים למיטת תינוק",
    "סט לול": "סט מצעים ללול ולעריסה",
    "סט עריסה": "סט מצעים ללול ולעריסה",
    "סט למיטת מטר": "סט מצעים למיטת מטר",
    "סט למיטת מעבר": "סט מצעים למיטת מעבר",
    "סט בגד גוף ורגליות": "סטים לתינוקות",
    "סט חולצה ומכנס": "סטים לתינוקות",
    "סט יחיד": "סטים לתינוקות",
    # החתלה / עגלה
    "מזרוני שידה עם ספוג": "משטחי החתלה",
    "מזרון עגלה עם חבקים": "עגלה ונסיעות",
    "ריפודית לסלקל": "עגלה ונסיעות",
    # בייבי נסט
    "בייבי נסט": "בייבי נסט",
    "משטח פעילות": "בייבי נסט",
    "פופים": "בייבי נסט",
    # חדר תינוק
    "דקורציה לקיר": "חדר תינוק",
    "וילונות": "חדר תינוק",
    "כילה": "חדר תינוק",
    "כילה מתקרה": "חדר תינוק",
    "שטיח": "חדר תינוק",
    # אחסון
    "ארגונית": "אחסון ואביזרים",
    "סל כביסה/אחסון": "אחסון ואביזרים",
    "סלסלה": "אחסון ואביזרים",
    "שק טיטולים": "אחסון ואביזרים",
    "תיקים": "אחסון ואביזרים",
    # האכלה
    "מוצרי האכלה": "מוצרי האכלה",
    "מוצרי האכלה סיליקון": "מוצרי האכלה",
    # שונות
    "בובות": "שונות",
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
    """Family (`תאור משפחה`) → sub-category collection name (Appendix A), or None."""
    return COLLECTION_BY_FAMILY.get(family)


def to_product_draft(group: ProductGroup) -> ProductDraft:
    """Build a draft product from a grouped Laura product."""
    variants = tuple(
        VariantSpec(
            sku=SKU(v.sku),
            option_value=v.size,
            barcode=v.barcode,
            price=v.price,
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
    )
