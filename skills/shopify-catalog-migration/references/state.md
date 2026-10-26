# Workspace and state

## Layout

```
migrations/<client-slug>/
├── exports/            # raw files from the Shopify backoffice (never modified)
├── products.json       # normalized products (shopify_csv.py, codes.py fix, codes.py fill)
├── fixes.json          # consolidation renames / skips, replayed on a new export
├── state.json          # decisions, shared elements, per-product progress
├── worklists/          # worker-<n>.json: handles assigned to each worker
├── results/            # worker-<n>.jsonl: one result line per product, written by workers
└── undo/               # deleted-<timestamp>.json: IRIs deleted by an undo run
```

All script commands below take the workspace folder as `<dir>`.

## products.json

`{"products": [...], "warnings": [...]}`. One product entry per Shopify handle (`row` is the first CSV line of the product):

```json
{
  "handle": "tshirt-basic", "row": 2, "title": "T-shirt basic", "description_html": "<p>...</p>",
  "vendor": "Acme", "type": "T-shirts", "product_category": "Apparel & Accessories > Clothing",
  "tags": ["cotton"], "status": "active", "published": true,
  "options": [{"name": "Size", "values": ["S", "M"]}],
  "variants": [{"sku": "TS-S", "sku_generated": false, "barcode": "2000000000015", "barcode_generated": true,
                "option_values": ["S"], "price": "19.90", "compare_at_price": null, "cost": "6.00",
                "grams": 200, "taxable": true, "market_prices": {"France": {"price": "19.90", "compare_at_price": null}}}],
  "images": ["https://cdn.shopify.com/..."],
  "metafields": {"custom.material": "Cotton"}
}
```

Warning entry: `{"row": 7, "handle": "cap", "code": "duplicate_sku", "message": "SKU 'TS-S' already used by 'tshirt-basic'"}`. Codes: `duplicate_sku`, `missing_sku`, `missing_barcode`, `short_option_name`, `orphan_row` (only at parse time). `shopify_csv.py --validate products.json` recomputes them after fixes.

Not migrated, but kept: `images` (no media creation through the MCP), `grams` (Flowkiwi variants have no weight field), `published` / `vendor` / `type` unless a module uses them.

## fixes.json

Every consolidation change, so it can be replayed on a new export:

```json
{"skus": {"cap": {"TS-S": "TS-S-2"}}, "option_names": {"poster": {"Sz": "Size"}}, "skip": ["old-product"]}
```

Applied with `codes.py fix products.json fixes.json` (idempotent).

## state.json

```json
{
  "version": 1, "client": "acme", "mcp_url": "https://...", "started_at": "2026-10-26T10:00:00Z",
  "decisions": {"modules": ["core", "prices"], "status_mode": "draft", "codes": {"prefix": "2", "next_start": 6, "sku_pattern": "{handle}-{index}"}},
  "shared": {"category:T-shirts": {"iri": "/api/categories/...", "status": "created", "module": "taxonomy"}},
  "products": {"tshirt-basic": {
    "status": "pending", "iri": null, "pilot": false, "error": null,
    "items": [{"kind": "variant", "iri": "/api/...", "status": "created", "module": "core"}]
  }}
}
```

- Product status: `pending`, `created`, `failed`, `deleted`, `skipped` (handle no longer in `products.json`; back to `pending` if it reappears).
- `module_errors`: `{"<module>": "<error>"}` from module-only runs; they never change the product status.
- `decisions.adopt`: `{"<handle>": "<tenant product IRI>"}` for Shopify products mapped to an existing tenant product during consolidation.
- Item status: `created` (made by this migration, undoable), `reused` (existed before, never deleted), `deleted`.
- Shared keys are `<kind>:<name>`, e.g. `category:T-shirts`, `supplier:Acme`, `metafield_definition:shopify.handle`.
- Item kinds, in undo order: `sale_offer`, `purchase_offer`, `metafield`, `variant`, `option_value`, `product_option`, `*_link`, `product`, `collection`, `attribute_option`, `attribute_definition`, `category`, `supplier`, `tax`, `metafield_definition`, `channel`, `country`.
- `*_link` items (`channel_link`, `collection_link`, `taxonomy_link`) record a relation set on the product, not a resource. Their IRI is `<product iri>#<relation>`. Undo removes the relation with `update_product`, or skips it when the product itself is deleted.

## Worker result line

```json
{"handle": "tshirt-basic", "status": "created", "iri": "/api/products/...", "error": null,
 "items": [{"kind": "product", "iri": "/api/products/...", "status": "created", "module": "core"}]}
```

`status` is `created` or `failed`. On failure, still list the items created before the failure so they can be reused or undone. Module-only runs add `"module": "<name>"`: the line then only adds items and a per-module error.

`merge` moves merged files to `results/merged/`, so a result line is never applied twice (e.g. after an undo).

## Commands

```bash
S=$SKILL/scripts
python3 $S/state.py init <dir> --client acme --mcp-url https://...   # refuses if state.json exists
python3 $S/state.py sync <dir>                                       # new handles from products.json
python3 $S/state.py decide <dir> --key status_mode --value-json '"draft"'
python3 $S/state.py shared <dir> --key category:T-shirts --iri /api/categories/1 --status created --module taxonomy
python3 $S/state.py worklists <dir> --workers 4 [--limit 5 --pilot] [--retry-failed] [--module prices]
python3 $S/state.py result <dir>/results/worker-1.jsonl --json '<result line>'
python3 $S/state.py merge <dir>
python3 $S/state.py undo-plan <dir> --scope all|pilot|module:<name>
python3 $S/state.py deleted <dir> --iris-file <dir>/undo/deleted-<timestamp>.json
python3 $S/state.py reset-deleted <dir>                              # deleted products back to pending
python3 $S/state.py summary <dir>
```

`worklists` first merges any unmerged results, then writes fresh worklists. Products sharing the same title always go to the same worker, so the orphan rule can never adopt another worker's product.

## Resume rules

- Resuming = invoking the skill again: `merge`, then `summary`, then continue.
- A product with no result line (worker died mid-product) stays `pending`. The next worker run finds it by marker (or adopts the orphan) before creating anything, per `core.md`.
- A `failed` product is only retried with `--retry-failed`. Its earlier items are in the state: the worker reuses them.
