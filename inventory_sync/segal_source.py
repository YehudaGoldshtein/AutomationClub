"""Segal source layer — pure parsers for the two data sources, no I/O.

Segal's data is hybrid (PRD §0):
  - the WooCommerce Store API product JSON  → structured fields
  - the product page HTML (`#more-info` tabs) → the metafield content

`parse_api_product` and `parse_tabs` are the pure transforms; the network lives
in adapters/segal_baby.py. See tests/test_segal_source.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup


@dataclass(frozen=True)
class SegalTab:
    """One product-page tab: its visible label + the pane's inner HTML."""
    label: str
    html: str


@dataclass(frozen=True)
class SegalProduct:
    """A Segal product assembled from the Store API (+ scraped tabs).

    `name` and `description_html` are raw (HTML entities not yet decoded — that
    happens in mapping). `price` is the regular price in major units.
    """
    sku: str
    name: str
    description_html: str
    price: Decimal | None
    sale_price: Decimal | None
    on_sale: bool
    image_urls: tuple[str, ...]
    category_slugs: tuple[str, ...]
    permalink: str
    in_stock: bool
    stock_qty: int | None
    tabs: tuple[SegalTab, ...] = ()


def _price(raw, minor_unit: int) -> Decimal | None:
    """WC Store API prices are integer strings in minor units. Scale to major."""
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw)).scaleb(-int(minor_unit or 0))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _stock_qty(data: dict) -> int:
    """0 when out of stock (out-of-stock still reports add_to_cart.maximum=1).

    When in stock, prefer add_to_cart.maximum (a clean int); fall back to
    parsing the "N במלאי" text.
    """
    if not data.get("is_in_stock"):
        return 0
    maximum = (data.get("add_to_cart") or {}).get("maximum")
    if isinstance(maximum, int) and maximum > 0:
        return maximum
    text = (data.get("stock_availability") or {}).get("text") or ""
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


def parse_api_product(data: dict, tabs: tuple[SegalTab, ...] = ()) -> SegalProduct:
    """WooCommerce Store API product dict -> SegalProduct."""
    prices = data.get("prices") or {}
    minor_unit = prices.get("currency_minor_unit", 0)
    return SegalProduct(
        sku=str(data.get("sku") or ""),
        name=str(data.get("name") or ""),
        description_html=str(data.get("description") or ""),
        price=_price(prices.get("regular_price"), minor_unit),
        sale_price=_price(prices.get("sale_price"), minor_unit),
        on_sale=bool(data.get("on_sale")),
        image_urls=tuple(
            img["src"] for img in (data.get("images") or []) if img.get("src")
        ),
        category_slugs=tuple(
            c["slug"] for c in (data.get("categories") or []) if c.get("slug")
        ),
        permalink=str(data.get("permalink") or ""),
        in_stock=bool(data.get("is_in_stock")),
        stock_qty=_stock_qty(data),
        tabs=tabs,
    )


def parse_tabs(html: str) -> tuple[SegalTab, ...]:
    """Product-page HTML -> tabs, keyed by the `#more-info` nav/tab-pane markup.

    Generic over how many tabs a product has: reads every `.nav-tabs a` label and
    matches it to its `.tab-content .tab-pane` by the anchor's `#tab-N` href.
    """
    soup = BeautifulSoup(html, "lxml")
    section = soup.find(id="more-info")
    if section is None:
        return ()
    tabs: list[SegalTab] = []
    for a in section.select(".nav-tabs a"):
        label = a.get_text(strip=True)
        href = a.get("href", "") or ""
        pane = section.find(id=href[1:]) if href.startswith("#") else None
        content = pane.decode_contents().strip() if pane is not None else ""
        tabs.append(SegalTab(label=label, html=content))
    return tuple(tabs)
