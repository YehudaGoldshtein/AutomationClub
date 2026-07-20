"""Bambino pre-import cleanup: delete the 94 legacy brand products (PRD §1).

The store's existing Joie/Infanti/Graco/Bambino products were NOT built from this
feed (internal SKUs, content from other sources, 0% overlap). The owner chose to
delete them all and re-import the 526 clean (§1). This is the delete half; it runs
ONCE, BEFORE `bambino-ingest`.

Safety:
  - **dry-run by default** — `confirm=False` only reports what it would delete.
  - **catalog guard** — never deletes a product whose SKU is a live Bambino
    catalogNumber (`protect_skus`), so a mis-ordered run can't nuke fresh imports.

See tests/test_bambino_delete.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# The legacy vendor tags to purge (§1). NOT the new import vendors beyond the
# lowercase big-three overlap — hence the catalog guard + delete-runs-first order.
TARGET_VENDORS: tuple[str, ...] = ("infanti", "joie", "graco", "GRACO", "BAMBINO")


@dataclass
class DeleteSummary:
    found: int = 0          # non-protected delete targets
    deleted: int = 0
    protected: int = 0      # skipped because their SKU is a live catalog item
    errors: int = 0
    confirmed: bool = False
    targets: list[dict] = field(default_factory=list)


def delete_existing_bambino_brands(store, logger, confirm: bool = False,
                                   protect_skus=frozenset(),
                                   vendors: tuple[str, ...] = TARGET_VENDORS) -> DeleteSummary:
    """Delete legacy brand products. Dry-run unless confirm=True."""
    summary = DeleteSummary(confirmed=confirm)
    protect = set(protect_skus)
    products = store.product_ids_by_vendor(vendors)
    logger.info("bambino_delete_scan", vendors=list(vendors), candidates=len(products),
                confirm=confirm, protect_count=len(protect))

    for p in products:
        if protect and any(s in protect for s in p.get("skus", [])):
            summary.protected += 1
            logger.info("bambino_delete_protected", product_id=p["id"], title=p["title"],
                        vendor=p["vendor"], skus=p.get("skus", []))
            continue

        summary.found += 1
        summary.targets.append(p)
        if not confirm:
            logger.info("bambino_delete_would_delete", product_id=p["id"],
                        vendor=p["vendor"], title=p["title"])
            continue

        try:
            store.delete_product(p["id"])
            summary.deleted += 1
            logger.info("bambino_delete_deleted", product_id=p["id"], vendor=p["vendor"],
                        title=p["title"])
        except Exception as e:
            summary.errors += 1
            logger.error("bambino_delete_failed", product_id=p["id"], error=str(e)[:200])

    logger.info("bambino_delete_summary", found=summary.found, deleted=summary.deleted,
                protected=summary.protected, errors=summary.errors, confirmed=confirm)
    return summary
