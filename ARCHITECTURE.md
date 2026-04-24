# Architecture — Inventory Sync

Living document. Updated whenever a structural change ships.

---

## Core principles

### ⚡ Everything pluggable

**Every external integration point lives behind an interface. No exceptions.**

We start with Shopify and one vendor. We are building a **platform**, not a one-off.
Every seam that touches the outside world — store platform, supplier source,
notification channel, stock action, scheduler, config store, log sink — is
implemented behind an abstraction **from day one**, so that adding the next
platform, vendor, channel, or sink is a new adapter, not a rewrite.

**Design for the second integration before shipping the first.** Non-negotiable and applies to every PR.

### 📝 Log everything, from day one

**The logger is the first thing built, and every subsequent component uses it.**

Structured logs hit rotating files + stdout. Every adapter, engine step, and
notification dispatch logs its outcome. There is no "we'll add logging later."
Logging is infrastructure, and it shipped in v0.1 before anything else. The
logger itself is a `Logger` interface, so swapping to Datadog/Axiom later was
one adapter change — and it already happened (see **Observability** below).

### 🏢 Row-level multi-tenancy

One set of tables, one `customer_id` column on every tenant-scoped row.
Industry-standard SaaS pattern (Stripe, Linear). Onboarding a new customer is
a row insert, not a DDL migration. Defense-in-depth via Postgres RLS policies
can be layered on later without schema changes.

---

## Deployment topology

```
   ┌──────────────────────────────┐            ┌───────────────────────────────┐
   │ GitHub Actions (hourly cron) │            │ automationclub-dashboard      │
   │   .github/workflows/sync.yml │            │ (Vercel, Next.js 16)          │
   │   +  workflow_dispatch       │◄───────────│ admin + per-customer UI       │
   │      (Trigger-Sync button)   │  POST      │ Auth.js credentials login     │
   └──────────────┬───────────────┘  /dispatch └──────────────┬────────────────┘
                  │                                           │ read-only
                  │  python -m inventory_sync sync            │ Drizzle ORM
                  ▼                                           │
    ┌──────────────────────────────────────────┐              │
    │  inventory_sync CLI — fresh container    │              │
    │  per tick; no long-running state         │              │
    └┬────────────┬───────────┬───────────┬────┘              │
     │            │           │           │                   │
     ▼            ▼           ▼           ▼                   ▼
 ┌────────┐ ┌──────────┐ ┌────────┐ ┌──────────────────┐ ┌─────────────────────┐
 │Shopify │ │Vendor    │ │Resend  │ │wa-notifier-bridge│ │Neon Postgres        │
 │Admin   │ │sites     │ │HTTPS   │ │(Fly.io, Go)      │ │state, cache, runs,  │
 │API     │ │(scrape)  │ │email   │ │POST /api/send    │ │customers,           │
 │per-    │ │          │ │        │ │(+ customer_id)   │ │store_products,      │
 │cust.   │ │          │ │        │ │                  │ │users                │
 └────────┘ └──────────┘ └────────┘ └──────────────────┘ └─────────────────────┘
     │          │           │           │                   ▲
     │          │           │           │                   │
     └──────────┴───────────┴───────────┴─────── structured logs ──┐
                                                                    ▼
                                               ┌───────────────────────────────┐
                                               │  Axiom (one dataset, three    │
                                               │  services). Cross-service     │
                                               │  filter: `customer_id == …`   │
                                               └───────────────────────────────┘
```

**Why GitHub Actions, not a long-running server:** sync is a scheduled batch
job. A fresh container per tick is cheaper and simpler than a VPS, and the
public repo gives unlimited Actions minutes. All persistent state lives in
Neon.

**Why Fly for the bridge:** WhatsApp Web needs a long-lived WebSocket + a
session SQLite on persistent disk. Serverless can't host that. The bridge is
one instance, one WhatsApp number, many caller tokens (multi-tenant).

**Why Vercel for the dashboard:** UI is stateless — all it does is read Neon
and dispatch a workflow. Serverless is the right fit, and Vercel gives
zero-config previews per PR.

---

## Sync lifecycle

A single `python -m inventory_sync sync` invocation:

