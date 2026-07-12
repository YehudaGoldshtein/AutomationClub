"""Activation reconcile: flip approved draft products live.

After the dashboard sets approved=true on a pending product, the tokened sync job
runs this to make it live in the store. Vendor-agnostic — it activates any
approved draft in store_products. SCAFFOLD — see tests/test_reconcile.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ReconcileSummary:
    activated: int = 0
    errors: int = 0
    activated_product_ids: list[str] = field(default_factory=list)


def reconcile_approved_drafts(store, product_store, customer_id: str, logger) -> ReconcileSummary:
    """Republish every approved draft (status=active) and mark it active in the DB."""
    raise NotImplementedError
