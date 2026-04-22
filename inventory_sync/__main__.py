"""CLI entrypoint.

Usage:
    python -m inventory_sync                     # bootstrap: load logger + config, print status
    python -m inventory_sync archive-audit       # dry-run: print unarchive candidates
    python -m inventory_sync archive-audit --send  # actually send via WhatsApp
"""
from __future__ import annotations

import argparse
import sys

import httpx

from inventory_sync.adapters.laura_design import LauraDesignScraperAdapter
from inventory_sync.adapters.shopify import ShopifyAdapter
from inventory_sync.adapters.whatsapp_bridge import WhatsAppBridgeAdapter
from inventory_sync.audit import (
    find_archived_but_available,
    format_archived_but_available_message,
)
from inventory_sync.config import Config, load as load_config
from inventory_sync.log import Logger, configure


def cmd_bootstrap(_args, log: Logger, cfg: Config) -> int:
    log.info(
        "boot_ok",
        store=cfg.shopify.store_url,
        vendor=cfg.vendor.name,
        interval=cfg.sync_interval,
    )
    return 0


def cmd_archive_audit(args, log: Logger, cfg: Config) -> int:
    log.info("archive_audit_start", send=args.send, recipient=args.to)

    store = _build_shopify_adapter(cfg, log)
    supplier = _build_laura_adapter(cfg, log)

    findings = find_archived_but_available(store=store, supplier=supplier, logger=log)
    subject, body = format_archived_but_available_message(findings, store_name="Max Baby")

    print("=" * 60)
    print(f"SUBJECT: {subject}")
    print("=" * 60)
    print(body)
    print("=" * 60)
    print(f"FINDINGS: {len(findings)}")

    if not args.send:
        print("Dry-run (use --send to deliver via WhatsApp).")
        return 0

    recipient = cfg.whatsapp.ops_number if args.to == "ops" else cfg.whatsapp.client_number
    if not recipient:
        log.error("notify_recipient_missing", to=args.to)
        print(f"ERROR: no WhatsApp number configured for '{args.to}'", file=sys.stderr)
        return 2

    notifier = _build_whatsapp_adapter(cfg, recipient, log)
    notifier.send(subject, body)
    print(f"SENT to {recipient} ({args.to})")
    return 0


def _build_shopify_adapter(cfg: Config, log: Logger) -> ShopifyAdapter:
    client = httpx.Client(
        base_url=cfg.shopify.admin_api_base_url,
        headers={"X-Shopify-Access-Token": cfg.shopify.admin_api_token},
        timeout=30.0,
    )
    return ShopifyAdapter(
        client=client,
        logger=log,
        vendor_filter=cfg.vendor.store_tag,
    )


def _build_laura_adapter(cfg: Config, log: Logger) -> LauraDesignScraperAdapter:
    client = httpx.Client(
        timeout=20.0,
        headers={"User-Agent": "Mozilla/5.0 (compatible; InventorySyncBot/0.1)"},
    )
    return LauraDesignScraperAdapter(
        client=client,
        logger=log,
        base_url=cfg.vendor.url.rstrip("/"),
        max_workers=8,
    )


def _build_whatsapp_adapter(cfg: Config, recipient: str, log: Logger) -> WhatsAppBridgeAdapter:
    client = httpx.Client(base_url=cfg.whatsapp.api_base_url, timeout=15.0)
    return WhatsAppBridgeAdapter(client=client, recipient=recipient, logger=log)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="inventory_sync")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("bootstrap", help="Load logger + config and print status")

    aa = sub.add_parser(
        "archive-audit",
        help="Find products archived in the store that are in stock at the supplier",
    )
    aa.add_argument("--send", action="store_true",
                    help="Actually send the audit via WhatsApp (default: dry-run)")
    aa.add_argument("--to", choices=["client", "ops"], default="client",
                    help="Recipient for --send (default: client)")

    args = parser.parse_args(argv)
    command = args.command or "bootstrap"

    log = configure()
    log.info("app_start", version="0.1.0", command=command)
    cfg = load_config(log=log)

    if command == "bootstrap":
        return cmd_bootstrap(args, log, cfg)
    if command == "archive-audit":
        return cmd_archive_audit(args, log, cfg)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
