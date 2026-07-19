# AutomationClub — Inventory Sync

Multi-tenant inventory service for Shopify stores. Two capabilities:

1. **Stock sync** — keep each store's stock in line with its vendors' availability (cache-backed scans shared across tenants).
2. **Product onboarding** — create net-new store products from a vendor as **drafts**, held for human approval in the dashboard, then activated.

Suppliers today:

| Supplier | Source | Onboarding | Stock sync |
|---|---|---|---|
| **Laura** | supplier Excel (`.xlsx`) + web scrape | `ingest` (Excel → draft products) | hourly scrape (binary in/out) |
| **Segal** | WooCommerce Store API + product-page tabs | `segal-ingest` (API → draft products) | `segal-sync` (exact counts, every 3h) |

Principles, deployment topology, data model, lifecycle, and interfaces: **[ARCHITECTURE.md](./ARCHITECTURE.md)** — start there to understand the system.

## Related services

| Repo | Live URL | Purpose |
|---|---|---|
| **[automationclub-dashboard](https://github.com/YehudaGoldshtein/automationclub-dashboard)** | [automationclub-dashboard.vercel.app](https://automationclub-dashboard.vercel.app) | Admin + per-customer web dashboard (Next.js on Vercel). Views of runs/state, product upload, Pending approve/ignore, and a trigger-sync button. **Never holds a Shopify token** — writes app state to Neon only. |
| **[whatsapp-notifier-bridge](https://github.com/YehudaGoldshtein/whatsapp-notifier-bridge)** | `wa-notifier-yehuda.fly.dev` | Minimal Go microservice wrapping whatsmeow. `POST /api/send` with Bearer auth. One deployment, many caller tokens. |

## Running locally

```bash
cp .env.example .env        # then fill in real values
pip install -e ".[postgres]"

python -m inventory_sync sync --dry-run          # stock sync (preview)
python -m inventory_sync sync                     # stock sync (writes Shopify)
python -m inventory_sync ingest --blob-url <url> --customer-id maxbaby --dry-run   # Laura onboarding
python -m inventory_sync segal-ingest --customer-id maxbaby --dry-run              # Segal onboarding
python -m inventory_sync segal-sync                # Segal stock sync
python -m inventory_sync reconcile --customer-id maxbaby   # activate approved / delete ignored drafts
```

## Running in production

GitHub Actions workflows (see [DEPLOY.md](./DEPLOY.md) for one-time setup):

| Workflow | Trigger | Does |
|---|---|---|
| `sync.yml` | hourly cron | Laura stock sync + post-sync audit + folds in reconcile |
| `segal-sync.yml` | every 3h cron / dispatch | Segal stock sync (quantity + in/out) |
| `inventory-ingest.yml` | dispatch (dashboard upload) | Laura Excel → draft products |
| `segal-ingest.yml` | dispatch | Segal Store API → draft products |
| `reconcile.yml` | dispatch ("activate now") | activate approved drafts / delete ignored |
| `tests.yml` | push / PR | test suite |
