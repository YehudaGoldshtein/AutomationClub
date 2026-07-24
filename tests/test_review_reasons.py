"""Tests for review-reason codes + the add/without/join helpers."""
from __future__ import annotations

from inventory_sync import review_reasons as rr


class TestCodes:
    def test_missing_at_source_code_exists(self):
        assert rr.MISSING_AT_SOURCE == "missing_at_source"


class TestJoin:
    def test_join_drops_none_and_comma_joins(self):
        assert rr.join(rr.NO_IMAGE, None, rr.NO_PRICE) == "no_image,no_price"

    def test_join_all_none_is_none(self):
        assert rr.join(None, None) is None


class TestAdd:
    def test_add_to_none_returns_single_code(self):
        assert rr.add(None, rr.MISSING_AT_SOURCE) == "missing_at_source"

    def test_add_appends_without_duplicating(self):
        assert rr.add("no_image", rr.MISSING_AT_SOURCE) == "no_image,missing_at_source"
        assert rr.add("no_image,missing_at_source", rr.MISSING_AT_SOURCE) == "no_image,missing_at_source"


class TestWithout:
    def test_without_removes_one_code(self):
        assert rr.without("no_image,missing_at_source", rr.MISSING_AT_SOURCE) == "no_image"

    def test_without_last_code_returns_none(self):
        assert rr.without("missing_at_source", rr.MISSING_AT_SOURCE) is None

    def test_without_absent_code_is_noop(self):
        assert rr.without("no_image", rr.MISSING_AT_SOURCE) == "no_image"

    def test_without_on_none_is_none(self):
        assert rr.without(None, rr.MISSING_AT_SOURCE) is None
