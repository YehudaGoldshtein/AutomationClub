"""Map a grouped Laura product (ProductGroup) into a create-ready ProductDraft.

SCAFFOLD — see tests/test_laura_mapping.py. Owns the PRD §4 body_html template,
the Appendix-A family → sub-category collection lookup, and the field mapping.
"""
from __future__ import annotations

from inventory_sync.domain import ProductDraft
from inventory_sync.laura_upload import ProductGroup

VENDOR = "לורה סוויסרה | laura swisra"
# Top-level category collection — every Laura product joins it (PRD §3).
CATEGORY_COLLECTION_ID = "477920559358"


def build_body_html(title: str, text: str | None) -> str:
    """Wrap title + `טקסט` in the fixed RTL template (PRD §4)."""
    raise NotImplementedError


def subcategory_collection(family: str) -> str | None:
    """Family (`תאור משפחה`) → sub-category collection name (Appendix A), or None."""
    raise NotImplementedError


def to_product_draft(group: ProductGroup) -> ProductDraft:
    """Build a draft product from a grouped Laura product."""
    raise NotImplementedError
