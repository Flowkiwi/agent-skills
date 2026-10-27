# Create

Phase 3: from an approved `products.json` to products in the Flowkiwi tenant. `$S` is `python3 $SKILL/scripts` and `<dir>` the workspace. Schemas below were checked on 2026-10-26. If a call is rejected for an unknown or missing field, run `describe_operation` on it and adapt; do not guess.

## Steps

1. **Blocking check**: `$S/catalog.py --check <dir>/products.json --state <dir>/state.json`. Do not go further while it exits 1: any `duplicate_key`, `duplicate_handle`, `missing_title`, `too_long`, `duplicate_sku`, `missing_sku`, `missing_barcode`, `short_option_name`, `duplicate_option_values`, or an `unknown_status` without a `status_map` entry when `status_mode` is `source`. They break the uniqueness lookups (parallel workers would race on the same SKU) or would fail every affected product. Settle them in Phase 2 (`references/products.md` `## Warnings`).
2. **Dry-run summary**: `$S/dryrun.py <dir>` (exit 1 while blocking warnings remain or `status_mode` / `data_language` is missing). Show its output: data language, status, counts to create, cleaning counts per rule, **agent choices** (every `"origin": "agent"` of the mappings and decisions, with its reason: the developer validates or corrects each one), generated codes, descriptions with up to 3 generated candidates, what is not migrated. Add the shared elements to create or reuse (`$S/plan_shared.py <dir>`, with every generated name: check none is left in another language than `data_language`), the data kept but not created (images, prices, columns not mapped), and 2-3 fully mapped sample products as MCP payloads: `$S/payload.py <dir> <handle> --dry-run` (pick several variants, a generated code or description, the longest description). It prints `<key IRI>` placeholders for shared elements not created yet; IRIs that only exist after an earlier call, like `<product IRI>`, stay placeholders. **Wait for explicit approval.**
3. **Shared elements**: `$S/plan_shared.py <dir>` prints the ordered plan from the decisions (the `import.key` marker definition in this version) with each `body`, `lookup`, `spec` and state key, skipping keys already recorded. Create them in this session, or delegate to one dedicated worker (`## Shared elements worker`, `{{WORK_DIR}}` = `<dir>/tmp/shared/`), looking up before each creation. Record them with `$S/state.py shared <dir> --file <results.json>`, each entry with the `spec` copied from its plan entry (rerun `plan_shared.py --ready` for the entries whose dependencies are now recorded).
4. **Pilot**: `$S/state.py worklists <dir> --workers 1 --pilot` (picks up to 5 representative products: several and single variants, with and without category, generated description, each status, longest description; or `--handles a,b,c` for an explicit choice), then `$S/verify.py sample <dir> --rate 0.1 --output <dir>/verify-sample.json` (pilot products are always in the sample), dispatch the worker (SKILL.md "Running workers"), `$S/state.py merge <dir>`. Show the pilot products (`## Verify`) and ask the developer to check them in Flowkiwi. If something is wrong: undo the pilot (`references/undo.md`, scope `pilot`), fix the mapping or the decision, extract again, `$S/state.py reset-deleted <dir>`, rerun the pilot.
5. **Full run**: `$S/state.py worklists <dir> --workers 4` (default 4, at most 6: more workers hit the MCP rate limit), `verify.py sample` as above, dispatch the workers, `state.py merge`, repeat until no `pending` product remains.
6. **Final report** from `$S/state.py summary <dir>`: created / reused / failed per kind, failures with errors and a retry offer (`state.py worklists <dir> --workers 4 --retry-failed`), description checks (`verify` counts: ok / fixed / mismatch, with the mismatched handles to review by hand), lines rejected by `merge` (`results/rejected-*.jsonl`), generated barcodes and SKUs count, generated keys (products without reference, for the client), decisions per origin and the agent choices to confirm with the client, images not migrated (count; URLs in `products.json`), data kept for later concepts (prices).

## Shared elements

The marker definition, before any product (first entry of `plan_shared.py`):

1. `call_operation list_metafield_definitions` and look for namespace `import`, key `key`, owner type products.
2. If missing, `call_write_operation create_metafield_definition` with the plan entry `body`, for example in `fr`:
   `{"body": {"namespace": "import", "key": "key", "name": "Clé d'import", "ownerType": "/api/metafield_owner_types/products", "type": "/api/metafield_definition_types/single_line_text_field", "description": "Marqueur technique posé par la migration de fichiers"}}`
   `name` and `description` are in the data language (`plan_shared.py` prints them, `marker_labels` overrides them). The namespace and key stay `import.key` in every language.
3. `$S/state.py shared <dir> --key metafield_definition:import.key --iri <iri> --status created|reused --module core --spec-json '<the plan entry spec>'`.

## Per product

