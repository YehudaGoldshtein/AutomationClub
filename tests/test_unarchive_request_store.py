"""Tests for SqlUnarchiveRequestStore — the dashboard→backend unarchive intent queue.

The dashboard writes an intent row (customer + store_product_id) when the user
clicks "unarchive"; the reconcile job drains it. Pending == a row exists.
"""
from __future__ import annotations

import sqlalchemy

from inventory_sync.log import get
from inventory_sync.persistence.unarchive_request_store import SqlUnarchiveRequestStore

C = "maxbaby"


def _store():
    engine = sqlalchemy.create_engine("sqlite:///:memory:")
    s = SqlUnarchiveRequestStore(engine=engine, logger=get("test"))
    s.create_schema()
    return s


class TestUnarchiveRequestStore:
    def test_add_then_list_pending(self):
        s = _store()
        s.add(C, "111")
        s.add(C, "222")
        assert set(s.list_pending(C)) == {"111", "222"}

    def test_add_is_idempotent(self):
        s = _store()
        s.add(C, "111")
        s.add(C, "111")  # second click on the same product
        assert s.list_pending(C) == ["111"]

    def test_mark_done_removes_it(self):
        s = _store()
        s.add(C, "111")
        s.mark_done(C, "111")
        assert s.list_pending(C) == []

    def test_pending_is_scoped_per_customer(self):
        s = _store()
        s.add(C, "111")
        s.add("other", "999")
        assert s.list_pending(C) == ["111"]
        assert s.list_pending("other") == ["999"]

    def test_mark_done_is_scoped(self):
        s = _store()
        s.add(C, "111")
        s.add("other", "111")   # same product id, different tenant
        s.mark_done(C, "111")
        assert s.list_pending(C) == []
        assert s.list_pending("other") == ["111"]
