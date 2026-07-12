"""Laura ingest core: parse the supplier xlsx and create net-new draft products.

SCAFFOLD — see tests/test_laura_ingest.py. Wires grouping (laura_upload) + mapping
(laura_mapping) + the store create seams + store_products pending rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from inventory_sync.laura_upload import LauraRow


@dataclass
class IngestSummary:
    created: int = 0
    skipped_existing: int = 0
    flagged_review: int = 0
    would_create: int = 0      # dry-run: products that would be created
    dry_run: bool = False
    created_skus: list[str] = field(default_factory=list)


def parse_laura_xlsx(data: bytes) -> list[LauraRow]:
    """Parse supplier xlsx bytes into LauraRows via header-based column mapping."""
    raise NotImplementedError


def ingest_products(rows, store, product_store, customer_id: str, logger, dry_run: bool = False) -> IngestSummary:
    """Group rows, skip existing SKUs, create new products as drafts, record pending."""
    raise NotImplementedError
