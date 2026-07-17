"""Failing-first tests for SegalBabyStoreApiAdapter (Phase 2).

Store API pagination + per-product tab scrape, driven by a fake httpx transport
(no network). Mirrors the real endpoints:
  GET /wp-json/wc/store/v1/products?category=<id>&per_page=&page=
  GET <permalink>  -> product HTML with #more-info tabs
"""
from __future__ import annotations

import httpx

from inventory_sync.adapters.segal_baby import SegalBabyStoreApiAdapter
from inventory_sync.log import get
from inventory_sync.segal_source import SegalProduct

BASE = "https://seg.test"


def _prod(sku, permalink, cat="beds"):
    return {
        "sku": sku,
        "name": f"מוצר {sku}",
        "description": "<p>desc</p>",
        "prices": {"regular_price": "100", "sale_price": "100", "currency_minor_unit": 0},
        "on_sale": False,
        "images": [{"src": f"http://img/{sku}.jpg"}],
        "categories": [{"slug": cat}, {"slug": "segal-baby"}],
        "permalink": f"{BASE}/product/{permalink}/",
        "is_in_stock": True,
        "add_to_cart": {"minimum": 1, "maximum": 7},
    }


PRODUCT_HTML = """
<section id="more-info">
  <ul class="nav nav-tabs">
    <li><a href="#tab-1">מידע כללי</a></li>
    <li><a href="#tab-2">פרטים טכניים</a></li>
  </ul>
  <div class="tab-content">
    <div class="tab-pane" id="tab-1"><p>כללי</p></div>
    <div class="tab-pane" id="tab-2"><p>מידות: 125</p></div>
  </div>
</section>
"""

# category 37 with per_page=2: page1 full (2), page2 partial (1) -> 3 total.
PAGES = {
    ("37", 1): [_prod("A-1", "a1"), _prod("A-2", "a2")],
    ("37", 2): [_prod("A-3", "a3")],
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/wp-json/wc/store/v1/products":
        cat = request.url.params.get("category")
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=PAGES.get((cat, page), []))
    if path.startswith("/product/"):
        return httpx.Response(200, text=PRODUCT_HTML)
    return httpx.Response(404)


def _adapter(handler=_handler, per_page=2):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SegalBabyStoreApiAdapter(client=client, base_url=BASE, per_page=per_page,
                                    logger=get("test"))


class TestListCategoryProducts:
    def test_paginates_until_partial_page(self):
        raw = _adapter().list_category_products(37)
        assert [p["sku"] for p in raw] == ["A-1", "A-2", "A-3"]

    def test_non_200_returns_what_it_has(self):
        def handler(request):
            if request.url.path == "/wp-json/wc/store/v1/products":
                if int(request.url.params.get("page", "1")) == 1:
                    return httpx.Response(200, json=PAGES[("37", 1)])
                return httpx.Response(500)
            return httpx.Response(404)
        raw = _adapter(handler).list_category_products(37)
        assert [p["sku"] for p in raw] == ["A-1", "A-2"]  # page 2 failed, page 1 kept


class TestFetchTabs:
    def test_parses_tabs_from_permalink(self):
        tabs = _adapter().fetch_tabs(f"{BASE}/product/a1/")
        assert [t.label for t in tabs] == ["מידע כללי", "פרטים טכניים"]

    def test_tab_fetch_failure_is_empty(self):
        def handler(request):
            return httpx.Response(404)
        assert _adapter(handler).fetch_tabs(f"{BASE}/product/x/") == ()


class TestFetchProducts:
    def test_returns_segalproducts_with_tabs(self):
        products = _adapter().fetch_products(37)
        assert all(isinstance(p, SegalProduct) for p in products)
        assert {p.sku for p in products} == {"A-1", "A-2", "A-3"}
        assert products[0].stock_qty == 7
        assert [t.label for t in products[0].tabs] == ["מידע כללי", "פרטים טכניים"]