The worker runs this for each handle of its worklist, in order. Every item created or reused is collected for the result line, tagged `"module": "core"`, with its `ref`: the SKU for a `variant`, the option title for a `product_option`, `<option title>:<value>` for an `option_value`, `namespace.key` for a `metafield` (`import.key` for the marker).

**Payloads.** First run `python3 $SKILL/scripts/payload.py <dir> <handle>`: it prints every body below (product, marker, options, variants). Build each MCP argument from that output, copying values verbatim: never retype them from `products.json`, never fix spelling, whitespace or HTML, and keep `\uXXXX` escapes as they are in the JSON arguments. Only IRIs returned at runtime (product, options, option values) are added by the worker. If `payload.py` fails (missing decision or shared IRI), fail the product with its message: never guess a value.

**Created vs reused.** An item is `created` when this migration made it: its `createdAt` is at or after `started_at` (compare full timestamps), or it is listed as `created` in `state.json` for this handle. Anything older is `reused`. This matters after a crash: items found again must stay `created` so undo removes them.

1. **Skip** if `state.json` says `created`.
2. **Adopted product**: if `decisions.adopt[<handle>]` exists (the developer chose to map this handle to an existing tenant product during Phase 2), use that product: write the marker (step 6) if missing, record the product `reused`, continue at step 7.
3. **Find by marker**: `list_products` with `query.metafield["import.key"] = <handle>`. Found: record it per the created/reused rule above and only create what is missing (compare options, variants by SKU).
4. **Orphan lookup**, always when the marker lookup found nothing (a previous worker may have died between creating the product and writing its marker): `list_products` with `query.title = <title>` and `query["createdAt[after]"] = <date part of started_at>`. Keep only candidates whose title is **exactly** equal and whose `createdAt` timestamp is at or after `started_at`, then check their metafields (`call_operation list_metafields` on `products/<id>`). A candidate **without** an `import.key` metafield is an orphan of this migration: adopt it (record `created`) and continue at step 6. More than one candidate: fail this product and report it (the developer decides).
5. **Create the product**: `call_write_operation create_product` with the payload `product.body`. **On error or timeout, do not retry blindly**: run the orphan lookup (step 4) first; retry the creation once only if it finds nothing.
6. **Marker**: `call_write_operation create_metafield` with `path {"ownerResource": "products", "ownerId": <id>}` and the payload `marker.body`. The value is translatable: never pass `locale`, so it is written and looked up in the tenant default locale. Record `metafield` with `"shared": "metafield_definition:import.key"` (the payload `marker.shared`) and `"ref": "import.key"`.
7. **Options**: `list_product_options` on the product first (after an adoption or a partial failure some exist). For each missing option of the payload `options`, `create_product_option` (`path.productId`, `title`, `position`), then `create_option_value` per missing value (`value`, `position`, `productOption` = the option IRI). Record `product_option` and `option_value` items.
8. **Variants**: for each variant of the payload `variants`, `list_variants` with `query.sku = <sku>` (partial match: only a returned `sku` exactly equal counts): on this product, record it per the created/reused rule; on another product, fail this product with a clear error. Else `create_variant` (`path.productId`, `sku`, `barcode`, `optionValues` = IRIs of the option values named in `option_values`, the n-th name belonging to the n-th option). Record `variant`.
9. **Description check**, only when the handle is in `<dir>/verify-sample.json` and the product was created in this run: `get_product`, write its `description` to `<work dir>/readback.txt` with a quoted heredoc, as the JSON string literal of the response (quotes and escapes included), then `python3 $SKILL/scripts/verify.py <dir> <handle> --field description --readback-file <work dir>/readback.txt`. Exit 0: `"verify": "ok"`. Exit 1: `update_product` once with the payload description, read back and verify again: `"fixed"` if it now matches, else `"mismatch"` (the product stays `created`). Add the `verify` key to the result line.
10. **Result**: write the line to `<work dir>/line.json` with a quoted heredoc, then `python3 $SKILL/scripts/state.py result <result file> --json-file <work dir>/line.json`. Line: `status: created`, the product IRI and all items, including a `product` item for the product itself. Store every `@id` exactly as the API returned it (option values are not nested under the product, options and variants are): `state.py result` rejects IRIs that are not `/api/<resource>/<uuid>`. On an unrecoverable error: `status: failed`, the error message, and the items created so far. Then go to the next handle: one failure never stops the worklist.

**Limits.** Every MCP argument is written by the model, even when copied from `payload.py`: invisible characters can still be dropped or replaced, and a retyped IRI can still be wrong. `payload.py` escapes, `verify.py` samples and detects, `state.py result` rejects malformed IRIs: this reduces and detects drift, it cannot guarantee a byte-exact copy. The final report shows the verification counts.

## Worker prompt template

Fill the placeholders (SKILL.md "Running workers"). In parallel mode, send one prompt per worklist to a subagent, all at once. In sequential mode, fill it for `worker-1` and follow it yourself.

