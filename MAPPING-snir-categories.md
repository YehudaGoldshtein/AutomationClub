# Snir — category & field mapping (LOCKED, build-ready)

> Companion to `PRD-snir-product-sync.md`. All decisions **approved by the store
> owner** and **verified against live data** (Snir WC Store API, 330 products /
> 292 with SKU · Max Baby Admin API, collection ids confirmed). Mapping is by
> **`category id`** (names/slugs are unreliable — see §1). Safe to hardcode.

## 0. Final decisions (binding)

1. **Scope:** import **everything except** spare-parts (id 129) **and** accessories
   (id 420). → **271 products** imported, 21 ignored.
2. **Precedence:** "room wins" (חדר גובר).
3. **Variations:** a **separate single-variant product per size/color** (not a
   multi-variant product).
4. **Non-furniture** lines (strollers, prams, high-chairs, bath, nursing chairs)
   **are imported** — with empty `product_type` + no template (store convention).
5. `template_suffix` derived from **product_type**, not name (supersedes PRD §2.1).
6. **OOS-at-source is not onboarded** (cross-supplier rule): a product with
   `is_in_stock=false` at the source is **not created** as a new draft — it onboards
   automatically on a later run once back in stock. *(Applies to Segal + Snir; **Laura
   is exempt** — its availability has a different source of truth: Excel two-truths +
   the אזל discontinued rule.)* Existing products going OOS are handled by **sync**
   (marked sold-out), not this rule — this gate is creation-only.

## 1. Source of truth: category **id**, not name

Every category has a stable numeric `id`; products carry those ids in `categories[]`.
Name/slug matching was rejected because on Snir:
- **Hebrew final-form trap:** PRD keyword `מזרן` (final `ן`) is not a substring of
  `מזרנים` (medial `נ`) → naive matching drops mattresses.
- **Slug ≠ name:** `id=127` is named `קטלוג חדרים` but its slug is `hdri-tinvqvt`;
  the real rooms category `id=118` has slug `furniture-for-babies`. Only the id is stable.

## 2. Category id → mapping (checked in priority order; first match wins)

A product usually sits in several ids. Route by the **first** priority row it matches.
Import a product if it matches **any** row 1–10; **ignore** it if its only categories
are MARKETING or EXCLUDE. Collection ids all verified against Max Baby (§6).

### Furniture (rows 1–6)
| # | Snir category id | → product_type | → collection (id) | template_suffix |
|---|---|---|---|---|
| 1 | 118 · 137 · 136 · 135 | חדרי תינוקות | חדרי תינוקות (480525418750) | furniture-product-page |
| 2 | 126 · 140 · 139 · 141 · *(142 kids-beds)* | מיטות תינוק | מיטות תינוק (480525025534) | **furniture-beds** |
| 3 | 128 · *(142 closets)* | ארונות לחדרי ילדים | ארונות לחדרי ילדים (480529645822) | furniture-product-page |
| 4 | 125 · 134 · 133 · 132 | שידות החתלה | שידות החתלה (480525451518) | furniture-product-page |
| 5 | 120 | מזרונים לתינוק | מזרונים לתינוק (477899096318) | furniture-product-page |
| 6 | 122 | כורסאות הנקה | כורסאות הנקה (480540197118) | furniture-product-page |

### Non-furniture (rows 7–10) — empty `product_type`, **no template** (store convention)
| # | Snir category id | → collection (id) |
|---|---|---|
| 7 | 130 (strollers) | טיולונים (477887889662) |
| 8 | 117 · 131 (prams) | עגלות תינוק (477885858046) |
| 9 | 121 (high chairs) | כסאות אוכל (477920526590) |
| 10 | 419 (bath) | אמבטיות ואביזריהם לתינוק (477887299838) |

### MARKETING — ignore for product_type (a product may also be in these)
`138` MIX AND MATCH · `119` מוצרים בדף הבית · `127` קטלוג חדרים · `252` קרוסלה ·
`124` מבצעים חמים · `331` מבצעים וחיסולי מלאי · `258` כללי

### EXCLUDE — not imported (owner decision)
`129` חלקי חילוף (spare parts) · `420` אביזרים לחדר תינוק (accessories).
A product is dropped only if it has **no** row 1–10 category (a bed also tagged 129
is still imported as a bed).

## 3. Corrections to the original plan (verified live)

- **snir-kids (142) is NOT "rooms".** Of its 11 products: **7 are closets** (also in
  128 → row 3) and **4 are kids-beds** (מיטת ילדים רומא/מילאנו, only in 142 → row 2).
  No room-sets in 142. Verified.
- **Ignoring 127 (קטלוג חדרים) loses nothing:** 0 of its 68 products are outside 118.
  Verified.
- **עריסה נצמדת (141) → מיטות תינוק** (`furniture-beds`). BÉBÉ co-sleepers, only in 141.

## 4. Per-product final scope (verified)

292 SKU'd · **271 in-scope** · 21 ignored (only spare-parts/accessories). 38 SKU-less
products are not onboardable (skipped).

Of the 271 in-scope, the OOS-at-source gate (§0.6) means **221 in-stock are created
now**; **50 OOS are skipped** (onboard later when back in stock). Counts below are the
full in-scope universe (in-stock + OOS).

