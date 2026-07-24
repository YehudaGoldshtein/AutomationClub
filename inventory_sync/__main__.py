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
from inventory_sync.adapters.segal_baby import SegalBabyStoreApiAdapter
from inventory_sync.adapters.bambino import BambinoApiAdapter
from inventory_sync.bambino_delete import delete_existing_bambino_brands
from inventory_sync.bambino_ingest import ingest_bambino
from inventory_sync.laura_ingest import ingest_products, parse_laura_xlsx
from inventory_sync.segal_ingest import ingest_segal
from inventory_sync.segal_pass import SegalUnifiedSource
from inventory_sync.snir_pass import SnirUnifiedSource
from inventory_sync.adapters.snir_baby import SnirStoreApiAdapter
from inventory_sync.browser_fetch import PlaywrightClient  # playwright imported lazily on open()
from inventory_sync.supplier_pass import unified_pass
from inventory_sync.log import Logger, configure
from inventory_sync.reconcile import reconcile_approved_drafts, reconcile_rejected_drafts
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
        # A single un-appliable change (e.g. one untracked SKU) must not fail the
        # whole job — only a fatal/aborted run (store or supplier unreachable) does.
        if run.aborted:
            worst_exit = 1
        elif run.errors:
            log.warning("sync_completed_with_isolated_errors",
                        customer_id=customer.id, errors=len(run.errors))

        # Activate approved drafts + delete ignored ones since the last run.
        # Reconcile against an UNFILTERED store: approved drafts span every supplier
        # (Bambino/Segal/Snir/Laura), so a vendor-filtered store would fail to resolve
        # non-primary-vendor SKUs on republish. The stock sync above stays filtered.
        if not args.dry_run:
            reconcile_store = _build_shopify_adapter_for(customer, log, vendor_filter=None)
            rec = reconcile_approved_drafts(reconcile_store, store_product_store, customer.id, log)
            rej = reconcile_rejected_drafts(reconcile_store, store_product_store, customer.id, log)
            if rec.errors or rej.errors:
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


