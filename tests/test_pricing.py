"""Tests for the pure pricing core (price-sync feature).

Covers the deterministic pieces the PRD pins exactly: minor-unit normalization,
sale detection from PRICES (not the lying on_sale flag), the Laura cost/price
formula, the >60% change guard, and the write-avoidance diff (skip Shopify when
nothing changed — the real perf win, since Shopify writes are the bottleneck).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from inventory_sync import pricing


D = Decimal


class TestToIls:
    def test_minor_unit_zero_is_passthrough(self):
        assert pricing.to_ils("1290", 0) == D("1290")

    def test_minor_unit_two_scales_by_100(self):
        assert pricing.to_ils("449", 2) == D("4.49")

    def test_blank_or_none_is_none(self):
        assert pricing.to_ils(None, 0) is None
        assert pricing.to_ils("", 0) is None


class TestSaleSignal:
    def test_real_sale(self):
        on, pct = pricing.sale_signal(D("1000"), D("750"))
        assert on is True and pct == 25

    def test_flag_lies_regular_equals_sale_is_not_a_sale(self):
        # Segal's trap: on_sale=true but regular==sale → NOT a sale (§5.3).
        on, pct = pricing.sale_signal(D("2830"), D("2830"))
        assert on is False and pct == 0

    def test_missing_sale_is_not_on_sale(self):
        assert pricing.sale_signal(D("1000"), None) == (False, 0)

    def test_pct_rounds(self):
        _, pct = pricing.sale_signal(D("1290"), D("850"))
        assert pct == 34  # 1 - 850/1290 = 34.1% → 34


class TestLauraFormula:
    def test_default_discount_price_matches_prd(self):
        # base 36 @ 10% → 63.72 (§11.2 worked example), no whole-shekel rounding.
        assert pricing.laura_price(D("36")) == D("63.72")

    def test_compare_at_is_base_times_1_77(self):
        assert pricing.laura_compare_at(D("36")) == D("63.72")

    def test_default_price_equals_compare_at(self):
        # At the base 10% discount there is no strikethrough (price == compare_at).
        base = D("500")
        assert pricing.laura_price(base) == pricing.laura_compare_at(base)

    def test_bigger_supplier_discount_lowers_price_below_compare_at(self):
        base = D("100")
        assert pricing.laura_price(base, D("0.30")) < pricing.laura_compare_at(base)

    def test_no_rounding_to_whole_shekels(self):
        assert pricing.laura_price(D("36")) != D("64")  # 63.72, not 64

    def test_trade_discount_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            pricing.laura_price(D("100"), D("0.05"))   # < 10%
        with pytest.raises(ValueError):
            pricing.laura_price(D("100"), D("0.60"))   # > 50%


class TestChangeGuard:
    def test_flags_change_over_60_percent(self):
        assert pricing.change_too_large(D("1000"), D("300")) is True   # -70%
        assert pricing.change_too_large(D("1000"), D("1700")) is True  # +70%

    def test_allows_change_under_60_percent(self):
        assert pricing.change_too_large(D("1000"), D("600")) is False  # -40%

    def test_no_previous_price_is_not_too_large(self):
        assert pricing.change_too_large(None, D("500")) is False


class TestResolveTarget:
    def test_on_sale_sets_compare_at(self):
        t = pricing.resolve_target(D("1000"), D("750"))
        assert t.price == D("750") and t.compare_at == D("1000")

    def test_not_on_sale_has_no_compare_at(self):
        t = pricing.resolve_target(D("1000"), None)
        assert t.price == D("1000") and t.compare_at is None

    def test_fake_sale_regular_equals_sale_is_plain_price(self):
        t = pricing.resolve_target(D("2830"), D("2830"))
        assert t.price == D("2830") and t.compare_at is None


class TestWooTarget:
    """WooCommerce Store API `prices` object → TargetPrice (Segal + Snir)."""

    def test_on_sale(self):
        t = pricing.woo_target({"regular_price": "1000", "sale_price": "750", "currency_minor_unit": 0})
        assert t.price == D("750") and t.compare_at == D("1000")

    def test_not_on_sale_regular_equals_sale(self):
        t = pricing.woo_target({"regular_price": "1000", "sale_price": "1000", "currency_minor_unit": 0})
        assert t.price == D("1000") and t.compare_at is None

    def test_minor_units_scaled(self):
        t = pricing.woo_target({"regular_price": "44900", "sale_price": "33700", "currency_minor_unit": 2})
        assert t.price == D("337.00") and t.compare_at == D("449.00")

    def test_missing_sale_price_is_plain(self):
        t = pricing.woo_target({"regular_price": "1000", "currency_minor_unit": 0})
        assert t.price == D("1000") and t.compare_at is None

    def test_no_regular_price_returns_none(self):
        assert pricing.woo_target({"currency_minor_unit": 0}) is None


class TestDecidePrice:
    """The per-product decision the sync uses: noop / write / blocked."""

    def test_noop_when_unchanged(self):
        t = pricing.TargetPrice(price=D("100"))
        assert pricing.decide_price(D("100"), None, t) is pricing.PriceAction.NOOP

    def test_write_on_small_change(self):
        t = pricing.TargetPrice(price=D("90"))
        assert pricing.decide_price(D("100"), None, t) is pricing.PriceAction.WRITE

    def test_blocked_on_change_over_60pct(self):
        t = pricing.TargetPrice(price=D("30"))          # -70%
        assert pricing.decide_price(D("100"), None, t) is pricing.PriceAction.BLOCKED

    def test_write_when_sale_starts_within_guard(self):
        t = pricing.TargetPrice(price=D("80"), compare_at=D("100"))
        assert pricing.decide_price(D("100"), None, t) is pricing.PriceAction.WRITE

    def test_no_previous_price_writes(self):
        t = pricing.TargetPrice(price=D("500"))
        assert pricing.decide_price(None, None, t) is pricing.PriceAction.WRITE


class TestSaleTags:
    def test_on_sale_yields_supplier_and_pct_tag(self):
        t = pricing.TargetPrice(price=D("750"), compare_at=D("1000"))
        assert pricing.sale_tags(t) == {"supplier-sale", "sale-25"}

    def test_not_on_sale_yields_no_tags(self):
        assert pricing.sale_tags(pricing.TargetPrice(price=D("1000"))) == set()


class TestReconcileTags:
    def test_adds_sale_tags_keeping_others(self):
        new = pricing.reconcile_tags({"furniture", "segal"}, pricing.TargetPrice(D("750"), D("1000")))
        assert new == {"furniture", "segal", "supplier-sale", "sale-25"}

    def test_sale_ended_removes_sale_tags_keeps_others(self):
        current = {"furniture", "supplier-sale", "sale-25"}
        new = pricing.reconcile_tags(current, pricing.TargetPrice(D("1000")))   # no sale
        assert new == {"furniture"}

    def test_pct_change_swaps_the_pct_tag(self):
        current = {"supplier-sale", "sale-40", "x"}
        new = pricing.reconcile_tags(current, pricing.TargetPrice(D("750"), D("1000")))  # now 25%
        assert new == {"supplier-sale", "sale-25", "x"}


class TestNeedsWrite:
    def test_no_change_skips_write(self):
        t = pricing.TargetPrice(price=D("100"), compare_at=None)
        assert pricing.needs_write(D("100"), None, t) is False

    def test_price_change_needs_write(self):
        t = pricing.TargetPrice(price=D("90"), compare_at=None)
        assert pricing.needs_write(D("100"), None, t) is True

    def test_sale_started_needs_write(self):
        # compare_at appears (went on sale) even if price row same-ish
        t = pricing.TargetPrice(price=D("90"), compare_at=D("100"))
        assert pricing.needs_write(D("90"), None, t) is True

    def test_sale_ended_needs_write(self):
        # compare_at cleared (sale ended)
        t = pricing.TargetPrice(price=D("100"), compare_at=None)
        assert pricing.needs_write(D("100"), D("100"), t) is True
