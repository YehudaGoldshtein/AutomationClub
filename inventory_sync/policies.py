"""Stock policies — decide what StockChanges are needed for a product/vendor-stock pair."""
from __future__ import annotations

from inventory_sync.domain import (
    ChangeKind,
    Product,
    StockChange,
    StockLevel,
)


class DefaultStockPolicy:
    """v0.1 policy.

    Vendor OOS: set store stock to 0 and unpublish (each only if not already so).
    Vendor in stock: set store stock to vendor stock (if different); republish if currently unpublished.
    """

    def decide(self, product: Product, vendor_stock: StockLevel) -> list[StockChange]:
        changes: list[StockChange] = []

        if vendor_stock.is_out_of_stock:
            if not product.stock.is_out_of_stock:
                changes.append(
                    StockChange(
                        sku=product.sku,
                        kind=ChangeKind.SET_STOCK,
                        new_stock=StockLevel(0),
                        reason="vendor out of stock",
                    )
                )
            if product.published:
                changes.append(
                    StockChange(
                        sku=product.sku,
                        kind=ChangeKind.UNPUBLISH,
                        reason="vendor out of stock",
                    )
                )
            return changes

        if product.stock != vendor_stock:
            changes.append(
                StockChange(
                    sku=product.sku,
                    kind=ChangeKind.SET_STOCK,
                    new_stock=vendor_stock,
                    reason="vendor stock changed",
                )
            )
        if not product.published:
            changes.append(
                StockChange(
                    sku=product.sku,
                    kind=ChangeKind.REPUBLISH,
                    reason="vendor back in stock",
                )
            )
        return changes
