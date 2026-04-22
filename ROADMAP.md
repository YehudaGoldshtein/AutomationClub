# Roadmap — post v0.1

Concrete engineering items deferred past the MVP. Each has enough design sketch
that future me (or a new collaborator) doesn't have to re-think it from scratch.

---

## 1. Persistent log sink

**Why:** rotating `logs/inventory_sync.log` works on a local machine but evaporates on Vercel (ephemeral FS). Once we deploy, we lose every log line past a single function invocation.

**Design sketch:**
- New interface `LogSink` (matches the pluggability principle — already listed in `ARCHITECTURE.md` seams table).
- v0.1 file+stdout implementation becomes the local/dev default.
- Production adapters to pick from:
  - **Axiom** or **Betterstack/Logtail** — hosted log service, JSON ingestion, free tiers; push via HTTPS, no infra to manage.
  - **Vercel log drains → external service** — zero code change, just a project setting. Works for stdout only.
  - **Postgres** — we'll already have a DB; a `log_lines` table works but is heavier than a purpose-built log service.
- Structured JSON already written — any destination that ingests JSON drops right in.

**Status:** not started. Decide destination when we deploy to Vercel.

---

## 2. Shared vendor snapshot cache

**Why:** when we have multiple customers using the same vendor (many Israeli baby stores sell Laura), every customer's hourly sync today would independently re-fetch the same Laura pages. At 817 variants per customer × N customers × hourly, that's a lot of wasted HTTP and unnecessary load on the vendor. Supplier fetches are also our slowest step — anything we can dedupe is a big win.

**Design sketch:**
- New interface `SnapshotCache` with `get(vendor_id, max_age) -> VendorProductSnapshot | None` and `set(vendor_id, snapshot)`.
- Cache key: `(vendor_source_name, vendor_product_id)` — e.g. `("laura-design", "2800-253")`.
- Per-customer **freshness threshold** (env/DB config): e.g., customer A accepts data up to 30 min old, customer B demands ≤5 min old.
- Adapter: `LauraDesignScraperAdapter` (or a wrapper `CachedSupplierSource`) checks cache first, fetches only on miss or stale entry, writes back after fetch.
- Storage: start with Postgres (we'll have one). Redis if we outgrow that.
- In-memory fake for tests, per pluggability principle.

**Status:** not started. Architecturally free (interface fits), needed when customer count > 1 or when fetch cost becomes painful.

---

## 3. Notification deduplication

**Why:** Eli asked for hourly sync, but he does NOT want a 21-item unarchive list every hour if the list hasn't changed. Repetitive notifications are noise that trains the recipient to ignore them.

**Design sketch:**
- Suppress a notification when its **content** is identical to the last one sent for the same `(event_type, recipient)` pair.
- Compute a stable hash over the structured content before formatting (e.g., sorted list of SKUs for archive_audit; not the subject/date line, which changes trivially).
- Persist `last_sent_hash` per `(event_type, recipient)` in DB.
- Before `notifier.dispatch(...)`: compute hash → compare with last → send + update on mismatch, suppress on match.
- Optional per-event override: `NOTIFY_<EVENT>_FORCE_INTERVAL=24h` to re-send even when unchanged after N hours (so the recipient sees a signal "the system is alive").
- Log every suppression with reason so it's visible why a notification didn't go out.

**Status:** not started. Needs the persistence layer. Day-one critical once sync goes live — without this, Eli gets spammed.

---

## Notes

- Items 1 and 3 both depend on **persistent storage** (Postgres). So naturally group them in the same phase as the admin UI.
- Item 2 is optional for single-tenant but becomes meaningful the moment customer #2 onboards.
