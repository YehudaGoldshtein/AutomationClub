# Benchmarks

Runnable via `PYTHONPATH=. python bench/<script>.py`. Not in CI — they're slow + shape-of-hardware-dependent. Run locally when you want concrete numbers for a design decision.

Last captured: **2026-04-22** on Windows 11, Python 3.12.2, simulated 50ms/request HTTP latency where applicable.

---

## 1. Sitemap parse scales linearly and cheaply

`bench/bench_sitemap_parse.py` — synthetic sitemap XML, pure parse (no HTTP).

| N products | XML size | parse time | SKUs found |
|---:|---:|---:|---:|
| 1,000 | 58 KB | 0.5 ms | 1,000 |
| 5,000 | 288 KB | 2.5 ms | 5,000 |
| 10,000 | 576 KB | 5.5 ms | 10,000 |
| 50,000 | 2,881 KB | 30.6 ms | 50,000 |
| 100,000 | 5,762 KB | 58.9 ms | 100,000 |

Linear, **~0.6 µs per URL**. Laura today is ~1,000 products / 743 KB → 1–2 ms parse. Even a 100× larger catalog costs a single sync-run request budget. No concern.

---

## 2. Request-reduction from sitemap pre-filter

`bench/bench_request_reduction.py` — MockTransport with 50ms/request latency, max_workers=4.

| Scenario | Vendor SKUs | Sitemap coverage | Old path | New path | Speedup | Requests saved |
|---|---:|---:|---:|---:|---:|---:|
| Current Max Baby | 817 | 79% | 10.6 s | 8.5 s | 1.2× | 167 |
| Larger single store | 5,000 | 80% | 64.5 s | 51.9 s | 1.2× | 999 |
| Giant store | 20,000 | 80% | 258.1 s | 207.0 s | 1.2× | 3,999 |

**Takeaways:**
- Savings scale linearly with the "dead fraction" — the 21% of Max Baby SKUs that Laura removed but still live in Shopify's catalog.
- For Max Baby's current ratio (168/817 dead), we save ~20% of wall time per sync run. Concretely: ~2 minutes off a ~10-minute run at real 3s/request latency.
- Larger stores → more requests saved in absolute terms; same percentage.
- Speedup *ratio* would grow if dead fraction grows (e.g., if 50% of catalog gets cleaned up over time).

---

## 3. State store scales comfortably to 100k rows

`bench/bench_item_state_store.py` — `set_active` (full replace) and `get_active_skus` against the `item_state` table.

### SQLite in-memory (tests / dev baseline)

| N SKUs | `set_active` | `get_active_skus` |
|---:|---:|---:|
| 100 | 3.4 ms | 0.4 ms |
| 1,000 | 5.6 ms | 1.1 ms |
| 10,000 | 42.2 ms | 6.3 ms |
| 100,000 | 562.4 ms | 84.4 ms |

### SQLite file (closer to prod-lite)

| N SKUs | `set_active` | `get_active_skus` |
|---:|---:|---:|
| 100 | 6.2 ms | 1.0 ms |
| 1,000 | 9.5 ms | 1.4 ms |
| 10,000 | 48.7 ms | 18.4 ms |
| 100,000 | 621.5 ms | 88.0 ms |

**Takeaways:**
- Both operations are O(N) as expected (full-replace semantics).
- At our expected scale (~hundreds to low-thousands of active SKUs per `(vendor, state_key)`), all ops are ≤ 10 ms. Negligible.
- Even 100k rows round-trip in well under a second per op — comfortable headroom for eventual multi-vendor multi-customer.
- **When this becomes a concern:** hundreds of thousands of rows per key AND frequent writes. Switch to Postgres + proper incremental upsert/delete (delta rather than full-replace). Easy migration path; not an issue today.

---

## Not benchmarked yet

- **Multi-customer shared-sitemap cache** (ROADMAP #2) — relevant only at 2+ customers. Will become worth measuring when we have customer #2 lined up.
- **Real Postgres performance** — sqlite file is a reasonable proxy, but true PG numbers (especially under connection-pool pressure) should be taken on the deployment target.
