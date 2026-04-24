# AutomationClub — Inventory Sync

Multi-tenant inventory sync service. Keeps each customer's Shopify store in sync with the stock state of their vendors, with cache-backed scans shared across tenants.

Architecture, principles, data model, and deployment topology: **[ARCHITECTURE.md](./ARCHITECTURE.md)**.

## Related services

| Repo | Live URL | Purpose |
|---|---|---|
| **[automationclub-dashboard](https://github.com/YehudaGoldshtein/automationclub-dashboard)** | [automationclub-dashboard.vercel.app](https://automationclub-dashboard.vercel.app) | Admin + per-customer web dashboard (Next.js on Vercel). Read-only views of runs/state + a trigger-sync button. |
| **[whatsapp-notifier-bridge](https://github.com/YehudaGoldshtein/whatsapp-notifier-bridge)** | `wa-notifier-yehuda.fly.dev` | Minimal Go microservice wrapping whatsmeow. `POST /api/send` with Bearer auth. One deployment, many caller tokens — shared by every project that needs WhatsApp notifications. |

## Running locally

```bash
# One-off: set up .env from .env.example and your own secrets
cp .env.example .env  # then fill in real values

pip install -e ".[postgres]"
python -m inventory_sync sync --dry-run   # preview
python -m inventory_sync sync             # real writes to Shopify
```

## Running in production

Hourly GitHub Actions workflow at `.github/workflows/sync.yml` — see [DEPLOY.md](./DEPLOY.md) for the one-time setup.