| product_type | count |
|---|---|
| מיטות תינוק (incl. 4 kids-beds + עריסה) | 78 |
| חדרי תינוקות | 71 |
| שידות החתלה | 60 |
| טיולונים (strollers) | 15 |
| ארונות לחדרי ילדים (incl. 7 closets from 142) | 14 |
| כסאות אוכל (high chairs) | 10 |
| מזרונים לתינוק | 8 |
| עגלות (prams) | 5 |
| אמבטיה (bath) | 5 |
| כורסאות הנקה | 5 |

## 5. Item field mapping (verified against live data, 292 SKU'd)

Structured fields from the Store API; **one** tab from the product-page HTML.

| source | → Shopify | notes |
|---|---|---|
| `sku` | `variant.sku` (key) | 292 present; SKU-less skipped |
| `name` | `product.title` (as-is; user enriches) | append variation value for split products (§7) |
| `short_description` | `product.body_html` | 290/292; distinct from description |
| `description` | metafield `custom.view_productss` | 277/292 — **from API, no scrape** |
| `prices.regular_price` | `variant.price` as-is + `supplier.price` | **`minor_unit=0`** → whole ₪, no /100 |
| `is_in_stock` | boolean availability | 242 in / 50 out; **binary only** (`maximum=9999`); **OOS → not onboarded** (§0.6) |
| `barcode` | — | not in API → not mapped |
| `images[].src` | `product.images[]` (download + upload) | |
| tab `tech_details` (HTML) | metafield `custom.infoo` | **the only HTML scrape needed** |
| *(constant)* | `custom.securingg` = "כל המוצרים מגיעים עם 12 חודשי אחריות לפי התקנון." | fixed 12mo (vs Segal 5yr) |
| *(constant)* | `custom.delivery` = furniture boilerplate (§8) | **identical to Segal `FURNITURE_DELIVERY_LINES`** |
| ← name / ← description | `global.title_tag` / `global.description_tag` | |
| *(constant)* | `vendor` = `שניר \| snir`, `status` = draft | |

`template_suffix`: `product_type == מיטות תינוק → furniture-beds`; other furniture →
`furniture-product-page`; non-furniture → none.

## 6. Collection ids — verified in Max Baby (safe to hardcode)

All 14 resolve to the exact expected title. Furniture + non-furniture ids are in §2.
Extra: לולים ועריסות (480539672830) — optional secondary home for עריסה.

**Vendor collection:** `שניר בבה` (478221697278) and `snirbebe` (478222582014) are
**both empty**. Not required (products live in their product_type collections). If a
brand filter is wanted, use `שניר בבה` and delete the duplicate `snirbebe`.

## 7. Variations — separate product per size/color

9–10 `variable` products have 2+ real variations (axes: mattress size 128/66·130/70·
140/70; dresser width 100/120; color white/natural-white). 168 "variable" products
have a single variation → collapse to one product automatically.

**Rule:** split each real variation into its **own single-variant Shopify product**
with that variation's SKU + price; append the variation value to the title (e.g.
"… 130/70"). **Build note:** the bulk `/products` list exposes only variation `id` +
`attributes` — **not per-variation SKU/price** → fetch each variable product's full
payload (or its variations) to get them.

## 8. Delivery text (`custom.delivery`) + Studio Boutique exception

Base text — identical to Segal's `FURNITURE_DELIVERY_LINES`:
```
שירות הובלה והרכבה לריהוט וחדרי תינוקות: התשלום עבור שירות ההובלה וההרכבה מתבצע ישירות למוביל, במזומן, בהתאם לסכום המצוין בעמוד המוצר.
ייתכנו תוספות תשלום במקרים מסוימים ובתיאום מראש, כגון: משלוחים ליעדים מרוחקים, מעבר לקו הירוק, הובלה מעל קומה שלישית ללא מעלית, גישה מוגבלת או צורך במנוף.
לקבלת פירוט מלא, קראו עוד על הובלות והרכבות.
```
**Studio Boutique exception (12 products, verified):** if `name` contains
`STUDIO BOUTIQUE` (or description contains `סטודיו בוטיק`), inject this price-list
block **before** the last line ("לקבלת פירוט מלא…"):
```
מחירון הובלה והרכבה קולקציה סטודיו בוטיק:
שידה- 400 ש"ח
מיטה- 350 ש"ח
שידה + מיטה – 600 ש"ח
```

## 9. Status — everything closed

| topic | status |
|---|---|
| scope (all except spare-parts 129 + accessories 420) → 271 | ✅ locked |
| id-based mapping (furniture + non-furniture) | ✅ locked |
| collection ids | ✅ verified |
| precedence (room wins) | ✅ locked |
| snir-kids (142) = closets + kids-beds (not rooms) | ✅ corrected |
| 127 ignore is safe (⊆ 118) | ✅ verified |
| variations = separate product per size | ✅ locked |
| Studio Boutique (detect + text) | ✅ locked |
| delivery text = Segal's | ✅ verified |
| template_suffix from product_type | ✅ locked |
| item field mapping (§5) | ✅ locked |

---
*Verified 2026-07-19 against live data: Snir WC Store API (`/products`,
`/products/categories`) + Max Baby Admin API (`collections/<id>`). Category ids and
collection ids are stable keys — safe to hardcode.*
