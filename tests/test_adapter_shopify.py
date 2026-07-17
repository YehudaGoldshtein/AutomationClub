"""Tests for ShopifyAdapter.

Uses httpx.MockTransport + a small stateful in-memory Shopify fake so we can
drive the full list -> update -> read back flow without live API calls.
Inherits StoreContract to prove the adapter satisfies the same behavior as
InMemoryStore.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest

from inventory_sync.adapters.shopify import ShopifyAdapter, ShopifyError
from inventory_sync.domain import SKU, Product, StockLevel, VendorProductId
from inventory_sync.log import get

from tests.test_store import SEEDED_PRODUCTS, StoreContract


# ---------- in-memory Shopify fake ----------

class _FakeShopifyApi:
    """Stateful fake of the subset of Shopify Admin API our adapter uses."""

    def __init__(self, products: list[dict], location_id: int = 999):
        self.products: dict[int, dict] = {p["id"]: p for p in products}
        self.location_id = location_id
        self.inventory: dict[int, int] = {}
        for p in products:
            for v in p.get("variants", []):
                self.inventory[v["inventory_item_id"]] = v.get("inventory_quantity", 0)
        self.request_log: list[tuple[str, str]] = []
        # creation / collections state
        self._next_id = 5000
        self.custom_collections: dict[int, dict] = {}  # id -> {"id", "title"}
        self.collects: list[dict] = []                 # {"product_id", "collection_id"}
        self.created_payloads: list[dict] = []         # bodies POSTed to /products.json

    def _new_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.request_log.append((method, path))

        if method == "GET" and path.endswith("/products.json"):
            return self._products_json(request)
        if method == "GET" and path.endswith("/locations.json"):
            return httpx.Response(
                200, json={"locations": [{"id": self.location_id, "name": "Primary"}]}
            )
        if method == "POST" and path.endswith("/inventory_levels/set.json"):
            body = request.content.decode() if request.content else "{}"
            import json as _json
            data = _json.loads(body)
            self.inventory[data["inventory_item_id"]] = data["available"]
            for p in self.products.values():
                for v in p.get("variants", []):
                    if v["inventory_item_id"] == data["inventory_item_id"]:
                        v["inventory_quantity"] = data["available"]
            return httpx.Response(200, json={"inventory_level": data})
        if method == "POST" and path.endswith("/products.json"):
            return self._create_product(request)
        if method == "GET" and path.endswith("/custom_collections.json"):
            params = dict(request.url.params)
            title = params.get("title")
            cols = list(self.custom_collections.values())
            if title is not None:
                # Server-side exact-title filter (what the real API supports).
                cols = [c for c in cols if c["title"] == title]
            else:
                # No filter → only the first page, modelling the real store where
                # a target collection can sit beyond the first page of 297.
                cols = cols[: int(params.get("limit", 50))]
            return httpx.Response(200, json={"custom_collections": cols})
        if method == "POST" and path.endswith("/custom_collections.json"):
            import json as _json
            title = _json.loads(request.content.decode())["custom_collection"]["title"]
            cid = self._new_id()
            self.custom_collections[cid] = {"id": cid, "title": title}
            return httpx.Response(201, json={"custom_collection": self.custom_collections[cid]})
        if method == "POST" and path.endswith("/collects.json"):
            import json as _json
            collect = _json.loads(request.content.decode())["collect"]
            self.collects.append(collect)
            return httpx.Response(201, json={"collect": collect})
        if method == "DELETE" and "/products/" in path and path.endswith(".json"):
            pid = int(path.rsplit("/", 1)[-1].replace(".json", ""))
            self.products.pop(pid, None)
            return httpx.Response(200, json={})
        if method == "PUT" and "/products/" in path and path.endswith(".json"):
            product_id_str = path.rsplit("/", 1)[-1].replace(".json", "")
            try:
                product_id = int(product_id_str)
            except ValueError:
                return httpx.Response(404, text=f"bad product id: {product_id_str}")
            import json as _json
            data = _json.loads(request.content.decode())
            new_status = data["product"]["status"]
            if product_id in self.products:
                self.products[product_id]["status"] = new_status
            return httpx.Response(200, json={"product": self.products.get(product_id, {})})

        return httpx.Response(404, text=f"unhandled {method} {path}")

    def _create_product(self, request: httpx.Request) -> httpx.Response:
        import json as _json
        body = _json.loads(request.content.decode())
        self.created_payloads.append(body)
        product = dict(body["product"])
        pid = self._new_id()
        product["id"] = pid
        stored_variants = []
        for v in product.get("variants", []):
            vid, inv = self._new_id(), self._new_id()
            stored = {**v, "id": vid, "inventory_item_id": inv,
                      "inventory_quantity": 0}
            stored_variants.append(stored)
            self.inventory[inv] = 0
        product["variants"] = stored_variants
        product.setdefault("status", "draft")
        self.products[pid] = product
        return httpx.Response(201, json={"product": product})

    def _products_json(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        limit = int(params.get("limit", 250))
        vendor = params.get("vendor")
        page_info = params.get("page_info")

        all_products = list(self.products.values())
        if vendor is not None:
            all_products = [p for p in all_products if p.get("vendor") == vendor]

        start = int(page_info) if page_info else 0
        page = all_products[start : start + limit]
        next_start = start + limit

        headers = {}
        if next_start < len(all_products):
            headers["link"] = (
                f'<https://shop.test/admin/api/2024-10/products.json?'
                f'limit={limit}&page_info={next_start}>; rel="next"'
            )

        return httpx.Response(200, json={"products": page}, headers=headers)


def _mk_variant(variant_id: int, inventory_item_id: int, sku: str, qty: int) -> dict:
    return {
        "id": variant_id,
        "inventory_item_id": inventory_item_id,
        "sku": sku,
        "inventory_quantity": qty,
    }


def _mk_product(
    product_id: int,
    variants: list[dict],
    status: str = "active",
    vendor: str | None = None,
) -> dict:
    return {
        "id": product_id,
        "status": status,
        "vendor": vendor,
        "variants": variants,
    }


def _make_adapter(fake: _FakeShopifyApi, **kwargs: Any) -> ShopifyAdapter:
    transport = httpx.MockTransport(fake.handle)
    client = httpx.Client(transport=transport, base_url="https://shop.test/admin/api/2024-10")
    return ShopifyAdapter(client=client, logger=get("test"), **kwargs)


# ---------- unit tests ----------

class TestListProducts:
    def test_flattens_variants_into_products(self):
        fake = _FakeShopifyApi([
            _mk_product(100, [
                _mk_variant(10, 1001, "A-1", 5),
                _mk_variant(11, 1002, "A-2", 0),
            ]),
        ])
        adapter = _make_adapter(fake)

        products = adapter.list_products()

        assert {p.sku for p in products} == {SKU("A-1"), SKU("A-2")}
        by_sku = {p.sku: p for p in products}
        assert by_sku[SKU("A-1")].stock == StockLevel(5)
        assert by_sku[SKU("A-2")].stock == StockLevel(0)

    def test_maps_sku_to_vendor_product_id_directly(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "2800-253", 0)])])
        adapter = _make_adapter(fake)

        products = adapter.list_products()
        assert products[0].vendor_product_id == VendorProductId("2800-253")

    def test_status_active_is_published(self):
        fake = _FakeShopifyApi([
            _mk_product(1, [_mk_variant(10, 100, "X", 0)], status="active"),
            _mk_product(2, [_mk_variant(20, 200, "Y", 0)], status="archived"),
        ])
        adapter = _make_adapter(fake)

        by_sku = {p.sku: p for p in adapter.list_products()}
        assert by_sku[SKU("X")].published is True
        assert by_sku[SKU("Y")].published is False

    def test_skips_variants_without_sku(self):
        fake = _FakeShopifyApi([
            _mk_product(1, [
                _mk_variant(10, 100, "HAS-SKU", 1),
                _mk_variant(11, 101, "", 1),
            ]),
        ])
        adapter = _make_adapter(fake)
        skus = [p.sku for p in adapter.list_products()]
        assert skus == [SKU("HAS-SKU")]

    def test_negative_inventory_coerced_to_zero(self):
        """Shopify allows negative inventory_quantity (oversold). Our domain disallows it."""
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", -3)])])
        adapter = _make_adapter(fake)
        assert adapter.list_products()[0].stock == StockLevel(0)

    def test_vendor_filter_is_sent_in_query(self):
        fake = _FakeShopifyApi([
            _mk_product(1, [_mk_variant(10, 100, "X", 1)], vendor="laura"),
            _mk_product(2, [_mk_variant(20, 200, "Y", 1)], vendor="other"),
        ])
        adapter = _make_adapter(fake, vendor_filter="laura")

        products = adapter.list_products()
        assert {p.sku for p in products} == {SKU("X")}

    def test_pagination_follows_link_header(self):
        variants = [
            _mk_variant(10 + i, 1000 + i, f"SKU-{i:03d}", i)
            for i in range(25)
        ]
        products = [_mk_product(i, [variants[i]]) for i in range(25)]
        fake = _FakeShopifyApi(products)
        adapter = _make_adapter(fake, page_size=10)

        all_products = adapter.list_products()
        assert len(all_products) == 25

    def test_populates_variant_cache_for_subsequent_mutations(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)])])
        adapter = _make_adapter(fake)
        adapter.list_products()
        adapter.update_stock(SKU("X"), StockLevel(3))
        assert fake.inventory[100] == 3


class TestUpdateStock:
    def test_writes_inventory_level_set(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)])])
        adapter = _make_adapter(fake)
        adapter.list_products()

        adapter.update_stock(SKU("X"), StockLevel(42))

        assert fake.inventory[100] == 42
        methods = [m for (m, p) in fake.request_log if p.endswith("/inventory_levels/set.json")]
        assert methods == ["POST"]

    def test_resolves_location_lazily_and_caches(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)])])
        adapter = _make_adapter(fake)
        adapter.list_products()

        adapter.update_stock(SKU("X"), StockLevel(1))
        adapter.update_stock(SKU("X"), StockLevel(2))

        location_calls = [p for (m, p) in fake.request_log if p.endswith("/locations.json")]
        assert len(location_calls) == 1

    def test_unknown_sku_raises(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)])])
        adapter = _make_adapter(fake)
        adapter.list_products()

        with pytest.raises(ShopifyError, match="no variant cached"):
            adapter.update_stock(SKU("NOT-CACHED"), StockLevel(1))


class TestPublishStatus:
    def test_unpublish_sets_status_archived(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)], status="active")])
        adapter = _make_adapter(fake)
        adapter.list_products()

        adapter.unpublish(SKU("X"))

        assert fake.products[1]["status"] == "archived"

    def test_republish_sets_status_active(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)], status="archived")])
        adapter = _make_adapter(fake)
        adapter.list_products()

        adapter.republish(SKU("X"))

        assert fake.products[1]["status"] == "active"

    def test_unpublish_unknown_sku_raises(self):
        fake = _FakeShopifyApi([_mk_product(1, [_mk_variant(10, 100, "X", 5)])])
        adapter = _make_adapter(fake)
        adapter.list_products()

        with pytest.raises(ShopifyError):
            adapter.unpublish(SKU("NOPE"))


class TestCreateProduct:
    def _draft(self, variants):
        from inventory_sync.domain import ProductDraft, VariantSpec  # noqa: F401
        return ProductDraft(
            title="חדש", body_html="<p>x</p>", vendor="לורה סוויסרה | laura swisra",
            product_type="בגד גוף", tags="בגד גוף", variants=tuple(variants), status="draft",
        )

    def test_posts_product_and_returns_ids(self):
        from inventory_sync.domain import SKU, VariantSpec
        fake = _FakeShopifyApi([])
        adapter = _make_adapter(fake)

        created = adapter.create_product(self._draft([VariantSpec(SKU("N-1"), price=Decimal("99.00"))]))

        assert ("POST", "/admin/api/2024-10/products.json") in fake.request_log
        assert created.store_product_id
        assert SKU("N-1") in created.variant_ids_by_sku

    def test_sends_draft_status_and_price(self):
        from inventory_sync.domain import SKU, VariantSpec
        fake = _FakeShopifyApi([])
        adapter = _make_adapter(fake)

        adapter.create_product(self._draft([VariantSpec(SKU("N-1"), price=Decimal("99.00"))]))

        product = fake.created_payloads[0]["product"]
        assert product["status"] == "draft"
        assert str(product["variants"][0]["price"]) == "99.00"

    def test_multi_variant_sets_size_option(self):
        from inventory_sync.domain import SKU, VariantSpec
        fake = _FakeShopifyApi([])
        adapter = _make_adapter(fake)

        adapter.create_product(self._draft([
            VariantSpec(SKU("N-1"), option_value="NB"),
            VariantSpec(SKU("N-2"), option_value="0-3"),
        ]))

        product = fake.created_payloads[0]["product"]
        assert product["options"] == [{"name": "מידה"}]
        assert {v["option1"] for v in product["variants"]} == {"NB", "0-3"}


class TestCreateProductMetafields:
    def test_writes_metafields_template_and_inventory_management(self):
        from inventory_sync.domain import ProductDraft, VariantSpec, SKU, Metafield
        fake = _FakeShopifyApi([])
        adapter = _make_adapter(fake)

        draft = ProductDraft(
            title="שידה", body_html="<p>x</p>", vendor="segal | סגל",
            product_type="שידות", tags="שידות",
            variants=(VariantSpec(SKU("S-1"), price=Decimal("100"), inventory_quantity=7),),
            status="draft",
            metafields=(Metafield("custom", "infoo", "rich_text_field", '{"x":1}'),),
            template_suffix="furniture-product-page",
        )
        adapter.create_product(draft)

        product = fake.created_payloads[0]["product"]
        assert product["template_suffix"] == "furniture-product-page"
        assert product["metafields"][0] == {
            "namespace": "custom", "key": "infoo",
            "type": "rich_text_field", "value": '{"x":1}',
        }
        assert product["variants"][0]["inventory_management"] == "shopify"

    def test_laura_style_draft_omits_new_keys(self):
        """Regression: a draft without metafields/template/stock (Laura) must produce
        the same minimal payload as before — no new keys leak in."""
        from inventory_sync.domain import ProductDraft, VariantSpec, SKU
        fake = _FakeShopifyApi([])
        adapter = _make_adapter(fake)

        adapter.create_product(ProductDraft(
            title="חדש", body_html="<p>x</p>", vendor="לורה סוויסרה | laura swisra",
            product_type="בגד גוף", tags="בגד גוף",
            variants=(VariantSpec(SKU("N-1"), price=Decimal("99")),), status="draft",
        ))

        product = fake.created_payloads[0]["product"]
        assert "metafields" not in product
        assert "template_suffix" not in product
        assert "inventory_management" not in product["variants"][0]

    def test_create_then_update_stock_sets_level(self):
        from inventory_sync.domain import ProductDraft, VariantSpec, SKU
        fake = _FakeShopifyApi([])
        adapter = _make_adapter(fake)

        adapter.create_product(ProductDraft(
            title="שידה", body_html="<p>x</p>", vendor="segal | סגל",
            product_type="שידות", tags="שידות",
            variants=(VariantSpec(SKU("S-1"), price=Decimal("100"), inventory_quantity=7),),
            status="draft",
        ))
        adapter.update_stock(SKU("S-1"), StockLevel(7))  # ref cached on create

        assert 7 in fake.inventory.values()


class TestEnsureCollection:
    def test_creates_when_missing(self):
        fake = _FakeShopifyApi([])
        adapter = _make_adapter(fake)

        ref = adapter.ensure_collection("אופנה")

        assert ref.created is True
        assert ref.id
        assert len(fake.custom_collections) == 1

    def test_reuses_existing_and_posts_once(self):
        fake = _FakeShopifyApi([])
        adapter = _make_adapter(fake)

        first = adapter.ensure_collection("אופנה")
        again = adapter.ensure_collection("אופנה")

        assert again.created is False
        assert again.id == first.id
        posts = [(m, p) for (m, p) in fake.request_log
                 if m == "POST" and p.endswith("/custom_collections.json")]
        assert len(posts) == 1

    def test_reuses_existing_collection_beyond_first_page(self):
        """Regression: with hundreds of collections, the target sits past page 1.
        A title filter must still find it instead of creating a duplicate."""
        fake = _FakeShopifyApi([])
        for i in range(100):
            fake.custom_collections[1000 + i] = {"id": 1000 + i, "title": f"brand-{i}"}
        fake.custom_collections[9999] = {"id": 9999, "title": "מגבות לתינוק"}  # buried
        adapter = _make_adapter(fake)

        ref = adapter.ensure_collection("מגבות לתינוק")

        assert ref.created is False
        assert ref.id == "9999"
        posts = [(m, p) for (m, p) in fake.request_log
                 if m == "POST" and p.endswith("/custom_collections.json")]
        assert posts == []  # must NOT create a duplicate

    def test_created_once_within_a_run(self):
        """Two products mapping to the same new collection → one create, not two."""
        fake = _FakeShopifyApi([])
        adapter = _make_adapter(fake)

        first = adapter.ensure_collection("סט מצעים למיטת תינוק")
        second = adapter.ensure_collection("סט מצעים למיטת תינוק")

        assert first.created is True
        assert second.created is False
        assert second.id == first.id
        posts = [(m, p) for (m, p) in fake.request_log
                 if m == "POST" and p.endswith("/custom_collections.json")]
        assert len(posts) == 1


class _RecordingLogger:
    """Captures (event, context) tuples so tests can assert on log output."""
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def _rec(self, event, **ctx):
        self.events.append((event, ctx))

    debug = info = warning = error = exception = _rec

    def bind(self, **ctx):
        return self

    def ctx(self, event):
        return [c for e, c in self.events if e == event]

    def names(self):
        return [e for e, _ in self.events]


class TestEnsureCollectionLogging:
    def _adapter(self, fake, log):
        transport = httpx.MockTransport(fake.handle)
        client = httpx.Client(transport=transport, base_url="https://shop.test/admin/api/2024-10")
        return ShopifyAdapter(client=client, logger=log)

    def test_logs_what_matched_on_hit(self):
        fake = _FakeShopifyApi([])
        fake.custom_collections[7] = {"id": 7, "title": "אופנה"}
        log = _RecordingLogger()
        self._adapter(fake, log).ensure_collection("אופנה")

        resolved = log.ctx("collection_resolved")
        assert resolved, "expected a collection_resolved log"
        assert resolved[0]["matched"] is True
        assert resolved[0]["collection_id"] == "7"
        assert resolved[0]["matched_title"] == "אופנה"

    def test_logs_the_mismatch_before_creating(self):
        fake = _FakeShopifyApi([])
        fake.custom_collections[7] = {"id": 7, "title": "אופנה"}  # a non-matching one
        log = _RecordingLogger()
        self._adapter(fake, log).ensure_collection("סינרים")  # different title → no match

        assert "collection_no_match" in log.names()
        assert "collection_created" in log.names()
        nm = log.ctx("collection_no_match")[0]
        assert nm["matched"] is False
        assert nm["title"] == "סינרים"
        assert "returned_titles" in nm  # what the query returned, for debugging


class TestAddToCollection:
    def test_posts_collect(self):
        fake = _FakeShopifyApi([])
        adapter = _make_adapter(fake)

        adapter.add_to_collection("12345", "67890")

        assert {"product_id": 12345, "collection_id": 67890} in [
            {"product_id": int(c["product_id"]), "collection_id": int(c["collection_id"])}
            for c in fake.collects
        ]


class TestShopifySatisfiesStoreContract(StoreContract):
    """Re-run the StorePlatform contract tests against the Shopify adapter.

    Each SEEDED_PRODUCT becomes a single-variant Shopify product so the
    contract's assertions about SKU, stock, published, and mutations all hold.
    """

    @pytest.fixture
    def store(self) -> ShopifyAdapter:
        shopify_products = [
            _mk_product(
                product_id=(i + 1) * 1000,
                status="active" if sp.published else "archived",
                variants=[
                    _mk_variant(
                        variant_id=(i + 1) * 100,
                        inventory_item_id=(i + 1) * 10,
                        sku=sp.sku,
                        qty=sp.stock.value,
                    )
                ],
            )
            for i, sp in enumerate(SEEDED_PRODUCTS)
        ]
        fake = _FakeShopifyApi(shopify_products)
        adapter = _make_adapter(fake)
        adapter.list_products()  # prime cache
        return adapter
