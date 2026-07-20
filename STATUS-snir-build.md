# Snir build — status & resume point

> Work paused mid-build to move to another supplier. This is the resume doc.
> Spec sources of truth: `PRD-snir-product-sync.md` + `MAPPING-snir-categories.md`
> (LOCKED mapping). Last worked: 2026-07-19/20.

## TL;DR

The **mapping is fully locked and validated** against live data (292 SKU'd / 271
in-scope / 21 ignored; 221 in-stock create-now / 50 OOS deferred). The **pure code
is built, tested, and committed** (Phase 0-1). The build **paused at the adapter
(Phase 2)** because two things surfaced that need decisions:

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

## What's NOT done (remaining phases)

- **Phase 2** — `adapters/snir_baby.py` (Store API fetch + tab scrape + fetch_snapshots). **Blocked on the fetch-strategy decision below.**
- **Phase 3** — `snir_ingest.py` (dedup / skip-existing / OOS-skip / create / record, error-isolated + dry-run). Mirror `segal_ingest` (which now has the OOS gate).
- **Phase 4-5** — `snir-ingest` + `snir-sync` CLI subcommands + GH workflows; reuse `reconcile` for approve/ignore.

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

### Open decision B — variable product handling
- **Recommended:** one draft per **source parent SKU** (parent price), single-variant,
  **`needs_review=True`** so the owner reconciles against any hand-split store versions
  before activation. Deterministic; avoids inventing SKUs and dodges price=0.
- Alternative the owner floated: synthesize suffixed SKUs per size — but it's not
  derivable from source data and risks duplicating existing hand-split products.
- Note: 3 of the 9 already exist (skip-existing handles them). Only ~6 are net-new.

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
