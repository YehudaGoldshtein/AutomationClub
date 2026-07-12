# Implementation Plan — Laura Product Upload (Excel → Shopify)

Turns the field-mapping spec (`PRD-laura-product-upload.md`, currently in Downloads —
**should be committed into this repo alongside this plan**) into a build. Covers the
code + data + workflow + dashboard changes needed to create net-new Shopify products
from Laura's "סטטוס מוצרים" xlsx, as **drafts** pending human confirmation.

> The PRD owns the **field mapping and grouping rules**. This document owns the
> **architecture, seams, storage, flow, and rollout**. Where they overlap, the PRD wins
> on *what goes in a field*; this plan wins on *how it's wired*.

---

## 1. Decisions locked (from design discussion)

| # | Decision |
|---|---|
| Price | Use `מחיר מומלץ` (recommended) directly as `variant.price`. No `×1.77` for now. **Price moves from "reference" into scope** — a create without price lists a live ₪0 product. |
| Missing sub-category collection | **Create it**, and flag the product `is_new_collection` for review. |
| Initial stock | Create with **0**; the next hourly scrape reconciles real stock (keeps the file out of the stock path — consistent with the "two truths"). |
| Publish state | **Always create as `status: draft`.** Never auto-live. |
| Confirmation | Dashboard "Pending" view → user approves → **DB flag**. The tokened sync job flips `draft → active`. Dashboard never touches Shopify. |
| Storage | **Add columns to `store_products`** — no new table. A draft product *is* a store product; the per-sync upsert is column-scoped so lifecycle fields survive. |

---

## 2. The two truths (context)

- **Eligibility / "allowed to sell"** — sourced from *this xlsx*. Its SKU set is the **catalog driver**: the set of products that may exist on the store. Laura-only, additive.
- **Availability / "in stock"** — sourced from the **web scrape**, as today. The file's own `מלאי זמין` column is **ignored** for stock.

This plan implements the *creation* half. Eligibility gating of existing items (unpublish
logic, the `>N%` guard) is tracked separately and is **not** part of this plan.

---

## 3. New / changed seams (code)

### 3.1 Domain types (`inventory_sync/domain.py`)
```
ProductDraft        title, body_html, vendor, product_type, tags,
                    option_name ("מידה"), variants: list[VariantSpec],
                    image_urls: list[str],
                    category_collection_id: str,          # constant 477920559358
                    subcategory_collection_name: str      # resolved to id at create
VariantSpec         option_value (size|None), sku, barcode, price: Decimal
CreatedProduct      store_product_id, variants: dict[sku -> (variant_id, inventory_item_id)]
CollectionRef       id: str, created: bool                # created=True → new collection flag
```

### 3.2 `StorePlatform` interface (`interfaces.py`) — three additions
```
def create_product(self, draft: ProductDraft) -> CreatedProduct: ...
def ensure_collection(self, title: str) -> CollectionRef: ...   # find-or-create custom collection
def add_to_collection(self, store_product_id: str, collection_id: str) -> None: ...
```
Price is set **inside** `create_product` (via `VariantSpec.price`) — no separate set-price
method for now. Every seam addition means:
- `ShopifyAdapter` real impl (below),
- **`InMemoryStore` fake** (`fakes.py`) gets all three (day-one-fake rule),
- contract/unit tests for both.

### 3.3 `ShopifyAdapter` (`adapters/shopify.py`) — REST calls
Per PRD §7:
```
create_product:    POST /products.json {title, body_html, vendor, product_type, tags,
                                        options:["מידה"], variants[], images[]}
                   → parse response → cache _VariantRef per new SKU → return CreatedProduct
ensure_collection: GET /custom_collections.json (paginate, match by title)
                   → if missing: POST /custom_collections.json {title} → created=True
add_to_collection: POST /collects.json {product_id, collection_id}
```
Notes:
- `create_product` mints the `product_id` / `inventory_item_id` that today's methods only
  ever *look up* — this is the one call that goes the other direction.
