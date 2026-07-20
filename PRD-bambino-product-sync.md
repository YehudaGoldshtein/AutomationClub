# מסמך דרישות — ייבוא וסנכרון מוצרי Bambino (Joie/Infanti/Graco +6) ל-Shopify

> **מטרה:** להגדיר, שדה-אחר-שדה, איך מוצר מהספק **Bambino** (bambinok.com) מיובא ל-Shopify (חנות MaxBaby) ומסונכרן.
> **חלק ממערכת Supplier Product Sync.** אומת מול החנות החיה + על 6 מוצרי-בדיקה חיים (Graco/Joie/Infanti/Bumprider/Mastela/Pesto).
> **ספק אחד, 9 מותגים, מקור אחד.** אימות: 2026-07-19.

---

## 0. מקור הנתונים — API יחיד (הכי נקי מכל הספקים)
כל 3 האתרים ששלח הבעלים (joiebaby.co.il / infanti.co.il / gracobaby.co.il) + bumprider.co.il הם **חלונות-ראווה לפי מותג של ספק אחד** — Bambino (bambinok.com). כולם מושכים מ-API יחיד:

```
GET https://api.bambinok.com/cache/Bambino     ← מקור יחיד ומלא. אין WAF. urllib רגיל.
```

מחזיר אובייקט עם: `products` (525), `websites` (מדיניות/אחריות per-מותג), `phrases`, `colors`, `redirects`, `articles`, `blogPosts`, `approvedSellers`.

**אימות ודאי (2026-07-19):** כל מוצר מכל אתר-מותג נמצא במאסטר — **0 חסרים** (Joie 152/152 · Infanti 291/291 · Graco 45/45 · Bumprider 30/30). המאסטר = **על-קבוצה מלאה** של 525 מוצרים.

> **חובה למתכנת:** למשוך **רק** מ-`/cache/Bambino`. אין צורך ולא נכון לגעת ב-4 האתרים הנפרדים — הכל שם, כולל מותגים בלי אתר (Mastela/Nuna/Safety1st/RycoBaby/Bambino).

**היקף:** כל 525 המוצרים, כל 9 המותגים (Infanti 266 · Joie 152 · Graco 45 · Bumprider 30 · Mastela 12 · Safety1st 9 · RycoBaby 5 · Bambino 4 · Nuna 2). **אין החרגות** (החלטת הבעלים: הכל).
- URL באתרי המקור = `catalogNumber` (למשל `/products/product/110104360`).

---

## 1. מפתח זיהוי + 94 הקיימים — מחיקה גורפת (החלטת בעלים)
- מפתח הספק = **`catalogNumber`** (9 ספרות, למשל `110104360`) ↔ `variant.sku`.
- **המוצרים הקיימים בחנות (94: infanti 45 / joie 33 / graco 6 / BAMBINO 10) לא נבנו מהפיד הזה** — מק"טים פנימיים (10152, ZEST), 0% חפיפה, body/infoo ממקורות אחרים (חלקם מ-babystav מתחרה).
- **✅ החלטת הבעלים: למחוק את כל 94 הקיימים ולהעלות את כל 525 מחדש מהפיד הנקי.**
  - **אין צורך ב-dedup / התאמת-שמות בכלל** — המחיקה הגורפת מבטלת את בעיית המפתח-החלש.
  - זרימת המתכנת: (1) למחוק את 94 מוצרי המותגים הקיימים (vendor infanti/joie/graco/GRACO/BAMBINO), (2) לייבא את 525 מחדש (§2-§8).
  - `catalogNumber` נשאר המפתח לסנכרון שוטף אחרי הייבוא.

---

