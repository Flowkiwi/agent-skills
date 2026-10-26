# Core: products, options, variants

Schemas below were checked on 2026-10-26. If a call is rejected for an unknown or missing field, run `describe_operation` on it and adapt; do not guess.

## Effort

~5 min of developer time plus answering consolidation questions.

## Collect

The Shopify product CSV only (`references/collect.md`).

## Map and consolidate

| Shopify (`products.json`)  | Flowkiwi                                                                                                                                                                  |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `title`                    | `create_product` `title` (max 255)                                                                                                                                        |
| `description_html`         | `description` (HTML kept as is; omit when null, `minLength` is 1)                                                                                                         |
| `tags`                     | `tags`: joined with `,` without spaces; omit when empty                                                                                                                   |
| `status`                   | `status`: `/api/statuses/DRAFT` when `status_mode` is `draft`, else `decisions.status_map` first, then `active` -> `ACTIVE`, `draft` -> `DRAFT`, `archived` -> `ARCHIVED` |
| `options[].name`           | `create_product_option` `title` (min 3 chars), `position` = index                                                                                                         |
| `options[].values[]`       | `create_option_value` `value`, `productOption`, `position` = index                                                                                                        |
| `variants[]`               | `create_variant` `sku`, `barcode` (both required)                                                                                                                         |
| `variants[].option_values` | `create_variant` `optionValues[]` (IRIs of the matching option values)                                                                                                    |
| `handle`                   | marker metafield `shopify.handle` on the product                                                                                                                          |

`payload.py` applies this table: workers copy its output instead of mapping by hand. The table stays for the dry-run and for review.

Not migrated by core: images (kept in `products.json`), variant weight (`grams`: Flowkiwi variants have no weight field). Say it once in the dry-run.

Consolidation questions, one at a time, in this order. Renames and skips go into `fixes.json` (`references/state.md`), never into `products.json` directly:
- **non_sellable** first (no price, no Shopify SKU, no type: recipes, content pages, samples stored as products). They also raise `missing_sku` and often `missing_barcode`, so settle them before anything else. Show the count and a few examples (title, handle, metafields), and ask whether to skip them all with one rule: `"skip_rules": [{"non_sellable": true}]`. Other rules match a metafield prefix (`{"metafield_prefix": "custom.recipe_"}`) or a handle pattern (`{"handle_pattern": "^recipe-"}`); a product matching any rule is skipped. `codes.py fix` prints how many products each rule skipped; run `state.py sync` after.
- **unknown_status** (e.g. `unlisted`): only matters when `status_mode` is `shopify`. Ask the Flowkiwi status for each unknown value and record `state.py decide <dir> --key status_map --value-json '{"unlisted": "DRAFT"}'` (`payload.py` applies it).
- **option_name_case** (`Size` vs `size` across products): propose the majority form given in the warning, as an `option_names` fix for every product (`{"option_names": {"*": {"size": "Size"}}}`) or per handle.
- **duplicate_sku**: SKUs must be unique. Show both products; the developer picks: rename one (`skus` fix, e.g. suffix `-2`), or skip a product (`skip` fix).
- **short_option_name**: Flowkiwi needs 3+ characters. Propose a rename (`Sz` -> `Size`) as an `option_names` fix.
- **missing_sku / missing_barcode**: `codes.py preview` then `codes.py fill` (SKILL.md Phase 2 step 5). Default barcode: EAN-13, prefix `2`, sequential. Default SKU pattern: `{handle}-{index}`, uppercased.
- **SKUs or barcodes already in the tenant** (checked after fill): `call_operation list_variants` with `query.sku[]` or `query.barcode[]` (batches of 50). These filters are **partial matches** (`TS-S` also returns `TS-S-2`): only exact `sku` / `barcode` equality is a collision. `list_variants` does not accept `expand`: each hit carries its `product` IRI, read it with `get_product` to show the product. The developer picks: skip the Shopify product (`skip` fix), rename the SKU (`skus` fix), or, when it is the same product, adopt it: `state.py decide <dir> --key adopt --value-json '{"<handle>": "<product IRI>", ...}'` (the whole map each time). A clashing barcode cannot be renamed through `fixes.json`: skip the product, or adopt it when it is the same product, or fix the barcode in Shopify and re-export.
- **Products already migrated**: `list_products` with `query.metafield["shopify.handle"]` for a few handles. Hits on a fresh workspace mean a previous migration ran on this tenant: warn and let the developer decide (reuse is automatic per product).

## Shared elements

