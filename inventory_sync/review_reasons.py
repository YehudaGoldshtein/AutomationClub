"""Canonical `needs_review` reason codes for onboarded draft products.

Stored in `store_products.needs_review_reason` (comma-joined when several apply)
so the dashboard can show WHY a draft is flagged, not just that it is. Kept as
short stable codes; the frontend maps them to human labels.
"""
from __future__ import annotations

NO_IMAGE = "no_image"            # no images in the supplier feed
NO_PRICE = "no_price"            # no price in the supplier feed
IMAGE_REJECTED = "image_rejected"  # had images but the store rejected them (created without)
NO_COLLECTION = "no_collection"  # no category collection mapped
NO_BODY = "no_body"              # no description/body text
SUPPLIER_FLAG = "supplier_flag"  # the supplier feed itself flagged it
MULTI_VARIANT = "multi_variant"  # source product has 2+ variations sharing one SKU;
                                 # onboarded single-variant on the parent SKU — owner
                                 # should reconcile against any hand-split store versions
# NOTE: "missing at source" (a live product dropped from the supplier catalog) is NOT
# a needs_review_reason code — it's the dedicated `store_products.missing_at_source`
# boolean the dashboard filters on. See SqlStoreProductStore.flag_missing_at_source.


def join(*reasons: str | None) -> str | None:
    """Comma-join the active reason codes; None when nothing needs review."""
    active = [r for r in reasons if r]
    return ",".join(active) or None
