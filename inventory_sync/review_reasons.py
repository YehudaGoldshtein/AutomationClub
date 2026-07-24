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
MISSING_AT_SOURCE = "missing_at_source"  # a store product that is no longer in the
                                         # supplier's catalog (supplier removed it) —
                                         # flag for review, never auto-delete (PRD-snir §1/§3)


def join(*reasons: str | None) -> str | None:
    """Comma-join the active reason codes; None when nothing needs review."""
    active = [r for r in reasons if r]
    return ",".join(active) or None


def _split(reason: str | None) -> list[str]:
    return [c for c in (reason or "").split(",") if c]


def add(reason: str | None, code: str) -> str | None:
    """Add `code` to a comma-joined reason string (no duplicates)."""
    codes = _split(reason)
    if code not in codes:
        codes.append(code)
    return ",".join(codes) or None


def without(reason: str | None, code: str) -> str | None:
    """Remove `code` from a comma-joined reason string; None if nothing remains."""
    return ",".join(c for c in _split(reason) if c != code) or None
