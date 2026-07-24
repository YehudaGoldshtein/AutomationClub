"""Tests for SnirStoreApiAdapter (Phase 2) + the browser-fetch challenge surface.

The adapter is transport-agnostic, so it is driven here by a fake httpx transport
(no browser, no network) — exactly the point of the PlaywrightClient duck-typing
httpx.Client. Mirrors the real endpoints:
  GET /wp-json/wc/store/v1/products?per_page=&page=   (all products)
  GET <permalink>  -> product HTML with .woocommerce-Tabs-panel--<name> tabs
"""
from __future__ import annotations

import httpx

from inventory_sync.adapters.snir_baby import SnirStoreApiAdapter
from inventory_sync.browser_fetch import BrowserResponse
from inventory_sync.domain import VendorProductId
from inventory_sync.log import get
from inventory_sync.snir_source import SnirProduct

BASE = "https://snir.test"

_BEDS = 126          # importable -> מיטות תינוק
_DRESSERS = 125      # importable -> שידות החתלה
_MARKETING = 999     # not in any route -> not importable


def _prod(sku, permalink, cat=_BEDS, in_stock=True, price="1490",
          wc_type="simple", variations=0):
    return {
        "sku": sku,
        "name": f"מוצר {sku} &#8211; NEW",
        "short_description": "<p>תקציר</p>",
        "description": "<p>תיאור</p>",
        "prices": {"regular_price": price, "currency_minor_unit": 0},
        "images": [{"src": f"http://img/{sku}.jpg"}],
        "categories": [{"id": cat}],
        "permalink": f"{BASE}/product/{permalink}/",
        "is_in_stock": in_stock,
        "add_to_cart": {"minimum": 1, "maximum": 9999},
        "type": wc_type,
        "variations": [{"id": 1000 + i} for i in range(variations)],
    }


PRODUCT_HTML = """
<div class="woocommerce-Tabs-panel woocommerce-Tabs-panel--description"><p>תיאור פריט</p></div>
<div class="woocommerce-Tabs-panel woocommerce-Tabs-panel--tech_details"><p>רוחב: 120 ס"מ</p></div>
<div class="woocommerce-Tabs-panel woocommerce-Tabs-panel--oc_theme_product_tab_2"><p>אחריות</p></div>
"""

# per_page=2: page1 full (2 in-scope), page2 partial (1) -> 3 in-scope total,
# plus a SKU-less and a non-importable product that must be dropped.
PAGES = {
    1: [_prod("bed-1", "b1"), _prod("dr-1", "d1", cat=_DRESSERS)],
    2: [
        _prod("bed-2", "b2"),
        _prod("", "nosku"),                    # no SKU -> dropped
        _prod("junk-1", "j1", cat=_MARKETING), # not importable -> dropped
    ],
}


def _handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/wp-json/wc/store/v1/products":
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=PAGES.get(page, []))
    if path.startswith("/product/"):
        return httpx.Response(200, text=PRODUCT_HTML)
    return httpx.Response(404)


def _adapter(handler=_handler, per_page=2):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return SnirStoreApiAdapter(client=client, base_url=BASE, per_page=per_page,
                               logger=get("test"))


class TestListProducts:
    def test_paginates_until_partial_page(self):
        raw = _adapter().list_products()
        assert [p["sku"] for p in raw] == ["bed-1", "dr-1", "bed-2", "", "junk-1"]

    def test_non_200_returns_what_it_has(self):
        def handler(request):
            if request.url.path == "/wp-json/wc/store/v1/products":
                if int(request.url.params.get("page", "1")) == 1:
                    return httpx.Response(200, json=PAGES[1])
                return httpx.Response(500)
            return httpx.Response(404)
        raw = _adapter(handler).list_products()
        assert [p["sku"] for p in raw] == ["bed-1", "dr-1"]  # page 2 failed, page 1 kept

    def test_challenge_non_json_stops_pagination(self):
        # A challenge page (HTML where JSON is expected) makes resp.json() raise;
        # the adapter must stop, not crash, keeping the good page.
        def handler(request):
            if request.url.path == "/wp-json/wc/store/v1/products":
                if int(request.url.params.get("page", "1")) == 1:
                    return httpx.Response(200, json=PAGES[1])
                return httpx.Response(200, text="<html><script>slowAES...</script></html>",
                                      headers={"content-type": "text/html"})
            return httpx.Response(404)
        raw = _adapter(handler).list_products()
        assert [p["sku"] for p in raw] == ["bed-1", "dr-1"]