The marker definition, before any product:
1. `call_operation list_metafield_definitions` and look for namespace `shopify`, key `handle`, owner type products.
2. If missing, `call_write_operation create_metafield_definition`:
   `{"body": {"namespace": "shopify", "key": "handle", "name": "Shopify handle", "description": "Technical marker set by the Shopify migration", "ownerType": "/api/metafield_owner_types/products", "type": "/api/metafield_definition_types/single_line_text_field"}}`
3. `state.py shared <dir> --key metafield_definition:shopify.handle --iri <iri> --status created|reused --module core`.

## Per product

The worker runs this for each handle of its worklist, in order. Every item created or reused is collected for the result line, tagged `"module": "core"`.

**Payloads.** First run `python3 $SKILL/scripts/payload.py <dir> <handle>`: it prints every body below (product, marker, options, variants, metafields). Build each MCP argument from that output, copying values verbatim: never retype them from `products.json`, never fix spelling, whitespace or HTML, and keep `\uXXXX` escapes as they are in the JSON arguments. Only IRIs returned at runtime (product, options, option values) are added by the worker. If `payload.py` fails (missing decision or shared IRI), fail the product with its message: never guess a value.

**Created vs reused.** An item is `created` when this migration made it: its `createdAt` is at or after `started_at` (compare full timestamps), or it is listed as `created` in `state.json` for this handle. Anything older is `reused`. This matters after a crash: items found again must stay `created` so undo removes them.

1. **Skip** if `state.json` says `created` (and this is not a module-only run).
2. **Adopted product**: if `decisions.adopt[<handle>]` exists (the developer chose to map this handle to an existing tenant product during consolidation), use that product: write the marker (step 6) if missing, record the product `reused`, continue at step 7.
3. **Find by marker**: `list_products` with `query.metafield["shopify.handle"] = <handle>`. Found: record it per the created/reused rule above and only create what is missing (compare options, variants by SKU).
4. **Orphan lookup**, always when the marker lookup found nothing (a previous worker may have died between creating the product and writing its marker): `list_products` with `query.title = <title>` and `query["createdAt[after]"] = <date part of started_at>`. Keep only candidates whose title is **exactly** equal and whose `createdAt` timestamp is at or after `started_at`, then check their metafields (`call_operation list_metafields` on `products/<id>`). A candidate **without** a `shopify.handle` metafield is an orphan of this migration: adopt it (record `created`) and continue at step 6. More than one candidate: fail this product and report it (the developer decides).
5. **Create the product**: `call_write_operation create_product` with the payload `product.body`, plus the product-level fields of the module sections (category, channels, collections, attributes). **On error or timeout, do not retry blindly**: run the orphan lookup (step 4) first; retry the creation once only if it finds nothing.
6. **Marker**: `call_write_operation create_metafield` with `path {"ownerResource": "products", "ownerId": <id>}` and the payload `marker.body`. The value is translatable: never pass `locale`, so it is written and looked up in the tenant default locale. Record `metafield`.
7. **Options**: `list_product_options` on the product first (after an adoption or a partial failure some exist). For each missing option of the payload `options`, `create_product_option` (`path.productId`, `title`, `position`), then `create_option_value` per missing value (`value`, `position`, `productOption` = the option IRI). Record `product_option` and `option_value` items.
8. **Variants**: for each variant of the payload `variants`, `list_variants` with `query.sku = <sku>` (partial match: only a returned `sku` exactly equal counts): on this product, record it per the created/reused rule; on another product, fail this product with a clear error. Else `create_variant` (`path.productId`, `sku`, `barcode`, `optionValues` = IRIs of the option values named in `option_values`, the n-th name belonging to the n-th option). Record `variant`. Keep a map `sku -> variant IRI` for the module sections.
9. **Description check**, only when the handle is in `<dir>/verify-sample.json` and the product was created in this run: `get_product`, write its `description` to `<work dir>/readback.txt` with a quoted heredoc, as the JSON string literal of the response (quotes and escapes included), then `python3 $SKILL/scripts/verify.py <dir> <handle> --field description --readback-file <work dir>/readback.txt`. Exit 0: `"verify": "ok"`. Exit 1: `update_product` once with the payload description, read back and verify again: `"fixed"` if it now matches, else `"mismatch"` (the product stays `created`). Add the `verify` key to the result line.
10. **Module sections**: run each `## Per product` section included below, in order.
11. **Result**: write the line to `<work dir>/line.json` with a quoted heredoc, then `python3 $SKILL/scripts/state.py result <result file> --json-file <work dir>/line.json`. Line: `status: created`, the product IRI and all items, including a `product` item for the product itself. Store every `@id` exactly as the API returned it (option values are not nested under the product, options and variants are): `state.py result` rejects IRIs that are not `/api/<resource>/<uuid>`. On an unrecoverable error: `status: failed`, the error message, and the items created so far. In `module-only` mode, add `"module": "<module name>"` to the line (one line per module): a failure is then recorded for that module only and the product stays `created`. Then go to the next handle: one failure never stops the worklist.

