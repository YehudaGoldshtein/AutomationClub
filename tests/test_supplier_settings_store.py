"""Tests for the per-supplier sync toggle store."""
from __future__ import annotations

import sqlalchemy

from inventory_sync.log import get
from inventory_sync.persistence.supplier_settings_store import (
    SUPPLIERS,
    SqlSupplierSettingsStore,
)

C = "maxbaby"


def _store() -> SqlSupplierSettingsStore:
    s = SqlSupplierSettingsStore(engine=sqlalchemy.create_engine("sqlite:///:memory:"), logger=get("test"))
    s.create_schema()
    return s


def test_missing_row_defaults_enabled():
    s = _store()
    assert s.is_enabled(C, "bambino") is True
    assert s.enabled_map(C) == {sup: True for sup in SUPPLIERS}


def test_disable_then_reenable():
    s = _store()
    s.set_enabled(C, "bambino", False)
    assert s.is_enabled(C, "bambino") is False
    assert s.enabled_map(C)["bambino"] is False
    assert s.enabled_map(C)["segal"] is True          # others unaffected
    s.set_enabled(C, "bambino", True)
    assert s.is_enabled(C, "bambino") is True


def test_set_is_upsert_idempotent():
    s = _store()
    s.set_enabled(C, "snir", False)
    s.set_enabled(C, "snir", False)
    assert s.enabled_map(C)["snir"] is False


def test_scoped_per_customer():
    s = _store()
    s.set_enabled(C, "snir", False)
    assert s.is_enabled("other", "snir") is True       # a different tenant is unaffected
