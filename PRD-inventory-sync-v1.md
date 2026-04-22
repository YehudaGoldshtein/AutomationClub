# Product Requirements Document
## Automatic Inventory Sync for Online Stores

**Version:** v1 (Draft)
**Date:** 21/04/2026
**Authors:** Eli, Yehuda
**Status:** Requirements — pre-design

---

## 1. Executive Summary

Online store owners (Shopify, WooCommerce) with thousands of SKUs sourced from multiple external suppliers suffer from constant inventory mismatch between their store and their suppliers. When a supplier runs out of stock on a product, the store owner often does not know for hours or days — meanwhile the store continues showing the product as available, Google Ads and Facebook campaigns keep driving paid traffic to it, orders come in that cannot be fulfilled, and both ad budget and customer trust are burned.

This product is a SaaS tool that **automatically synchronizes inventory data from each supplier into the store in real time (or at a configurable frequency)**, and takes configurable automated actions in the store when stock changes — such as taking a product offline and notifying the owner.

**Target scope for v1:** stock-level synchronization only. Pricing sync, new-product sync, ad-platform integration, and a customer-facing dashboard are explicitly out of scope for v1 and parked as future roadmap items.

---

## 2. Problem Statement

Store owners operating multi-supplier catalogs face three compounding problems when inventory drifts out of sync:

1. **Ad budget burn.** Google Ads / Facebook Ads continue serving ads for out-of-stock SKUs, consuming budget on traffic that cannot convert.
2. **Customer experience damage.** Customers place orders for products that cannot be fulfilled, leading to cancellations, refunds, support load, and negative reviews.
3. **Operational chaos.** Store owners manually log into supplier portals, spreadsheets, and emails to reconcile inventory — a daily task that scales badly past a few dozen SKUs and a few suppliers.

The deeper root cause: **suppliers and store platforms are not integrated by default.** Every store owner has to solve this integration themselves, usually manually or with brittle one-off solutions.

---

## 3. Target Users

**Primary persona:** Owner or operations manager of an e-commerce store running on Shopify or WooCommerce, with the following characteristics:

- Sells physical products sourced from external suppliers (distributors, wholesalers, manufacturers).
- Has hundreds to thousands of SKUs.
- Works with multiple suppliers (typically 5–50+).
- Runs paid acquisition campaigns (Google Ads, Facebook Ads) where inventory accuracy directly impacts ROAS.
- Currently manages inventory sync manually or through limited one-off integrations.

**Initial inspiration vertical:** baby product retailers (e.g., Shilav-type stores). The product is **not limited** to this vertical — it is designed to be vertical-agnostic.

**Not a target for v1:** single-supplier stores, print-on-demand stores, digital goods stores, marketplaces.

---

## 4. Goals and Success Metrics

### Product Goals

1. Eliminate the gap between supplier stock and store stock for participating customers.
2. Reduce paid ad spend wasted on out-of-stock SKUs.
3. Reduce canceled/refunded orders caused by inventory mismatch.
4. Free the store owner from manual inventory reconciliation work.

### Candidate Success Metrics

| Metric | Target direction | Notes |
|---|---|---|
| % reduction in ad spend on out-of-stock SKUs | ↓ meaningful reduction | Measured by comparing same-period ad spend pre- and post-adoption |
| % reduction in canceled orders due to stock-out | ↓ meaningful reduction | Measured from store order data |
| Median sync latency (supplier change → store update) | ≤ configured sync window | System-level KPI |
| Manual reconciliation time saved per week | ↓ hours | Self-reported by customer |
| Customer retention after 3 months | ≥ target TBD | Business metric |

> **⚠️ NOTE — measurement depends on customer data access.** Quantifying ad-spend-on-OOS and canceled-order reduction requires historical data from the customer: at minimum 12 months of ad spend data and order cancellation data. This must be requested from the customer during onboarding or an evaluation period. If the customer is unwilling to share, we fall back to system-level metrics (sync latency, uptime) only.

---

## 5. In Scope for v1

- **Store platform support:** Shopify, WooCommerce.
- **Data synced:** stock levels / quantity available, per SKU, per supplier.
- **Sync direction:** one-way, supplier → store.
- **Stock-change actions:** configurable (see section 7.5).
- **Notifications:** Email, WhatsApp. Configurable per event and per channel (see section 7.6).
- **Product-to-supplier mapping:** customer-provided via CSV or manual UI (see section 7.3).
- **Configuration model:** per-customer configuration covering sync frequency, actions, notifications, and multi-supplier behavior.

