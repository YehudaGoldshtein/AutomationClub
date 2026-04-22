"""Tests for the Notifier — config-driven routing to channels."""
from __future__ import annotations

import pytest

from inventory_sync.config import NotificationConfig
from inventory_sync.fakes import InMemoryNotifier
from inventory_sync.log import Logger, configure
from inventory_sync.notifications import (
    EVENT_ARCHIVE_AUDIT,
    EVENT_SYNC_ERROR,
    EVENT_SYNC_SUMMARY,
    Notifier,
)


@pytest.fixture
def log(tmp_path) -> Logger:
    return configure(log_dir=tmp_path / "logs")


def _cfg(
    ops_enabled: bool = True,
    client_enabled: bool = True,
    routes: dict[str, str] | None = None,
) -> NotificationConfig:
    return NotificationConfig(
        ops_enabled=ops_enabled,
        client_enabled=client_enabled,
        routes=routes or {},
    )


def _notifier(cfg, log, ops=None, client=None) -> Notifier:
    return Notifier(
        config=cfg, ops_channel=ops, client_channel=client, logger=log,
    )


class TestRouting:
    def test_ops_only(self, log):
        ops, client = InMemoryNotifier(), InMemoryNotifier()
        n = _notifier(_cfg(routes={EVENT_SYNC_ERROR: "ops"}), log, ops=ops, client=client)
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert ops.sent == [("s", "b")]
        assert client.sent == []

    def test_client_only(self, log):
        ops, client = InMemoryNotifier(), InMemoryNotifier()
        n = _notifier(_cfg(routes={EVENT_ARCHIVE_AUDIT: "client"}), log, ops=ops, client=client)
        n.dispatch(EVENT_ARCHIVE_AUDIT, "s", "b")
        assert ops.sent == []
        assert client.sent == [("s", "b")]

    def test_both(self, log):
        ops, client = InMemoryNotifier(), InMemoryNotifier()
        n = _notifier(_cfg(routes={EVENT_SYNC_SUMMARY: "both"}), log, ops=ops, client=client)
        n.dispatch(EVENT_SYNC_SUMMARY, "s", "b")
        assert ops.sent == [("s", "b")]
        assert client.sent == [("s", "b")]


class TestGating:
    def test_none_route_silences(self, log):
        ops, client = InMemoryNotifier(), InMemoryNotifier()
        n = _notifier(_cfg(routes={EVENT_SYNC_ERROR: "none"}), log, ops=ops, client=client)
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert ops.sent == [] and client.sent == []

    def test_empty_route_silences(self, log):
        ops, client = InMemoryNotifier(), InMemoryNotifier()
        n = _notifier(_cfg(routes={EVENT_SYNC_ERROR: ""}), log, ops=ops, client=client)
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert ops.sent == [] and client.sent == []

    def test_unknown_event_silences(self, log):
        ops, client = InMemoryNotifier(), InMemoryNotifier()
        n = _notifier(_cfg(routes={}), log, ops=ops, client=client)
        n.dispatch("completely_unknown_event", "s", "b")
        assert ops.sent == [] and client.sent == []

    def test_ops_disabled_silences_ops_routed_events(self, log):
        ops, client = InMemoryNotifier(), InMemoryNotifier()
        cfg = _cfg(ops_enabled=False, routes={EVENT_SYNC_ERROR: "ops"})
        n = _notifier(cfg, log, ops=ops, client=client)
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert ops.sent == []

    def test_client_disabled_silences_client_routed_events(self, log):
        ops, client = InMemoryNotifier(), InMemoryNotifier()
        cfg = _cfg(client_enabled=False, routes={EVENT_ARCHIVE_AUDIT: "client"})
        n = _notifier(cfg, log, ops=ops, client=client)
        n.dispatch(EVENT_ARCHIVE_AUDIT, "s", "b")
        assert client.sent == []

    def test_both_with_one_category_disabled_delivers_only_to_enabled(self, log):
        ops, client = InMemoryNotifier(), InMemoryNotifier()
        cfg = _cfg(client_enabled=False, routes={EVENT_SYNC_SUMMARY: "both"})
        n = _notifier(cfg, log, ops=ops, client=client)
        n.dispatch(EVENT_SYNC_SUMMARY, "s", "b")
        assert ops.sent == [("s", "b")]
        assert client.sent == []


class TestFailureIsolation:
    def test_channel_exception_does_not_propagate(self, log):
        """Notifications must never break the caller."""
        class BrokenChannel:
            def send(self, subject, body):
                raise RuntimeError("simulated channel outage")

        n = _notifier(
            _cfg(routes={EVENT_SYNC_ERROR: "ops"}),
            log,
            ops=BrokenChannel(),
        )
        # No raise
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")

    def test_route_to_ops_with_no_ops_channel_is_noop(self, log):
        """Config says route to ops but no ops channel wired — skip silently."""
        client = InMemoryNotifier()
        n = _notifier(_cfg(routes={EVENT_SYNC_ERROR: "ops"}), log, ops=None, client=client)
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert client.sent == []  # client shouldn't get it just because ops is missing
