"""Stock policies — decide what StockChanges are needed for a product/vendor-snapshot pair."""
from __future__ import annotations

from inventory_sync.domain import (
    ChangeKind,
    Product,
    StockChange,
    StockLevel,
    VendorProductSnapshot,
)


class DefaultStockPolicy:
    """v0.1 policy — handles both exact-count and binary-only vendor signals.

    - `snapshot.stock_count is not None` -> exact mode: sync store to that number
      when it differs.
    - `snapshot.stock_count is None` -> binary mode:
        * vendor out of stock -> set store stock to 0 (if store has any).
        * vendor in stock AND store has stock -> no change (preserve store's exact count).
        * vendor in stock AND store at 0 -> set to 1 (back-in-stock, at least one).

    Does NOT automatically emit UNPUBLISH / REPUBLISH — those are owner-triggered
    manually in v0.1. The ChangeKinds stay available for a future manual entrypoint.

    `binary_restock_qty` is the placeholder set when a binary-only vendor reports
    back-in-stock but gives no exact count (default 10, not 1 — so it doesn't read
    as "last one" on the storefront).
    """

    def __init__(self, binary_restock_qty: int = 10) -> None:
        self.binary_restock_qty = binary_restock_qty

    def decide(self, product: Product, snapshot: VendorProductSnapshot) -> list[StockChange]:
        # Exact-count mode: vendor gave us a specific number.
        if snapshot.stock_count is not None:
            target = StockLevel(snapshot.stock_count)
            if product.stock != target:
                return [
                    StockChange(
                        sku=product.sku,
                        kind=ChangeKind.SET_STOCK,
                        new_stock=target,
                        reason="vendor exact count",
                    )
                ]
            return []

        # Binary mode — we only know in/out of stock.
        if not snapshot.is_available:
            if not product.stock.is_out_of_stock:
                return [
                    StockChange(
                        sku=product.sku,
                        kind=ChangeKind.SET_STOCK,
                        new_stock=StockLevel(0),
                        reason="vendor out of stock",
                    )
                ]
            return []

        # Vendor is available but no exact count — preserve store's count when positive.
        if product.stock.is_out_of_stock:
            return [
                StockChange(
                    sku=product.sku,
                    kind=ChangeKind.SET_STOCK,
                    new_stock=StockLevel(self.binary_restock_qty),
                    reason=f"vendor back in stock (binary, default {self.binary_restock_qty})",
                )
            ]
        return []
