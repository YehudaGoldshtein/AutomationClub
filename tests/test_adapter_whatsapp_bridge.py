"""Tests for WhatsAppBridgeAdapter — httpx.MockTransport for the Go bridge."""
from __future__ import annotations

import httpx
import pytest

from inventory_sync.adapters.whatsapp_bridge import (
    WhatsAppBridgeAdapter,
    WhatsAppBridgeError,
)
from inventory_sync.log import get

from tests.test_notifier import NotifierContract


def _make_adapter(handler, recipient: str = "972504265054") -> WhatsAppBridgeAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://bridge.test/api")
    return WhatsAppBridgeAdapter(client=client, recipient=recipient, logger=get("test"))


def _success_handler(captured: list) -> callable:
    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured.append({
            "path": request.url.path,
            "body": _json.loads(request.content.decode()),
        })
        return httpx.Response(
            200, json={"ok": True, "message_id": "3EB0ABC123"}
        )
    return handler


class TestHappyPath:
    def test_posts_to_send_endpoint(self):
        seen: list = []
        adapter = _make_adapter(_success_handler(seen))
        adapter.send("Subject", "Body")
        assert seen[0]["path"].endswith("/send")

    def test_recipient_included_in_body(self):
        seen: list = []
        adapter = _make_adapter(_success_handler(seen), recipient="12345")
        adapter.send("S", "B")
        assert seen[0]["body"]["recipient"] == "12345"

    def test_subject_rendered_as_bold_header(self):
        seen: list = []
        adapter = _make_adapter(_success_handler(seen))
        adapter.send("Sync error", "Supplier unreachable")
        assert seen[0]["body"]["message"] == "*Sync error*\nSupplier unreachable"

    def test_empty_subject_sends_body_only(self):
        seen: list = []
        adapter = _make_adapter(_success_handler(seen))
        adapter.send("", "just body")
        assert seen[0]["body"]["message"] == "just body"

    def test_empty_body_sends_subject_only(self):
        seen: list = []
        adapter = _make_adapter(_success_handler(seen))
        adapter.send("only subject", "")
        assert seen[0]["body"]["message"] == "only subject"


class TestFailures:
    def test_http_non_200_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        adapter = _make_adapter(handler)
        with pytest.raises(WhatsAppBridgeError, match="HTTP 500"):
            adapter.send("s", "b")

    def test_bridge_reports_unsuccessful_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": False, "error": "not connected"})

        adapter = _make_adapter(handler)
        with pytest.raises(WhatsAppBridgeError, match="not connected"):
            adapter.send("s", "b")

    def test_network_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        adapter = _make_adapter(handler)
        with pytest.raises(WhatsAppBridgeError, match="unreachable"):
            adapter.send("s", "b")

    def test_invalid_json_response_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json")

        adapter = _make_adapter(handler)
        with pytest.raises(WhatsAppBridgeError, match="invalid JSON"):
            adapter.send("s", "b")


class TestWhatsAppBridgeSatisfiesNotifierContract(NotifierContract):
    """Re-run the NotificationChannel contract tests against the real adapter."""

    @pytest.fixture
    def notifier(self) -> WhatsAppBridgeAdapter:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True, "message_id": "3EB0XXX"})
        return _make_adapter(handler)
