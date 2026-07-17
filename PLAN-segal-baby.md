# PLAN — Segal Baby onboarding (implementation)

Field mapping & scope: **`PRD-segal-product-sync.md`** is the source of truth.
This plan is the *build* sequence. TDD (red → green) per phase, mirroring the Laura pipeline.

## Reuse (unchanged)
`store_products` lifecycle (draft → approve/ignore → activate), `reconcile.py`,
`ensure_collection` / `add_to_collection` / `delete_product`, the ingest/reconcile
workflows, and the skip-dominant ingest shape from `laura_ingest.py`.

## New / extended
- **domain**: `Metafield` type; `ProductDraft` gains `metafields`, `template_suffix`;
  `VariantSpec` gains `inventory_quantity` (Segal has real stock at create time).
- **`segal_source.py`** (pure): `SegalTab`, `SegalProduct`, `parse_api_product(dict)`,
  `parse_tabs(html)` — no I/O, tested against the real sample JSON/HTML.
- **`segal_mapping.py`** (pure): tab-label → metafield routing (discard+log unknown),
  HTML → `rich_text` JSON, HTML-entity decode, category → product_type/collections,
  stock/price mapping, `to_product_draft(product)`.
- **`adapters/segal_baby.py`**: `SegalBabyStoreApiAdapter` — paginate
  `products?category=<id>`, fetch + `parse_tabs` per product (1 extra GET/product).
- **`segal_ingest.py`**: discover 11 categories → dedup by SKU → skip existing →
  create draft (+ metafields, collections, initial stock) → `write_pending`.
- **shopify `create_product`**: write `metafields` + `template_suffix`; set
  `inventory_management="shopify"` when a variant carries `inventory_quantity`.
- **CLI + workflow**: `segal-ingest` subcommand; reuse reconcile for activate/ignore.

## Phases (TDD each)
- **Phase 0 — foundation**: domain extensions + `segal_source` parsing (API product + tabs). ← start here
- **Phase 1 — mapping**: tab routing, rich_text, entity decode, category map, `to_product_draft`.
- **Phase 2 — adapter**: Store API pagination + per-product tab scrape (fake httpx transport).
- **Phase 3 — ingest**: dedup / skip / create / record, error-isolated + dry-run.
- **Phase 4 — store write**: `create_product` metafields/template_suffix/stock; contract tests.
- **Phase 5 — wiring**: CLI subcommand + `inventory-ingest`-style workflow.

## Scope (from PRD §0)
Brand `segal-baby` (16) only; 11 categories; ~150 pre-dedup. Deferred: segal-kids,
rooms, shop-the-look, outlet/outlet-sale/spare-parts (PRD §10).
