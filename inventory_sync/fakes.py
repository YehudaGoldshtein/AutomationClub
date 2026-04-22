"""In-memory implementations of every interface. For tests and local runs."""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from inventory_sync.domain import (
    SKU,
    Product,
    StockLevel,
    VendorProductId,
)


class InMemoryStore:
    def __init__(self, products: list[Product] | None = None):
        self._products: dict[SKU, Product] = {p.sku: p for p in (products or [])}

    def list_products(self) -> list[Product]:
        return list(self._products.values())

    def update_stock(self, sku: SKU, stock: StockLevel) -> None:
        self._products[sku] = replace(self._products[sku], stock=stock)

    def unpublish(self, sku: SKU) -> None:
        self._products[sku] = replace(self._products[sku], published=False)

    def republish(self, sku: SKU) -> None:
        self._products[sku] = replace(self._products[sku], published=True)

    def get(self, sku: SKU) -> Product:
        return self._products[sku]


class InMemorySupplier:
    def __init__(self, stock: dict[VendorProductId, StockLevel] | None = None):
        self._stock: dict[VendorProductId, StockLevel] = dict(stock or {})

    def fetch_stock(
        self, ids: Iterable[VendorProductId]
    ) -> dict[VendorProductId, StockLevel]:
        return {vid: self._stock[vid] for vid in ids if vid in self._stock}

    def set_stock(self, vid: VendorProductId, level: StockLevel) -> None:
        self._stock[vid] = level


class InMemoryNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, subject: str, body: str) -> None:
        self.sent.append((subject, body))
