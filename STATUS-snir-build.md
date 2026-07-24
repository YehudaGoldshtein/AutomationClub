# Snir build — status & resume point

> Work paused mid-build to move to another supplier. This is the resume doc.
> Spec sources of truth: `PRD-snir-product-sync.md` + `MAPPING-snir-categories.md`
> (LOCKED mapping). Last worked: 2026-07-19/20.

## TL;DR

The **mapping is fully locked and validated** against live data (292 SKU'd / 271
in-scope / 21 ignored; 221 in-stock create-now / 50 OOS deferred). Pure code
(Phase 0-1) **and now the Phase 2 adapter + Playwright fetch engine** are built,
tested, and green. Remaining: Phase 3 ingest + Phase 4-5 CLI/workflows.

**Decision A is RESOLVED → Playwright** (`browser_fetch.PlaywrightClient`). Live
recon (2026-07-24, via a real browser) re-confirmed: one navigate solves the WAF,
after which same-origin `fetch()` returns clean JSON + product HTML. ⚠️ Correction
to the note below: **`x-robots-tag: noindex` is NOT a challenge signal** — it is
present on *valid* JSON responses too. The reliable signal is `content-type:
text/html` (+ challenge script markers) where JSON was expected.

Two challenges originally surfaced here (Decision B still open for Phase 3):

1. **The anti-bot WAF is real and rate/behavior-gated** — it triggered mid-recon.
2. **Variable products share one SKU** across variations — "product per size" isn't
   algorithmically reproducible.

## What's DONE (committed on `main`)

| commit | what |
|---|---|
| `04028e9` | **Segal retrofit** — OOS-at-source products no longer onboarded (cross-supplier rule; `IngestSummary.skipped_oos`). Own commit, as requested. |
| `58d6def` | **Docs** — PRD + `MAPPING-snir-categories.md` (locked id-based mapping). |
| `e56fa00` | **Snir Phase 0-1** — `snir_source.py` + `snir_mapping.py` + tests. Validated live: 292/271/21, 221/50. |

Full suite green at pause: **471 passed.**

Code landed:
- `inventory_sync/snir_source.py` — `parse_api_product` (whole-unit price, binary
  stock, category ids, short/description split), `parse_tabs`
  (`woocommerce-Tabs-panel--<name>`), `tab_html`.
- `inventory_sync/snir_mapping.py` — precedence id-routing (`route`), `is_importable`,
  field assembly (`to_product_draft`), warranty/delivery constants, studio-boutique
  delivery block, `collections_for`, template-from-product_type. `DEFAULT_STOCK_QTY=10`.
- Tests: `tests/test_snir_source.py` (9), `tests/test_snir_mapping.py` (20).

Cross-supplier rule saved to memory: `oos-not-onboarded-rule` (Laura exempt).

## Phase 2 — DONE (built this session, not yet committed)

- `inventory_sync/browser_fetch.py` — **`PlaywrightClient`**: duck-types the
  `httpx.Client.get(url, params=)` slice adapters use (returns `BrowserResponse`
  with `.status_code`/`.text`/`.json()`). Solves the WAF once on `open()`, then
  same-origin `fetch()` per GET, with rate-limit (`min_interval`), exponential
  backoff, and challenge-detect/re-solve/retry. Optional `browser` extra in
  pyproject (`pip install -e ".[browser]" && python -m playwright install chromium`).
