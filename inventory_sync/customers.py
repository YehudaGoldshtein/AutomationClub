"""Customer domain model — a tenant in the multi-customer inventory sync.

A Customer binds one store (e.g. a Shopify) to one or more vendors, plus
notification preferences and a sync cadence. Secrets (tokens, passwords)
are NOT stored here; they're resolved from env at runtime, keyed by
customer id. This keeps the DB safe to snapshot and the config portable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CustomerStoreConfig:
    platform: str                  # "shopify" for now
    store_url: str                 # customer-facing URL, e.g. https://www.maxbaby.co.il/
    myshopify_domain: str | None   # admin-API domain for Shopify platform
    api_version: str | None        # platform-specific version tag
    display_name: str              # human-readable store name for messages


@dataclass(frozen=True)
class CustomerVendorBinding:
    """How this customer's store relates to a given vendor."""
    name: str                 # vendor slug — key into shared vendor_snapshot_cache
    url: str                  # vendor site URL
    store_tag: str | None     # exact product.vendor string in the customer's store


@dataclass(frozen=True)
class Recipient:
    whatsapp: str | None
    email: str | None


@dataclass(frozen=True)
class RouteSpec:
    to: str   # 'ops' | 'client' | 'both' | 'none'
    via: str  # 'whatsapp' | 'email' | 'both' | 'none'


@dataclass(frozen=True)
class CustomerNotifications:
    ops_enabled: bool
    client_enabled: bool
    whatsapp_enabled: bool
    email_enabled: bool
    recipients: dict[str, Recipient]        # 'ops' | 'client' -> Recipient
    routes: dict[str, RouteSpec]            # event_type -> RouteSpec

    def route_for(self, event_type: str) -> RouteSpec:
        return self.routes.get(event_type) or RouteSpec(to="none", via="none")


@dataclass(frozen=True)
class Customer:
    id: str                                 # slug, e.g. "maxbaby"
    display_name: str
    sync_interval_minutes: int
    last_synced_at: datetime | None
    store: CustomerStoreConfig
    vendors: list[CustomerVendorBinding] = field(default_factory=list)
    notifications: CustomerNotifications | None = None

    def to_config_json(self) -> str:
        return json.dumps(_encode_config(self), ensure_ascii=False, sort_keys=True)

    @staticmethod
    def from_row(
        *,
        id: str,
        display_name: str,
        sync_interval_minutes: int,
        last_synced_at: datetime | None,
        config_json: str,
    ) -> "Customer":
        data = json.loads(config_json)
        return _decode_config(
            id=id,
            display_name=display_name,
            sync_interval_minutes=sync_interval_minutes,
            last_synced_at=last_synced_at,
            data=data,
        )


def _encode_config(c: Customer) -> dict[str, Any]:
    store = {
        "platform": c.store.platform,
        "store_url": c.store.store_url,
        "myshopify_domain": c.store.myshopify_domain,
        "api_version": c.store.api_version,
        "display_name": c.store.display_name,
    }
    vendors = [
        {"name": v.name, "url": v.url, "store_tag": v.store_tag}
        for v in c.vendors
    ]
    notifications: dict[str, Any] | None = None
    if c.notifications:
        notifications = {
            "ops_enabled": c.notifications.ops_enabled,
            "client_enabled": c.notifications.client_enabled,
            "whatsapp_enabled": c.notifications.whatsapp_enabled,
            "email_enabled": c.notifications.email_enabled,
            "recipients": {
                role: {"whatsapp": r.whatsapp, "email": r.email}
                for role, r in c.notifications.recipients.items()
            },
            "routes": {
                name: {"to": r.to, "via": r.via}
                for name, r in c.notifications.routes.items()
            },
        }
    return {"store": store, "vendors": vendors, "notifications": notifications}


def _decode_config(
    *,
    id: str,
    display_name: str,
    sync_interval_minutes: int,
    last_synced_at: datetime | None,
    data: dict[str, Any],
) -> Customer:
    s = data.get("store") or {}
    store = CustomerStoreConfig(
        platform=s.get("platform") or "shopify",
        store_url=s.get("store_url") or "",
        myshopify_domain=s.get("myshopify_domain"),
        api_version=s.get("api_version"),
        display_name=s.get("display_name") or display_name,
    )
    vendors = [
        CustomerVendorBinding(
            name=v["name"],
            url=v.get("url") or "",
            store_tag=v.get("store_tag"),
        )
        for v in (data.get("vendors") or [])
    ]
    notifications: CustomerNotifications | None = None
    n = data.get("notifications")
    if n:
        recipients = {
            role: Recipient(whatsapp=r.get("whatsapp"), email=r.get("email"))
            for role, r in (n.get("recipients") or {}).items()
        }
        routes = {
            name: RouteSpec(to=r.get("to", "none"), via=r.get("via", "none"))
            for name, r in (n.get("routes") or {}).items()
        }
        notifications = CustomerNotifications(
            ops_enabled=bool(n.get("ops_enabled", True)),
            client_enabled=bool(n.get("client_enabled", True)),
            whatsapp_enabled=bool(n.get("whatsapp_enabled", True)),
            email_enabled=bool(n.get("email_enabled", True)),
            recipients=recipients,
            routes=routes,
        )
    return Customer(
        id=id,
        display_name=display_name,
        sync_interval_minutes=sync_interval_minutes,
        last_synced_at=last_synced_at,
        store=store,
        vendors=vendors,
        notifications=notifications,
    )