def cmd_ingest(args, log: Logger, cfg: Config) -> int:
    """Ingest a Laura product xlsx blob: create net-new products as drafts."""
    log = log.bind(customer_id=args.customer_id)
    log.info("ingest_command_start", blob_url=args.blob_url, dry_run=args.dry_run)

    resp = httpx.get(args.blob_url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    rows = parse_laura_xlsx(resp.content)
    log.info("ingest_parsed", rows=len(rows))

    store = _build_shopify_adapter(cfg, log)
    product_store = _build_store_product_store(cfg, log)
    summary = ingest_products(rows, store, product_store, args.customer_id, log, dry_run=args.dry_run)

    print(
        f"ingest: parsed={len(rows)} created={summary.created} "
        f"skipped_existing={summary.skipped_existing} flagged_review={summary.flagged_review} "
        f"archived={summary.archived} would_archive={summary.would_archive} "
        f"errors={summary.errors} would_create={summary.would_create} dry_run={summary.dry_run}"
    )
    return 0


def cmd_segal_ingest(args, log: Logger, cfg: Config) -> int:
    """Ingest Segal Baby products from the WC Store API: create net-new drafts."""
    log = log.bind(customer_id=args.customer_id)
    log.info("segal_ingest_command_start", dry_run=args.dry_run)

    source = _build_segal_adapter(log)
    # No vendor filter: dedup must catch an existing SKU regardless of its vendor tag.
    store = _build_shopify_adapter(cfg, log, vendor_filter=None)
    product_store = _build_store_product_store(cfg, log)
    summary = ingest_segal(source, store, product_store, args.customer_id, log, dry_run=args.dry_run)

    print(
        f"segal-ingest: created={summary.created} skipped_existing={summary.skipped_existing} "
        f"skipped_oos={summary.skipped_oos} errors={summary.errors} "
        f"would_create={summary.would_create} dry_run={summary.dry_run}"
    )
    return 1 if summary.errors else 0


def cmd_segal_sync(args, log: Logger, cfg: Config) -> int:
    """Sync stock (quantity + in/out of stock) for existing Segal products.

    Reuses the sync engine + DefaultStockPolicy: exact vendor count -> SET_STOCK
    (0 when out of stock). Never archives/unpublishes and never touches price.
    """
    from inventory_sync.segal_mapping import VENDOR as SEGAL_VENDOR

    log = log.bind(customer_id=args.customer_id)  # uniform customer_id-bound Axiom events
    log.info("segal_sync_command_start", dry_run=args.dry_run)
    supplier = _build_segal_adapter(log)
    raw_store = _build_shopify_adapter(cfg, log, vendor_filter=SEGAL_VENDOR)
    store = _DryRunStore(raw_store, log) if args.dry_run else raw_store

    run = SyncEngine(store=store, supplier=supplier, policy=DefaultStockPolicy(), logger=log).run()

    print(
        f"segal-sync: items_checked={run.items_checked} "
        f"changes_planned={len(run.changes_planned)} changes_applied={len(run.changes_applied)} "
        f"errors={len(run.errors)} vendor_missing={len(run.vendor_missing)} dry_run={args.dry_run}"
    )
    # Fail only on a fatal/aborted run; isolated per-item errors (e.g. a transient
    # 429) are logged but don't fail the job.
    if not run.aborted and run.errors:
        log.warning("segal_sync_completed_with_isolated_errors", errors=len(run.errors))
    return 1 if run.aborted else 0


def cmd_segal_pass(args, log: Logger, cfg: Config) -> int:
    """Unified Segal pass: stock-sync existing products + onboard new ones, one run.

    Supersedes running segal-sync + segal-ingest separately (PRD steady state):
    lists the in-scope categories once, syncs stock on products already in the
    store, and drafts any new in-stock products (tab-scraping only those). New
    products are drafts (approval-gated); a notification fires when any are created.
    """
    log = log.bind(customer_id=args.customer_id)
    log.info("segal_pass_command_start", dry_run=args.dry_run)

    source = SegalUnifiedSource(adapter=_build_segal_adapter(log), logger=log)
    raw_store = _build_shopify_adapter(cfg, log, vendor_filter=None)  # need all products to detect new
    store = _DryRunStore(raw_store, log) if args.dry_run else raw_store
    product_store = _build_store_product_store(cfg, log)

    def _notify_new(skus: list[str]) -> None:
        log.info("segal_pass_new_drafts", count=len(skus), skus=skus[:50])
        try:
            body = (f"{len(skus)} new Segal products drafted (pending approval):\n"
                    + ", ".join(skus[:30]) + ("" if len(skus) <= 30 else f" … +{len(skus)-30} more"))
            _build_notifier(cfg, log).dispatch(EVENT_SYNC_SUMMARY, "Segal: new draft products", body)
        except Exception:
            log.warning("segal_pass_notify_failed")

    summary = unified_pass(source, store, product_store, DefaultStockPolicy(), args.customer_id, log,
                           dry_run=args.dry_run, on_new_drafts=None if args.dry_run else _notify_new)

    print(
        f"segal-pass: items_checked={summary.items_checked} "
        f"stock_applied={summary.stock_changes_applied} stock_errors={summary.stock_errors} "
        f"created={summary.created} skipped_oos={summary.skipped_oos} "
        f"skipped_uncategorized={summary.skipped_uncategorized} create_errors={summary.create_errors} "
        f"would_create={summary.would_create} dry_run={summary.dry_run}"
    )
    return 1 if (summary.stock_errors or summary.create_errors) else 0


def cmd_snir_pass(args, log: Logger, cfg: Config) -> int:
    """Unified Snir pass: stock-sync existing products + onboard new ones, one run.

    Snir sits behind a WAF, so all fetching runs through a headless browser
    (PlaywrightClient) that solves the JS-challenge once and serves same-origin
    GETs. The catalog is listed once; existing products are stock-synced, new
    in-stock in-scope products are tab-scraped + drafted (approval-gated). The
    cross-supplier OOS gate (skip new out-of-stock products) is enforced by
    unified_pass. Requires the `browser` extra + `playwright install chromium`.
    """
    log = log.bind(customer_id=args.customer_id)
    log.info("snir_pass_command_start", dry_run=args.dry_run, headed=args.headed)

    raw_store = _build_shopify_adapter(cfg, log, vendor_filter=None)  # all products to detect new
    store = _DryRunStore(raw_store, log) if args.dry_run else raw_store
    product_store = _build_store_product_store(cfg, log)

    def _notify_new(skus: list[str]) -> None:
        log.info("snir_pass_new_drafts", count=len(skus), skus=skus[:50])
        try:
            body = (f"{len(skus)} new Snir products drafted (pending approval):\n"
                    + ", ".join(skus[:30]) + ("" if len(skus) <= 30 else f" … +{len(skus)-30} more"))
            _build_notifier(cfg, log).dispatch(EVENT_SYNC_SUMMARY, "Snir: new draft products", body)
        except Exception:
            log.warning("snir_pass_notify_failed")

    with PlaywrightClient(headless=not args.headed, logger=log) as client:
        source = SnirUnifiedSource(adapter=SnirStoreApiAdapter(client=client, logger=log), logger=log)
        summary = unified_pass(source, store, product_store, DefaultStockPolicy(), args.customer_id, log,
                               dry_run=args.dry_run, on_new_drafts=None if args.dry_run else _notify_new)

    print(
        f"snir-pass: items_checked={summary.items_checked} "
        f"stock_applied={summary.stock_changes_applied} stock_errors={summary.stock_errors} "
        f"created={summary.created} skipped_oos={summary.skipped_oos} "
        f"skipped_uncategorized={summary.skipped_uncategorized} create_errors={summary.create_errors} "
        f"would_create={summary.would_create} dry_run={summary.dry_run}"
    )
    return 1 if (summary.stock_errors or summary.create_errors) else 0


def cmd_bambino_ingest(args, log: Logger, cfg: Config) -> int:
    """Ingest Bambino products from the master API: create net-new drafts (§2-§9)."""
    log = log.bind(customer_id=args.customer_id)
    log.info("bambino_ingest_command_start", dry_run=args.dry_run)

    source = _build_bambino_adapter(log)
    # No vendor filter: dedup must catch an existing SKU regardless of its vendor tag.
    store = _build_shopify_adapter(cfg, log, vendor_filter=None)
    product_store = _build_store_product_store(cfg, log)
    summary = ingest_bambino(source, store, product_store, args.customer_id, log, dry_run=args.dry_run)

    print(
        f"bambino-ingest: created={summary.created} skipped_existing={summary.skipped_existing} "
        f"skipped_oos={summary.skipped_oos} skipped_uncategorized={summary.skipped_uncategorized} "
        f"linked={summary.linked} errors={summary.errors} "
        f"would_create={summary.would_create} dry_run={summary.dry_run}"
    )
    return 1 if summary.errors else 0


def cmd_bambino_sync(args, log: Logger, cfg: Config) -> int:
    """Sync stock (quantity + in/out) for existing Bambino products.

    Bambino spans 9 vendors under one feed, so we can't vendor-filter the store.
    Instead we list all products, keep only those whose SKU is a Bambino
    catalogNumber, and sync just those (avoids vendor-missing noise). Stock only;
    never archives and never touches price.
    """
    log = log.bind(customer_id=args.customer_id)
    log.info("bambino_sync_command_start", dry_run=args.dry_run)
    supplier = _build_bambino_adapter(log)
    raw_store = _build_shopify_adapter(cfg, log, vendor_filter=None)
    store = _DryRunStore(raw_store, log) if args.dry_run else raw_store

    catalog_skus = {p.catalog_number for p in supplier.fetch_all_products() if p.catalog_number}
    products = [p for p in store.list_products() if str(p.sku) in catalog_skus]
    snapshots = supplier.fetch_snapshots([p.vendor_product_id for p in products])
    run = SyncEngine(store=store, supplier=supplier, policy=DefaultStockPolicy(),
                     logger=log).run_with_data(products, snapshots)

    print(
        f"bambino-sync: items_checked={run.items_checked} "
        f"changes_planned={len(run.changes_planned)} changes_applied={len(run.changes_applied)} "
        f"errors={len(run.errors)} vendor_missing={len(run.vendor_missing)} dry_run={args.dry_run}"
    )
    if not run.aborted and run.errors:
        log.warning("bambino_sync_completed_with_isolated_errors", errors=len(run.errors))
    return 1 if run.aborted else 0


def cmd_bambino_delete_existing(args, log: Logger, cfg: Config) -> int:
    """Delete the 94 legacy brand products before re-import (§1). Dry-run by default.

    DESTRUCTIVE: requires --confirm to actually delete. A catalog guard (on unless
    --no-guard) never deletes a product whose SKU is a live Bambino catalogNumber.
    """
    log = log.bind(customer_id=args.customer_id)
    log.info("bambino_delete_command_start", confirm=args.confirm, no_guard=args.no_guard)
    store = _build_shopify_adapter(cfg, log, vendor_filter=None)

    protect_skus: set[str] = set()
    if not args.no_guard:
        supplier = _build_bambino_adapter(log)
        protect_skus = {p.catalog_number for p in supplier.fetch_all_products() if p.catalog_number}

    summary = delete_existing_bambino_brands(store, log, confirm=args.confirm,
                                             protect_skus=protect_skus)
    print(
        f"bambino-delete-existing: found={summary.found} deleted={summary.deleted} "
        f"protected={summary.protected} errors={summary.errors} confirmed={summary.confirmed}"
    )
    if not summary.confirmed:
        print("DRY-RUN — no products deleted. Re-run with --confirm to delete.")
    return 1 if summary.errors else 0


def cmd_reconcile(args, log: Logger, cfg: Config) -> int:
    """Activate approved draft products (draft → active) for one customer."""
    log = log.bind(customer_id=args.customer_id)
    log.info("reconcile_command_start")
    # No vendor filter: activate/delete drafts of any vendor (Laura + Segal).
    store = _build_shopify_adapter(cfg, log, vendor_filter=None)
    product_store = _build_store_product_store(cfg, log)
    act = reconcile_approved_drafts(store, product_store, args.customer_id, log)
    rej = reconcile_rejected_drafts(store, product_store, args.customer_id, log)
    print(f"reconcile: activated={act.activated} deleted={rej.deleted} "
          f"errors={act.errors + rej.errors}")
    return 1 if (act.errors or rej.errors) else 0


_VENDOR_FILTER_DEFAULT = object()


def _build_shopify_adapter(cfg: Config, log: Logger, vendor_filter=_VENDOR_FILTER_DEFAULT) -> ShopifyAdapter:
    """Legacy single-customer Shopify adapter (archive-audit, ingest, reconcile).

    vendor_filter defaults to the configured (Laura) store tag; pass None to
    list across all vendors (Segal ingest dedup, cross-vendor reconcile).
    """
    client = httpx.Client(
        base_url=cfg.shopify.admin_api_base_url,
        headers={"X-Shopify-Access-Token": cfg.shopify.admin_api_token},
        # Generous read timeout: create_product makes Shopify fetch images
        # server-side, which can exceed 30s and (on a slow link) time out the
        # read AFTER the product is created — causing duplicate creates.
        timeout=httpx.Timeout(120.0, connect=15.0),
    )
    vf = cfg.vendor.store_tag if vendor_filter is _VENDOR_FILTER_DEFAULT else vendor_filter
    return ShopifyAdapter(client=client, logger=log, vendor_filter=vf)


def _build_segal_adapter(log: Logger) -> SegalBabyStoreApiAdapter:
    client = httpx.Client(
        timeout=30.0,
        headers={"User-Agent": "Mozilla/5.0 (compatible; InventorySyncBot/0.1)"},
        follow_redirects=True,
    )
    return SegalBabyStoreApiAdapter(client=client, logger=log)


def _build_bambino_adapter(log: Logger) -> BambinoApiAdapter:
    client = httpx.Client(
        timeout=60.0,
        headers={"User-Agent": "Mozilla/5.0 (compatible; InventorySyncBot/0.1)"},
        follow_redirects=True,
    )
    return BambinoApiAdapter(client=client, logger=log)


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


def _build_shopify_adapter_for(customer: Customer, log: Logger,
                               vendor_filter=_VENDOR_FILTER_DEFAULT) -> ShopifyAdapter:
    """Build a Shopify adapter scoped to a specific customer.

    vendor_filter defaults to the customer's first vendor tag (stock sync); pass
    None to list across all vendors (reconcile activates approved drafts of every
    supplier, not just the primary one).
    """
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
    if vendor_filter is _VENDOR_FILTER_DEFAULT:
        vendor_filter = customer.vendors[0].store_tag if customer.vendors else None
    client = httpx.Client(
        base_url=base_url,
        headers={"X-Shopify-Access-Token": token},
        timeout=httpx.Timeout(120.0, connect=15.0),  # image-heavy create_product can exceed 30s
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
        ops_whatsapp=_build_whatsapp_adapter(cfg, ops.whatsapp, log, customer_id=customer.id) if ops and ops.whatsapp else None,
        client_whatsapp=_build_whatsapp_adapter(cfg, client.whatsapp, log, customer_id=customer.id) if client and client.whatsapp else None,
        ops_email=_build_email_adapter(cfg, ops.email, log) if ops and ops.email else None,
        client_email=_build_email_adapter(cfg, client.email, log) if client and client.email else None,
        logger=log,
    )


def _build_whatsapp_adapter(
    cfg: Config,
    recipient: str,
    log: Logger,
    customer_id: str | None = None,
) -> WhatsAppBridgeAdapter:
    headers = {}
    if cfg.whatsapp.api_token:
        headers["Authorization"] = f"Bearer {cfg.whatsapp.api_token}"
    client = httpx.Client(base_url=cfg.whatsapp.api_base_url, headers=headers, timeout=15.0)
    return WhatsAppBridgeAdapter(
        client=client, recipient=recipient, customer_id=customer_id, logger=log,
    )


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

    ing = sub.add_parser(
        "ingest",
        help="Ingest a Laura product xlsx blob: create net-new products as drafts",
    )
    ing.add_argument("--blob-url", required=True, help="URL of the uploaded xlsx blob")
    ing.add_argument("--customer-id", required=True, help="Tenant the blob belongs to")
    ing.add_argument("--dry-run", action="store_true",
                     help="Parse + group + report what would be created, but write nothing")

    seg = sub.add_parser(
        "segal-ingest",
        help="Ingest Segal Baby products from the WC Store API: create net-new drafts",
    )
    seg.add_argument("--customer-id", required=True, help="Tenant to ingest into")
    seg.add_argument("--dry-run", action="store_true",
                     help="Fetch + report what would be created, but write nothing")

    segsync = sub.add_parser(
        "segal-sync",
        help="Sync stock (quantity + in/out) for existing Segal products",
    )
    segsync.add_argument("--customer-id", default="maxbaby",
                         help="Tenant to tag Axiom events with (default: maxbaby)")
    segsync.add_argument("--dry-run", action="store_true",
                         help="Plan stock changes but don't write to the store")

    bam = sub.add_parser(
        "bambino-ingest",
        help="Ingest Bambino products from the master API: create net-new drafts",
    )
    bam.add_argument("--customer-id", required=True, help="Tenant to ingest into")
    bam.add_argument("--dry-run", action="store_true",
                     help="Fetch + report what would be created, but write nothing")

    bamsync = sub.add_parser(
        "bambino-sync",
        help="Sync stock (quantity + in/out) for existing Bambino products",
    )
    bamsync.add_argument("--customer-id", default="maxbaby",
                         help="Tenant to tag Axiom events with (default: maxbaby)")
    bamsync.add_argument("--dry-run", action="store_true",
                         help="Plan stock changes but don't write to the store")

    bamdel = sub.add_parser(
        "bambino-delete-existing",
        help="Delete the 94 legacy brand products before re-import (§1). Dry-run by default",
    )
    bamdel.add_argument("--customer-id", default="maxbaby", help="Tenant (default: maxbaby)")
    bamdel.add_argument("--confirm", action="store_true",
                        help="Actually delete (default: dry-run, deletes nothing)")
    bamdel.add_argument("--no-guard", action="store_true",
                        help="Disable the catalog guard that protects live Bambino SKUs")

    sp = sub.add_parser(
        "segal-pass",
        help="Unified Segal pass: stock-sync existing + onboard new drafts in one run",
    )
    sp.add_argument("--customer-id", default="maxbaby", help="Tenant (default: maxbaby)")
    sp.add_argument("--dry-run", action="store_true",
                    help="Plan stock changes + report new products, but write nothing")

    snp = sub.add_parser(
        "snir-pass",
        help="Unified Snir pass (via headless browser): stock-sync existing + onboard new drafts",
    )
    snp.add_argument("--customer-id", default="maxbaby", help="Tenant (default: maxbaby)")
    snp.add_argument("--dry-run", action="store_true",
                     help="Plan stock changes + report new products, but write nothing")
    snp.add_argument("--headed", action="store_true",
                     help="Run the browser headed (debugging; default headless)")

    rec = sub.add_parser(
        "reconcile",
        help="Activate approved draft products (draft → active)",
    )
    rec.add_argument("--customer-id", required=True, help="Tenant to reconcile")

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
    if command == "ingest":
        return cmd_ingest(args, log, cfg)
    if command == "segal-ingest":
        return cmd_segal_ingest(args, log, cfg)
    if command == "segal-sync":
        return cmd_segal_sync(args, log, cfg)
    if command == "segal-pass":
        return cmd_segal_pass(args, log, cfg)
    if command == "snir-pass":
        return cmd_snir_pass(args, log, cfg)
    if command == "bambino-ingest":
        return cmd_bambino_ingest(args, log, cfg)
    if command == "bambino-sync":
        return cmd_bambino_sync(args, log, cfg)
    if command == "bambino-delete-existing":
        return cmd_bambino_delete_existing(args, log, cfg)
    if command == "reconcile":
        return cmd_reconcile(args, log, cfg)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
