"""Resend email adapter — NotificationChannel over the Resend HTTP API.

Resend is one of potentially many email providers. Swapping to SendGrid /
SMTP / SES is a new adapter file implementing NotificationChannel — the
Notifier and everything above it doesn't change.

Resend API: POST https://api.resend.com/emails
Auth: Authorization: Bearer <api_key>
"""
from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from inventory_sync.log import Logger, get


class EmailSendError(Exception):
    pass


@dataclass
class ResendEmailAdapter:
    client: httpx.Client       # base_url = https://api.resend.com
    api_key: str
    from_address: str
    recipient: str
    logger: Logger = field(default_factory=lambda: get("adapters.email_resend"))

    def send(self, subject: str, body: str) -> None:
        log = self.logger.bind(recipient=self.recipient)
        try:
            resp = self.client.post(
                "/emails",
                json={
                    "from": self.from_address,
                    "to": [self.recipient],
                    "subject": subject,
                    "text": body,
                },
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except Exception as e:
            log.exception("email_send_network_failed")
            raise EmailSendError(f"email provider unreachable: {e}") from e

        if resp.status_code >= 400:
            log.error("email_send_bad_status", status=resp.status_code, body=resp.text[:200])
            raise EmailSendError(
                f"email provider returned HTTP {resp.status_code}: {resp.text[:200]}"
            )

        try:
            payload = resp.json()
        except ValueError as e:
            log.exception("email_send_bad_json")
            raise EmailSendError("email provider returned invalid JSON") from e

        log.info("email_sent", id=payload.get("id"), subject=subject)