```
                                      ┌────────────────────────────┐
                                      │ list due customers         │  ← customers.last_synced_at
                                      │ (list_due, honors          │     + sync_interval_minutes
                                      │  sync_interval_minutes)    │
                                      └──────────────┬─────────────┘
                                                     │
                                                     ▼
                             for each due customer ◄─┘
                                     │
                                     ▼
          ┌──────────────────────────────────────────────────────────┐
          │  vendor_scan_pass(vendor, ids, ttl)                      │
          │  ┌──────────────────────────────────────────────┐        │
          │  │ 1. read vendor_snapshot_cache (fresh rows)   │ ← cache│
          │  │ 2. for stale/missing ids, hit vendor network │        │
          │  │ 3. upsert back into cache                    │        │
          │  │ 4. return merged dict                        │        │
          │  └──────────────────────────────────────────────┘        │
          │                                                          │
          │  (One network scan per vendor. Customer #2 sharing the   │
          │   same vendor gets cache hits → near-zero cost.)         │
          └──────────────────────────┬───────────────────────────────┘
                                     ▼
          ┌──────────────────────────────────────────────────────────┐
          │  orchestrator.run_sync_pass(customer_id, vendor, …)      │
          │   a. fetch store products + catalog-filter via sitemap   │
          │   b. upsert store_products (handle, title, product_id)   │
          │      so the dashboard can build deep links               │
          │   c. engine applies stock policy (set-stock / un/rep.)   │
          │   d. compute unarchive-candidate delta vs item_state     │
          │   e. dispatch aggregated summary (first-run / delta /    │
          │      errors) per customer.notifications routing          │
          │   f. persist new item_state + sync_run + sync_run_changes│
          └──────────────────────────┬───────────────────────────────┘
                                     ▼
                    customers.mark_synced(customer_id, now)
```

**Caching gate (`ttl_minutes`).** Defaults to `min(customer.sync_interval, 60)`.
A customer on an hourly cadence sees data at most 1 hour old. A customer on a
15-min cadence sees data at most 15 min old. The cache itself is global, so
simultaneous customers benefit automatically.

**Dry-run mode** (`--dry-run`): wraps the Shopify adapter with a no-write
proxy, wraps the ItemStateStore with a no-write proxy, swaps the real
Notifier for `PreviewNotifier`, and skips `customer_repo.mark_synced()`. The
vendor cache still updates — it's raw vendor data, useful regardless.

---

## Interfaces

Each row below is a Protocol in the domain layer. Concrete implementations
live under `inventory_sync/adapters/` or `inventory_sync/persistence/` and
are selected via config, never by `if` branches in callers.

| Seam | Interface | v1 impl | Future impls |
|---|---|---|---|
| Store platform | `StorePlatform` | `ShopifyAdapter` | WooCommerce, Magento, BigCommerce |
| Supplier source | `SupplierSource` (+ optional `fetch_catalog_skus` for sitemap pre-filter) | `LauraDesignScraperAdapter` | Other scrapers, vendor REST APIs, CSV feeds |
| Notification channel | `NotificationChannel` | `ResendEmailAdapter`, `WhatsAppBridgeAdapter` | SMS, Slack, Telegram, webhooks |
| Stock policy | `StockPolicy` | `DefaultStockPolicy` (binary + exact-count modes) | pause-ads, auto-reorder, per-product overrides |
| Sync run store | `SyncRunStore` | `SqlSyncRunStore` (SQLAlchemy Core) | S3 snapshot, external log service |
| Item state store | `ItemStateStore` | `SqlItemStateStore` | same |
| Customer repository | `CustomerRepository` | `SqlCustomerRepository` | admin-UI-backed |
| Store product store | `StoreProductStore` | `SqlStoreProductStore` | — (cache of Shopify handles for deep links) |
| Vendor snapshot cache | `VendorSnapshotCache` | `SqlVendorSnapshotCache` | Redis, in-memory (fakes for tests) |
| Logger | `Logger` | `StdlibLogger` (rotating files + stdout) + `AxiomBatchHandler` when `AXIOM_*` env set | Datadog, OTel |

Every interface has an `InMemory*` fake under `inventory_sync/fakes.py` for
unit tests; contract tests run the same suite against the fake and the real
SQL adapter to prove drop-in equivalence.

---

## Data model

### Tenant-scoped tables (row-level tenancy)

| Table | Primary key | Purpose |
|---|---|---|
| `customers` | `(id)` | Tenant registry. Non-secret config in `config_json` blob; secrets resolved from env keyed by `id`. Tracks `last_synced_at` + `sync_interval_minutes` for scheduling. |
| `item_state` | `(customer_id, vendor_name, state_key, sku)` | "Currently in this state" set. Rows exist only while active. Drives delta-based notifications. |
| `item_state_seeded` | `(customer_id, vendor_name, state_key)` | Marker: "have we ever written to this triple?" Distinguishes first-run (dispatch informational) from observed-empty (silent). |
| `sync_runs` | `(run_id)` + `customer_id` index | Run history. Joins to `sync_run_changes` and `sync_run_errors` on `run_id`. |
| `sync_run_changes` | autoincrement | Every planned + applied `StockChange` for a run. |
| `sync_run_errors` | autoincrement | Every error from a run, with optional `sku`. |
| `store_products` | `(customer_id, sku)` | Shopify handle + title + product-id per SKU. Written every sync pass; read by the dashboard to build storefront and admin deep links. |

