# Snir — new items skipped as "uncategorized"

Source: orchestrator run [30754220989](https://github.com/YehudaGoldshtein/AutomationClub/actions/runs/30754220989), 2026-08-02.
Snir onboarding: `items_checked=226 · created=0 · skipped_oos=44 · skipped_uncategorized=21`.

These **21** are new Snir items (not yet in the store) that the pass skipped with
`unified_pass_skip_uncategorized`. Fetched live from snir-bebe.com and matched by SKU
(all 21 matched, all in stock).

## Why they're skipped — not junk, they're spare parts / accessories

Every one is a real, in-stock product, but they all sit **only** in categories that
aren't in Snir's importable route map:

- **129 — חלקי חילוף** (spare parts: rails, legs, ladders, handles, wheel kits)
- **420 — אביזרים לחדר תינוק** (nursery accessories: rugs, baskets)
- **138 — MIX AND MATCH** (hardware kits)

`route(category_ids)` returns None for all of them → `is_importable=False` → correctly
skipped. We onboard furniture (cribs, bedroom sets, dressers), not replacement legs and
handles. This is the category filter working as intended, not a miss.

**If you want any of these onboarded**, the fix is to add the relevant category id
(129 / 420 / 138) to the route map in `inventory_sync/snir_mapping.py` — but note the
"real product" ones below are genuine catalog items with placeholder-looking SKUs
(999, 998, reg-01, digit-mash), which is Snir's own SKU scheme for parts.

## The 21 (SKU · product · categories · link)

| SKU | Product (he) | Categories | Link |
|-----|--------------|-----------|------|
| 345665432-1 | סלסלת קאמל-חום אבן (basket) | 138, 420, 129 | https://www.snir-bebe.com/product/%d7%a1%d7%9c%d7%a1%d7%9c%d7%aa-%d7%a7%d7%90%d7%9e%d7%9c/ |
| 345665432 | סלסלת במבוק (bamboo basket) | 138, 129, 420 | https://www.snir-bebe.com/product/st-przvl-mith-em-msilvt-mtkt-1710864250/ |
| reg-01 | רגל עץ לשידה אלכסון (wood leg) | 129, 420 | https://www.snir-bebe.com/product/%d7%a8%d7%92%d7%9c-%d7%a2%d7%a5-%d7%9c%d7%a9%d7%99%d7%93%d7%94-%d7%90%d7%9c%d7%9b%d7%a1%d7%95%d7%9f/ |
| 4567654323456765432-1-2 | רגל אקרילי זהב ברונזה (acrylic leg) | 129, 420 | https://www.snir-bebe.com/product/%d7%a8%d7%92%d7%9c-%d7%90%d7%a7%d7%a8%d7%99%d7%9c%d7%99-%d7%a9%d7%99%d7%9c%d7%95%d7%91-%d7%96%d7%94%d7%91-%d7%91%d7%a8%d7%95%d7%a0%d7%96%d7%94/ |
| 4567654323456765432-1-1-1-1-1 | שטיח לחדר ילדים לוסי אפור (rug) | 420 | https://www.snir-bebe.com/product/%d7%a9%d7%98%d7%99%d7%97-%d7%9c%d7%97%d7%93%d7%a8-%d7%99%d7%9c%d7%93%d7%99%d7%9d-%d7%9c%d7%95%d7%a1%d7%99-%d7%90%d7%a4%d7%95%d7%a8/ |
| 4567654323456765432-1-1-1-1 | שטיח לחדר ילדים באפלה אפור (rug) | 420 | https://www.snir-bebe.com/product/%d7%a9%d7%98%d7%99%d7%97-%d7%9c%d7%97%d7%93%d7%a8-%d7%99%d7%9c%d7%93%d7%99%d7%9d-%d7%91%d7%90%d7%a4%d7%9c%d7%94-%d7%90%d7%a4%d7%95%d7%a8/ |
| 4567654323456765432-1-1-1 | שטיח לחדר ילדים באפלה שמנת (rug) | 420 | https://www.snir-bebe.com/product/%d7%a9%d7%98%d7%99%d7%97-%d7%9c%d7%97%d7%93%d7%a8-%d7%99%d7%9c%d7%93%d7%99%d7%9d-%d7%a7%d7%a9%d7%aa%d7%95%d7%aa-%d7%a9%d7%9e%d7%a0%d7%aa/ |
| 4567654323456765432-1-1 | ידית מוט עץ חיבור לבן (handle) | 129, 420 | https://www.snir-bebe.com/product/%d7%99%d7%93%d7%99%d7%aa-%d7%9e%d7%95%d7%98-%d7%a2%d7%a5-%d7%97%d7%99%d7%91%d7%95%d7%a8-%d7%9c%d7%91%d7%9f/ |
| 4567654323456765432-1 | ידית אקרילי זהב ברונזה (handle) | 129, 420 | https://www.snir-bebe.com/product/%d7%99%d7%93%d7%99%d7%95%d7%aa-%d7%90%d7%a7%d7%a8%d7%99%d7%9c%d7%99-%d7%91%d7%a8%d7%95%d7%a0%d7%96%d7%94-%d7%9c%d7%a9%d7%99%d7%93%d7%94/ |
| 456787654 | ידית עץ סטנדרט (wood handle) | 129, 420 | https://www.snir-bebe.com/product/bracker-wheels-1710862972/ |
| 4567654323456765432 | ידית טבעת שחורה (ring handle) | 129, 420 | https://www.snir-bebe.com/product/bracker-wheels-1710862966/ |
| 6543456787654 | ידית עור כפולה ארוכה (leather handle) | 129, 420 | https://www.snir-bebe.com/product/bracker-wheels-1710862960/ |
| 765456787654 | ידית עור כפולה (leather handle) | 129, 420 | https://www.snir-bebe.com/product/bracker-wheels-1710862156/ |
| 34567654323456789 | מסילה גלגל לבן לשידה (drawer slide) | 129 | https://www.snir-bebe.com/product/bracker-wheels-1710862902/ |
| 6789965790 | מסילה טלסקופית לשידה (telescopic slide) | 129 | https://www.snir-bebe.com/product/bracker-wheels-1710862145/ |
| 4353534643 | תחתית למיטת תינוק (crib base) | 129 | https://www.snir-bebe.com/product/st-przvl-mith-em-msilvt-mtkt-1710862243/ |
| 44445354 | סולם אחורי קבוע למיטת תינוק (fixed rear ladder) | 129 | https://www.snir-bebe.com/product/st-przvl-mith-em-msilvt-mtkt-1710862240/ |
| 2343443 | סולם קדמי עולה יורד למיטת תינוק (front ladder) | 129 | https://www.snir-bebe.com/product/st-przvl-mith-em-msilvt-mtkt-1710862236/ |
| 997 | סט פרזול מיטה עם מסילות פלסטיק (bed hardware kit) | 138, 129 | https://www.snir-bebe.com/product/st-przvl-mith-em-msilvt-mtkt-1710860283/ |
| 998 | סט פרזול מיטה עם פיונים (bed hardware kit) | 138, 129 | https://www.snir-bebe.com/product/st-przvl-mith-em-msilvt-mtkt/ |
| 999 | סט גלגלים עם פינים למיטה (wheel+pin kit) | 138, 129 | https://www.snir-bebe.com/product/pinned-wheels/ |

Category legend: **129** = חלקי חילוף (spare parts) · **420** = אביזרים לחדר תינוק (nursery
accessories) · **138** = MIX AND MATCH (hardware kits). None are importable.

Raw data: `snir-uncategorized.json` (same folder).
