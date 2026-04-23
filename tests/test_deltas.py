"""Tests for compute_delta — pure set-diff, used for state-transition detection."""
from __future__ import annotations

from inventory_sync.deltas import compute_delta


class TestComputeDelta:
    def test_empty_current_empty_stored(self):
        added, removed = compute_delta(current=set(), stored=set())
        assert added == set()
        assert removed == set()

    def test_all_fresh_additions(self):
        added, removed = compute_delta(current={"A", "B"}, stored=set())
        assert added == {"A", "B"}
        assert removed == set()

    def test_all_removed(self):
        added, removed = compute_delta(current=set(), stored={"A", "B"})
        assert added == set()
        assert removed == {"A", "B"}

    def test_no_change(self):
        added, removed = compute_delta(current={"A", "B"}, stored={"A", "B"})
        assert added == set()
        assert removed == set()

    def test_mixed(self):
        added, removed = compute_delta(current={"A", "B", "C"}, stored={"B", "C", "D"})
        assert added == {"A"}
        assert removed == {"D"}

    def test_inputs_not_mutated(self):
        current = {"A"}
        stored = {"B"}
        compute_delta(current=current, stored=stored)
        assert current == {"A"}
        assert stored == {"B"}

    def test_returned_sets_are_independent_objects(self):
        current = {"A"}
        stored: set[str] = set()
        added, _ = compute_delta(current=current, stored=stored)
        # Mutating the return value shouldn't affect the input
        added.add("Z")
        assert current == {"A"}
