"""Pure pricing core for the price-sync feature. No I/O.

Design (from the PRD + our discussion):
  - Decide sales from PRICES, never the on_sale flag (it lies ~half the time).
  - Store price as a re-derivable value; recompute from the fetched input each run.
  - Write-avoidance is the point: `needs_write` lets the caller skip Shopify (the
    2 req/s bottleneck) for the ~99% of products whose price didn't change.

Money is Decimal throughout; prices round to 2 places (no whole-shekel rounding).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum

# --- Laura cost/price formula (PRD §11.2). The trade discount passes to the
# customer; markup keeps a constant ~67% margin on cost. ---
BASE_TRADE_DISCOUNT = Decimal("0.10")   # standard trade discount
MAX_TRADE_DISCOUNT = Decimal("0.50")    # cap (§11.3)
VAT = Decimal("1.18")
MARKUP_NUM, MARKUP_DEN = Decimal(5), Decimal(3)   # 5/3 ≈ 1.6667 — kept exact as a ratio
COMPARE_AT_FACTOR = Decimal("1.77")     # base × 1.77 == price at the default discount

MAX_CHANGE = Decimal("0.60")            # block a price move larger than 60% (safety)

_CENTS = Decimal("0.01")


def _round2(x: Decimal) -> Decimal:
    return x.quantize(_CENTS, rounding=ROUND_HALF_UP)


def to_ils(raw, minor_unit) -> Decimal | None:
    """Store-API price (integer string in minor units) → major units (÷10**minor_unit).

    None/'' → None. Never assume minor_unit=0 — a wrong assumption shifts the whole
    catalog by 100x."""
    if raw in (None, ""):
        return None
    return Decimal(str(raw)) / (Decimal(10) ** int(minor_unit or 0))


def sale_signal(regular, sale) -> tuple[bool, int]:
    """(is_on_sale, discount_pct) decided from the numbers, not the on_sale flag.

    A sale exists only when both prices are present and sale < regular; otherwise
    (False, 0) — including the Segal trap where on_sale=true but regular == sale."""
    if regular is None or sale is None or regular <= 0 or sale >= regular:
        return False, 0
    pct = ((Decimal(1) - Decimal(sale) / Decimal(regular)) * 100).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return True, int(pct)


def _check_trade(trade: Decimal) -> None:
    if not (BASE_TRADE_DISCOUNT <= trade <= MAX_TRADE_DISCOUNT):
        raise ValueError(f"trade discount out of range [0.10, 0.50]: {trade}")


def laura_cost(base: Decimal, trade: Decimal = BASE_TRADE_DISCOUNT) -> Decimal:
    """Actual cost incl. VAT: base × (1 − trade) × VAT."""
    _check_trade(trade)
    return base * (Decimal(1) - trade) * VAT


def laura_price(base: Decimal, trade: Decimal = BASE_TRADE_DISCOUNT) -> Decimal:
    """Shelf price: cost × markup. The trade discount is passed to the customer."""
    _check_trade(trade)
    return _round2(base * (Decimal(1) - trade) * VAT * MARKUP_NUM / MARKUP_DEN)


def laura_compare_at(base: Decimal) -> Decimal:
    """The 'regular' price — always at the base discount (base × 1.77)."""
    return _round2(base * COMPARE_AT_FACTOR)


def change_too_large(old, new) -> bool:
    """True if |new − old| / old exceeds MAX_CHANGE. No previous price → not too large."""
    if old is None or Decimal(old) == 0:
        return False
    return abs(Decimal(new) - Decimal(old)) / Decimal(old) > MAX_CHANGE


@dataclass(frozen=True)
class TargetPrice:
    """The price we want on the store: `price`, and `compare_at` (struck-through
    original) only when on sale — None clears any strikethrough."""
    price: Decimal
    compare_at: Decimal | None = None


def resolve_target(regular, sale) -> TargetPrice:
    """Extracted (regular, sale) → the store TargetPrice.

    On sale (sale < regular): sell at `sale`, strike through `regular`. Otherwise
    sell at `regular` with no strikethrough. Sale is decided by the numbers, so a
    fake flag (regular == sale) yields a plain price."""
    on, _ = sale_signal(regular, sale)
    if on:
        return TargetPrice(price=Decimal(sale), compare_at=Decimal(regular))
    return TargetPrice(price=Decimal(regular), compare_at=None)


def needs_write(current_price, current_compare_at, target: TargetPrice) -> bool:
    """Write-avoidance: only touch Shopify when price OR compare_at actually differs."""
    return current_price != target.price or current_compare_at != target.compare_at


def woo_target(prices: dict) -> TargetPrice | None:
    """WooCommerce Store API `prices` object → TargetPrice (Segal + Snir).

    Uses regular_price / sale_price (normalized by currency_minor_unit) and decides
    the sale from the numbers. None when there is no regular price to work from."""
    minor = prices.get("currency_minor_unit", 0)
    regular = to_ils(prices.get("regular_price"), minor)
    sale = to_ils(prices.get("sale_price"), minor)
    if regular is None:
        return None
    return resolve_target(regular, sale)


class PriceAction(str, Enum):
    NOOP = "noop"        # nothing changed → skip Shopify (the bottleneck)
    WRITE = "write"      # price/compare_at changed within the guard → update
    BLOCKED = "blocked"  # change exceeds the >60% guard → skip + report, never write


def decide_price(current_price, current_compare_at, target: TargetPrice) -> PriceAction:
    """Per-product decision for the sync: noop / write / blocked.

    NOOP is the common case (prices are stable) — that's the write-avoidance that
    keeps us off Shopify's rate limit. BLOCKED protects against a bad match / feed
    glitch writing a wildly wrong price."""
    if not needs_write(current_price, current_compare_at, target):
        return PriceAction.NOOP
    if change_too_large(current_price, target.price):
        return PriceAction.BLOCKED
    return PriceAction.WRITE
