# MVP Scope — Inventory Sync v0.1

**Customer:** Max Baby (https://www.maxbaby.co.il/) — Shopify
**Vendor:** Laura Design (https://www.laura-design.net/) — Magento 2 storefront, no public API; we parse embedded JSON-LD
**Stack:** Python 3.11+
**Hosting:** probably Vercel (TBD — flagged: Vercel's ephemeral FS means we'll need a remote log-sink adapter before going live there)
**Source of truth:** this doc for MVP scope; `PRD-inventory-sync-v1.md` for product intent; `ARCHITECTURE.md` for non-negotiable design rules.

---

## Decisions

| Area | v0.1 choice | Future |
|---|---|---|
| Store platform | **Shopify** Admin API (against `bguhwj-wj.myshopify.com`) | Woo / Magento / etc. via `StorePlatform` interface |
| Supplier source | **Scraper** of laura-design.net, parsing JSON-LD on each `/<sku>` page. Binary availability only. | Other vendors via new `SupplierSource` adapters |
| Sync granularity | **Variant-level** — each Shopify variant has its own Laura SKU in `variant.sku`. The domain's `Product` type represents a syncable unit == one variant. | Product-level for single-variant catalogs (compatible) |
| SKU ↔ vendor mapping | **Direct**: Shopify `variant.sku` IS Laura's SKU. No metafield / tag lookup required. Vendor-scoped by `product.vendor == 'לורה סוויסרה \| laura swisra'` (298 products in Max Baby). | Mapping UI / CSV import for customers without direct equivalence |
| Sync trigger | **Hourly** cron | Webhook / real-time / manual |
| Stock-out action | **Set stock to 0** (Shopify auto-shows "Out of stock" on the variant; no product-level change). UNPUBLISH is **NOT** auto-emitted — owner-triggered only (see below). | Auto-unpublish when all variants OOS, ad-pause, auto-reorder |
| Back-in-stock action | Vendor binary in-stock + store at 0 → **set stock to 1**. Vendor binary in-stock + store already > 0 → no change (preserve Max Baby's count). | Exact-count sync via non-binary vendor adapters |
| UNPUBLISH / REPUBLISH | **Interface kept, no auto-emission.** The `ChangeKind.UNPUBLISH` / `REPUBLISH` values and `StorePlatform.unpublish()`/`republish()` methods stay in the codebase for a future manual entrypoint (CLI subcommand) triggered by the store owner. | Policy-driven emission when all variants of a product are OOS |
| Notifications | **Interface only** — `NotificationChannel` defined; no concrete adapter wired in v0.1 | Email, WhatsApp, SMS, Slack |
| Conflict resolution | **Sync wins for OOS** (vendor OOS overrides any manual count). **Sync preserves for in-stock** (vendor binary in-stock does NOT overwrite Max Baby's manual count). | Detect + flag manual edits for review |
| Multi-vendor per SKU | Not supported (single vendor only) | Primary+fallback, split |
| Logging | **Structured logger to rotating file + pretty stdout**, every component uses it | Pluggable `LogSink` (external log services) |

## Out of Scope for v0.1

Everything the PRD flags out-of-scope, plus: multi-vendor per SKU, low-stock alerts, digest emails, config UI, dashboard, non-Shopify platforms, wired-up notification sending (interfaces only), automated UNPUBLISH (manual only).

## Vendor Stock Signal

`SupplierSource.fetch_snapshots()` returns `VendorProductSnapshot` objects with two orthogonal fields describing stock:

- **`is_available: bool`** — every vendor tells us this, binary in/out-of-stock.
- **`stock_count: int | None`** — exact count when the vendor provides one; `None` when only binary is known.

Constructor enforces consistency: zero count can't be "available", positive count can't be "unavailable", negative counts rejected. `DefaultStockPolicy` reads both and adapts — exact-count sync when available, binary logic (preserve / bump-to-1 / zero-out) when not.

This keeps the contract honest for any future vendor — whether they give us a count (REST API, CSV) or only a flag (JSON-LD scraper, legacy portal).

## Architecture → Concrete v0.1 Adapters

Per `ARCHITECTURE.md`, every seam is behind an interface. v0.1 ships with:

| Interface | v0.1 implementation |
|---|---|
| `Logger` | `StdlibLogger` (rotating file + pretty stdout) |
| `StorePlatform` | **next: `ShopifyAdapter`** (variant-level; `update_stock` via InventoryLevel API; `unpublish`/`republish` via `product.status`) |
| `SupplierSource` | `LauraDesignScraperAdapter` — returns snapshots with `is_available`, `stock_count=None` |
| `NotificationChannel` | **interface only** — no concrete adapter yet |
| `StockPolicy` | `DefaultStockPolicy` — handles both binary and exact-count signals |
| `SyncTrigger` | external cron (Vercel Cron / systemd / etc.) — no in-process scheduler for v0.1 |
| `ConfigStore` | `.env` via `python-dotenv` |

No Shopify- or vendor-specific terms may exist outside their adapter. The sync engine and everything above it speak only the domain model. Every component receives and uses a `Logger`.

## Still Open (non-blocking)

- **Vercel vs host with persistent FS.** Affects whether we need a remote log-sink adapter before v0.1 goes live.
- **Notification adapters** wiring (WhatsApp, email) — postponed to v0.2.
- **Manual unpublish entrypoint.** CLI subcommand or small web UI — scoped separately once the sync engine is running in production.

## Build Order

Each step produces working, logged, independently runnable code.

1. ✅ **Logger** (step 0 — foundation). `Logger` interface + `StdlibLogger`.
2. ✅ **Config loader.** `.env` → typed `Config` object, fails loudly on missing required keys.
3. ✅ **Domain model.** `SKU`, `StockLevel`, `Product`, `StockChange`, `SyncRun`, `VendorProductSnapshot`.
4. ✅ **Interfaces + in-memory fakes** with contract tests.
5. ✅ **Sync engine** wired on the interfaces.
6. ✅ **`LauraDesignScraperAdapter`.** Live-tested against laura-design.net.
7. **Next: `ShopifyAdapter`.** Read variants + vendor mapping (`product.vendor`), write stock via InventoryLevel API, archive/unarchive via `product.status`.
8. CLI entrypoint: loads config, builds real engine (Shopify + Laura + DefaultStockPolicy), runs a sync. Smoke-test on a small SKU subset against the real store, then full catalog.
