"""Playwright-backed HTTP client for WAF-protected origins (Snir).

Snir's WooCommerce Store API and product pages sit behind an anti-bot WAF that
serves a JS-challenge (a cookie-setting page that computes a token and reloads)
to plain HTTP clients — they receive `content-type: text/html` where JSON is
expected, never the data. A *real* browser solves the challenge on first
navigation; from then on same-origin `fetch()` returns clean JSON and product
HTML. (Verified live 2026-07-24: after one navigate to the site, an in-page
`fetch('/wp-json/wc/store/v1/products')` returned JSON; `x-robots-tag: noindex`
is present on *valid* responses too, so it is NOT a challenge signal.)

The design goal is that adapters stay transport-agnostic. This client exposes
the slice of `httpx.Client` the adapters use — `.get(url, params=None)`
returning an object with `.status_code`, `.text`, `.json()` — so
`adapters/snir_baby.py` is written like any other adapter and is unit-tested
with a plain `httpx.MockTransport` (this engine never runs in tests).

Requires the optional `browser` extra:
    pip install -e ".[browser]"  &&  python -m playwright install chromium
"""
from __future__ import annotations

import json as _json
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from inventory_sync.log import Logger, get

# The challenge page is HTML (where we often expect JSON) whose body runs a
# cookie-setting anti-bot script and reloads. These markers identify it so we
# re-solve + retry instead of parsing a challenge as data. `x-robots-tag` is
# deliberately absent — it appears on valid responses too (see module docstring).
_CHALLENGE_MARKERS = re.compile(
    r"slowAES|toNumbers|aes\.js|hex_md5|a=toNumbers|jschl|__cf|"
    r"document\.cookie\s*=|Checking your browser|Just a moment",
    re.IGNORECASE,
)

# Same-origin fetch run inside the solved page. credentials:'include' attaches
# the WAF cookie; we return status + content-type + raw text (parsing is the
# caller's job, exactly like httpx).
_FETCH_JS = """
async (u) => {
  const r = await fetch(u, {
    credentials: 'include',
    headers: {'Accept': 'application/json, text/html;q=0.9, */*;q=0.8'},
  });
  const text = await r.text();
  return {status: r.status, contentType: r.headers.get('content-type') || '', text};
}
"""

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class BrowserResponse:
    """httpx.Response look-alike (the slice adapters actually use)."""
    status_code: int
    text: str
    content_type: str = ""

    def json(self) -> Any:
        return _json.loads(self.text)

    @property
    def is_challenge(self) -> bool:
        """True when the body looks like the WAF JS-challenge rather than data.

        A JSON content-type is never a challenge. Otherwise we look for the
        challenge script markers in the first slice of the body (the real
        product page is ~290KB of content and carries none of them).
        """
        if "json" in self.content_type.lower():
            return False
        return bool(_CHALLENGE_MARKERS.search(self.text[:4000]))


@dataclass
class PlaywrightClient:
    """A browser that solves the WAF once, then serves same-origin GETs.

    Use as a context manager (or call `open()` / `close()`). `.get()` mirrors
    `httpx.Client.get(url, params=...)`.

      base_url      origin to solve + fetch same-origin against (use the final
                    www host; a redirecting host would make fetches cross-origin)
      headless      run Chromium headless (default True)
      min_interval  minimum seconds between requests (rate-limit; WAF is
                    volume/behaviour-gated per the recon)
      max_retries   challenge re-solve attempts before returning the challenge
                    response (which the adapter treats as a failed fetch)
    """
    base_url: str = "https://www.snir-bebe.com"
    headless: bool = True
    min_interval: float = 1.0
    max_retries: int = 3
    backoff_base: float = 2.0
    nav_timeout_ms: int = 45_000
    user_agent: str = _DEFAULT_UA
    locale: str = "he-IL"
    logger: Logger = field(default_factory=lambda: get("browser_fetch"))

    _pw: Any = field(default=None, init=False, repr=False)
    _browser: Any = field(default=None, init=False, repr=False)
    _context: Any = field(default=None, init=False, repr=False)
    _page: Any = field(default=None, init=False, repr=False)
    _last_request: float = field(default=0.0, init=False, repr=False)

    # --- lifecycle ---------------------------------------------------------

    def open(self) -> "PlaywrightClient":
        # Imported lazily so the module (and the test suite) load without the
        # optional `browser` extra installed.
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            user_agent=self.user_agent,
            locale=self.locale,
            viewport={"width": 1366, "height": 900},
        )
        self._page = self._context.new_page()
        self._page.set_default_navigation_timeout(self.nav_timeout_ms)
        self._solve_challenge()
        return self

    def close(self) -> None:
        for obj, meth in (
            (self._context, "close"),
            (self._browser, "close"),
            (self._pw, "stop"),
        ):
            try:
                if obj is not None:
                    getattr(obj, meth)()
            except Exception:
                self.logger.exception("browser_teardown_failed")
        self._context = self._browser = self._pw = self._page = None

    def __enter__(self) -> "PlaywrightClient":
        return self.open()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- httpx.Client-shaped surface --------------------------------------

    def get(self, url: str, params: dict | None = None) -> BrowserResponse:
        """GET `url` (with optional query `params`) via same-origin fetch.

        On a detected challenge page, re-solves the WAF and retries with
        exponential backoff up to `max_retries`; the final challenge response is
        returned so the adapter's non-JSON handling can log + skip it.
        """
        if self._page is None:
            raise RuntimeError("PlaywrightClient is not open(); use as a context manager")
        full = f"{url}?{urlencode(params)}" if params else url
        resp = self._fetch(full)
        attempt = 0
        while resp.is_challenge and attempt < self.max_retries:
            attempt += 1
            self.logger.warning("challenge_detected", url=full, attempt=attempt,
                                 status=resp.status_code)
            self._solve_challenge()
            time.sleep(self.backoff_base ** attempt)
            resp = self._fetch(full)
        if resp.is_challenge:
            self.logger.error("challenge_unsolved", url=full, attempts=attempt)
        return resp

    # --- internals ---------------------------------------------------------

    def _fetch(self, full_url: str) -> BrowserResponse:
        self._throttle()
        try:
            result = self._page.evaluate(_FETCH_JS, full_url)
        except Exception:
            self.logger.exception("browser_fetch_failed", url=full_url)
            return BrowserResponse(status_code=0, text="", content_type="")
        return BrowserResponse(
            status_code=int(result.get("status", 0)),
            text=result.get("text", "") or "",
            content_type=result.get("contentType", "") or "",
        )

    def _solve_challenge(self) -> None:
        """Navigate to the origin so the browser runs + clears the JS-challenge."""
        try:
            self._page.goto(self.base_url, wait_until="networkidle")
        except Exception:
            # networkidle can time out on a busy page even after the challenge
            # cleared; a load is enough for the cookie to be set.
            self.logger.warning("solve_navigation_slow", base_url=self.base_url)
            try:
                self._page.goto(self.base_url, wait_until="load")
            except Exception:
                self.logger.exception("solve_navigation_failed", base_url=self.base_url)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()
