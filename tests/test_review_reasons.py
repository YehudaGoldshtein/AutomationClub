"""Tests for the review-reason join helper."""
from __future__ import annotations

from inventory_sync import review_reasons as rr


class TestJoin:
    def test_join_drops_none_and_comma_joins(self):
        assert rr.join(rr.NO_IMAGE, None, rr.NO_PRICE) == "no_image,no_price"

    def test_join_all_none_is_none(self):
        assert rr.join(None, None) is None
