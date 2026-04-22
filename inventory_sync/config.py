"""Typed config loaded from a pluggable ConfigStore. Required keys fail loudly."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dotenv import dotenv_values

from inventory_sync.log import Logger, get


class ConfigError(Exception):
    pass


def _bool(raw: str | None, *, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _collect_notification_routes(store: ConfigStore) -> dict[str, RouteSpec]:
    """Fold NOTIFY_<EVENT>_TO and NOTIFY_<EVENT>_VIA env pairs into per-event RouteSpecs.

    Adding a new event needs only two env vars — no code change. Master switches like
    NOTIFY_OPS_ENABLED are naturally skipped because they lack the _TO/_VIA suffix.
    """
    partial: dict[str, dict[str, str]] = {}
    for key, value in store.scan("NOTIFY_").items():
        for suffix, field in (("_TO", "to"), ("_VIA", "via")):
            if key.endswith(suffix):
                event_name = key[len("NOTIFY_"):-len(suffix)].lower()
                partial.setdefault(event_name, {})[field] = value
                break
    return {
        event: RouteSpec(
            to=(parts.get("to") or "none").strip().lower(),
            via=(parts.get("via") or "none").strip().lower(),
        )
        for event, parts in partial.items()
    }


class ConfigStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def require(self, key: str) -> str: ...
    def scan(self, prefix: str) -> dict[str, str]: ...


class DotenvConfigStore:
    def __init__(self, path: Path | str = ".env"):
        self._path = Path(path)
        file_values = {k: v for k, v in dotenv_values(str(self._path)).items() if v}
        self._values: dict[str, str] = {**file_values, **os.environ}

    def get(self, key: str) -> str | None:
        v = self._values.get(key)
        return v if v else None

    def require(self, key: str) -> str:
        v = self.get(key)
        if v is None:
            raise ConfigError(
                f"Missing required config key '{key}' - not found in {self._path} or environment."
            )
        return v

    def scan(self, prefix: str) -> dict[str, str]:
        """Return all known keys (from .env + environment) starting with `prefix`.

        Values are returned as-is; empty values are omitted by the dotenv loader
        but an explicit empty env var may still appear here.
        """
        return {k: v for k, v in self._values.items() if k.startswith(prefix)}


@dataclass(frozen=True)
class ShopifyConfig:
    store_url: str
    admin_api_token: str
    myshopify_domain: str
    api_version: str

    @property
    def admin_api_base_url(self) -> str:
        return f"https://{self.myshopify_domain}/admin/api/{self.api_version}"


@dataclass(frozen=True)
class VendorConfig:
    name: str
    url: str
    username: str | None
    password: str | None
    store_tag: str | None  # exact string in Shopify product.vendor field for this vendor


@dataclass(frozen=True)
class WhatsAppConfig:
    api_base_url: str | None
    api_token: str | None
    ops_number: str | None     # dev/ops alerts (sync errors, infra)
    client_number: str | None  # business alerts for the store owner (OOS, digests)

    @property
    def is_configured(self) -> bool:
        # api_token is optional — the local Go bridge has no auth; remote bridges may require it.
        # Either ops or client number is enough to enable WhatsApp notifications.
        return bool(self.api_base_url and (self.ops_number or self.client_number))


@dataclass(frozen=True)
class EmailConfig:
    """Generic email config. Provider-specific adapter reads api_key; interface stays the same."""
    provider: str | None          # 'resend' for now; adapter factory keys off this
    api_base_url: str | None      # e.g. https://api.resend.com
    api_key: str | None
    from_address: str | None
    ops_address: str | None
    client_address: str | None

    @property
    def is_configured(self) -> bool:
        return bool(
            self.provider and self.api_key and self.from_address
            and (self.ops_address or self.client_address)
        )


@dataclass(frozen=True)
class RouteSpec:
    """Two-dimensional routing for one event: who gets it and how."""
    to: str   # 'ops' | 'client' | 'both' | 'none'
    via: str  # 'whatsapp' | 'email' | 'both' | 'none'


@dataclass(frozen=True)
class NotificationConfig:
    """Two-dimensional routing per event:
      - recipient category (ops | client | both | none)
      - delivery channel   (whatsapp | email | both | none)

    Master switches silence a whole dimension:
      - ops_enabled / client_enabled    — recipient kill-switches
      - whatsapp_enabled / email_enabled — channel kill-switches
    """
    ops_enabled: bool
    client_enabled: bool
    whatsapp_enabled: bool
    email_enabled: bool
    routes: dict[str, RouteSpec]

    def route_for(self, event_type: str) -> RouteSpec:
        return self.routes.get(event_type) or RouteSpec(to="none", via="none")


@dataclass(frozen=True)
class Config:
    shopify: ShopifyConfig
    vendor: VendorConfig
    whatsapp: WhatsAppConfig
    email: EmailConfig
    notifications: NotificationConfig
    sync_interval: str
    database_url: str


def load(store: ConfigStore | None = None, log: Logger | None = None) -> Config:
    store = store or DotenvConfigStore()
    log = log or get("config")

    config = Config(
        shopify=ShopifyConfig(
            store_url=store.require("SHOPIFY_STORE_URL"),
            admin_api_token=store.require("SHOPIFY_ADMIN_API_TOKEN"),
            myshopify_domain=store.require("SHOPIFY_MYSHOPIFY_DOMAIN"),
            api_version=store.get("SHOPIFY_API_VERSION") or "2024-10",
        ),
        vendor=VendorConfig(
            name=store.require("VENDOR_NAME"),
            url=store.require("VENDOR_URL"),
            username=store.get("VENDOR_USERNAME"),
            password=store.get("VENDOR_PASSWORD"),
            store_tag=store.get("VENDOR_STORE_TAG"),
        ),
        whatsapp=WhatsAppConfig(
            api_base_url=store.get("WHATSAPP_API_BASE_URL"),
            api_token=store.get("WHATSAPP_API_TOKEN"),
            ops_number=store.get("WHATSAPP_OPS_NUMBER"),
            client_number=store.get("WHATSAPP_CLIENT_NUMBER"),
        ),
        email=EmailConfig(
            provider=store.get("EMAIL_PROVIDER"),
            api_base_url=store.get("EMAIL_API_BASE_URL"),
            api_key=store.get("EMAIL_API_KEY"),
            from_address=store.get("EMAIL_FROM"),
            ops_address=store.get("EMAIL_OPS_ADDRESS"),
            client_address=store.get("EMAIL_CLIENT_ADDRESS"),
        ),
        notifications=NotificationConfig(
            ops_enabled=_bool(store.get("NOTIFY_OPS_ENABLED"), default=True),
            client_enabled=_bool(store.get("NOTIFY_CLIENT_ENABLED"), default=True),
            whatsapp_enabled=_bool(store.get("NOTIFY_WHATSAPP_ENABLED"), default=True),
            email_enabled=_bool(store.get("NOTIFY_EMAIL_ENABLED"), default=True),
            routes=_collect_notification_routes(store),
        ),
        sync_interval=store.get("SYNC_INTERVAL") or "hourly",
        database_url=store.get("DATABASE_URL") or "sqlite:///inventory_sync.db",
    )

    # Never log secret values — only metadata and booleans.
    log.info(
        "config_loaded",
        shopify_store=config.shopify.store_url,
        vendor=config.vendor.name,
        vendor_url=config.vendor.url,
        vendor_auth=bool(config.vendor.username),
        sync_interval=config.sync_interval,
        whatsapp_configured=config.whatsapp.is_configured,
        email_configured=config.email.is_configured,
    )

    return config