- Cache the resolved collection name→id map for the run (like `_location_id`).
- Requires the custom-app token to have **`write_products`** scope — **verify before building**
  (today's token only demonstrably has read + inventory/status write).

---

## 4. Data model — `store_products` new columns

`store_products` is keyed `(customer_id, sku)`; add lifecycle state:

| Column | Type | Default (existing rows) | Meaning |
|---|---|---|---|
| `status` | String | `'active'` | `draft` \| `active` |
| `approved` | Boolean | `true` | dashboard sets `true` to confirm |
| `approved_at` | DateTime? | `null` | when approved |
| `is_new_collection` | Boolean | `false` | ingest created a collection for this product |
| `needs_review` | Boolean | `false` | missing image/text, or grouping < confident |

**Backfill matters:** existing products must default to `status='active', approved=true`
so they are **not** swept into the pending queue.

**Migration:** there is **no migration framework** — persistence calls
`metadata.create_all()`, which is create-if-not-exists only and will **not** add columns to
the existing `store_products` table. So:
- Add the `Column(...)` defs to `schema.py` (covers fresh dev SQLite).
- Ship a one-shot `scripts/migrate_store_products_lifecycle.py` that runs the explicit
  `ALTER TABLE store_products ADD COLUMN ...` (with the backfill defaults) against Neon
  **and** any existing dev SQLite. Idempotent (check `information_schema` / `PRAGMA`).

**Keep the upsert column-scoped.** `StoreProductStore.upsert_many`'s
`on_conflict_do_update` set must continue to touch only `handle/title/store_product_id/updated_at`
(store_product_store.py:52-56). Adding the lifecycle columns to that `set_` would clobber
approval on every hourly sync — the whole reason this works is that it doesn't.

**New `StoreProductStore` methods:**
```
write_pending(customer_id, CreatedProduct+meta)   # status=draft, approved=false, flags
list_pending(customer_id) -> grouped by store_product_id
list_approved_drafts(customer_id) -> rows where status=draft AND approved=true
mark_active(customer_id, store_product_id)         # status=active
```

---

## 5. Ingest flow (the real body of `inventory-ingest.yml`)

New CLI entrypoint: `python -m inventory_sync ingest --blob-url <url> --customer-id <id>`.
This replaces the stub in `.github/workflows/inventory-ingest.yml`.

```
1. Download blob (public Vercel URL, plain GET — no auth).
2. Parse Sheet1 with openpyxl → rows (10 content columns).
3. DETECT NEW (skip-dominant): for each row, check its SKU against the store
   (store_products cache / list_products). SKU already on store → SKIP. This is the
   common case — a typical upload has ~2025 rows and only ~1-10 survive as new.
   Keep only the NEW-SKU rows.
4. GROUP the new-SKU rows into products (PRD §2):
     - extract size token (lexicon §2.1); title = description minus size;
     - color = product; sizes = variants under option "מידה";
     - metric size (34*44…) → variant only if base has ≥2 sizes, else single-variant;
     - normalize typos (6-3, 3-0).
   Unknown size token / grouping miss → mark needs_review.
   SAFETY GUARD (for the "new SKU = new product, probably" assumption): if a new SKU's
   computed title matches a product ALREADY on the store, do NOT create a duplicate —
   flag needs_review and skip create. Catches the rare "new size of an existing product"
   case without building an add-variant path in v1.
5. For each NEW product group:
     a. resolve subcategory collection: ensure_collection(Appendix-A[family])
        → CollectionRef; is_new_collection = ref.created.
     b. build ProductDraft (price = מחיר מומלץ; body_html = §4 template; stock left at 0).
     c. create_product(draft)  [status=draft]
     d. add_to_collection(pid, 477920559358)        # category (always)
        add_to_collection(pid, ref.id)              # subcategory
     e. store.write_pending(...) → store_products rows (draft, approved=false, flags)
6. Emit Axiom event per created product (extend the stub's inventory_ingest event).
```
Runs happy-path safe: a `--dry-run` variant parses + groups + logs with **no** Shopify writes
(mirrors the existing sync `--dry-run`), so the grouping algorithm can be validated in CI /
against a real file before any product is created.

---

## 6. Activation (draft → active) reconcile

Where the DB flag becomes a live product. Fold into the **existing sync pass** (so the hourly
cron activates confirmed items with no new infra) + optional dashboard "activate now":
```
in orchestrator.run_sync_pass (or a small reconcile step):
  for row in store.list_approved_drafts(customer_id):
      store_platform.republish(sku)      # reuse: sets Shopify status=active
      store.mark_active(customer_id, row.store_product_id)
```
`republish` already sets `status='active'` — no new Shopify call needed. Activation is
product-level: group approved rows by `store_product_id`.

---

## 7. Dashboard changes (`automationclub-dashboard` repo)

- **Drizzle schema:** mirror the 5 new `store_products` columns.
- **New route** `/c/[customerId]/pending`: rows where `status='draft' AND approved=false`,
  **grouped by `store_product_id`**; show image, title, `product_type`, price, and ⚠️ badges
  (`is_new_collection`, `needs_review`).
- **Approve action:** a server action / `POST /api/pending/[productId]/approve` that sets
  `approved=true, approved_at=now` for all rows of that `store_product_id`.
  - This is a **new write path** for the dashboard (previously read-only + dispatch). It writes
    **app state in Neon only** — it must **not** gain a Shopify token. Security model intact.
- **Optional** "Activate now" button → `workflow_dispatch` a reconcile run (same pattern as the
  existing trigger button) instead of waiting for the next hourly tick.

---

## 8. Testing

- **Grouping algorithm** — unit tests for every §2 edge: size at end / middle, metric vs
  clothing (both sides ≤24 = months), the ≥2-sizes metric rule, typos (6-3, 3-0),
  single-variant majority (513/606). This is where correctness lives.
- **`InMemoryStore` fake** gains the 3 methods; **contract tests** run the shared suite against
  fake + real SQL persistence (existing pattern).
- **ShopifyAdapter** — httpx-mocked unit tests for create/ensure_collection/add_to_collection
  incl. find-vs-create collection branch and 4xx handling.
- **Reverse-validation harness** — keep the PRD's rules-vs-live-site check (99.6% / 809-812) as
  a CI guard against regressions in the grouping rules.

---

## 9. Phasing

| Phase | Deliverable | Behavior change | Status |
|---|---|---|---|
| 0 | `store_products` columns + `scripts/migrate_*` + backfill; keep upsert column-scoped | none (data only) | ✅ done |
| 1 | domain types + 3 `StorePlatform` methods + `ShopifyAdapter` impl + fake + tests | none (not wired) | ✅ done |
| 2 | xlsx parser + grouping algorithm + mapping + `ingest` CLI with `--dry-run` | none (dry-run only) | ✅ done |
| 3 | wire real `inventory-ingest.yml`; create drafts; write pending rows | **creates draft products** | ✅ done |
| 4 | activation reconcile in sync pass + `reconcile` CLI + `reconcile.yml` (`approved → active`) | approved drafts go live | ✅ done |
| 5 | dashboard Pending view + approve write (+ optional activate-now) | human confirmation loop | ⬜ dashboard repo |

Phases 0-4 (all Python) shipped behind tests (356 green). First live effect is Phase 3, and it
only ever creates **drafts** — invisible on the storefront until Phase 4/5 confirmation. Low blast
radius by construction. Phase 5 lives in `automationclub-dashboard` (prompt provided).

**Go-live prerequisites:** (a) run `scripts/migrate_store_products_lifecycle` against Neon once;
(b) load the authoritative Appendix-A family strings (see §10.9); (c) validate with a
`dry_run=true` ingest dispatch before the first live create.

---

## 10. Open items / risks

1. ~~**`write_products` scope**~~ — ✅ **confirmed** (2026-07-12): the custom-app token grants
   `write_products`, `read_products`, `write_inventory`, `write_publications`, `write_content`.
   Product + custom-collection + collect creation are all unblocked.
2. **Barcode data quality** — a probe of the 16/06 snapshot showed `ברקוד` with only ~20
   distinct values across 2025 rows, contradicting PRD §1's "barcode as-is". Re-verify on the
   30/06 file before trusting `variant.barcode`.
3. **Size lexicon (§2.1) is hardcoded** — new sizes will silently fall through to
   single-variant. Log unknown tokens; plan periodic review.
4. **Existing-SKU update path (§8)** is underspecified (which fields to overwrite; don't clobber
   manual tags §5). **Phase 2+**; ingest v1 only *creates*, skips existing SKUs.
5. **Idempotency** — re-running the same blob must not duplicate products; the step-4a existence
   check is the guard. Test double-ingest.
6. **Price semantics** — is `מחיר מומלץ` final (VAT-inclusive) and the intended sell price? Confirm.
7. ~~**`store_products` conceptual drift**~~ — ✅ ARCHITECTURE.md updated: the invariant now
   notes it's cache + lifecycle state, and that the metadata-scoped upsert protects it.
8. **The 0.4% grouping misses** (3/812) — route to `needs_review`, never auto-create silently.
9. **Appendix-A family strings** — resolved (2026-07-12): PRD creator supplied 82 exact
   `תאור משפחה` values. Code uses `.strip()` (hidden leading/trailing spaces in the data) +
   the authoritative map. **12 families are inferred/unconfirmed** (not yet seen in the store) —
   kept OUT of the active map so they fall to `needs_review` until the site owner confirms them.
