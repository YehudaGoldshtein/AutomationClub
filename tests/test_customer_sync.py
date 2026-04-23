"""Integration tests for customer_sync_pass — cache-backed, customer-aware."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import pytest

from inventory_sync.customer_sync import customer_sync_pass
from inventory_sync.customers import (
    Customer,
    CustomerNotifications,
    CustomerStoreConfig,
    CustomerVendorBinding,
    Recipient,
    RouteSpec,
)
from inventory_sync.domain import (
    SKU,
    Product,
    StockLevel,
    VendorProductId,
    VendorProductSnapshot,
)
from inventory_sync.fakes import (
    InMemoryCustomerRepository,
    InMemoryItemStateStore,
    InMemoryNotifier,
    InMemoryStore,
    InMemorySupplier,
    InMemorySyncRunStore,
    InMemoryVendorSnapshotCache,
)
from inventory_sync.log import Logger, configure
from inventory_sync.notifications import EVENT_SYNC_SUMMARY, Notifier
from inventory_sync.policies import DefaultStockPolicy


@pytest.fixture
def log(tmp_path) -> Logger:
    return configure(log_dir=tmp_path / "logs")


def _p(sku: str, vid: str, qty: int, published: bool) -> Product:
    return Product(SKU(sku), VendorProductId(vid), StockLevel(qty), published=published)


def _snap(vid: str, avail: bool = True) -> VendorProductSnapshot:
    return VendorProductSnapshot(
        vendor_product_id=VendorProductId(vid),
        is_available=avail,
        stock_count=None,
    )


class _StubSupplier:
    def __init__(self, catalog_skus, snapshots):
        self._catalog = set(catalog_skus)
        self._snapshots = dict(snapshots)
        self.fetch_calls: list[set[str]] = []

    def fetch_catalog_skus(self):
        return set(self._catalog)

    def fetch_snapshots(self, ids: Iterable[VendorProductId]):
        ids_set = {str(v) for v in ids}
        self.fetch_calls.append(ids_set)
        return {v: self._snapshots[v] for v in ids_set if v in self._snapshots}


def _make_customer() -> Customer:
    return Customer(
        id="maxbaby",
        display_name="Max Baby",
        sync_interval_minutes=60,
        last_synced_at=None,
        store=CustomerStoreConfig(
            platform="shopify",
            store_url="https://www.maxbaby.co.il/",
            myshopify_domain="bguhwj-wj.myshopify.com",
            api_version="2024-10",
            display_name="Max Baby",
        ),
        vendors=[
            CustomerVendorBinding(
                name="laura",
                url="https://www.laura-design.net/",
                store_tag="x",
            )
        ],
        notifications=CustomerNotifications(
            ops_enabled=True, client_enabled=True,
            whatsapp_enabled=True, email_enabled=True,
            recipients={"ops": Recipient(whatsapp="1", email=None)},
            routes={EVENT_SYNC_SUMMARY: RouteSpec(to="ops", via="whatsapp")},
        ),
    )


def _build_notifier(log, ops_wa) -> Notifier:
    from inventory_sync.config import NotificationConfig, RouteSpec as CfgRouteSpec
    cfg = NotificationConfig(
        ops_enabled=True, client_enabled=True,
        whatsapp_enabled=True, email_enabled=True,
        routes={EVENT_SYNC_SUMMARY: CfgRouteSpec(to="ops", via="whatsapp")},
    )
    return Notifier(
        config=cfg,
        ops_whatsapp=ops_wa, ops_email=None,
        client_whatsapp=None, client_email=None,
        logger=log,
    )


class TestCustomerSyncPass:
    def test_first_run_writes_cache_and_marks_customer_synced(self, log):
        store = InMemoryStore(products=[_p("A", "VA", 0, published=False)])
        supplier = _StubSupplier(
            catalog_skus={"VA"},
            snapshots={"VA": _snap("VA", True)},
        )
        cache = InMemoryVendorSnapshotCache()
        repo = InMemoryCustomerRepository()
        customer = _make_customer()
        repo.upsert(customer)

        customer_sync_pass(
            customer=customer,
            store=store, supplier=supplier, cache=cache,
            policy=DefaultStockPolicy(),
            notifier=_build_notifier(log, InMemoryNotifier()),
            item_state_store=InMemoryItemStateStore(),
            sync_run_store=InMemorySyncRunStore(),
            customer_repo=repo,
            logger=log,
            ttl_minutes=30,
        )

        # Network hit happened once
        assert supplier.fetch_calls == [{"VA"}]
        # Cache populated
        cached = cache.get_fresh("laura", ["VA"], ttl_minutes=30)
        assert cached["VA"].is_available is True
        # Customer marked as synced
        assert repo.get("maxbaby").last_synced_at is not None

    def test_second_customer_sharing_vendor_skips_network(self, log):
        """Two customers, same vendor: the second should hit the cache, not network."""
        store_a = InMemoryStore(products=[_p("A", "VA", 0, published=False)])
        store_b = InMemoryStore(products=[_p("B", "VA", 0, published=False)])
        supplier = _StubSupplier(
            catalog_skus={"VA"},
            snapshots={"VA": _snap("VA", True)},
        )
        cache = InMemoryVendorSnapshotCache()
        repo = InMemoryCustomerRepository()
        cust_a = _make_customer()
        cust_b = Customer(
            id="other",
            display_name="Other",
            sync_interval_minutes=60,
            last_synced_at=None,
            store=CustomerStoreConfig(
                platform="shopify", store_url="u", myshopify_domain="d",
                api_version="2024-10", display_name="Other",
            ),
            vendors=cust_a.vendors,  # same vendor
            notifications=cust_a.notifications,
        )
        repo.upsert(cust_a)
        repo.upsert(cust_b)

        customer_sync_pass(
            customer=cust_a, store=store_a, supplier=supplier, cache=cache,
            policy=DefaultStockPolicy(),
            notifier=_build_notifier(log, InMemoryNotifier()),
            item_state_store=InMemoryItemStateStore(),
            sync_run_store=InMemorySyncRunStore(),
            customer_repo=repo, logger=log, ttl_minutes=30,
        )
        customer_sync_pass(
            customer=cust_b, store=store_b, supplier=supplier, cache=cache,
            policy=DefaultStockPolicy(),
            notifier=_build_notifier(log, InMemoryNotifier()),
            item_state_store=InMemoryItemStateStore(),
            sync_run_store=InMemorySyncRunStore(),
            customer_repo=repo, logger=log, ttl_minutes=30,
        )

        # Only one fetch across both customers
        assert supplier.fetch_calls == [{"VA"}]

    def test_customer_without_vendors_raises(self, log):
        customer = Customer(
            id="x", display_name="X",
            sync_interval_minutes=60, last_synced_at=None,
            store=CustomerStoreConfig(
                platform="shopify", store_url="u", myshopify_domain="d",
                api_version="2024-10", display_name="X",
            ),
            vendors=[],
        )
        with pytest.raises(ValueError, match="no vendors"):
            customer_sync_pass(
                customer=customer,
                store=InMemoryStore(), supplier=_StubSupplier(set(), {}),
                cache=InMemoryVendorSnapshotCache(),
                policy=DefaultStockPolicy(),
                notifier=_build_notifier(log, InMemoryNotifier()),
                item_state_store=InMemoryItemStateStore(),
                sync_run_store=InMemorySyncRunStore(),
                customer_repo=None, logger=log,
            )
