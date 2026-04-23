"""WhatsApp notification adapter backed by the whatsapp-notifier-bridge microservice.

Implements NotificationChannel. Posts to the bridge's HTTP API (the bridge
holds the WhatsApp session; this adapter is a thin client).

Bridge protocol:
    POST <base>/send  { recipient: "<phone>", message: "<text>" }
    -> 200 { ok: true,  message_id: "<id>" }
    -> 4xx/5xx { ok: false, error: "<reason>" }

The Bearer token is attached to httpx.Client.headers by the caller (see
_build_whatsapp_adapter in __main__.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from inventory_sync.log import Logger, get


class WhatsAppBridgeError(Exception):
    pass


@dataclass
class WhatsAppBridgeAdapter:
    client: httpx.Client  # base_url = http://<bridge-host>:8080/api
    recipient: str         # phone number or JID to notify
    logger: Logger = field(default_factory=lambda: get("adapters.whatsapp"))

    def send(self, subject: str, body: str) -> None:
        text = self._format(subject, body)
        log = self.logger.bind(recipient=self.recipient)

        try:
            resp = self.client.post("/send", json={"recipient": self.recipient, "message": text})
        except Exception as e:
            log.exception("whatsapp_send_network_failed")
            raise WhatsAppBridgeError(f"bridge unreachable: {e}") from e

        if resp.status_code != 200:
            log.error("whatsapp_send_bad_status", status=resp.status_code, body=resp.text[:200])
            raise WhatsAppBridgeError(f"bridge returned HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except ValueError as e:
            log.exception("whatsapp_send_bad_json")
            raise WhatsAppBridgeError("bridge returned invalid JSON") from e

        if not payload.get("ok"):
            message = payload.get("error", "unknown bridge error")
            log.error("whatsapp_send_unsuccessful", bridge_message=message)
            raise WhatsAppBridgeError(f"bridge reported failure: {message}")

        log.info("whatsapp_sent", message_id=payload.get("message_id"), subject=subject)

    @staticmethod
    def _format(subject: str, body: str) -> str:
        """WhatsApp has no 'subject' concept. We render subject as bold header when present."""
        if subject and body:
            return f"*{subject}*\n{body}"
        return subject or body
