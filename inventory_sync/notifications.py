"""Notifier — routes events to channels per NotificationConfig.

Two-dimensional routing:
  - TO:  ops | client | both | none   (recipient category)
  - VIA: whatsapp | email | both | none  (delivery channel)

Master switches silence a whole dimension. Four channel slots total:
  ops_whatsapp, ops_email, client_whatsapp, client_email.
Any of them can be None (unconfigured) — the notifier just skips that slot.

Channels never raise through the Notifier: send failures are logged but
never propagate. Notifications are side channels; they must not break
the main sync flow.
"""
from __future__ import annotations

from dataclasses import dataclass

from inventory_sync.config import NotificationConfig, RouteSpec
from inventory_sync.interfaces import NotificationChannel
from inventory_sync.log import Logger


EVENT_SYNC_ERROR = "sync_error"
EVENT_SYNC_SUMMARY = "sync_summary"
EVENT_ARCHIVE_AUDIT = "archive_audit"


_VALID_RECIPIENTS = {"ops", "client", "both", "none", ""}
_VALID_VIAS = {"whatsapp", "email", "both", "none", ""}


@dataclass
class PreviewNotifier:
    """Notifier stand-in for --dry-run.

    Same dispatch signature but never sends — prints a short summary to stdout
    and logs intent so the operator can see what the live run would emit,
    without pinging anyone.
    """
    logger: Logger

    def dispatch(self, event_type: str, subject: str, body: str) -> None:
        first_line = body.split("\n", 1)[0][:120]
        self.logger.info("preview_dispatch", event_type=event_type, subject=subject)
        print(f"[DRY-RUN] would dispatch {event_type!r}: {subject!r} | {first_line}")


@dataclass
class Notifier:
    config: NotificationConfig
    ops_whatsapp: NotificationChannel | None
    ops_email: NotificationChannel | None
    client_whatsapp: NotificationChannel | None
    client_email: NotificationChannel | None
    logger: Logger

    def dispatch(self, event_type: str, subject: str, body: str) -> None:
        route = self.config.route_for(event_type)
        log = self.logger.bind(event_type=event_type, to=route.to, via=route.via)

        if route.to not in _VALID_RECIPIENTS or route.via not in _VALID_VIAS:
            log.warning("notification_unknown_route", to=route.to, via=route.via)
            return

        targets = self._resolve(route)
        if not targets:
            log.info("notification_skipped", reason="no_targets")
            return

        for name, channel in targets:
            try:
                channel.send(subject, body)
                log.info("notification_sent", target=name)
            except Exception:
                log.exception("notification_send_failed", target=name)

    def _resolve(self, route: RouteSpec) -> list[tuple[str, NotificationChannel]]:
        cfg = self.config
        recipients = []
        if route.to in ("ops", "both"):
            recipients.append("ops")
        if route.to in ("client", "both"):
            recipients.append("client")
        vias = []
        if route.via in ("whatsapp", "both"):
            vias.append("whatsapp")
        if route.via in ("email", "both"):
            vias.append("email")

        channel_map: dict[tuple[str, str], NotificationChannel | None] = {
            ("ops", "whatsapp"): self.ops_whatsapp,
            ("ops", "email"): self.ops_email,
            ("client", "whatsapp"): self.client_whatsapp,
            ("client", "email"): self.client_email,
        }

        targets: list[tuple[str, NotificationChannel]] = []
        for recipient in recipients:
            if recipient == "ops" and not cfg.ops_enabled:
                continue
            if recipient == "client" and not cfg.client_enabled:
                continue
            for via in vias:
                if via == "whatsapp" and not cfg.whatsapp_enabled:
                    continue
                if via == "email" and not cfg.email_enabled:
                    continue
                channel = channel_map.get((recipient, via))
                if channel is not None:
                    targets.append((f"{recipient}.{via}", channel))
        return targets