- `inventory_sync/adapters/snir_baby.py` — **`SnirStoreApiAdapter`** (mirrors
  `segal_baby`): `list_products` (paginate all), `fetch_tabs`, `fetch_products`
  (only GETs pages for in-scope = importable + SKU'd, to spare the WAF),
  `fetch_all`, `fetch_snapshots` (binary stock, no tab fetch). Transport-agnostic.
- `tests/test_snir_adapter.py` — 15 tests via `httpx.MockTransport` (no browser),
  incl. challenge/non-JSON handling + `BrowserResponse.is_challenge` surface.
  Full suite green: **593 passed.**
- ✅ **Dry run verified end-to-end against live Snir** (2026-07-24, `snir_dry_run.py`,
  headless): `PlaywrightClient` solved the WAF from Python, listed 342 products,
  scoped to 291 SKU'd / **270 in-scope** / 220 in-stock / 50 OOS (spec 292/271/221/50
  — off by ≤1 from catalog drift), scraped `tech_details` tabs, and mapped a sample
  cleanly (routing precedence + template split + collections + metafields all correct).
  ✅ **Full-volume run** (2026-07-24): all 270 in-scope mapped, 0 errors, **0 WAF
  challenges** across ~290 fetches, 412s (~7min), 3 non-200 tab pages (graceful), 29
  with no tech tab (valid per PRD §8). Still untested from a GHA datacenter IP —
  watch for a harder challenge there.
- SKU uniqueness verified: 291 SKU'd → 291 distinct; 270 in-scope → 270 distinct (0 dupes).

## Phase 3 — DONE (unified pass, this session, not yet committed)

Owner decision: **catalog + stock sync unified** (not a separate ingest). Built as a
`UnifiedSource` binding on the existing `supplier_pass.unified_pass` engine (same as
Segal/Bambino), so one pass lists the catalog once, stock-syncs existing products, and
onboards new ones — the expensive tab scrape runs only for genuinely-new products.
- `inventory_sync/snir_pass.py` — **`SnirUnifiedSource`** (mirrors `segal_pass`).
- **OOS gate**: enforced by `unified_pass` (a new product OOS at source is not onboarded).
- **Decision B**: multi-variation shared-SKU products are onboarded single-variant on the
  parent SKU and flagged `MULTI_VARIANT` (new `review_reasons` code) for owner reconcile.
- **Scan dedup**: `list_catalog` first-SKU-wins; SKU-less products dropped.
- CLI: **`snir-pass`** subcommand (`--dry-run`, `--headed`) in `__main__.py`, managing the
  PlaywrightClient lifecycle. `tests/test_snir_pass.py` (7). Full suite: **605 passed.**

## What's NOT done (remaining)

- **Phase 4-5** — GH workflow(s) for `snir-pass` (cron every few hours), like `segal-pass.yml`.
  Reuse `reconcile` for approve/activate (already cross-supplier). Decide runner: GHA
  datacenter IP may need a harder-challenge fallback (self-hosted runner / proxy) — test first.
- Live `snir-pass --dry-run` end-to-end (needs Shopify creds; the Snir-only path is verified).

## ⚠️ Challenge 1 — the WAF (blocking the adapter design)

Plain HTTP worked all session, then **sustained automated fetches tripped a WAF**.
The same API URL began returning (evidence captured):
```
HTTP/2 200 · content-type: text/html · x-robots-tag: noindex
<!DOCTYPE html>...<script>!function(n){...MD5...}</script>   ← JS-challenge, not JSON
```
This is exactly PRD §0's MD5 JS-challenge. Conclusions:
- The WAF is **real and volume/behavior-gated**, not always-on (that's why early
  probing succeeded and misled the initial "no WAF" read).
- Plain `requests`/`curl` cannot pass it (no JS execution).
- Production concern: Snir onboarding = ~271 products + a page scrape each → high
  volume → likely to trip the WAF and/or get GHA's datacenter IP challenged.

### Open decision A — fetch strategy
Options:
1. **Plain HTTP + rate-limit + backoff + challenge-detection/retry** — cheapest; may
   still get hard-blocked on CI.
2. **Headless browser (Playwright)** per PRD §0 — solve the JS-challenge once in a
   real browser → capture cookie → reuse for same-origin `fetch()` of JSON + pages.
   Runs on GHA technically; **will pass a pure JS-challenge from any IP**, but GHA's
   datacenter IP *might* get a harder challenge (CAPTCHA — unsolvable) or headless
   fingerprinting. Unknown until tested.
