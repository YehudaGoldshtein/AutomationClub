"""Audit routines — diagnose drift between store and supplier that isn't solved by sync.

Currently: `find_archived_but_available` — products archived in the store that
are still in stock at the supplier (unarchive candidates).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from inventory_sync.domain import Product, VendorProductSnapshot
from inventory_sync.interfaces import StorePlatform, SupplierSource
from inventory_sync.log import Logger


@dataclass(frozen=True)
class AuditFinding:
    product: Product
    snapshot: VendorProductSnapshot


def find_archived_but_available(
    store: StorePlatform,
    supplier: SupplierSource,
    logger: Logger,
) -> list[AuditFinding]:
    """Return products archived in the store that the supplier reports as available."""
    log = logger.bind(audit="archived_but_available")
    log.info("audit_start")

    all_products = store.list_products()
    archived = [p for p in all_products if not p.published]
    log.info("archived_collected", archived_count=len(archived), total=len(all_products))

    vendor_ids = [p.vendor_product_id for p in archived]
    snapshots = supplier.fetch_snapshots(vendor_ids)
    log.info("snapshots_loaded", returned=len(snapshots), requested=len(vendor_ids))

    findings: list[AuditFinding] = []
    for p in archived:
        snap = snapshots.get(p.vendor_product_id)
        if snap and snap.is_available:
            findings.append(AuditFinding(product=p, snapshot=snap))

    log.info(
        "audit_complete",
        archived=len(archived),
        findings=len(findings),
        supplier_missing=len(archived) - len(snapshots),
    )
    return findings


def format_archived_but_available_message(
    findings: list[AuditFinding], store_name: str = "your store"
) -> tuple[str, str]:
    """Return (subject, body) suitable for a WhatsApp / email notification."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = f"Archive audit for {store_name} — {date}"

    if not findings:
        body = (
            f"No archived products in {store_name} are currently in stock at the supplier. "
            "Nothing to unarchive."
        )
        return subject, body

    lines: list[str] = [
        f"{len(findings)} archived product(s) in {store_name} are in stock at the supplier "
        f"right now — consider unarchiving:",
        "",
    ]
    for i, f in enumerate(findings, 1):
        parts = [f"{i}. {f.product.sku}"]
        price_str = _format_price(f.snapshot.price, f.snapshot.currency)
        if price_str:
            parts.append(price_str)
        if f.snapshot.name:
            parts.append(f.snapshot.name)
        lines.append(" - ".join(parts))

    body = "\n".join(lines)
    return subject, body


def _format_price(price: Decimal | None, currency: str | None) -> str | None:
    """Render as "NN ILS" or "NN.NN ILS", stripping trailing zeros from the fraction."""
    if price is None or not currency:
        return None
    quantized = price.quantize(Decimal("0.01"))
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} {currency}"
