"""Laura ingest core: parse the supplier xlsx and create net-new draft products.

Wires grouping (laura_upload) + mapping (laura_mapping) + the store create seams
+ store_products pending rows. See tests/test_laura_ingest.py.

Ingest is skip-dominant: a typical upload has ~2025 rows, almost all already on
the store; only a handful survive as new. New SKUs are assumed to be new products
(a title collision with an existing product is flagged for manual review, not
duplicated).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import openpyxl

from inventory_sync.laura_mapping import (
    CATEGORY_COLLECTION_ID,
    subcategory_collection,
    to_product_draft,
)
from inventory_sync.laura_upload import LauraRow, group_products
from inventory_sync.persistence.store_product_store import NewStoreProduct


@dataclass
class IngestSummary:
    created: int = 0
    skipped_existing: int = 0
    flagged_review: int = 0
    would_create: int = 0      # dry-run: products that would be created
    dry_run: bool = False
    created_skus: list[str] = field(default_factory=list)


_EXACT_HEADERS = {
    "מקט": "sku",
    "ברקוד": "barcode",
    "תיאור פריט": "description",
    "תאור משפחה": "family",
    "טקסט": "text",
    "מחיר מומלץ": "recommended_price",
}


def _s(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_laura_xlsx(data: bytes) -> list[LauraRow]:
    """Parse supplier xlsx bytes into LauraRows via header-based column mapping."""
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        return []

    col: dict[str, int] = {}
    for i, h in enumerate(header):
        if h is None:
            continue
        hs = str(h).strip()
        if hs in _EXACT_HEADERS:
            col[_EXACT_HEADERS[hs]] = i
        elif "קישור" in hs or "link" in hs.lower():
            col["image_url"] = i

    def cell(row, field_name):
        i = col.get(field_name)
        return row[i] if i is not None and i < len(row) else None

    out: list[LauraRow] = []
    for row in rows_iter:
        if row is None or all(c is None for c in row):
            continue
        sku = _s(cell(row, "sku"))
        if not sku:
            continue
        out.append(LauraRow(
            sku=sku,
            description=_s(cell(row, "description")) or "",
            family=_s(cell(row, "family")) or "",
            barcode=_s(cell(row, "barcode")),
            text=_s(cell(row, "text")),
            image_url=_s(cell(row, "image_url")),
            recommended_price=_dec(cell(row, "recommended_price")),
        ))
    return out


def ingest_products(rows, store, product_store, customer_id: str, logger, dry_run: bool = False) -> IngestSummary:
    """Group rows, skip existing SKUs, create new products as drafts, record pending."""
    summary = IngestSummary(dry_run=dry_run)
    existing = store.list_products()
    existing_skus = {str(p.sku) for p in existing}
    existing_titles = {p.title for p in existing if p.title}

    for group in group_products(rows):
        group_skus = {str(v.sku) for v in group.variants}

        if group_skus & existing_skus:
            summary.skipped_existing += 1
            logger.info("ingest_skip_existing", title=group.title, skus=sorted(group_skus))
            continue

        if group.title in existing_titles:
            # New SKU whose title matches an existing product — likely a new size of
            # an existing product (add-variant, not create). Flag, don't duplicate.
            summary.flagged_review += 1
            logger.warning("ingest_title_collision", title=group.title, skus=sorted(group_skus))
            continue

        sub_name = subcategory_collection(group.family)
        needs_review = bool(
            group.needs_review
            or sub_name is None
            or not group.image_urls
            or not group.body_text
        )

        if dry_run:
            summary.would_create += 1
            logger.info("ingest_would_create", title=group.title,
                        variants=len(group.variants), needs_review=needs_review)
            continue

        draft = to_product_draft(group)
        sub_ref = store.ensure_collection(sub_name) if sub_name else None
        created = store.create_product(draft)
        store.add_to_collection(created.store_product_id, CATEGORY_COLLECTION_ID)
        if sub_ref is not None:
            store.add_to_collection(created.store_product_id, sub_ref.id)

        product_store.write_pending(customer_id, [
            NewStoreProduct(
                sku=str(v.sku),
                store_product_id=created.store_product_id,
                title=group.title,
                is_new_collection=bool(sub_ref and sub_ref.created),
                needs_review=needs_review,
            )
            for v in group.variants
        ])
        summary.created += 1
        summary.created_skus.extend(sorted(group_skus))
        logger.info("ingest_created", title=group.title,
                    store_product_id=created.store_product_id, variants=len(group.variants),
                    is_new_collection=bool(sub_ref and sub_ref.created), needs_review=needs_review)

    logger.info("ingest_summary", customer_id=customer_id, created=summary.created,
                skipped_existing=summary.skipped_existing, flagged_review=summary.flagged_review,
                would_create=summary.would_create, dry_run=dry_run)
    return summary
