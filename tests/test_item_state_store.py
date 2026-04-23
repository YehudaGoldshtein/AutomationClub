"""Contract tests for ItemStateStore.

Same suite runs against InMemoryItemStateStore and SqlItemStateStore.
Both must behave identically — proving the SQL adapter is a drop-in.
"""
from __future__ import annotations

import pytest
import sqlalchemy

from inventory_sync.fakes import InMemoryItemStateStore
from inventory_sync.interfaces import ItemStateStore
from inventory_sync.log import get
from inventory_sync.persistence.item_state_store import SqlItemStateStore

C = "maxbaby"   # customer_id used throughout the base contract
OTHER = "other"  # second customer for isolation tests


class ItemStateStoreContract:
    @pytest.fixture
    def store(self) -> ItemStateStore:
        raise NotImplementedError

    # --- fresh / empty state ---

    def test_fresh_store_returns_empty_set(self, store: ItemStateStore):
        assert store.get_active_skus(C, "laura", "unarchive_candidate") == set()

    def test_fresh_store_is_not_seeded(self, store: ItemStateStore):
        assert store.is_seeded(C, "laura", "unarchive_candidate") is False

    # --- set_active basic behavior ---

    def test_set_active_writes_skus(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"A", "B", "C"})
        assert store.get_active_skus(C, "laura", "unarchive_candidate") == {"A", "B", "C"}

    def test_set_active_marks_as_seeded(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", set())
        assert store.is_seeded(C, "laura", "unarchive_candidate") is True

    def test_set_active_marks_seeded_even_for_non_empty_set(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"X"})
        assert store.is_seeded(C, "laura", "unarchive_candidate") is True

    def test_set_active_replaces_prior_set_entirely(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"A", "B"})
        store.set_active(C, "laura", "unarchive_candidate", {"B", "C"})
        assert store.get_active_skus(C, "laura", "unarchive_candidate") == {"B", "C"}

    def test_set_active_empty_removes_all(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"A", "B"})
        store.set_active(C, "laura", "unarchive_candidate", set())
        assert store.get_active_skus(C, "laura", "unarchive_candidate") == set()

    def test_seeded_persists_even_when_active_set_emptied(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"A"})
        store.set_active(C, "laura", "unarchive_candidate", set())
        assert store.is_seeded(C, "laura", "unarchive_candidate") is True

    # --- isolation between vendors / state_keys ---

    def test_different_vendors_do_not_collide(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"X"})
        store.set_active(C, "snir", "unarchive_candidate", {"X", "Y"})
        assert store.get_active_skus(C, "laura", "unarchive_candidate") == {"X"}
        assert store.get_active_skus(C, "snir", "unarchive_candidate") == {"X", "Y"}

    def test_clearing_one_vendor_leaves_other_intact(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"X"})
        store.set_active(C, "snir", "unarchive_candidate", {"X"})
        store.set_active(C, "laura", "unarchive_candidate", set())
        assert store.get_active_skus(C, "snir", "unarchive_candidate") == {"X"}
        assert store.is_seeded(C, "snir", "unarchive_candidate") is True

    def test_different_state_keys_do_not_collide(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"X"})
        store.set_active(C, "laura", "oos", {"Y"})
        store.set_active(C, "laura", "unarchive_candidate", set())
        assert store.get_active_skus(C, "laura", "oos") == {"Y"}
        assert store.is_seeded(C, "laura", "oos") is True

    def test_seeded_flag_is_per_vendor_and_per_key(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"A"})
        assert store.is_seeded(C, "laura", "unarchive_candidate") is True
        assert store.is_seeded(C, "laura", "oos") is False
        assert store.is_seeded(C, "snir", "unarchive_candidate") is False

    # --- customer isolation ---

    def test_different_customers_do_not_collide(self, store: ItemStateStore):
        """Two customers carrying the same SKU from the same vendor must not collide."""
        store.set_active(C, "laura", "unarchive_candidate", {"1234"})
        store.set_active(OTHER, "laura", "unarchive_candidate", {"1234", "5678"})
        assert store.get_active_skus(C, "laura", "unarchive_candidate") == {"1234"}
        assert store.get_active_skus(OTHER, "laura", "unarchive_candidate") == {"1234", "5678"}

    def test_clearing_one_customer_leaves_other_intact(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"1234"})
        store.set_active(OTHER, "laura", "unarchive_candidate", {"1234"})
        store.set_active(C, "laura", "unarchive_candidate", set())
        assert store.get_active_skus(OTHER, "laura", "unarchive_candidate") == {"1234"}
        assert store.is_seeded(OTHER, "laura", "unarchive_candidate") is True

    def test_seeded_flag_is_per_customer(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"X"})
        assert store.is_seeded(C, "laura", "unarchive_candidate") is True
        assert store.is_seeded(OTHER, "laura", "unarchive_candidate") is False

    # --- bulk / scale smoke ---

    def test_can_store_large_active_set(self, store: ItemStateStore):
        big = {f"SKU-{i:06d}" for i in range(1000)}
        store.set_active(C, "laura", "unarchive_candidate", big)
        assert store.get_active_skus(C, "laura", "unarchive_candidate") == big

    def test_returned_set_is_independent_of_store_state(self, store: ItemStateStore):
        store.set_active(C, "laura", "unarchive_candidate", {"A", "B"})
        result = store.get_active_skus(C, "laura", "unarchive_candidate")
        result.add("Z")
        # Subsequent read must not reflect the caller's mutation
        assert store.get_active_skus(C, "laura", "unarchive_candidate") == {"A", "B"}


class TestInMemoryItemStateStore(ItemStateStoreContract):
    @pytest.fixture
    def store(self) -> ItemStateStore:
        return InMemoryItemStateStore()


class TestSqlItemStateStore(ItemStateStoreContract):
    @pytest.fixture
    def store(self) -> ItemStateStore:
        engine = sqlalchemy.create_engine("sqlite:///:memory:")
        sql = SqlItemStateStore(engine=engine, logger=get("test"))
        sql.create_schema()
        return sql