### Global tables (shared across tenants)

| Table | Primary key | Purpose |
|---|---|---|
| `vendor_snapshot_cache` | `(vendor_name, vendor_product_id)` | Shared vendor data. One row per product per vendor, with `fetched_at` for TTL gating. Customers sharing a vendor amortize the scan cost. |

### Dashboard-owned tables

| Table | Primary key | Purpose |
|---|---|---|
| `users` | `(id)` | Dashboard accounts. `role` ∈ {admin, customer}; customer rows carry `customer_id` for tenant scoping. Email lowercased on insert + auth. Password hashed with bcrypt. Not touched by the Python sync; owned by `automationclub-dashboard`. |

### Key invariants

- `item_state` rows exist **only** for currently-active SKUs. Absence = inactive. No `is_active` boolean.
- `vendor_snapshot_cache.fetched_at` is the freshness authority — TTL is applied in code (`vendor_scan_pass`), not in the schema, so different callers can choose different tolerances.
- `customers.last_synced_at` is written only by `mark_synced`, never clobbered by config `upsert`.
- `store_products` is a write-through cache of Shopify metadata. Rows survive between runs so deep links still work even if a SKU isn't touched on a given sync.

---

## Dashboard

Standalone Next.js app (`automationclub-dashboard`, Vercel). Read-only over
the shared Neon database, plus one write path: `workflow_dispatch` to
`AutomationClub/.github/workflows/sync.yml`.

**Auth (Auth.js v5 credentials, JWT sessions).** `auth.ts` authorizes against
the dashboard-owned `users` table; bcrypt-compared passwords. Session carries
`{ role, customerId }`. `middleware.ts` gates everything except `/login` and
`/api/auth/*`.

**Tenant isolation (`lib/auth-helpers.ts`).** `resolveCustomerScope(requested)`
hard-forces `customer`-role users to their own `customerId` regardless of the
URL. Admin can view any customer. The dashboard never trusts URL params for
authorization — every route resolves scope server-side.

**Routes.**

| Path | Purpose |
|---|---|
| `/login` | Server-action credentials form, redirects `?next=` after login. |
| `/` | Admin → customer list. Customer → `/c/{own id}`. |
| `/c/[customerId]` | Customer home: last sync time, trigger button, summary. |
| `/c/[customerId]/runs` | Run history with expandable rows (`RunRow.tsx`) — each expansion calls `/api/runs/[runId]/changes` and renders SKU + name + image + storefront + admin deep links. |
| `/c/[customerId]/state` | Current `item_state` per vendor/key. |
| `/api/sync/trigger` | `POST` → GitHub `workflow_dispatch` with the customer id as input. Enforces scope (customer can only trigger their own). |
| `/api/runs/[runId]/changes` | JSON, joins `sync_run_changes` + `store_products` so UI can build links without a second round trip. |

**GitHub token.** Fine-grained PAT with `actions:write` scoped to only the
AutomationClub repo, stored as a Sensitive Vercel env var. Never exposed to
the browser — the trigger endpoint runs server-side.

---

## Secrets & config conventions

**Config (non-secret):** in `customers.config_json` — store platform, vendor
bindings (name, URL, `store_tag`), notification routing (per-event to/via +
recipient phone/email). Versionable, snapshot-friendly, easy to edit.

**Secrets:** env-only, keyed per customer (for the sync job) or global (for
the bridge + dashboard).

### Sync job env (GitHub Actions + local `.env`)

| Convention | Example | Source |
|---|---|---|
| `SHOPIFY_TOKEN_<UPPER_CUSTOMER_ID>` | `SHOPIFY_TOKEN_MAXBABY` | GH secrets for cloud; `.env` for local |
| `SHOPIFY_ADMIN_API_TOKEN` | (legacy fallback for `maxbaby`) | same |
| `WHATSAPP_API_BASE_URL` | `https://wa-notifier-yehuda.fly.dev/api` | GH vars |
| `WHATSAPP_API_TOKEN` | `tok_…` (per project token on the bridge) | GH secrets |
| `EMAIL_API_KEY` | `re_…` (Resend) | GH secrets |
| `DATABASE_URL` | `postgresql+psycopg://…` | GH secrets |
| `AXIOM_API_TOKEN` / `AXIOM_DATASET` / `AXIOM_API_URL` | `xaat_…` / `automationclub` / (default `api.axiom.co`) | GH secrets |

### Dashboard env (Vercel)

