"""Tests for ResendEmailAdapter — httpx.MockTransport for the Resend API."""
from __future__ import annotations

import json
import httpx
import pytest

from inventory_sync.adapters.email_resend import (
    EmailSendError,
    ResendEmailAdapter,
)
from inventory_sync.log import get

from tests.test_notifier import NotifierContract


def _make_adapter(handler, recipient: str = "dest@example.com") -> ResendEmailAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="https://api.resend.test")
    return ResendEmailAdapter(
        client=client,
        api_key="re_test_token",
        from_address="noreply@automationclub.test",
        recipient=recipient,
        logger=get("test"),
    )


def _success_handler(captured: list) -> callable:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({
            "path": request.url.path,
            "authorization": request.headers.get("authorization"),
            "body": json.loads(request.content.decode()),
        })
        return httpx.Response(200, json={"id": "abc-123"})
    return handler


class TestHappyPath:
    def test_posts_to_emails_endpoint(self):
        seen: list = []
        adapter = _make_adapter(_success_handler(seen))
        adapter.send("Subject", "Body")
        assert seen[0]["path"].endswith("/emails")

    def test_body_includes_from_to_subject_text(self):
        seen: list = []
        adapter = _make_adapter(_success_handler(seen), recipient="eli@maxbaby.test")
        adapter.send("Hi", "Body line 1\nBody line 2")
        body = seen[0]["body"]
        assert body["from"] == "noreply@automationclub.test"
        assert body["to"] == ["eli@maxbaby.test"]
        assert body["subject"] == "Hi"
        assert body["text"] == "Body line 1\nBody line 2"

    def test_authorization_header_carries_bearer_token(self):
        seen: list = []
        adapter = _make_adapter(_success_handler(seen))
        adapter.send("S", "B")
        assert seen[0]["authorization"] == "Bearer re_test_token"


class TestFailures:
    def test_http_4xx_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="unauthorized")
        with pytest.raises(EmailSendError, match="HTTP 401"):
            _make_adapter(handler).send("s", "b")

    def test_http_5xx_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="downtime")
        with pytest.raises(EmailSendError, match="HTTP 503"):
            _make_adapter(handler).send("s", "b")

    def test_network_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")
        with pytest.raises(EmailSendError, match="unreachable"):
            _make_adapter(handler).send("s", "b")

    def test_invalid_json_response_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json")
        with pytest.raises(EmailSendError, match="invalid JSON"):
            _make_adapter(handler).send("s", "b")


class TestResendSatisfiesNotifierContract(NotifierContract):
    @pytest.fixture
    def notifier(self) -> ResendEmailAdapter:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "id-xyz"})
        return _make_adapter(handler)
