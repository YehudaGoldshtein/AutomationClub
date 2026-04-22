"""Contract tests for NotificationChannel. Every implementation must pass these."""
from __future__ import annotations

import pytest

from inventory_sync.fakes import InMemoryNotifier
from inventory_sync.interfaces import NotificationChannel


class NotifierContract:
    """Mix into a concrete test class and provide the `notifier` fixture."""

    @pytest.fixture
    def notifier(self) -> NotificationChannel:
        raise NotImplementedError("provide a `notifier` fixture in the subclass")

    def test_send_does_not_raise(self, notifier: NotificationChannel):
        notifier.send(subject="test", body="hello")

    def test_send_multiple_ok(self, notifier: NotificationChannel):
        notifier.send("a", "1")
        notifier.send("b", "2")

    def test_send_empty_body_ok(self, notifier: NotificationChannel):
        notifier.send("subject only", "")


class TestInMemoryNotifier(NotifierContract):
    @pytest.fixture
    def notifier(self) -> NotificationChannel:
        return InMemoryNotifier()

    def test_records_sent_messages_for_assertion(self):
        """InMemoryNotifier-specific capability: exposes `sent` list so tests can assert on delivery."""
        n = InMemoryNotifier()
        n.send("s1", "b1")
        n.send("s2", "b2")
        assert n.sent == [("s1", "b1"), ("s2", "b2")]
