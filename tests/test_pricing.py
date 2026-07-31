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
