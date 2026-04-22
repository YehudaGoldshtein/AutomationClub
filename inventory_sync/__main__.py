"""CLI entrypoint.

Usage:
    python -m inventory_sync                          # bootstrap (load config, print status)
    python -m inventory_sync sync [--dry-run]         # stock sync + post-sync audit + notifications
    python -m inventory_sync archive-audit [--send]   # standalone audit (dry-run by default)
"""
from __future__ import annotations

import argparse
import sys

import httpx
import sqlalchemy

from inventory_sync.adapters.email_resend import ResendEmailAdapter
from inventory_sync.adapters.laura_design import LauraDesignScraperAdapter
from inventory_sync.adapters.shopify import ShopifyAdapter
from inventory_sync.adapters.whatsapp_bridge import WhatsAppBridgeAdapter
from inventory_sync.persistence.sync_run_store import SqlSyncRunStore
from inventory_sync.audit import (
    find_archived_but_available,
    format_archived_but_available_message,
)
from inventory_sync.config import Config, load as load_config
from inventory_sync.engine import SyncEngine
from inventory_sync.log import Logger, configure
from inventory_sync.notifications import (
    EVENT_ARCHIVE_AUDIT,
    EVENT_SYNC_ERROR,
    EVENT_SYNC_SUMMARY,
    Notifier,
    PreviewNotifier,
)
from inventory_sync.policies import DefaultStockPolicy


def cmd_bootstrap(_args, log: Logger, cfg: Config) -> int:
    log.info(
        "boot_ok",
        store=cfg.shopify.store_url,
        vendor=cfg.vendor.name,
        interval=cfg.sync_interval,
        ops_enabled=cfg.notifications.ops_enabled,
        client_enabled=cfg.notifications.client_enabled,
    )
    return 0


def cmd_archive_audit(args, log: Logger, cfg: Config) -> int:
    store = _build_shopify_adapter(cfg, log)
    supplier = _build_laura_adapter(cfg, log)

    findings = find_archived_but_available(store=store, supplier=supplier, logger=log)
    subject, body = format_archived_but_available_message(findings, store_name="Max Baby")

    _print_preview(subject, body, f"FINDINGS: {len(findings)}")

    if not args.send:
        print("Dry-run (use --send to deliver via configured Notifier route).")
        return 0

    notifier = _build_notifier(cfg, log)
    notifier.dispatch(EVENT_ARCHIVE_AUDIT, subject, body)
    print("Dispatched via Notifier (see log for which channels were reached).")
    return 0


def cmd_sync(args, log: Logger, cfg: Config) -> int:
    """Run one sync pass: fetch once, stock-sync + audit on shared data, notify per config."""
    log.info("sync_command_start", dry_run=args.dry_run)
    store = _build_shopify_adapter(cfg, log)
    supplier = _build_laura_adapter(cfg, log)
    notifier = PreviewNotifier(logger=log) if args.dry_run else _build_notifier(cfg, log)
    run_store = _build_sync_run_store(cfg, log)

    # Share one fetch across sync and audit.
    try:
        products = store.list_products()
    except Exception as e:
        log.exception("sync_fetch_store_failed")
        notifier.dispatch(
            EVENT_SYNC_ERROR,
            "Inventory sync aborted",
            f"Could not read store catalog: {e}",
        )
        return 1

    vendor_ids = [p.vendor_product_id for p in products]
    try:
        snapshots = supplier.fetch_snapshots(vendor_ids)
    except Exception as e:
        log.exception("sync_fetch_supplier_failed")
        notifier.dispatch(
            EVENT_SYNC_ERROR,
            "Inventory sync aborted",
            f"Could not read supplier: {e}",
        )
        return 1

    # Stock sync.
    engine = SyncEngine(
        store=_DryRunStore(store, log) if args.dry_run else store,
        supplier=supplier,
        policy=DefaultStockPolicy(),
        logger=log,
    )
    run = engine.run_with_data(products, snapshots)

    # Post-sync audit on the same data (no extra fetches).
    archived = [p for p in products if not p.published]
    findings = []
    for p in archived:
        snap = snapshots.get(p.vendor_product_id)
        if snap and snap.is_available:
            from inventory_sync.audit import AuditFinding
            findings.append(AuditFinding(product=p, snapshot=snap))
    log.info("audit_post_sync", archived=len(archived), findings=len(findings))

    # Notifications.
    if run.errors:
        notifier.dispatch(
            EVENT_SYNC_ERROR,
            f"Sync run {run.run_id} had {len(run.errors)} error(s)",
            _format_sync_error_body(run),
        )
    notifier.dispatch(
        EVENT_SYNC_SUMMARY,
        f"Sync summary {run.run_id}",
        _format_sync_summary(run, findings_count=len(findings), dry_run=args.dry_run),
    )
    if findings:
        subject, body = format_archived_but_available_message(findings, store_name="Max Baby")
        notifier.dispatch(EVENT_ARCHIVE_AUDIT, subject, body)

    # Persist the run (never crashes sync on storage failure; log and continue).
    try:
        run_store.save(run)
    except Exception:
        log.exception("sync_run_persist_failed", run_id=run.run_id)

    # Stdout summary for the operator running the command.
    print(f"run_id={run.run_id}  items_checked={run.items_checked}"
          f"  changes_planned={len(run.changes_planned)}"
          f"  changes_applied={len(run.changes_applied)}"
          f"  errors={len(run.errors)}"
          f"  vendor_missing={len(run.vendor_missing)}"
          f"  duration={run.duration_seconds:.1f}s"
          f"  dry_run={args.dry_run}"
          f"  audit_findings={len(findings)}")
    return 0 if not run.errors else 1