3. **Probe first (recommended next action):** a throwaway GHA workflow that curls the
   API and does a Playwright fetch, printing JSON-vs-challenge for each. Only this
   tells us definitively whether GHA is fine / needs-browser / hard-blocked. Cheap.

Fallbacks if GHA is hard-blocked: self-hosted runner on a non-datacenter IP, or a
proxy. Don't build these until the probe proves they're needed.

Whatever the choice, the adapter must **rate-limit + backoff + reuse a solved cookie**
and detect the challenge page (HTML/`content-type: text/html` where JSON is expected)
and retry rather than parse it as data.

## ⚠️ Challenge 2 — variable products share one SKU

The 9 `variable` products with 2+ real variations **all reuse the parent SKU across
every variation** (verified via `/products/<variation_id>`). Variations differ by
size/color and sometimes price; a few have **price=0** (broken). SKU is our identity
key (§1), so there is no per-variation SKU to split on.

The store's own handling is **inconsistent/manual**: `ro001-1` and `miz-sweet` are
single products; `2345654321` was split by color into `2345654321` + `-1` (with a
*third* suffix `-2` as the source parent); `567887654321234567-1` is a *different*
mattress, not a size. **No algorithmic rule reproduces the owner's hand-suffixing.**

Re-verified live 2026-07-24 (`snir_scrape/variations.json`): **12 products have 2+
variations; ALL 12 share one SKU, 0 have per-variation SKUs.** 9 of the 12 have a real
parent SKU; the other 3 have an **empty** SKU. 2 have a zero-price variation.

### Decision B — RESOLVED (owner, 2026-07-24)
- **Onboard as single-variant on the parent SKU** (parent price) — "add the first
  variant, skip the additional". No whole-product skip. Dodges price=0 and invents no SKUs.
- **Skip products with no SKU** (the 3 empty-SKU variable products drop out here).
- **Scan dedup:** first product to claim a SKU wins; a later product whose SKU is already
  taken is skipped (product-level, in the adapter). Store-side skip-existing = Phase 3.
- `snir_mapping.shares_variant_sku` flags these (variable + 2+ variations) so Phase 3
  ingest can set **`needs_review`**. `SnirProduct` now has `wc_type` + `variation_count`.
- Implemented + tested in `adapters/snir_baby.py`, `snir_mapping.py`, `snir_source.py`
  (full suite 598 passed).

## Other decisions still open (from MAPPING §7, minor)

- Vendor collection binding: `שניר בבה` (478221697278) vs `snirbebe` (478222582014)
  — both empty. Not required; if wanted, use `שניר בבה` and delete the duplicate.

## Recommended resume sequence

1. Run the **GHA probe** (decision A) → pick plain-HTTP-hardened vs Playwright.
2. Confirm **decision B** (variable products → parent-SKU + needs_review).
3. Build Phase 2 adapter around the chosen fetch path (rate-limit + cookie reuse +
   challenge-retry), then Phase 3 ingest (mirror `segal_ingest`, incl. OOS gate),
   then Phase 4-5 CLI + workflows.

## Handy verified facts (avoid re-deriving)

- Store API: `GET /wp-json/wc/store/v1/products?per_page=100&page=N` (4 pages, 330),
  `/products/categories?per_page=100`, `/products/<id>` for a variation.
- Price `currency_minor_unit=0` (whole ₪). Stock binary (`add_to_cart.maximum=9999`).
- body_html ← `short_description`; `custom.view_productss` ← API `description`;
  `custom.infoo` ← `tech_details` tab (only scrape needed); warranty/delivery constants.
- Collection ids all verified (MAPPING §6). Excluded ids: 129, 420.
- Repo store: `bguhwj-wj.myshopify.com`; Snir vendor tag `שניר | snir`.
