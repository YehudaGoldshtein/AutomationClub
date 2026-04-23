# Deploying the hourly sync

Target: GitHub Actions scheduled workflow (`.github/workflows/sync.yml`). Free for public repos, unlimited minutes.

## Architecture in production

```
GitHub Actions (hourly cron)
    │
    ├─ reads Max Baby via Shopify Admin API (HTTPS)
    ├─ reads Laura catalog + product pages (HTTPS)
    ├─ writes stock to Max Baby (HTTPS)
    ├─ persists state + sync history → Neon Postgres (HTTPS)
    └─ notifies via Resend email (HTTPS)
```

**WhatsApp is disabled in the cloud** — the Go bridge lives on your local machine and isn't reachable from GitHub's runners. Local CLI runs can still use WhatsApp via `python -m inventory_sync sync` on your machine when the bridge is up.

## One-time setup

### 1. Postgres (Neon free tier)

1. Sign up at https://neon.tech (free forever, 500 MB storage, serverless auto-sleep)
2. Create a project (any region — the sync runs from US-East GH runners, so US East or Europe West is fine)
3. From the dashboard, copy the **connection string** — looks like:
   `postgresql://user:password@ep-xxxx.neon.tech/dbname?sslmode=require`
4. Adapt for the `psycopg` driver SQLAlchemy expects by prefixing: `postgresql+psycopg://user:...` (replace `postgresql://` with `postgresql+psycopg://`)

### 2. GitHub repository secrets

Repo → Settings → Secrets and variables → Actions → **Secrets** tab → New repository secret:

| Name | Value |
|---|---|
| `SHOPIFY_ADMIN_API_TOKEN` | `shpat_...` from your `.env` |
| `EMAIL_API_KEY` | `re_...` from your `.env` |
| `DATABASE_URL` | `postgresql+psycopg://...` from Neon (step 1) |

### 3. GitHub repository variables

Same page → **Variables** tab → New repository variable:

| Name | Value |
|---|---|
| `SHOPIFY_STORE_URL` | `https://www.maxbaby.co.il/` |
| `SHOPIFY_MYSHOPIFY_DOMAIN` | `bguhwj-wj.myshopify.com` |
| `SHOPIFY_API_VERSION` | `2024-10` |
| `VENDOR_NAME` | `laura-design` |
| `VENDOR_URL` | `https://www.laura-design.net/` |
| `VENDOR_STORE_TAG` | `לורה סוויסרה \| laura swisra` |
| `EMAIL_FROM` | `noreply@maxbaby.co.il` |
| `EMAIL_OPS_ADDRESS` | `yehudashtein@gmail.com` |
| `EMAIL_CLIENT_ADDRESS` | `Elishosh687@gmail.com` |

### 4. First run

1. Push the workflow file to `main`
2. Repo → Actions tab → **hourly-sync** workflow → **Run workflow** button
3. Check **Run with `dry_run: true`** for the first manual test
4. Wait ~5 min, verify:
   - Job completes successfully
   - Neon DB has schema tables and one `sync_runs` row
   - Both email recipients got the informational initial-reconciliation message
5. After confirming dry-run output looks right, re-run **without** dry-run — actual stock writes land in Max Baby
6. Hourly cron takes over from next top-of-hour onward

## Operating

### Running manually

- From GitHub: Actions tab → Run workflow → optionally check dry_run
- From your own machine: same as before — `python -m inventory_sync sync`. Your local `.env` keeps WhatsApp enabled; your local sqlite DB is independent of the cloud Postgres

### Monitoring

- Workflow run history: Actions tab
- Sync-run history: query Neon `sync_runs` table (admin UI later)
- Immediate alerts: email to `EMAIL_OPS_ADDRESS` on errors (`NOTIFY_SYNC_ERROR_TO=ops`)

### Silencing Eli temporarily

- Flip `EMAIL_CLIENT_ADDRESS` variable to empty in GH settings → client email channel becomes unconfigured and is skipped

### Pausing the sync

- Actions → hourly-sync → `...` menu → Disable workflow

## When to graduate off free tier

- Sync frequency needs to exceed hourly: upgrade to a VPS or Vercel Pro
- Catalog grows past ~5000 SKUs: sync wall time may exceed GH Actions' 6h job limit (extreme)
- WhatsApp notifications needed in production: host the Go bridge on a VPS with a public HTTPS endpoint; switch `NOTIFY_WHATSAPP_ENABLED=true` in GH vars + set `WHATSAPP_API_BASE_URL` secret
