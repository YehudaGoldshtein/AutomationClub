"""CLI entrypoint.

Usage:
    python -m inventory_sync                          # bootstrap (load config, print status)
    python -m inventory_sync sync [--dry-run]         # stock sync + post-sync audit + notifications
    python -m inventory_sync archive-audit [--send]   # standalone audit (dry-run by default)
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx
import sqlalchemy

from inventory_sync.adapters.email_resend import ResendEmailAdapter
from inventory_sync.adapters.laura_design import LauraDesignScraperAdapter
from inventory_sync.adapters.shopify import ShopifyAdapter
from inventory_sync.adapters.whatsapp_bridge import WhatsAppBridgeAdapter
from inventory_sync.audit import (
    find_archived_but_available,
    format_archived_but_available_message,
)
from inventory_sync.config import Config, load as load_config
from inventory_sync.customer_sync import customer_sync_pass
from inventory_sync.customers import (
    Customer,
    CustomerNotifications,
    CustomerStoreConfig,
    CustomerVendorBinding,
    Recipient,
    RouteSpec,
)
from inventory_sync.engine import SyncEngine
from inventory_sync.log import Logger, configure
from inventory_sync.notifications import (
    EVENT_ARCHIVE_AUDIT,
    EVENT_SYNC_ERROR,
    EVENT_SYNC_SUMMARY,
    Notifier,
    PreviewNotifier,
)
from inventory_sync.persistence.customer_repository import SqlCustomerRepository
from inventory_sync.persistence.item_state_store import SqlItemStateStore
from inventory_sync.persistence.store_product_store import SqlStoreProductStore
from inventory_sync.persistence.sync_run_store import SqlSyncRunStore
from inventory_sync.persistence.vendor_snapshot_cache import SqlVendorSnapshotCache
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
    """Run one sync pass for every due customer.

    Customers are loaded from the DB. On first boot, the legacy single-customer
    env config is seeded as customer id=maxbaby. Vendor fetches go through a
    shared TTL-gated cache — two customers sharing a vendor get one network round.

    --dry-run swaps in a no-write store wrapper and a preview notifier, and
    skips item_state + customer mark-synced writes.
    """
    log.info("sync_command_start", dry_run=args.dry_run)

    customer_repo = _build_customer_repo(cfg, log)
    _seed_customer_from_env_if_missing(customer_repo, cfg, log)
    # Pick only customers whose sync_interval has elapsed since last_synced_at.
    # Force-dispatched workflow runs honor this — useful when the master cron
    # wakes up every 15 min and only a subset of customers is actually due.
    customers = customer_repo.list_due()
    if not customers:
        log.info("no_customers_due")
        return 0

    run_store = _build_sync_run_store(cfg, log)
    raw_item_state_store = _build_item_state_store(cfg, log)
    item_state_store = _DryRunItemStateStore(raw_item_state_store, log) if args.dry_run else raw_item_state_store
    cache = _build_vendor_snapshot_cache(cfg, log)
    store_product_store = _build_store_product_store(cfg, log)
    effective_repo = None if args.dry_run else customer_repo

    # Shared Laura adapter (single vendor across all current customers).
    supplier = _build_laura_adapter(cfg, log)

    worst_exit = 0
    for customer in customers:
        raw_store = _build_shopify_adapter_for(customer, log)
        store = _DryRunStore(raw_store, log) if args.dry_run else raw_store
        notifier = PreviewNotifier(logger=log) if args.dry_run else _build_notifier_for(customer, cfg, log)

        run = customer_sync_pass(
            customer=customer,
            store=store,
            supplier=supplier,
            cache=cache,
            policy=DefaultStockPolicy(),
            notifier=notifier,
            item_state_store=item_state_store,
            sync_run_store=run_store,
            customer_repo=effective_repo,
            logger=log,
            store_product_store=store_product_store,
        )

        print(f"customer={customer.id}  run_id={run.run_id}"
              f"  items_checked={run.items_checked}"
              f"  changes_planned={len(run.changes_planned)}"
              f"  changes_applied={len(run.changes_applied)}"
              f"  errors={len(run.errors)}"
              f"  vendor_missing={len(run.vendor_missing)}"
              f"  duration={run.duration_seconds:.1f}s"
              f"  dry_run={args.dry_run}")
        if run.errors:
            worst_exit = 1
    return worst_exit


def _seed_customer_from_env_if_missing(
    repo: SqlCustomerRepository, cfg: Config, log: Logger
) -> None:
    """Bootstrap: if no customers exist, seed 'maxbaby' from the env config.

    Idempotent — re-runs upsert (preserves last_synced_at) or does nothing if
    the customer already exists. Env remains the source of truth for secrets;
    this only populates non-secret config.
    """
    if repo.get("maxbaby") is not None:
        return
    notifications = CustomerNotifications(
        ops_enabled=cfg.notifications.ops_enabled,
        client_enabled=cfg.notifications.client_enabled,
        whatsapp_enabled=cfg.notifications.whatsapp_enabled,
        email_enabled=cfg.notifications.email_enabled,
        recipients={
            "ops": Recipient(
                whatsapp=cfg.whatsapp.ops_number,
                email=cfg.email.ops_address,
            ),
            "client": Recipient(
                whatsapp=cfg.whatsapp.client_number,
                email=cfg.email.client_address,
            ),
        },
        routes={
            name: RouteSpec(to=r.to, via=r.via)
            for name, r in cfg.notifications.routes.items()
        },
    )
    customer = Customer(
        id="maxbaby",
        display_name="Max Baby",
        sync_interval_minutes=60,
        last_synced_at=None,
        store=CustomerStoreConfig(
            platform="shopify",
            store_url=cfg.shopify.store_url,
            myshopify_domain=cfg.shopify.myshopify_domain,
            api_version=cfg.shopify.api_version,
            display_name="Max Baby",
        ),
        vendors=[
            CustomerVendorBinding(
                name=cfg.vendor.name,
                url=cfg.vendor.url,
                store_tag=cfg.vendor.store_tag,
            )
        ],
        notifications=notifications,
    )
    repo.upsert(customer)
    log.info("seeded_customer_from_env", customer_id="maxbaby")


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


class _DryRunItemStateStore:
    """Read-through, write-skip wrapper for ItemStateStore. Used in --dry-run."""

    def __init__(self, inner, logger: Logger):
        self._inner = inner
        self._log = logger

    def get_active_skus(self, customer_id, vendor_name, state_key):
        return self._inner.get_active_skus(customer_id, vendor_name, state_key)

    def is_seeded(self, customer_id, vendor_name, state_key):
        return self._inner.is_seeded(customer_id, vendor_name, state_key)

    def set_active(self, customer_id, vendor_name, state_key, skus):
        self._log.info(
            "DRY_RUN_set_active_skipped",
            customer_id=customer_id,
            vendor_name=vendor_name,
            state_key=state_key,
            sku_count=len(skus),
        )


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
    """Legacy single-customer Shopify adapter (used by archive-audit command)."""
    client = httpx.Client(
        base_url=cfg.shopify.admin_api_base_url,
        headers={"X-Shopify-Access-Token": cfg.shopify.admin_api_token},
        timeout=30.0,
    )
    return ShopifyAdapter(client=client, logger=log, vendor_filter=cfg.vendor.store_tag)


def _resolve_shopify_token(customer_id: str) -> str:
    """Per-customer Shopify Admin token.

    Convention: SHOPIFY_TOKEN_<UPPER_CUSTOMER_ID> (hyphens → underscores).
    Falls back to the legacy SHOPIFY_ADMIN_API_TOKEN for maxbaby so existing
    deployments keep working unchanged.
    """
    env_key = f"SHOPIFY_TOKEN_{customer_id.upper().replace('-', '_')}"
    token = os.environ.get(env_key, "").strip()
    if token:
        return token
    return os.environ.get("SHOPIFY_ADMIN_API_TOKEN", "").strip()


def _build_shopify_adapter_for(customer: Customer, log: Logger) -> ShopifyAdapter:
    """Build a Shopify adapter scoped to a specific customer."""
    if not customer.store.myshopify_domain or not customer.store.api_version:
        raise ValueError(
            f"customer {customer.id!r} has incomplete Shopify config "
            "(myshopify_domain / api_version required)"
        )
    token = _resolve_shopify_token(customer.id)
    if not token:
        raise ValueError(f"no Shopify token found for customer {customer.id!r}")
    base_url = (
        f"https://{customer.store.myshopify_domain}"
        f"/admin/api/{customer.store.api_version}"
    )
    vendor_filter = customer.vendors[0].store_tag if customer.vendors else None
    client = httpx.Client(
        base_url=base_url,
        headers={"X-Shopify-Access-Token": token},
        timeout=30.0,
    )
    return ShopifyAdapter(client=client, logger=log, vendor_filter=vendor_filter)


def _build_laura_adapter(cfg: Config, log: Logger) -> LauraDesignScraperAdapter:
    client = httpx.Client(
        timeout=20.0,
        headers={"User-Agent": "Mozilla/5.0 (compatible; InventorySyncBot/0.1)"},
    )
    return LauraDesignScraperAdapter(
        client=client, logger=log, base_url=cfg.vendor.url.rstrip("/"), max_workers=4,
    )


def _build_notifier(cfg: Config, log: Logger) -> Notifier:
    """Legacy single-customer notifier (used by archive-audit command)."""
    return Notifier(
        config=cfg.notifications,
        ops_whatsapp=_build_whatsapp_adapter(cfg, cfg.whatsapp.ops_number, log) if cfg.whatsapp.ops_number else None,
        client_whatsapp=_build_whatsapp_adapter(cfg, cfg.whatsapp.client_number, log) if cfg.whatsapp.client_number else None,
        ops_email=_build_email_adapter(cfg, cfg.email.ops_address, log) if cfg.email.ops_address else None,
        client_email=_build_email_adapter(cfg, cfg.email.client_address, log) if cfg.email.client_address else None,
        logger=log,
    )


def _build_notifier_for(customer: Customer, cfg: Config, log: Logger) -> Notifier:
    """Per-customer notifier. Routing + recipients come from customer.notifications;
    transport credentials (bridge url+token, Resend API key) stay global in cfg."""
    from inventory_sync.config import NotificationConfig as CfgNotifConfig, RouteSpec as CfgRouteSpec

    notif = customer.notifications
    if notif is None:
        raise ValueError(f"customer {customer.id!r} has no notifications config")

    cfg_notifications = CfgNotifConfig(
        ops_enabled=notif.ops_enabled,
        client_enabled=notif.client_enabled,
        whatsapp_enabled=notif.whatsapp_enabled,
        email_enabled=notif.email_enabled,
        routes={name: CfgRouteSpec(to=r.to, via=r.via) for name, r in notif.routes.items()},
    )

    ops = notif.recipients.get("ops")
    client = notif.recipients.get("client")

    return Notifier(
        config=cfg_notifications,
        ops_whatsapp=_build_whatsapp_adapter(cfg, ops.whatsapp, log) if ops and ops.whatsapp else None,
        client_whatsapp=_build_whatsapp_adapter(cfg, client.whatsapp, log) if client and client.whatsapp else None,
        ops_email=_build_email_adapter(cfg, ops.email, log) if ops and ops.email else None,
        client_email=_build_email_adapter(cfg, client.email, log) if client and client.email else None,
        logger=log,
    )


def _build_whatsapp_adapter(cfg: Config, recipient: str, log: Logger) -> WhatsAppBridgeAdapter:
    headers = {}
    if cfg.whatsapp.api_token:
        headers["Authorization"] = f"Bearer {cfg.whatsapp.api_token}"
    client = httpx.Client(base_url=cfg.whatsapp.api_base_url, headers=headers, timeout=15.0)
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


def _build_engine(cfg: Config) -> sqlalchemy.Engine:
    # pool_pre_ping: Neon serverless drops idle SSL connections during long vendor-fetch loops; revive silently instead of crashing.
    return sqlalchemy.create_engine(cfg.database_url, future=True, pool_pre_ping=True)


def _build_sync_run_store(cfg: Config, log: Logger) -> SqlSyncRunStore:
    store = SqlSyncRunStore(engine=_build_engine(cfg), logger=log)
    store.create_schema()
    return store


def _build_item_state_store(cfg: Config, log: Logger) -> SqlItemStateStore:
    store = SqlItemStateStore(engine=_build_engine(cfg), logger=log)
    store.create_schema()
    return store


def _build_customer_repo(cfg: Config, log: Logger) -> SqlCustomerRepository:
    repo = SqlCustomerRepository(engine=_build_engine(cfg), logger=log)
    repo.create_schema()
    return repo


def _build_vendor_snapshot_cache(cfg: Config, log: Logger) -> SqlVendorSnapshotCache:
    cache = SqlVendorSnapshotCache(engine=_build_engine(cfg), logger=log)
    cache.create_schema()
    return cache


def _build_store_product_store(cfg: Config, log: Logger) -> SqlStoreProductStore:
    sps = SqlStoreProductStore(engine=_build_engine(cfg), logger=log)
    sps.create_schema()
    return sps


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
