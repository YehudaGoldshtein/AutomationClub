"""Contract tests for CustomerRepository.

Runs the same suite against InMemoryCustomerRepository and
SqlCustomerRepository. Both must behave identically.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy

from inventory_sync.customers import (
    Customer,
    CustomerNotifications,
    CustomerStoreConfig,
    CustomerVendorBinding,
    Recipient,
    RouteSpec,
)
from inventory_sync.fakes import InMemoryCustomerRepository
from inventory_sync.log import get
from inventory_sync.persistence.customer_repository import SqlCustomerRepository


def _make_customer(
    customer_id: str = "maxbaby",
    sync_interval_minutes: int = 60,
    last_synced_at: datetime | None = None,
) -> Customer:
    return Customer(
        id=customer_id,
        display_name="Max Baby",
        sync_interval_minutes=sync_interval_minutes,
        last_synced_at=last_synced_at,
        store=CustomerStoreConfig(
            platform="shopify",
            store_url="https://www.maxbaby.co.il/",
            myshopify_domain="bguhwj-wj.myshopify.com",
            api_version="2024-10",
            display_name="Max Baby",
        ),
        vendors=[
            CustomerVendorBinding(
                name="laura-design",
                url="https://www.laura-design.net/",
                store_tag="לורה סוויסרה | laura swisra",
            )
        ],
        notifications=CustomerNotifications(
            ops_enabled=True,
            client_enabled=True,
            whatsapp_enabled=True,
            email_enabled=True,
            recipients={
                "ops": Recipient(whatsapp="972504265054", email="yehudashtein@gmail.com"),
                "client": Recipient(whatsapp="972525755705", email="Elishosh687@gmail.com"),
            },
            routes={"sync_summary": RouteSpec(to="both", via="both")},
        ),
    )


class CustomerRepositoryContract:
    @pytest.fixture
    def repo(self):
        raise NotImplementedError

    def test_empty_list(self, repo):
        assert repo.list_all() == []
        assert repo.get("x") is None

    def test_upsert_then_get_roundtrip(self, repo):
        repo.upsert(_make_customer())
        c = repo.get("maxbaby")
        assert c is not None
        assert c.display_name == "Max Baby"
        assert c.sync_interval_minutes == 60
        assert c.store.myshopify_domain == "bguhwj-wj.myshopify.com"
        assert len(c.vendors) == 1
        assert c.vendors[0].name == "laura-design"
        assert c.vendors[0].store_tag == "לורה סוויסרה | laura swisra"  # unicode round-trips
        assert c.notifications.recipients["ops"].email == "yehudashtein@gmail.com"
        assert c.notifications.route_for("sync_summary").via == "both"

    def test_upsert_preserves_last_synced_at(self, repo):
        c = _make_customer()
        repo.upsert(c)
        # First sync
        synced_at = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
        repo.mark_synced("maxbaby", synced_at)
        # Re-upsert the base record (e.g., a config change) must NOT clobber bookkeeping
        repo.upsert(_make_customer(sync_interval_minutes=30))
        reloaded = repo.get("maxbaby")
        assert reloaded.last_synced_at == synced_at
        assert reloaded.sync_interval_minutes == 30  # but config fields did update

    def test_mark_synced_sets_timestamp(self, repo):
        repo.upsert(_make_customer())
        when = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
        repo.mark_synced("maxbaby", when)
        assert repo.get("maxbaby").last_synced_at == when

    # --- due logic ---

    def test_list_due_includes_never_synced(self, repo):
        repo.upsert(_make_customer())
        assert [c.id for c in repo.list_due()] == ["maxbaby"]

    def test_list_due_excludes_recently_synced(self, repo):
        repo.upsert(_make_customer(sync_interval_minutes=60))
        now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
        repo.mark_synced("maxbaby", now - timedelta(minutes=10))
        assert repo.list_due(now=now) == []

    def test_list_due_includes_overdue(self, repo):
        repo.upsert(_make_customer(sync_interval_minutes=60))
        now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=timezone.utc)
        repo.mark_synced("maxbaby", now - timedelta(minutes=61))
        assert [c.id for c in repo.list_due(now=now)] == ["maxbaby"]


class TestInMemoryCustomerRepository(CustomerRepositoryContract):
    @pytest.fixture
    def repo(self):
        return InMemoryCustomerRepository()


class TestSqlCustomerRepository(CustomerRepositoryContract):
    @pytest.fixture
    def repo(self):
        engine = sqlalchemy.create_engine("sqlite:///:memory:")
        sql = SqlCustomerRepository(engine=engine, logger=get("test"))
        sql.create_schema()
        return sql
