"""Bambino source layer — pure parsers for the single master API, no I/O.

Bambino is the cleanest supplier: every brand storefront (Joie/Infanti/Graco/…)
is a window onto one master feed, `api.bambinok.com/cache/Bambino`, a single JSON
with `products` (526) + `websites` (per-brand policies incl. warranty). No WAF,
no per-product page scrape — everything is in the one document (PRD §0).

`parse_products` and `parse_warranties` are the pure transforms; the network
lives in adapters/bambino.py. See tests/test_bambino_source.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True)
class BambinoDiscount:
    """An active-sale window (PRD §2, `discount` type=overwrite).

    `amount` is the sale price; the product's regular `price` becomes the
    compare-at. Dates come as MM/DD/YYYY strings; None means open-ended.
    """
    amount: Decimal
    start_date: date | None
    end_date: date | None

    def active_on(self, day: date) -> bool:
        if self.start_date and day < self.start_date:
            return False
        if self.end_date and day > self.end_date:
            return False
        return True


@dataclass(frozen=True)
class BambinoProduct:
    """One Bambino catalog record (= one color) from the master feed.

    `id` is the feed's internal id (used only for color grouping + related
    linking); `catalog_number` is the 9-digit SKU key (§1). Text fields are raw
    (HTML entities decoded in mapping). Structured attrs feed `custom.infoo`;
    `specifications_html` feeds `custom.view_productss` (PRD §3).
    """
    id: int
    catalog_number: str
    title: str
    name: str
    color: str
    brand: str
    description_html: str
    specifications_html: str
    price: Decimal | None
    quantity: int
    barcode: str
    image_urls: tuple[str, ...]
    type_ids: tuple[int, ...]
    type_names: tuple[str, ...]
    is_main_color: bool
    main_color_product_id: int | None
    # structured attributes → custom.infoo (מאפיינים)
    age_from: int | None
    age_to: int | None
    weight: str
    height: str
    width: str
    length: str
    standard: str
    isofix: str
    # media
    video_urls: tuple[str, ...]
    product_manual: str
    related_product_ids: tuple[int, ...]
    discount: BambinoDiscount | None
    meta_title: str
    meta_description: str

    @property
    def in_stock(self) -> bool:
        return self.quantity > 0

    @property
    def group_id(self) -> int:
        """Color-group key: the main record's id (a variant points at it via
        `mainColorProductId`; the main has it null and is its own group)."""
        return self.main_color_product_id or self.id


def _decimal(raw) -> Decimal | None:
    """Money → Decimal. 0/empty/None → None (a $0 variant is a missing price)."""
    if raw in (None, "", 0, 0.0):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _num_str(raw) -> str:
    """A numeric attribute → display string; 0/empty → '' (unspecified)."""
    if raw in (None, "", 0, 0.0):
        return ""
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw)


def _parse_date(raw) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw), "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return None


def _discount(data: dict) -> BambinoDiscount | None:
    d = data.get("discount")
    if not isinstance(d, dict) or d.get("type") != "overwrite":
        return None
    amount = _decimal(d.get("amount"))
    if amount is None:
        return None
    return BambinoDiscount(
        amount=amount,
        start_date=_parse_date(d.get("startDate")),
        end_date=_parse_date(d.get("endDate")),
    )


def _video_urls(data: dict) -> tuple[str, ...]:
    """`video` (single URL) + `videos[].url` merged, deduped, order preserved."""
    urls: list[str] = []
    single = data.get("video")
    if isinstance(single, str) and single.strip():
        urls.append(single.strip())
    for item in data.get("videos") or []:
        url = item.get("url") if isinstance(item, dict) else None
        if isinstance(url, str) and url.strip():
            urls.append(url.strip())
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return tuple(out)


def parse_api_product(data: dict) -> BambinoProduct:
    """One master-feed product dict → BambinoProduct."""
    age = data.get("age") if isinstance(data.get("age"), dict) else {}
    return BambinoProduct(
        id=int(data.get("id") or 0),
        catalog_number=str(data.get("catalogNumber") or ""),
        title=str(data.get("title") or ""),
        name=str(data.get("name") or ""),
        color=str(data.get("color") or ""),
        brand=str(data.get("brand") or ""),
        description_html=str(data.get("description") or ""),
        specifications_html=str(data.get("specifications") or ""),
        price=_decimal(data.get("price")),
        quantity=int(data.get("quantity") or 0),
        barcode=str(data.get("barcode") or ""),
        image_urls=tuple(u for u in (data.get("images") or []) if isinstance(u, str) and u),
        type_ids=tuple(int(t["id"]) for t in (data.get("types") or []) if t.get("id") is not None),
        type_names=tuple(str(t.get("name") or "") for t in (data.get("types") or [])),
        is_main_color=bool(data.get("isMainColor")),
        main_color_product_id=(
            int(data["mainColorProductId"]) if data.get("mainColorProductId") else None
        ),
        age_from=age.get("from") if isinstance(age.get("from"), int) else None,
        age_to=age.get("to") if isinstance(age.get("to"), int) else None,
        weight=_num_str(data.get("weight")),
        height=_num_str(data.get("height")),
        width=_num_str(data.get("width")),
        length=_num_str(data.get("length")),
        standard=str(data.get("standard") or ""),
        isofix=str(data.get("isofix") or ""),
        video_urls=_video_urls(data),
        product_manual=str(data.get("productManual") or ""),
        related_product_ids=tuple(
            int(x) for x in (data.get("relatedProducts") or []) if isinstance(x, int)
        ),
        discount=_discount(data),
        meta_title=str(data.get("metaTitle") or ""),
        meta_description=str(data.get("metaDescription") or ""),
    )


def parse_products(master: dict) -> list[BambinoProduct]:
    """The master feed → all products (dedup/scope is the mapping/ingest's job)."""
    return [parse_api_product(p) for p in (master.get("products") or [])]


def parse_warranties(master: dict) -> dict[str, str]:
    """Brand → warranty HTML from `websites[].policies.warranty` (PRD §7).

    Keyed by the exact brand string ('Joie', 'Graco', …). Brands without a
    website row are absent here; mapping falls back to 'Bambino' (the master).
    """
    out: dict[str, str] = {}
    for site in master.get("websites") or []:
        brand = site.get("brand")
        warranty = (site.get("policies") or {}).get("warranty")
        if brand and isinstance(warranty, str) and warranty.strip():
            out[str(brand)] = warranty
    return out
