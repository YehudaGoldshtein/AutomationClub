# MVP Scope — Inventory Sync v0.1

**Customer:** Max Baby (https://www.maxbaby.co.il/) — Shopify
**Vendor:** Laura Design (https://www.laura-design.net/) — no public API, scraper-first with parallel REST-sniffing
**Stack:** Python 3.11+
**Hosting:** probably Vercel (TBD — flagged: Vercel's ephemeral FS means we'll need a remote log-sink adapter before going live there)
**Source of truth:** this doc for MVP scope; `PRD-inventory-sync-v1.md` for product intent; `ARCHITECTURE.md` for non-negotiable design rules.

---

## Decisions

| Area | v0.1 choice | Future |
|---|---|---|
| Store platform | **Shopify** Admin API | Woo / Magento / etc. via `StorePlatform` interface |
| Supplier source | **Scraper** of laura-design.net, parallel effort to sniff the vendor's internal REST and swap to a REST adapter | Other vendors via new `SupplierSource` adapters |
| Sync trigger | **Hourly** cron | Webhook / real-time / manual |
| SKU ↔ vendor mapping | **Use the store's existing mapping** (store already carries vendor product ID per SKU) | CSV import + UI for customers without it |
| Stock-out action | **Both** — set stock to 0 **and** unpublish the product | Ad-pause, auto-reorder |
| Back-in-stock action | **Stubbed** — interface defined, behavior TBD | Auto-republish, per-product override |
| Notifications | **Interface only** — `NotificationChannel` defined; no concrete adapter wired in v0.1 | Email, WhatsApp, SMS, Slack |
| Conflict resolution | **Sync wins** — overwrites any manual store edits | Detect + preserve manual edits |
| Multi-vendor per SKU | Not supported (single vendor only) | Primary+fallback, split |
| Logging | **Structured logger to rotating file + pretty stdout**, every component uses it | Pluggable `LogSink` (external log services) |

## Out of Scope for v0.1

Everything the PRD flags out-of-scope, plus: multi-vendor per SKU, low-stock alerts, digest emails, config UI, dashboard, non-Shopify platforms, wired-up notification sending (interfaces only).

## Architecture → Concrete v0.1 Adapters

Per `ARCHITECTURE.md`, every seam is behind an interface. v0.1 ships with:

| Interface | v0.1 implementation |
|---|---|
| `Logger` | `StdlibLogger` (rotating file + pretty stdout) |
| `StorePlatform` | `ShopifyAdapter` |
| `SupplierSource` | `LauraDesignScraperAdapter` (→ `LauraDesignRestAdapter` when sniffed) |
| `NotificationChannel` | **interface only** — no concrete adapter yet |
| `StockAction` | `UpdateCountAction`, `UnpublishAction`, `NotifyAction`; `RepublishAction` **stubbed** |
| `SyncTrigger` | `HourlyCronTrigger` |
| `ConfigStore` | `.env` via `python-dotenv` |

No Shopify- or vendor-specific terms may exist outside their adapter. The sync engine and everything above it speak only the domain model. Every component receives and uses a `Logger`.

## Still Open (non-blocking)

- **Vendor scraping auth:** do we need a login to laura-design.net for stock data?
- **Hosting:** Vercel vs a host with persistent FS. Affects whether we need a remote log-sink adapter before v0.1 goes live.
- **Notification adapters:** wiring WhatsApp (Yehuda's existing API) + an email sender — postponed to v0.2.

## Build Order

Each step produces working, logged, independently runnable code.

1. **Logger** (step 0 — foundation for everything). `Logger` interface + `StdlibLogger` backing. Rotating file in `logs/`, pretty stdout for dev.
2. **Config loader.** `.env` → typed config object. Fails loudly on missing required keys.
3. **Domain model.** `SKU`, `StockLevel`, `Product`, `StockChange`, etc. No I/O.
4. **Interfaces.** `StorePlatform`, `SupplierSource`, `NotificationChannel`, `StockAction`, `SyncTrigger`. In-memory fakes for every one.
5. **`ShopifyAdapter`.** Read catalog, write stock, toggle publish status. Smoke-tested against the real store with a tiny subset.
6. **`LauraDesignScraperAdapter`.** Get stock per SKU. In parallel: sniff the site's network traffic, look for the REST endpoints, build `LauraDesignRestAdapter` when viable.
7. **Sync engine.** Pulls from `SupplierSource`, diffs against `StorePlatform`, emits `StockAction`s. Depends only on interfaces.
8. **`HourlyCronTrigger`** + entrypoint script.
9. Smoke-test on a small SKU subset against the real store. Flip full catalog.
