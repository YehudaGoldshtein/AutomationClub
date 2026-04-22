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


class ConfigStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def require(self, key: str) -> str: ...


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


@dataclass(frozen=True)
class ShopifyConfig:
    store_url: str
    admin_api_token: str


@dataclass(frozen=True)
class VendorConfig:
    name: str
    url: str
    username: str | None
    password: str | None


@dataclass(frozen=True)
class WhatsAppConfig:
    api_base_url: str | None
    api_token: str | None
    notify_to: str | None

    @property
    def is_configured(self) -> bool:
        return bool(self.api_base_url and self.api_token and self.notify_to)


@dataclass(frozen=True)
class EmailConfig:
    provider: str | None
    from_address: str | None
    api_key: str | None
    notify_to: str | None

    @property
    def is_configured(self) -> bool:
        return bool(self.provider and self.from_address and self.notify_to)


@dataclass(frozen=True)
class Config:
    shopify: ShopifyConfig
    vendor: VendorConfig
    whatsapp: WhatsAppConfig
    email: EmailConfig
    sync_interval: str


def load(store: ConfigStore | None = None, log: Logger | None = None) -> Config:
    store = store or DotenvConfigStore()
    log = log or get("config")

    config = Config(
        shopify=ShopifyConfig(
            store_url=store.require("SHOPIFY_STORE_URL"),
            admin_api_token=store.require("SHOPIFY_ADMIN_API_TOKEN"),
        ),
        vendor=VendorConfig(
            name=store.require("VENDOR_NAME"),
            url=store.require("VENDOR_URL"),
            username=store.get("VENDOR_USERNAME"),
            password=store.get("VENDOR_PASSWORD"),
        ),
        whatsapp=WhatsAppConfig(
            api_base_url=store.get("WHATSAPP_API_BASE_URL"),
            api_token=store.get("WHATSAPP_API_TOKEN"),
            notify_to=store.get("WHATSAPP_NOTIFY_TO"),
        ),
        email=EmailConfig(
            provider=store.get("EMAIL_PROVIDER"),
            from_address=store.get("EMAIL_FROM"),
            api_key=store.get("EMAIL_API_KEY"),
            notify_to=store.get("EMAIL_NOTIFY_TO"),
        ),
        sync_interval=store.get("SYNC_INTERVAL") or "hourly",
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
