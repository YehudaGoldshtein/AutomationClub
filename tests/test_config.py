"""Contract tests for config loading + the ConfigStore interface."""
from __future__ import annotations

from pathlib import Path

import pytest

from inventory_sync.config import ConfigError, DotenvConfigStore, load
from inventory_sync.log import configure


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip project env vars so each test starts from a clean slate."""
    for key in [
        "SHOPIFY_STORE_URL", "SHOPIFY_ADMIN_API_TOKEN",
        "SHOPIFY_MYSHOPIFY_DOMAIN", "SHOPIFY_API_VERSION",
        "VENDOR_NAME", "VENDOR_URL", "VENDOR_USERNAME", "VENDOR_PASSWORD",
        "VENDOR_STORE_TAG",
        "WHATSAPP_API_BASE_URL", "WHATSAPP_API_TOKEN",
        "WHATSAPP_OPS_NUMBER", "WHATSAPP_CLIENT_NUMBER",
        "EMAIL_PROVIDER", "EMAIL_API_BASE_URL", "EMAIL_API_KEY",
        "EMAIL_FROM", "EMAIL_OPS_ADDRESS", "EMAIL_CLIENT_ADDRESS",
        "NOTIFY_OPS_ENABLED", "NOTIFY_CLIENT_ENABLED",
        "NOTIFY_WHATSAPP_ENABLED", "NOTIFY_EMAIL_ENABLED",
        "SYNC_INTERVAL", "DATABASE_URL",
    ]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join([
            "SHOPIFY_STORE_URL=https://example.myshopify.com/",
            "SHOPIFY_ADMIN_API_TOKEN=shpat_testtoken",
            "SHOPIFY_MYSHOPIFY_DOMAIN=example.myshopify.com",
            "VENDOR_NAME=test-vendor",
            "VENDOR_URL=https://vendor.example/",
        ]),
        encoding="utf-8",
    )
    return path


def test_load_raises_on_missing_required_key(tmp_path: Path):
    store = DotenvConfigStore(path=tmp_path / "nonexistent.env")
    with pytest.raises(ConfigError, match="SHOPIFY_STORE_URL"):
        load(store=store)


def test_load_happy_path(env_file: Path):
    store = DotenvConfigStore(path=env_file)
    config = load(store=store)
    assert config.shopify.store_url == "https://example.myshopify.com/"
    assert config.shopify.admin_api_token == "shpat_testtoken"
    assert config.vendor.name == "test-vendor"
    assert config.vendor.url == "https://vendor.example/"
    assert config.vendor.username is None
    assert config.sync_interval == "hourly"
    assert config.whatsapp.is_configured is False
    assert config.email.is_configured is False


def test_os_env_overrides_dotenv(env_file: Path, monkeypatch):
    monkeypatch.setenv("SHOPIFY_STORE_URL", "https://override.myshopify.com/")
    store = DotenvConfigStore(path=env_file)
    config = load(store=store)
    assert config.shopify.store_url == "https://override.myshopify.com/"


def test_empty_string_treated_as_missing(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text(
        "SHOPIFY_STORE_URL=\nSHOPIFY_ADMIN_API_TOKEN=x\nSHOPIFY_MYSHOPIFY_DOMAIN=x\nVENDOR_NAME=v\nVENDOR_URL=u\n",
        encoding="utf-8",
    )
    store = DotenvConfigStore(path=path)
    with pytest.raises(ConfigError, match="SHOPIFY_STORE_URL"):
        load(store=store)


def test_sync_interval_default_is_hourly(env_file: Path):
    config = load(store=DotenvConfigStore(path=env_file))
    assert config.sync_interval == "hourly"


def test_sync_interval_overridable(env_file: Path, monkeypatch):
    monkeypatch.setenv("SYNC_INTERVAL", "daily")
    config = load(store=DotenvConfigStore(path=env_file))
    assert config.sync_interval == "daily"


def test_whatsapp_is_configured_requires_url_and_at_least_one_number(env_file: Path, monkeypatch):
    """Token is optional. Base URL + at least one of ops/client number is required."""
    monkeypatch.setenv("WHATSAPP_API_BASE_URL", "https://wa.example")
    c = load(store=DotenvConfigStore(path=env_file))
    assert c.whatsapp.is_configured is False  # no recipients yet

    monkeypatch.setenv("WHATSAPP_OPS_NUMBER", "972504265054")
    c = load(store=DotenvConfigStore(path=env_file))
    assert c.whatsapp.is_configured is True  # ops alone is enough


def test_notification_routes_picked_up_dynamically(env_file: Path, monkeypatch):
    """Adding NOTIFY_<NEW_EVENT>_TO / _VIA should require no code change."""
    monkeypatch.setenv("NOTIFY_ARCHIVE_AUDIT_TO", "client")
    monkeypatch.setenv("NOTIFY_ARCHIVE_AUDIT_VIA", "whatsapp")
    monkeypatch.setenv("NOTIFY_SOMETHING_NEW_TO", "both")
    monkeypatch.setenv("NOTIFY_SOMETHING_NEW_VIA", "email")
    monkeypatch.setenv("NOTIFY_OPS_ENABLED", "true")  # master switch, not a route
    c = load(store=DotenvConfigStore(path=env_file))
    r1 = c.notifications.routes.get("archive_audit")
    r2 = c.notifications.routes.get("something_new")
    assert r1 is not None and r1.to == "client" and r1.via == "whatsapp"
    assert r2 is not None and r2.to == "both" and r2.via == "email"
    assert "ops_enabled" not in c.notifications.routes


def test_notification_master_switches_default_to_enabled(env_file: Path):
    c = load(store=DotenvConfigStore(path=env_file))
    assert c.notifications.ops_enabled is True
    assert c.notifications.client_enabled is True
    assert c.notifications.whatsapp_enabled is True
    assert c.notifications.email_enabled is True


def test_notification_channel_master_switches_can_be_disabled(env_file: Path, monkeypatch):
    monkeypatch.setenv("NOTIFY_WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "false")
    c = load(store=DotenvConfigStore(path=env_file))
    assert c.notifications.whatsapp_enabled is False
    assert c.notifications.email_enabled is False


def test_route_with_missing_via_defaults_to_none(env_file: Path, monkeypatch):
    """Missing VIA silences the event by default (safe default)."""
    monkeypatch.setenv("NOTIFY_FOO_TO", "ops")
    # no _VIA
    c = load(store=DotenvConfigStore(path=env_file))
    route = c.notifications.routes.get("foo")
    assert route is not None
    assert route.to == "ops"
    assert route.via == "none"


def test_whatsapp_client_number_alone_also_enables(env_file: Path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_API_BASE_URL", "https://wa.example")
    monkeypatch.setenv("WHATSAPP_CLIENT_NUMBER", "972500000000")
    c = load(store=DotenvConfigStore(path=env_file))
    assert c.whatsapp.is_configured is True
    assert c.whatsapp.client_number == "972500000000"
    assert c.whatsapp.ops_number is None


def test_email_is_configured_requires_provider_key_from_and_recipient(env_file: Path, monkeypatch):
    """Provider + api_key + from + at least one of ops/client addresses must all be present."""
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    c = load(store=DotenvConfigStore(path=env_file))
    assert c.email.is_configured is False  # missing api_key, from, addresses

    monkeypatch.setenv("EMAIL_API_KEY", "re_test")
    monkeypatch.setenv("EMAIL_FROM", "noreply@example.com")
    c = load(store=DotenvConfigStore(path=env_file))
    assert c.email.is_configured is False  # still no recipient

    monkeypatch.setenv("EMAIL_OPS_ADDRESS", "owner@example.com")
    c = load(store=DotenvConfigStore(path=env_file))
    assert c.email.is_configured is True  # ops recipient alone is enough


def test_secret_token_never_written_to_log(tmp_path: Path):
    """Critical: the Shopify token must never appear in any log output."""
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "SHOPIFY_STORE_URL=https://example.myshopify.com/",
            "SHOPIFY_ADMIN_API_TOKEN=SECRET_MARKER_DO_NOT_LEAK",
            "SHOPIFY_MYSHOPIFY_DOMAIN=example.myshopify.com",
            "VENDOR_NAME=test-vendor",
            "VENDOR_URL=https://vendor.example/",
        ]),
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"
    configure(log_dir=log_dir)

    load(store=DotenvConfigStore(path=env))

    log_text = (log_dir / "inventory_sync.log").read_text(encoding="utf-8")
    assert "SECRET_MARKER_DO_NOT_LEAK" not in log_text