class _DryRunStore:
    """Wrapper that lets reads through but no-ops writes. Used in --dry-run."""

    def __init__(self, inner, logger: Logger):
        self._inner = inner
        self._log = logger

    def list_products(self):
        return self._inner.list_products()

    def update_stock(self, sku, stock):
        self._log.info("DRY_RUN_update_stock", sku=sku, new_stock=stock.value)

    def unpublish(self, sku):
        self._log.info("DRY_RUN_unpublish", sku=sku)

    def republish(self, sku):
        self._log.info("DRY_RUN_republish", sku=sku)


def _format_sync_error_body(run) -> str:
    lines = [f"Errors in run {run.run_id}:"]
    for e in run.errors[:10]:
        lines.append(f"  - [{e.sku or '-'}] {e.message[:120]}")
    if len(run.errors) > 10:
        lines.append(f"  ... and {len(run.errors) - 10} more")
    return "\n".join(lines)


def _format_sync_summary(run, findings_count: int, dry_run: bool) -> str:
    return (
        f"Run {run.run_id}\n"
        f"Duration: {run.duration_seconds:.1f}s\n"
        f"Items checked: {run.items_checked}\n"
        f"Changes planned: {len(run.changes_planned)}\n"
        f"Changes applied: {len(run.changes_applied)}\n"
        f"Errors: {len(run.errors)}\n"
        f"Vendor-missing SKUs (no longer in vendor catalog): {len(run.vendor_missing)}\n"
        f"Unarchive candidates: {findings_count}\n"
        f"Mode: {'DRY-RUN' if dry_run else 'LIVE'}"
    )


def _print_preview(subject: str, body: str, footer: str = "") -> None:
    print("=" * 60)
    print(f"SUBJECT: {subject}")
    print("=" * 60)
    print(body)
    print("=" * 60)
    if footer:
        print(footer)


def _build_shopify_adapter(cfg: Config, log: Logger) -> ShopifyAdapter:
    client = httpx.Client(
        base_url=cfg.shopify.admin_api_base_url,
        headers={"X-Shopify-Access-Token": cfg.shopify.admin_api_token},
        timeout=30.0,
    )
    return ShopifyAdapter(client=client, logger=log, vendor_filter=cfg.vendor.store_tag)


def _build_laura_adapter(cfg: Config, log: Logger) -> LauraDesignScraperAdapter:
    client = httpx.Client(
        timeout=20.0,
        headers={"User-Agent": "Mozilla/5.0 (compatible; InventorySyncBot/0.1)"},
    )
    return LauraDesignScraperAdapter(
        client=client, logger=log, base_url=cfg.vendor.url.rstrip("/"), max_workers=4,
    )


def _build_notifier(cfg: Config, log: Logger) -> Notifier:
    return Notifier(
        config=cfg.notifications,
        ops_whatsapp=_build_whatsapp_adapter(cfg, cfg.whatsapp.ops_number, log) if cfg.whatsapp.ops_number else None,
        client_whatsapp=_build_whatsapp_adapter(cfg, cfg.whatsapp.client_number, log) if cfg.whatsapp.client_number else None,
        ops_email=_build_email_adapter(cfg, cfg.email.ops_address, log) if cfg.email.ops_address else None,
        client_email=_build_email_adapter(cfg, cfg.email.client_address, log) if cfg.email.client_address else None,
        logger=log,
    )


def _build_whatsapp_adapter(cfg: Config, recipient: str, log: Logger) -> WhatsAppBridgeAdapter:
    client = httpx.Client(base_url=cfg.whatsapp.api_base_url, timeout=15.0)
    return WhatsAppBridgeAdapter(client=client, recipient=recipient, logger=log)


def _build_email_adapter(cfg: Config, recipient: str, log: Logger):
    """Build an email channel for `recipient` using whatever provider is configured.

    Provider-swap pattern: add another `elif` branch here, implement NotificationChannel
    in a new adapters/email_<provider>.py — the Notifier above doesn't change.
    """
    if not cfg.email.is_configured:
        return None
    provider = (cfg.email.provider or "").lower()
    if provider == "resend":
        base_url = cfg.email.api_base_url or "https://api.resend.com"
        client = httpx.Client(base_url=base_url, timeout=15.0)
        return ResendEmailAdapter(
            client=client,
            api_key=cfg.email.api_key,
            from_address=cfg.email.from_address,
            recipient=recipient,
            logger=log,
        )
    log.warning("unknown_email_provider", provider=provider)
    return None


def _build_sync_run_store(cfg: Config, log: Logger) -> SqlSyncRunStore:
    engine = sqlalchemy.create_engine(cfg.database_url, future=True)
    store = SqlSyncRunStore(engine=engine, logger=log)
    store.create_schema()
    return store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inventory_sync")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("bootstrap", help="Load logger + config and print status")

    aa = sub.add_parser(
        "archive-audit",
        help="Find products archived in the store that are in stock at the supplier",
    )
    aa.add_argument("--send", action="store_true",
                    help="Dispatch via Notifier using the configured route (default: dry-run to stdout)")

    s = sub.add_parser(
        "sync",
        help="Run one sync pass: stock sync + post-sync audit + notifications",
    )
    s.add_argument("--dry-run", action="store_true",
                   help="Plan changes and send summary notifications, but don't write to the store")

    args = parser.parse_args(argv)
    command = args.command or "bootstrap"

    log = configure()
    log.info("app_start", version="0.1.0", command=command)
    cfg = load_config(log=log)

    if command == "bootstrap":
        return cmd_bootstrap(args, log, cfg)
    if command == "archive-audit":
        return cmd_archive_audit(args, log, cfg)
    if command == "sync":
        return cmd_sync(args, log, cfg)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