## 2. מיפוי שדות הליבה (POST `products.json`)
| שדה API | → שדה Shopify | כלל |
|---|---|---|
| `title` + `name` (+`color`) | `product.title` | `"{title} {name}"`; לוריאנט-צבע להוסיף `" - {color}"` |
| `catalogNumber` | `variant.sku` | מפתח |
| `barcode` | `variant.barcode` | as-is (93% מלא) |
| `price` | **`variant.price`** | ⭐ לייבא כמו שהוא. המשתמש משנה אח"כ עם הנוסחה שלו |
| `discount` (type=overwrite) | `variant.compare_at_price` + `price` | אם מבצע פעיל: `price`=`discount.amount`, `compare_at_price`=המחיר המקורי. יש תאריכי `startDate/endDate` |
| `quantity` | `variant.inventory_quantity` | `inventory_management="shopify"` + לעדכן level |
| `images[]` | `product.images[]` | להוריד ולהעלות ל-CDN (לא hotlink) |
| `description` | `product.body_html` | as-is (523/525 מלא) |
| `brand` | `vendor` | joie/infanti/graco (lowercase, תואם קיים) · אחרים לפי שם המותג |
| `types[]` | אוסף (§5) | product_type **ריק** |
| *(קבוע)* | `template_suffix` = **`bambino`** · `status` = **draft** (חדש) | §6 |
| `metaTitle`/`metaDescription` | `global.title_tag`/`description_tag` | **תמיד ריקים (0%)** → fallback ל-title/description |

**וריאנטים:** מוצר בודד (option `Title`/`Default Title`, **בלי option צבע** — מונע swatch ריק). §4 צבעים.

---

## 3. Metafields — התוכן והלשוניות
| metafield | לשונית בדף | סוג | מקור ב-API |
|---|---|---|---|
| `custom.infoo` | **מאפיינים** | rich_text | **שדות מובנים**: `age` (X-Y חודשים) · `weight` · `height×width×length` · `standard` · `isofix` |
| `custom.view_productss` | **תיאור פריט** | rich_text | `specifications` (רשימת `<ul><li>`) |
| `custom.securingg` | **אחריות** | rich_text | **per-מותג** `websites[brand].policies.warranty` (§7) |
| `custom.delivery` | **משלוחים והחזרות** | rich_text | **קבוע** (boilerplate, §8) |
| `custom.videos` | **סרטוני הדרכה** | list.url | `video` + `videos[].url` (תמיד YouTube) |
| `custom.manual` | **סרטוני הדרכה** | url | `productManual` (PDF) |
| `custom.faq` | *(עתידי)* | json | `productFaq.questions` |
| `custom.feature_slider` | *(עתידי — סליידר)* | json | `features[]` (image+title+text) |
| `custom.related_products` | Related | list.product_reference | אחי-צבע (§4) + `relatedProducts` |
| `global.title_tag`/`description_tag` | SEO | string | ← title/description (metaTitle ריק) |

> ⚠️ **קריטי — מאפיינים ⇄ תיאור פריט:** `infoo`(מאפיינים)=שדות מובנים · `view_productss`(תיאור פריט)=specifications. (זה **התיקון** — לא הפוך.)

**המרה ל-rich_text:** HTML של `specifications`/`warranty` → עץ rich_text (`paragraph`/`list`/`list-item`, `text` עם `bold`). `<ul><li>`→list, `<p>`→paragraph, `<strong>`→bold.

**fill-rates /525 (לא כל שדה קיים בכל מוצר — הכלל אחיד, לשונית ריקה נעלמת):**
description 99% · specifications 91% · features 62% · productManual 47% · video 40% · videos 25% · **productFaq 10%** · discount 35% · metaTitle **0%**.

---

