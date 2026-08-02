# Snir — price changes blocked by the >60% guard

Source: orchestrator run [30754220989](https://github.com/YehudaGoldshtein/AutomationClub/actions/runs/30754220989), 2026-08-02.
Snir price-sync summary: `checked=226 · unchanged=68 · updated=152 · blocked=6`.

These 6 were **not written** — each exceeded the >60% change guard. They are logged in
Axiom as `price_change_blocked` (fields `was`/`target`/`pct`) and will keep being
blocked every run until reviewed. All are Snir-vendor, ACTIVE, big-ticket furniture,
priced at roughly **half** the current supplier target (store `compare_at` is also low,
so the whole listing was built at ~½ the supplier price — likely stale store pricing).

| # | SKU | Product | Store now | compare_at | Supplier target | Δ |
|---|-----|---------|-----------|------------|-----------------|---|
| 1 | be004       | מיטה לתינוק כלנית לבן (Kalanit crib, white)              | ₪940.47  | ₪1045 | ₪1990 | +112% |
| 2 | be005       | מיטה לתינוק כלנית טבעי (Kalanit crib, natural)           | ₪940.29  | ₪1045 | ₪1990 | +112% |
| 3 | ro006-1-1-1 | חדר שינה אירוס לבן (Iris bedroom set)                    | ₪1520.77 | ₪1859 | ₪3990 | +162% |
| 4 | ro008-1     | חדר שינה כרמל לבן, טריקה שקטה (Carmel set)               | ₪2420.84 | ₪2959 | ₪4380 | +81%  |
| 5 | ro0021      | חדר שינה נגב לבן, טריקה שקטה (Negev set)                 | ₪2420.96 | ₪2959 | ₪4380 | +81%  |
| 6 | ro006-1-1   | חדר שינה תמרה לבן טבעי, טריקה שקטה (Tamara set)          | ₪2420.75 | ₪2959 | ₪4280 | +77%  |

## Links (admin / storefront)

1. **be004** — https://www.maxbaby.co.il/admin/products/9158349684990
   https://www.maxbaby.co.il/en/products/%D7%9E%D7%99%D7%98%D7%94-%D7%9C%D7%AA%D7%99%D7%A0%D7%95%D7%A7-%D7%9B%D7%9C%D7%A0%D7%99%D7%AA-%D7%9C%D7%91%D7%9F
2. **be005** — https://www.maxbaby.co.il/admin/products/9158349160702
   https://www.maxbaby.co.il/en/products/%D7%9E%D7%99%D7%98%D7%94-%D7%9C%D7%AA%D7%99%D7%A0%D7%95%D7%A7-%D7%9B%D7%9C%D7%A0%D7%99%D7%AA-%D7%98%D7%91%D7%A2%D7%99
3. **ro006-1-1-1** — https://www.maxbaby.co.il/admin/products/9158363840766
   https://www.maxbaby.co.il/en/products/%D7%97%D7%93%D7%A8-%D7%A9%D7%99%D7%A0%D7%94-%D7%9C%D7%AA%D7%99%D7%A0%D7%95%D7%A7-%D7%95%D7%9E%D7%A2%D7%91%D7%A8-%D7%90%D7%99%D7%A8%D7%95%D7%A1-%D7%9C%D7%91%D7%9F
4. **ro008-1** — https://www.maxbaby.co.il/admin/products/9158355190014
   https://www.maxbaby.co.il/en/products/%D7%97%D7%93%D7%A8-%D7%A9%D7%99%D7%A0%D7%94-%D7%9C%D7%AA%D7%99%D7%A0%D7%95%D7%A7-%D7%95%D7%9E%D7%A2%D7%91%D7%A8-%D7%9B%D7%A8%D7%9E%D7%9C-%D7%9C%D7%91%D7%9F-%D7%9E%D7%95%D7%98%D7%95%D7%AA-%D7%A9%D7%A7%D7%95%D7%A4%D7%99%D7%9D-%D7%98%D7%A8%D7%99%D7%A7%D7%94-%D7%A9%D7%A7%D7%98%D7%94
5. **ro0021** — https://www.maxbaby.co.il/admin/products/9158355517694
   https://www.maxbaby.co.il/en/products/%D7%97%D7%93%D7%A8-%D7%A9%D7%99%D7%A0%D7%94-%D7%9C%D7%AA%D7%99%D7%A0%D7%95%D7%A7-%D7%95%D7%9E%D7%A2%D7%91%D7%A8-%D7%A0%D7%92%D7%91-%D7%9C%D7%91%D7%9F-%D7%9E%D7%95%D7%98%D7%95%D7%AA-%D7%A9%D7%A7%D7%95%D7%A4%D7%99%D7%9D-%D7%98%D7%A8%D7%99%D7%A7%D7%94-%D7%A9%D7%A7%D7%98%D7%94
6. **ro006-1-1** — https://www.maxbaby.co.il/admin/products/9158363021566
   https://www.maxbaby.co.il/en/products/%D7%97%D7%93%D7%A8-%D7%A9%D7%99%D7%A0%D7%94-%D7%9C%D7%AA%D7%99%D7%A0%D7%95%D7%A7-%D7%95%D7%9E%D7%A2%D7%91%D7%A8-%D7%AA%D7%9E%D7%A8%D7%94-%D7%9C%D7%91%D7%9F-%D7%98%D7%91%D7%A2%D7%99-%D7%98%D7%A8%D7%99%D7%A7%D7%94-%D7%A9%D7%A7%D7%98%D7%94

## Next options
1. Spot-check one SKU against Snir's live site to confirm the target is the real retail price.
2. Apply all 6 manually (one-off write, bypassing the guard for just these SKUs).
3. Leave as-is (stay at current price; guard keeps blocking + logging each run).