---

## 6. Out of Scope for v1

The following are explicitly **not** included in v1 and are tracked in the roadmap (section 10):

- Pricing / cost sync from supplier to store.
- New product / catalog item sync (supplier adds a product → it appears in store).
- Full catalog sync (descriptions, images, variants, attributes).
- Customer-facing dashboard / UI beyond what is required for setup and configuration.
- Direct integration with Google Ads / Facebook Ads to pause/unpause ads automatically.
- Auto-reordering from supplier when stock is low.
- Two-way sync (store changes pushed back to supplier).
- Support for store platforms other than Shopify and WooCommerce (e.g., Magento, BigCommerce, custom).
- Mobile app.

---

## 7. Functional Requirements

### 7.1 Store Integration

The system **must** connect to the customer's store via the store's official API.

- **Shopify:** the customer provides API access (app install, private app, or equivalent) sufficient to read the product catalog and update stock / product status.
- **WooCommerce:** the customer provides REST API credentials sufficient to read the product catalog and update stock / product status.

Required capabilities on the store side:

- Read full product list with SKU, current stock level, publish status.
- Update stock level per product/variant.
- Change publish status (publish / unpublish / draft) per product.

### 7.2 Supplier Integration

Every customer falls into **one of two tracks** depending on what their suppliers expose:

**Track A — Supplier API available.** The customer provides supplier API access (credentials, endpoints, or equivalent). The system pulls stock data directly from the supplier API.

**Track B — No supplier API available.** The customer provides the supplier portal / source location and whatever access mechanism is feasible (this v1 PRD does **not** prescribe the mechanism — that is an implementation decision). The PRD only requires that the system **must be able to operate even when no supplier API is available**, i.e., the product must not require supplier API access as a precondition for the customer to use the service.

The system **must** support a mix of tracks for a single customer (some suppliers with API, some without).

### 7.3 Product-to-Supplier Mapping

The mapping of "SKU X is supplied by Supplier Y" is **the customer's responsibility.** The system does not auto-detect or guess supplier mapping.

Two supported input methods in v1:

**Method 1 — CSV import.** The customer uploads a CSV of their products with a column indicating the supplier (and, where applicable, the supplier-side SKU). Many stores already maintain this data natively in their product catalog.

**Method 2 — Manual mapping UI.** For customers who do not have supplier information on their products, the system provides a UI to assign a supplier to each product manually. The time investment is the customer's; the system's responsibility is to offer a workable interface.

Both methods must be supported in v1. A single customer may use both (bulk CSV first, then manual for the long tail).

### 7.4 Stock Sync Engine

The sync engine **must**:

- Pull current stock data from each configured supplier source for each mapped SKU.
- Compare to the store's current stock level.
- Push updated stock values to the store when they differ.
- Run at the frequency configured per customer (see 7.5).
- Log each sync run (timestamp, items checked, items changed, errors) for audit purposes.
- Handle partial failures gracefully — if one supplier is unreachable, other suppliers must still sync.

### 7.5 Actions on Stock Changes

All behaviors are **configurable per customer** via a configuration file / settings interface. The system must support any combination of the following behaviors.

**Sync frequency options:**

- Real-time / near real-time
- Hourly
- Multiple times per day
- Daily
- Custom interval

**Stock-out action options (any combination):**

