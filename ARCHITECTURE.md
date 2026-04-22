# Architecture Principles — Inventory Sync

> # ⚡ CORE PRINCIPLE: EVERYTHING PLUGGABLE ⚡
>
> **Every external integration point lives behind an interface. No exceptions.**
>
> We start with Shopify and one vendor. We are building a **platform**, not a one-off.
> Every seam that touches the outside world — store platform, supplier source,
> notification channel, stock action, scheduler, config store, log sink — is
> implemented behind an abstraction **from day one**, so that adding the next
> platform, vendor, channel, or sink is a new adapter, not a rewrite.
>
> **Design for the second integration before shipping the first.**
> This is non-negotiable and applies to every PR.

> # 📝 CORE PRINCIPLE: LOG EVERYTHING, FROM DAY ONE 📝
>
> **The logger is the first thing built, and every subsequent component uses it.**
>
> All code — adapters, engine, triggers, actions, notifications — logs to a
> structured log that lands in a log directory (rotating files) and stdout
> (human-readable) during development, so debugging is **rapid and high-quality
> both in dev and in production**. If a component fails silently, the bug is in
> the component — not in the logging strategy. There is no "we'll add logging
> later." Logging is infrastructure, and it ships in v0.1 before anything else.
>
> The logger itself follows the pluggability principle: callers depend on a
> `Logger` interface, never on a concrete implementation.

---

## The Seams

Each row below is an interface in the domain layer. Concrete implementations live in an `adapters/` (or equivalent) directory and are selected by configuration, never by code branches in callers.

| Seam | Pattern | v1 implementation | Future implementations (non-exhaustive) |
|---|---|---|---|
| Store platform | Bridge | Shopify | WooCommerce, Magento, BigCommerce, custom |
| Supplier source | Adapter / Strategy | Vendor scraper (→ REST when sniffed) | Other vendor APIs, CSV feeds, email-parsed feeds |
| Notification channel | Strategy | Interface only in v0.1 | Email, WhatsApp, SMS, Slack, Telegram, webhooks |
| Stock-change action | Command / Strategy | update-count, unpublish, notify | pause-ads, auto-reorder |
| Multi-supplier policy | Strategy | single-supplier | primary+fallback, split-inventory |
| Sync trigger | Strategy | interval (hourly cron) | webhook, manual, real-time push |
| Config store | Repository | `.env` via `python-dotenv` | sqlite, DB, UI-backed |
| Log sink | Adapter | rotating file + pretty stdout | external log service (Logtail, Datadog, S3) |
| Audit sink | Adapter | TBD | stdout, file, DB, external |

## Rules of the Road

1. **No store-specific logic outside the store adapter.** Shopify terminology (product_id, variant_id, metafield, etc.) never leaks into the sync engine or any other layer.
2. **No vendor-specific logic outside the vendor adapter.** Same rule.
3. **New integration = new adapter file implementing the interface.** Not a conditional branch inside an existing adapter.
4. **Interfaces are defined by our domain, not by vendors.** We do not adopt Shopify's data model (or any vendor's) as our canonical model. We translate in the adapter.
5. **Configuration drives selection.** Which adapter runs is a config choice, not a code branch.
6. **Every adapter is independently testable.** With a fake/in-memory implementation of the same interface available for tests of everything above it.
7. **Every component takes a `Logger` and uses it.** No `print()`. No silent failures. If an operation can fail or an event matters, log it.

## When Is It OK to Break These Rules?

**Never in v1.** If you feel a rule should be broken, the interface is wrong — fix the interface, don't route around it.

---

## Why This Matters

Inventory sync is only valuable if it generalizes. Stores come in many flavors, vendors come in many flavors, and the product's moat is how fast we can onboard a new customer — which is a function of how cleanly the seams are drawn. A sync engine that hardcodes Shopify is a throwaway script. A sync engine built on the seams above is a product. Add aggressive logging on top and we can debug any customer's issue from the logs alone, instead of needing to reproduce it.