| Var | Purpose |
|---|---|
| `AUTH_SECRET` | Auth.js JWT signing key. |
| `DATABASE_URL` | Same Neon URL (Drizzle strips `postgresql+psycopg://` prefix if present). |
| `GITHUB_TOKEN` | Fine-grained PAT for `workflow_dispatch`; `actions:write` on AutomationClub only. |
| `GITHUB_REPO` | `YehudaGoldshtein/AutomationClub`. |
| `AXIOM_API_TOKEN` / `AXIOM_DATASET` / `AXIOM_API_URL` | Same dataset as the sync job. |

### Bridge env (Fly)

| Var | Purpose |
|---|---|
| `STORE_DIR` | Persistent volume path for `whatsmeow.db` (session SQLite). |
| `AUTH_TOKENS` | `tok_abc:inventory-sync,tok_def:dating-crm` — multi-tenant caller registry. Unset = dev mode (unauthenticated). |
| `AXIOM_API_TOKEN` / `AXIOM_DATASET` / `AXIOM_API_URL` | Same dataset. |

**Shopify token resolution order** (`_resolve_shopify_token`):
1. `SHOPIFY_TOKEN_<UPPER_ID>` (hyphens → underscores)
2. `SHOPIFY_ADMIN_API_TOKEN` (legacy single-customer)

---

## Notification routing

Two orthogonal dimensions per event:

- `to`: `ops` | `client` | `both` | `none`
- `via`: `whatsapp` | `email` | `both` | `none`

Kill-switches: `ops_enabled`, `client_enabled`, `whatsapp_enabled`, `email_enabled`.

The `Notifier` fans out into up to 4 concrete channels (`ops_whatsapp`,
`ops_email`, `client_whatsapp`, `client_email`), each of which is a
`NotificationChannel` adapter. Per-event routing is driven by
`customer.notifications.routes`.

Dedup: **we only notify on state transitions**. `run_sync_pass` computes
added/removed deltas against the stored `item_state` set, plus a first-run
one-shot. Identical hourly runs with no deltas and no errors are silent.

`WhatsAppBridgeAdapter` forwards `customer_id` to the bridge in the POST
body; the bridge echoes it on its `send_ok` log. This closes the loop for
cross-service Axiom filtering (see below).

---

## Observability

All three services ship structured JSON logs to **one Axiom dataset**. The
query key across services is `customer_id` — every tenant-scoped log event
carries it, so a single APL expression catches Python sync runs, dashboard
user actions, and bridge send confirmations for a given customer.

### Emitters

| Service | Code | Batching | Flush | Service tag |
|---|---|---|---|---|
| `inventory-sync` (Python) | `inventory_sync/axiom_handler.py` (via `log.py`) | In-memory buffer | `atexit` (short-lived CLI, one POST per run) | `inventory-sync` |
| `whatsapp-notifier-bridge` (Go) | `logger.go` + `axiom_handler.go` wrapping `slog.JSONHandler` | In-memory, background goroutine | Every 3 s or 100 events; graceful drain on `Close()` during `SIGTERM` | `whatsapp-notifier-bridge` |
| `automationclub-dashboard` (Next.js) | `lib/log.ts` | None | Fire-and-forget per event (`keepalive: true`) | `automationclub-dashboard` |

All emitters fall back to stdout when `AXIOM_API_TOKEN` / `AXIOM_DATASET` are
unset — so local dev and CI work without any Axiom setup, and Axiom is
opt-in per deployment.

### Cross-service query pattern

```
['automationclub']
| where customer_id == "maxbaby"
| sort by _time desc
```

Captures:
- Python: `customer_sync_start`, `run_sync_pass` events, adapter errors
- Dashboard: `login_ok`, `trigger_sync_ok`, `run_changes_fetched`
- Bridge: `send_ok` for any WhatsApp notification the sync triggered on that
  customer's behalf

Untagged bridge events (`http_listening`, `qr_new_code_published`) are
service-health signals and intentionally not customer-scoped.

---

## Related repos

- **[AutomationClub](https://github.com/YehudaGoldshtein/AutomationClub)** — this repo (inventory sync service).
- **[automationclub-dashboard](https://github.com/YehudaGoldshtein/automationclub-dashboard)** — Next.js admin + per-customer UI (Vercel). Read-only over Neon + `workflow_dispatch` trigger. Owns only the `users` table.
- **[whatsapp-notifier-bridge](https://github.com/YehudaGoldshtein/whatsapp-notifier-bridge)** — minimal Go microservice wrapping whatsmeow. `POST /api/send` with Bearer auth, one deployment serves many client projects (inventory-sync, dating-crm). Deployed to Fly (`wa-notifier-yehuda.fly.dev`).

---

## When to break these rules

**Never in v1.** If a rule feels wrong, the interface is wrong — fix the interface, don't route around it.