## 4. צבעים — מוצר נפרד לכל צבע + קישור אחים
הספק שומר כל צבע כ**רשומה נפרדת**, מקושרת דרך `mainColorProductId` (הראשי `isMainColor:true`, `mainColorProductId:null`; האחרים מצביעים עליו).
1. **לקבץ** לפי `mainColorProductId` (כל הרשומות עם אותו main = אותו דגם).
2. כל צבע → **מוצר Shopify נפרד** (מק"ט/תמונה/מלאי/עמוד משלו). (החלטת הבעלים — גם באתר המקור אלו 2 מוצרים מקושרים, לא וריאנטים.)
3. אחרי יצירת כל הצבעים בקבוצה → למלא `custom.related_products` בכל אחד = ה-GIDs של האחים (שחזור הקישור שבמקור).
- 525 רשומות = 525 מוצרים · 283 דגמים ייחודיים (268 ראשי + 257 וריאנט-צבע).

---

## 5. קטגוריות — `types` → אוסף
**קבוע:** product_type **ריק** · template `(bambino)` · + אוסף-מותג (joie/infanti/graco...) + אוסף-קטגוריה. (אומת: כל 94 הקיימים = product_type ריק, template none.)

### 5.1 מיפוי `types` → אוסף קיים
| type (id) | → אוסף (id) |
|---|---|
| טיולונים (28) | טיולונים (477887889662) |
| עגלות מגיל לידה (18) · עגלות תאומים (33·56) | עגלות תינוק (477885858046) / עגלות תאומים (477886382334) |
| אביזרים לעגלות (27) · בסיסים/אביזרי בטיחות (30·32) | אקססוריז לעגלה/טיולון (477886546174) |
| סלקלים (20) | סל קל (478223565054) |
| כסאות אוכל (25) | כסאות אוכל (477920526590) |
| נדנדות (29) | נדנדה לתינוק (477887561982) |
| טרמפולינות (34) | טרמפולינה לתינוק (477886284030) |
| הליכונים (35) | הליכונים לתינוק (477894115582) |
| בימבות (72·24) | בימבה (477892509950) |
| תלת אופן (59) | תלת אופן (477892673790) |
| אופני איזון (73) | אופני איזון (477896769790) |
| צעצועים (55) + ג'אמפרים (19·47) + מטבחים (66) + בתי בובות (67) + מובייל (76) + עגלות בובה (68) | צעצועים (477893460222) |
| משטחי פעילות (75) | מזרן ומשטח פעילות (477895983358) |
| אמבטיה (69·43) | אמבטיות ואביזריהם (477887299838) |
| עריסה/לולים (60·31·26) | לולים ועריסות (480539672830) |
| מיטת תינוק (61) | מיטות תינוק (480525025534) |
| מיטות מעבר (62) | מיטות מעבר (477898440958) |
| שערים/אביזרי בטיחות (36·52) · מגני מיטה (74) | שערי בטיחות (477891756286) / אביזרי בטיחות (477887201534) |
| תיקים/ארגוניות (65·44·58) | תיקים (477895852286) |
| שולחן וכסאות (64) | שולחנות כיסאות ספות (477896507646) |
| גמילה מטיטולים (70) | סירים וישבנונים (477887168766) |

### 5.2 🆕 3 אוספים חדשים ליצור
| אוסף חדש | types |  # |
|---|---|---|
| **כסאות בטיחות** | כסאות בטיחות (23) · בוסטרים (22) · דו-צדדיים (46) · מגיל שנה (54) · מושבי הגבהה (152) · בוסטר הגבהה (45) | ~140 |
| **מנשאים** | מנשאים (38) | 5 |
| **מגדל למידה** | מגדל למידה (134) | 3 |

### 5.3 להתעלם
**Signature (37, 49 מוצרים)** = קו-מוצרים של Joie, **לא קטגוריה**. לסווג לפי ה-type האמיתי השני. (מוצר עם כמה types → precedence: להתעלם מ-Signature, לבחור את הספציפי.)

---

## 6. Template + לשוניות (theme — כבר בוצע)
- **`templates/product.bambino.json`** — נוצר (מבוסס על product.json הדיפולטי). לשייך אליו את כל מוצרי Bambino (`template_suffix="bambino"`).
- לשוניות (collapsible-row blocks, מצביעות על metafields): מאפיינים←infoo · תיאור פריט←view_productss · אחריות←securingg · משלוחים והחזרות←delivery.
- **לשונית "סרטוני הדרכה והוראות שימוש"** — בלוק `custom-liquid` מקונן שמטמיע **נגן YouTube אמיתי** מ-`custom.videos` + קישור PDF מ-`custom.manual`. הקוד בתבנית (חל אוטומטית על כל מוצר; מוצר בלי סרטון → לשונית ריקה בלי שגיאה).
- **הגדרות metafield** (כולן קיימות + pinned): infoo/view_productss/securingg/delivery/videos/manual/faq/feature_slider/related_products/hadracha.

---

## 7. אחריות — קבוע **per-מותג** (`websites[brand].policies.warranty`)
טקסט אחריות מלא (HTML → rich_text), **לא per-product**. תקופות:
- **Graco / Infanti** → שנה · **Joie / Bumprider** → שנתיים.
- מותג בלי אתר (Mastela/Nuna/Safety1st/RycoBaby) → להשתמש במדיניות של אתר **Bambino** (המאסטר).
- באותו `policies` יש גם `shippingPolicy` / `refundAndReturn` per-מותג (רזרבה אם רוצים להחליף את ה-boilerplate).

---

## 8. משלוחים — boilerplate קבוע (גרסת non-furniture / ביגוד)
`custom.delivery` = קבוע (אומת מול הקיימים בחנות, verbatim):
```
משלוח עד הבית
✓ שליח עד הבית חינם בהזמנה מעל 499 ₪ (לא כולל ריהוט)
✓ מתחת ל-499 ₪ עלות המשלוח 29 ₪
✓ אספקת הזמנה עד 7 ימי עסקים (לא כולל ריהוט) · ריהוט עד 14 ימי עסקים
החלפות והחזרות: עד 30 יום החלפה · 14 יום החזרה · דרך maxbabyonline@gmail.com
```
*(הטקסט המלא שמור: `bambino/output/_delivery_verbatim.json`.)*

---

## 9. שני תהליכים
- **ייבוא ראשוני:** כל 525 → Draft + כל §2-§8. קיבוץ צבעים (§4). 3 אוספים חדשים (§5.2).
- **סנכרון (catalogNumber):** מלאי (`quantity`) · מחיר + מבצע (`discount`) · לוג. תדירות 3-6ש'.

---

## 10. מה על המתכנת להשלים (Implementation TODO)
1. **להריץ על כל 525** ולאמת ב-100% (המיפוי אומת על **מדגם של 6 מוצרים** — לא על כל הקטלוג).
2. **תמונות:** הורדה+העלאה ל-CDN (לא hotlink).
3. **קיבוץ צבעים + related_products** (§4) — חובה, אחרי יצירת כל הקבוצה.
4. **3 אוספים חדשים** (§5.2) + מיפוי types מלא.
5. **למחוק את 94 הקיימים** (vendor infanti/joie/graco/BAMBINO) לפני הייבוא — ואז להעלות 525 מחדש. אין dedup.
6. **אחריות per-מותג** (§7) — לשלוף מ-`websites[brand].policies.warranty`, לא לנחש.
7. **template + לשוניות** (§6) — כבר קיים; לוודא שכל מוצר משויך ל-`bambino`.

---

## ⚠️ הסתייגות — היקף הבדיקה
- **הקטלוג:** נמשכו כל 525 (API) ✅.
- **המיפוי + התבנית + הלשוניות:** נבדקו על **6 מוצרי-בדיקה** (Graco/Joie/Infanti/Bumprider/Mastela/Pesto) — לא על כל הקטלוג. המתכנת חייב QA מלא, במיוחד: קיבוץ צבעים, האחריות per-מותג, ו-types נדירים.
- **המיפוי הוא הליבה המאומתת.** השליפה הוכחה מ-API יחיד; היקף מלא = QA.

---
*נבנה מאימות ישיר: `api.bambinok.com/cache/Bambino` ↔ MaxBaby Admin API. 2026-07-19. מוצרי-בדיקה חיים (Draft) בחנות. category/collection ids ומבנה שדות = מפתחות יציבים לקידוד.*
