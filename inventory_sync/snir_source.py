"""Snir source layer — pure parsers for the two data sources, no I/O.

Snir's data is hybrid (see MAPPING-snir-categories.md §5):
  - the WooCommerce Store API product JSON → structured fields
  - the product page HTML (`.woocommerce-Tabs-panel--tech_details`) → the one tab
    we still need (technical details → custom.infoo). Everything else is either an
    API field (description) or a constant (warranty, delivery).

Differences from Segal: price is in whole units (`minor_unit=0`), stock is binary
(`is_in_stock` only — `add_to_cart.maximum` is always 9999), body_html comes from
`short_description`, and categories are routed by **id** not slug.

The network lives in adapters/snir_baby.py. See tests/test_snir_source.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup

_PANEL_PREFIX = "woocommerce-Tabs-panel--"


@dataclass(frozen=True)
class SnirTab:
    """One product-page tab: the panel name (e.g. "tech_details") + inner HTML."""
    label: str
    html: str


@dataclass(frozen=True)
class SnirProduct:
    """A Snir product assembled from the Store API (+ scraped tabs).

    `name`, `short_description_html` and `description_html` are raw (HTML entities
    not yet decoded — that happens in mapping). `price` is the regular price in
    major units. Stock is binary: `in_stock` only (Snir exposes no quantity).
    """
    sku: str
    name: str
    short_description_html: str   # → product.body_html
    description_html: str          # → metafield custom.view_productss
    price: Decimal | None
    in_stock: bool
    image_urls: tuple[str, ...]
    category_ids: tuple[int, ...]
    permalink: str
    tabs: tuple[SnirTab, ...] = ()
    wc_type: str = ""          # WooCommerce product type: "simple" | "variable"
    variation_count: int = 0   # number of variations (from the Store API list object)


def _price(raw, minor_unit: int) -> Decimal | None:
    """WC Store API prices are integer strings in minor units. Scale to major.

    Snir uses `minor_unit=0` (whole shekels), so this is usually a no-op scale.
    """
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw)).scaleb(-int(minor_unit or 0))
    except (InvalidOperation, ValueError, TypeError):
        return None


def parse_api_product(data: dict, tabs: tuple[SnirTab, ...] = ()) -> SnirProduct:
    """WooCommerce Store API product dict -> SnirProduct."""
    prices = data.get("prices") or {}
    minor_unit = prices.get("currency_minor_unit", 0)
    return SnirProduct(
        sku=str(data.get("sku") or ""),
        name=str(data.get("name") or ""),
        short_description_html=str(data.get("short_description") or ""),
        description_html=str(data.get("description") or ""),
        price=_price(prices.get("regular_price"), minor_unit),
        in_stock=bool(data.get("is_in_stock")),
        image_urls=tuple(
            img["src"] for img in (data.get("images") or []) if img.get("src")
        ),
        category_ids=tuple(
            c["id"] for c in (data.get("categories") or []) if c.get("id") is not None
        ),
        permalink=str(data.get("permalink") or ""),
        tabs=tabs,
        wc_type=str(data.get("type") or ""),
        variation_count=len(data.get("variations") or []),
    )


def parse_tabs(html: str) -> tuple[SnirTab, ...]:
    """Product-page HTML -> tabs, keyed by the WooCommerce panel class name.

    Each tab pane is `<div class="... woocommerce-Tabs-panel--<name> ...">`. The
    `<name>` (e.g. `tech_details`, `description`, `oc_theme_product_tab_2`) is the
    stable, position-independent key. Only `tech_details` is consumed downstream.
    """
    soup = BeautifulSoup(html or "", "lxml")
    tabs: list[SnirTab] = []
    for panel in soup.select(f"[class*='{_PANEL_PREFIX}']"):
        name = next(
            (c[len(_PANEL_PREFIX):] for c in (panel.get("class") or [])
             if c.startswith(_PANEL_PREFIX)),
            None,
        )
        if name:
            tabs.append(SnirTab(label=name, html=panel.decode_contents().strip()))
    return tuple(tabs)


def tab_html(tabs: tuple[SnirTab, ...], label: str) -> str:
    """Inner HTML of the tab with `label`, or "" if absent."""
    for tab in tabs:
        if tab.label == label:
            return tab.html
    return ""