**Limits.** Every MCP argument is written by the model, even when copied from `payload.py`: invisible characters (no-break spaces, zero-width spaces) can still be dropped or replaced, and a retyped IRI can still be wrong. `payload.py` escapes, `verify.py` samples and detects, `state.py result` rejects malformed IRIs: this reduces and detects drift, it cannot guarantee a byte-exact copy. The final report shows the verification counts.

## Worker prompt template

Fill the placeholders. In parallel mode, send one prompt per worklist to a subagent, all at once. In sequential mode, fill it for `worker-1` and follow it yourself.

````
You are a worker of a Shopify -> Flowkiwi catalog migration. You create products in Flowkiwi through the Flowkiwi MCP tools (dedicated tools, or search_operations / describe_operation / call_operation / call_write_operation; your client may prefix their names, e.g. `mcp__flowkiwi__list_products`, and may need you to load them first). The developer already approved the mapping: do not ask questions, do not change any decision.

Inputs:
- Workspace: {{DIR}} (`<dir>` below)
- Your handles, in order: {{WORKLIST_PATH}}
- Normalized products: {{PRODUCTS_PATH}} (read only the entries of your handles)
- Decisions and shared element IRIs: {{STATE_PATH}} (`decisions`, `shared`, and `products.<handle>` for items from earlier attempts). Read only, never write it.
- Handles whose description is checked after creation: {{DIR}}/verify-sample.json
- Migration start date (for the orphan rule): {{MIGRATION_STARTED_AT}}
- Your result file: {{RESULT_PATH}}
- Your work dir: {{WORK_DIR}} (`<work dir>` below)
- Mode: {{MODE}} (`full`: core procedure then module sections; `module-only`: the product already exists, use `products.<handle>.iri` and run only the module sections)

Rules:
- Process handles one by one. For each, follow the procedure below, then append exactly one result line:
  cat > {{WORK_DIR}}/line.json <<'LINE_EOF'
  <line>
  LINE_EOF
  python3 {{SKILL}}/scripts/state.py result {{RESULT_PATH}} --json-file {{WORK_DIR}}/line.json
  Line format: {"handle", "status": "created"|"failed", "iri", "items": [{"kind", "iri", "status": "created"|"reused", "module"}], "error"}, plus "verify" when the description was checked. If the command rejects the line, fix the line (never the IRIs you did not read from a response) and run it again.
- Build every MCP argument from `python3 {{SKILL}}/scripts/payload.py {{DIR}} <handle>`: copy values verbatim, never retype them from products.json, never fix spelling or whitespace, keep `\uXXXX` escapes as is in the JSON arguments.
- Write helper and temporary files only in {{WORK_DIR}} (`mkdir -p` it first), never in a shared scratch dir, always with a quoted heredoc (`<<'EOF'`). Never name shell variables `path`, `PATH`, `HOME` or other environment names.
- Look up before every creation. After any error or timeout on a write, look up before retrying. Never create duplicates.
- Never modify or delete anything you did not create in this run, except adopting an orphan as described.
- Never create media. Never search for bulk operations.
- One product failing never stops the others.
- When done, reply with 3 lines only: handles processed, created/failed counts, failed handles with a short reason.

Core procedure:
{{CORE_PER_PRODUCT}}

Module sections:
{{MODULE_SECTIONS}}
````

`{{CORE_PER_PRODUCT}}` is the mapping table of `## Map and consolidate` followed by the `## Per product` section above (omit both in `module-only` mode), with `$SKILL` replaced by the skill path. `{{MODULE_SECTIONS}}` is, for each chosen module in menu order, its mapping or conversion rules followed by its `## Per product` section. Workers only see this prompt: anything they need to compute a payload must be in it.

## Verify

After the pilot: `get_product` on each pilot product, then `list_product_variants` and compare titles, options and variant SKUs/barcodes with `products.json`. Show the comparison to the developer.

## Undo notes

Product deletion may cascade to its options, variants and metafields: check with a read before deleting children (`references/undo.md`). The marker definition is deleted last, and only on a full undo.