- Update stock count in store only (passive — rely on store's built-in OOS display).
- Take product offline (unpublish / mark as draft) when stock hits zero.
- Pause the product's ads on Google / Facebook _(depends on future ad-integration capability — see roadmap)._
- Notify owner.

**Back-in-stock action options (any combination):**

- Auto-republish when supplier stock returns.
- Notify owner only — owner decides whether to republish.
- Per-product override.
- Global default with per-product override.

**Multi-supplier per SKU options:**

- Single supplier per SKU (simplest).
- Primary + fallback (if primary supplier is out, use backup).
- Split inventory across suppliers (sum stock levels).

> **Principle:** every customer configures the behavior they want. Defaults must be chosen such that a customer who configures nothing still gets a sensible working setup.

### 7.6 Notifications

**Channels supported in v1:** Email, WhatsApp.

**Events that can trigger notifications** (all configurable per customer, per event, per channel):

1. SKU went out of stock.
2. SKU came back in stock.
3. Sync error / supplier unreachable.
4. Daily or weekly digest summary ("today: 12 products went OOS, 3 came back").
5. Low-stock alert, triggered when a SKU falls below a customer-defined threshold.

For each event, the customer configures:

- On / off.
- Which channel(s) to use (email, WhatsApp, or both).
- Thresholds or windows where relevant (e.g., low-stock threshold, digest frequency).

---

## 8. Customer Configuration Model

The product is a **configuration-driven platform**. The same codebase serves all customers, with per-customer configuration driving behavior.

**Configurable parameters per customer:**

| Area | Options |
|---|---|
| Sync frequency | real-time / hourly / multiple daily / daily / custom |
| Stock-out actions | update only / take offline / pause ads (future) / notify / any combo |
| Back-in-stock actions | auto-republish / notify only / per-product override / global + override |
| Multi-supplier per SKU | single / primary+fallback / split |
| Notification channels | email / WhatsApp / both — per event |
| Notification events | out-of-stock / back-in-stock / sync error / digest / low-stock — each on/off |
| Low-stock threshold | numeric, per-product or global |
| Digest frequency | daily / weekly |

> **Open question:** some configuration options may be gated behind pricing tiers (e.g., real-time sync available only on a Pro plan). This is flagged for the business / pricing phase — see section 11.

---

## 9. Mandatory vs Optional Customer Inputs

**Mandatory inputs (service cannot run without these):**

1. Store API access (Shopify or WooCommerce).
2. Complete list of products the customer wants synced.
3. Product-to-supplier mapping for those products.

**Optional inputs:**

1. Supplier API access — provided by some customers, unavailable to others. The product must work in both cases.
2. Historical data for success measurement — 12 months of ad spend data and canceled order data. Useful for ROI measurement but not required for the service to function.

---

## 10. Future Considerations / Roadmap

Not in v1, planned for future versions:

| Feature | Rationale |
|---|---|
| Price / cost sync | Natural extension once stock sync is proven |
| New product sync | Supplier adds product → appears as draft in store for review |
| Full catalog sync | Descriptions, images, variants, attributes |
| Customer-facing dashboard | Sync status, history, error log, stock visibility |
| Google Ads / Facebook Ads direct integration | Auto-pause / unpause ads on stock change |
| Auto-reorder from supplier | When stock low, auto-place PO |
| Additional store platforms | Magento, BigCommerce, custom stores |
| Per-variant / bundle handling | Advanced inventory logic |
| Multi-warehouse / multi-location | Beyond single-supplier-per-SKU |

---

## 11. Open Questions and Risks

**Open questions to resolve before or during design:**

1. **Pricing tier model.** Should configuration options be gated by plan (e.g., real-time = Pro)? Needs business input.
2. **Supplier access patterns.** When no supplier API is available, what mechanisms are acceptable to customers and viable to build? (Implementation decision, not a PRD decision — but it affects feasibility of Track B in section 7.2.)
3. **Success metric baselines.** How do we get comparable pre-adoption data from every customer? Standardize the onboarding data request.
4. **Data retention and privacy.** What data do we store, for how long, and under what terms? Needs legal / compliance input before launch.
5. **Conflict resolution.** What happens if a store owner manually changes stock in the store between sync runs? Does the next sync overwrite their change? Needs a rule.
6. **Failure ownership.** If the supplier source is down / changes format / blocks us, who bears the cost — do we refund, credit, or just log and notify? Needs SLA definition.

**Risks:**

- **Supplier-side fragility.** Customers without a supplier API depend on whatever mechanism we build for Track B. That mechanism may be brittle and will require ongoing maintenance as supplier portals change.
- **Configuration complexity.** A highly configurable product is harder to build, test, support, and sell. Default configurations must be well-chosen to avoid decision paralysis.
- **Data accuracy liability.** If our sync is wrong, the customer may lose sales or anger customers. Clear ToS and error handling are essential.
- **Pricing / willingness to pay.** Untested — needs customer discovery in parallel with build.

---

## 12. Next Steps After This PRD

1. Review and approve this PRD (Eli, Yehuda).
2. Customer discovery: interview 3–5 potential store owners to validate problem severity, price sensitivity, and supplier landscape.
3. Technical design / architecture document — decide implementation approach for Track B (supplier integration without API).
4. Define pricing tiers and which configuration options are gated.
5. Define MVP build scope within v1 (smallest slice that can serve a first paying customer).
6. Build plan with milestones.

---

_End of document._
