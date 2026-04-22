"""Notifier — routes events to channels per NotificationConfig.

Two-level gate: a category master switch (ops_enabled / client_enabled) plus
per-event routing (ops | client | both | none). The master switch wins — if
a category is disabled, events routed to it are silently skipped.

Channels never raise through the Notifier: a send failure is logged but never
propagates. Notifications are side channels; they must not break the main flow.
"""
from __future__ import annotations

from dataclasses import dataclass

from inventory_sync.config import NotificationConfig
from inventory_sync.interfaces import NotificationChannel
from inventory_sync.log import Logger


# Canonical event type constants. Code should use these, not raw strings.
EVENT_SYNC_ERROR = "sync_error"
EVENT_SYNC_SUMMARY = "sync_summary"
EVENT_ARCHIVE_AUDIT = "archive_audit"


_VALID_ROUTES = {"ops", "client", "both", "none", ""}


@dataclass
class PreviewNotifier:
    """Notifier stand-in for --dry-run.

    Has the same dispatch signature but never sends — prints a short summary
    to stdout and logs the intent so the operator can see what the live run
    would emit, without pinging anyone.
    """
    logger: Logger

    def dispatch(self, event_type: str, subject: str, body: str) -> None:
        first_line = body.split("\n", 1)[0][:120]
        self.logger.info("preview_dispatch", event_type=event_type, subject=subject)
        print(f"[DRY-RUN] would dispatch {event_type!r}: {subject!r} | {first_line}")


@dataclass
class Notifier:
    config: NotificationConfig
    ops_channel: NotificationChannel | None
    client_channel: NotificationChannel | None
    logger: Logger

    def dispatch(self, event_type: str, subject: str, body: str) -> None:
        route = self.config.route_for(event_type)
        log = self.logger.bind(event_type=event_type, route=route)

        if route not in _VALID_ROUTES:
            log.warning("notification_unknown_route", configured=route)
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

    def _resolve(self, route: str) -> list[tuple[str, NotificationChannel]]:
        targets: list[tuple[str, NotificationChannel]] = []
        wants_ops = route in ("ops", "both")
        wants_client = route in ("client", "both")
        if wants_ops and self.config.ops_enabled and self.ops_channel is not None:
            targets.append(("ops", self.ops_channel))
        if wants_client and self.config.client_enabled and self.client_channel is not None:
            targets.append(("client", self.client_channel))
        return targets