````
You are a worker of a files -> Flowkiwi catalog migration. You create products in Flowkiwi through the Flowkiwi MCP tools (dedicated tools, or search_operations / describe_operation / call_operation / call_write_operation; your client may prefix their names, e.g. `mcp__flowkiwi__list_products`, and may need you to load them first). The developer already approved the mapping: do not ask questions, do not change any decision.

Inputs:
- Workspace: {{DIR}} (`<dir>` below)
- Your handles, in order: {{WORKLIST_PATH}}
- Products extracted from the source files: {{PRODUCTS_PATH}} (read only the entries of your handles)
- Decisions and shared element IRIs: {{STATE_PATH}} (`decisions`, `shared`, and `products.<handle>` for items from earlier attempts). Read only, never write it.
- Handles whose description is checked after creation: {{DIR}}/verify-sample.json
- Migration start date (for the orphan rule): {{MIGRATION_STARTED_AT}}
- Your result file: {{RESULT_PATH}}
- Your work dir: {{WORK_DIR}} (`<work dir>` below)

Rules:
- Process handles one by one. For each, follow the procedure below, then append exactly one result line:
  cat > {{WORK_DIR}}/line.json <<'LINE_EOF'
  <line>
  LINE_EOF
  python3 {{SKILL}}/scripts/state.py result {{RESULT_PATH}} --json-file {{WORK_DIR}}/line.json
  Line format: {"handle", "status": "created"|"failed", "iri", "items": [{"kind", "iri", "status": "created"|"reused", "module", "ref", "shared"?}], "error"}, plus "verify" when the description was checked. "ref" names the item in the product: the SKU of a variant, the title of an option, "<option title>:<value>" of an option value, "namespace.key" of a metafield. "shared" is the shared key an item depends on (the marker: given by payload.py). If the command rejects the line, fix the line (never the IRIs you did not read from a response) and run it again.
- Build every MCP argument from `python3 {{SKILL}}/scripts/payload.py {{DIR}} <handle>`: copy values verbatim, never retype them from the products file, never fix spelling or whitespace, keep `\uXXXX` escapes as is in the JSON arguments.
- Write helper and temporary files only in {{WORK_DIR}} (`mkdir -p` it first), never in a shared scratch dir, always with a quoted heredoc (`<<'EOF'`). Never name shell variables `path`, `PATH`, `HOME` or other environment names.
- Look up before every creation. After any error or timeout on a write, look up before retrying. Never create duplicates.
- On `rate limit exceeded` (HTTP 429): wait with `sleep 5`, then `sleep 15`, then `sleep 45` between attempts, and always look up before retrying a write (the first attempt may have gone through). Still limited after that: fail the product and go on.
- Never modify or delete anything you did not create in this run, except adopting an orphan as described.
- Never create media. Never search for bulk operations.
- One product failing never stops the others.
- When done, reply with 3 lines only: handles processed, created/failed counts, failed handles with a short reason.

Procedure:
{{PER_PRODUCT}}
````

`{{PER_PRODUCT}}` is the fields table of `references/products.md` `## Map` followed by the `## Per product` section above, with `$SKILL` replaced by the skill path. Workers only see this prompt: anything they need to compute a payload must be in it.

## Shared elements worker

The shared elements can be created in the main session, or by one dedicated worker (never several: later concepts add elements that depend on each other). Prompt:

````
You create the shared elements of a files -> Flowkiwi catalog migration through the Flowkiwi MCP tools (search_operations / describe_operation / call_operation / call_write_operation; your client may prefix their names). The developer already approved the plan: do not ask questions, do not change anything in it.

Loop until the plan is empty:
1. python3 {{SKILL}}/scripts/plan_shared.py {{DIR}} --ready
2. For each entry, in order: look up first as its "lookup" says (an exact match found: status "reused", its @id); else call_write_operation with the entry's "operation", "path" when present and "body" copied verbatim (keep \uXXXX escapes): status "created", the @id returned. On error or timeout, look up before retrying once. On `rate limit exceeded` (HTTP 429), wait with `sleep 5`, `sleep 15`, `sleep 45` and look up before retrying.
3. Write [{"key", "iri", "status", "module", "spec"}, ...] for the entries done to {{WORK_DIR}}/shared.json with a quoted heredoc (<<'EOF'), "module" and "spec" copied from the plan entry, then python3 {{SKILL}}/scripts/state.py shared {{DIR}} --file {{WORK_DIR}}/shared.json
Write temporary files only in {{WORK_DIR}}. Never modify or delete tenant items. When done, reply with 3 lines only: created count, reused count, failed keys with a short reason.
````

## Verify

After the pilot: `get_product` on each pilot product, then `list_product_variants` and compare titles, options and variant SKUs/barcodes with `products.json`. Show the comparison to the developer.
