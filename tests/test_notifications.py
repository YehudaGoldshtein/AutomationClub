"""Tests for the Notifier — 4-channel routing with TO + VIA dimensions."""
from __future__ import annotations

import pytest

from inventory_sync.config import NotificationConfig, RouteSpec
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
    whatsapp_enabled: bool = True,
    email_enabled: bool = True,
    routes: dict[str, RouteSpec] | None = None,
) -> NotificationConfig:
    return NotificationConfig(
        ops_enabled=ops_enabled,
        client_enabled=client_enabled,
        whatsapp_enabled=whatsapp_enabled,
        email_enabled=email_enabled,
        routes=routes or {},
    )


def _notifier(cfg, log, ow=None, oe=None, cw=None, ce=None) -> Notifier:
    return Notifier(
        config=cfg,
        ops_whatsapp=ow, ops_email=oe,
        client_whatsapp=cw, client_email=ce,
        logger=log,
    )


class TestRecipientRouting:
    def test_to_ops_via_whatsapp(self, log):
        ow, oe, cw, ce = (InMemoryNotifier() for _ in range(4))
        n = _notifier(
            _cfg(routes={EVENT_SYNC_ERROR: RouteSpec("ops", "whatsapp")}),
            log, ow=ow, oe=oe, cw=cw, ce=ce,
        )
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert ow.sent == [("s", "b")]
        assert oe.sent == [] and cw.sent == [] and ce.sent == []

    def test_to_client_via_email(self, log):
        ow, oe, cw, ce = (InMemoryNotifier() for _ in range(4))
        n = _notifier(
            _cfg(routes={EVENT_ARCHIVE_AUDIT: RouteSpec("client", "email")}),
            log, ow=ow, oe=oe, cw=cw, ce=ce,
        )
        n.dispatch(EVENT_ARCHIVE_AUDIT, "s", "b")
        assert ce.sent == [("s", "b")]
        assert cw.sent == [] and ow.sent == [] and oe.sent == []

    def test_to_both_via_both_hits_four_targets(self, log):
        ow, oe, cw, ce = (InMemoryNotifier() for _ in range(4))
        n = _notifier(
            _cfg(routes={EVENT_SYNC_SUMMARY: RouteSpec("both", "both")}),
            log, ow=ow, oe=oe, cw=cw, ce=ce,
        )
        n.dispatch(EVENT_SYNC_SUMMARY, "s", "b")
        for ch in (ow, oe, cw, ce):
            assert ch.sent == [("s", "b")]

    def test_to_both_via_whatsapp_reaches_both_recipients_one_channel(self, log):
        ow, oe, cw, ce = (InMemoryNotifier() for _ in range(4))
        n = _notifier(
            _cfg(routes={EVENT_SYNC_SUMMARY: RouteSpec("both", "whatsapp")}),
            log, ow=ow, oe=oe, cw=cw, ce=ce,
        )
        n.dispatch(EVENT_SYNC_SUMMARY, "s", "b")
        assert ow.sent == [("s", "b")]
        assert cw.sent == [("s", "b")]
        assert oe.sent == [] and ce.sent == []


class TestGating:
    def test_none_to_silences(self, log):
        ow = InMemoryNotifier()
        n = _notifier(
            _cfg(routes={EVENT_SYNC_ERROR: RouteSpec("none", "whatsapp")}),
            log, ow=ow,
        )
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert ow.sent == []

    def test_none_via_silences(self, log):
        ow = InMemoryNotifier()
        n = _notifier(
            _cfg(routes={EVENT_SYNC_ERROR: RouteSpec("ops", "none")}),
            log, ow=ow,
        )
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert ow.sent == []

    def test_unknown_event_silences(self, log):
        ow = InMemoryNotifier()
        n = _notifier(_cfg(routes={}), log, ow=ow)
        n.dispatch("unknown_event", "s", "b")
        assert ow.sent == []


class TestMasterSwitches:
    def test_ops_disabled_silences_ops_routes(self, log):
        ow = InMemoryNotifier()
        cfg = _cfg(ops_enabled=False, routes={EVENT_SYNC_ERROR: RouteSpec("ops", "whatsapp")})
        _notifier(cfg, log, ow=ow).dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert ow.sent == []

    def test_client_disabled_silences_client_routes(self, log):
        ce = InMemoryNotifier()
        cfg = _cfg(client_enabled=False, routes={EVENT_ARCHIVE_AUDIT: RouteSpec("client", "email")})
        _notifier(cfg, log, ce=ce).dispatch(EVENT_ARCHIVE_AUDIT, "s", "b")
        assert ce.sent == []

    def test_whatsapp_disabled_falls_back_to_email_when_via_both(self, log):
        """Kill-switching WhatsApp should still deliver via email when via=both."""
        ow, oe = InMemoryNotifier(), InMemoryNotifier()
        cfg = _cfg(whatsapp_enabled=False, routes={EVENT_SYNC_ERROR: RouteSpec("ops", "both")})
        _notifier(cfg, log, ow=ow, oe=oe).dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert ow.sent == []
        assert oe.sent == [("s", "b")]

    def test_email_disabled_falls_back_to_whatsapp_when_via_both(self, log):
        ow, oe = InMemoryNotifier(), InMemoryNotifier()
        cfg = _cfg(email_enabled=False, routes={EVENT_SYNC_ERROR: RouteSpec("ops", "both")})
        _notifier(cfg, log, ow=ow, oe=oe).dispatch(EVENT_SYNC_ERROR, "s", "b")
        assert ow.sent == [("s", "b")]
        assert oe.sent == []

    def test_both_recipients_one_disabled_still_delivers_to_other(self, log):
        ow, cw = InMemoryNotifier(), InMemoryNotifier()
        cfg = _cfg(client_enabled=False, routes={EVENT_SYNC_SUMMARY: RouteSpec("both", "whatsapp")})
        _notifier(cfg, log, ow=ow, cw=cw).dispatch(EVENT_SYNC_SUMMARY, "s", "b")
        assert ow.sent == [("s", "b")]
        assert cw.sent == []


class TestFailureIsolation:
    def test_channel_exception_does_not_propagate(self, log):
        class BrokenChannel:
            def send(self, subject, body):
                raise RuntimeError("simulated outage")

        n = _notifier(
            _cfg(routes={EVENT_SYNC_ERROR: RouteSpec("ops", "whatsapp")}),
            log, ow=BrokenChannel(),
        )
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")  # must not raise

    def test_missing_channel_skips_without_error(self, log):
        """Config says to route somewhere but no adapter wired there."""
        n = _notifier(_cfg(routes={EVENT_SYNC_ERROR: RouteSpec("ops", "email")}), log)
        n.dispatch(EVENT_SYNC_ERROR, "s", "b")  # must not raise