class TestFetchTabs:
    def test_parses_named_panels(self):
        tabs = _adapter().fetch_tabs(f"{BASE}/product/b1/")
        assert [t.label for t in tabs] == ["description", "tech_details", "oc_theme_product_tab_2"]

    def test_tab_fetch_failure_is_empty(self):
        assert _adapter(lambda r: httpx.Response(404)).fetch_tabs(f"{BASE}/product/x/") == ()

    def test_empty_permalink_is_empty(self):
        assert _adapter().fetch_tabs("") == ()


class TestFetchProducts:
    def test_returns_in_scope_products_with_tabs(self):
        products = _adapter().fetch_products()
        assert all(isinstance(p, SnirProduct) for p in products)
        # SKU-less and non-importable products are dropped.
        assert {p.sku for p in products} == {"bed-1", "dr-1", "bed-2"}
        assert [t.label for t in products[0].tabs] == \
            ["description", "tech_details", "oc_theme_product_tab_2"]

    def test_multi_variation_shared_sku_is_kept_as_single_variant(self):
        # Owner rule: add the first variant, skip the additional — the product
        # is still onboarded (single-variant on the parent SKU), not dropped.
        def handler(request):
            if request.url.path == "/wp-json/wc/store/v1/products":
                page = int(request.url.params.get("page", "1"))
                var = _prod("var-1", "v1", wc_type="variable", variations=3)
                return httpx.Response(200, json=[var] if page == 1 else [])
            return httpx.Response(200, text=PRODUCT_HTML)
        products = _adapter(handler).fetch_products()
        assert [p.sku for p in products] == ["var-1"]
        assert products[0].variation_count == 3 and products[0].wc_type == "variable"

    def test_duplicate_sku_within_scan_is_skipped(self):
        # Second product claiming an already-taken SKU is skipped, not duplicated.
        def handler(request):
            if request.url.path == "/wp-json/wc/store/v1/products":
                page = int(request.url.params.get("page", "1"))
                dupes = [_prod("dup", "d1"), _prod("dup", "d2")]
                return httpx.Response(200, json=dupes if page == 1 else [])
            return httpx.Response(200, text=PRODUCT_HTML)
        products = _adapter(handler).fetch_products()
        assert [p.sku for p in products] == ["dup"]  # only the first

    def test_does_not_fetch_pages_for_out_of_scope(self):
        # No product page GET for the SKU-less / non-importable products.
        calls: list[str] = []

        def handler(request):
            calls.append(request.url.path)
            return _handler(request)

        _adapter(handler).fetch_products()
        page_gets = [p for p in calls if p.startswith("/product/")]
        # only the 3 in-scope products' pages, never /product/nosku or /product/j1
        assert sorted(page_gets) == ["/product/b1/", "/product/b2/", "/product/d1/"]


class TestFetchSnapshots:
    def test_returns_snapshots_for_requested_skus_only(self):
        snaps = _adapter().fetch_snapshots([VendorProductId("bed-1"), VendorProductId("bed-2")])
        assert set(snaps) == {"bed-1", "bed-2"}
        s = snaps[VendorProductId("bed-1")]
        assert s.is_available is True
        assert s.stock_count is None          # binary source -> no fabricated count
        assert "&#8211;" not in (s.name or "")  # entities decoded

    def test_sku_not_in_catalog_is_absent(self):
        assert _adapter().fetch_snapshots([VendorProductId("GHOST")]) == {}

    def test_out_of_stock_snapshot_is_zero_and_unavailable(self):
        def handler(request):
            if request.url.path == "/wp-json/wc/store/v1/products":
                page = int(request.url.params.get("page", "1"))
                return httpx.Response(200, json=[_prod("oos-1", "oos", in_stock=False)] if page == 1 else [])
            return httpx.Response(404)
        snap = _adapter(handler).fetch_snapshots([VendorProductId("oos-1")])[VendorProductId("oos-1")]
        assert snap.is_available is False
        assert snap.stock_count == 0

    def test_does_not_fetch_tabs(self):
        calls: list[str] = []

        def handler(request):
            calls.append(request.url.path)
            return _handler(request)

        _adapter(handler).fetch_snapshots([VendorProductId("bed-1")])
        assert not any(p.startswith("/product/") for p in calls)


class TestBrowserResponseChallenge:
    """The engine's pure surface — no browser needed (playwright import is lazy)."""

    def test_json_content_type_is_never_challenge(self):
        r = BrowserResponse(200, '[{"sku":"x"}]', content_type="application/json")
        assert r.is_challenge is False
        assert r.json() == [{"sku": "x"}]

    def test_html_with_marker_is_challenge(self):
        r = BrowserResponse(200, "<html><script>a=toNumbers(slowAES)...</script></html>",
                            content_type="text/html")
        assert r.is_challenge is True

    def test_real_html_page_is_not_challenge(self):
        r = BrowserResponse(200, "<html><body><h1>שידת רויאל</h1></body></html>",
                            content_type="text/html")
        assert r.is_challenge is False
