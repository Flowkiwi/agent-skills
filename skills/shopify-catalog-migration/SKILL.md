---
name: shopify-catalog-migration
description: Use when onboarding a Flowkiwi client whose catalog lives in a Shopify store, to rebuild that catalog in their Flowkiwi tenant through the Flowkiwi MCP from Shopify backoffice exports (no Shopify API), or to resume, extend, retry or undo such a migration.
compatibility: Validated with Claude Code. Written for any Agent Skills client with shell access and MCP (Codex, Cursor, Gemini CLI...), untested there; runs workers sequentially when the client cannot spawn subagents.
metadata:
  author: Flowkiwi
---

# Shopify catalog migration

Guide a Flowkiwi developer from Shopify backoffice exports to a catalog created in a Flowkiwi tenant, **through the Flowkiwi MCP only**. Start from the strict minimum (products + variants) and offer the rest as optional modules. Keep it simple for the developer: one question at a time, effort announced before each module.

`$SKILL` below is this skill's folder: the base directory given when the skill loads (for example `.claude/skills/shopify-catalog-migration` or `.agents/skills/shopify-catalog-migration` in a project, the same under the home directory when installed globally, or a folder of the plugin cache when installed as a Claude Code plugin). Always use the base directory given at load time, never a guessed path. Scripts are run as `python3 $SKILL/scripts/<name>.py`. Migration workspaces are created under the current working directory, never inside `$SKILL`.

## Prerequisites

- An agent with shell access and an MCP client. Subagents are optional (see "Running workers").
- `python3` 3.8+ (scripts use the standard library only). Check once with `python3 --version`; if the command does not exist (often on Windows), use `python` or `py -3` instead everywhere this skill says `python3`, including in worker prompts.
- The Flowkiwi MCP server connected to the target tenant (`https://<tenant>.product-management.<env>.flowkiwi.net/api/mcp`, OAuth). If its tools are still missing after one retry (see "Start or resume") or ask for authentication, stop and give the developer the setup for their agent from `references/setup.md`.

## Hard rules

- **No write before confirmation.** Nothing is created in Flowkiwi before the developer approved the dry-run summary (Phase 3).
- **Check before create, check before retry.** Every creation is preceded by a lookup (state, then Flowkiwi). After any failed or timed-out write, look up before retrying. Never create a duplicate.
- **Never modify tenant items you did not create**, except reusing them as-is (recorded `reused`) or adopting an orphan per `references/core.md`.
- **State after each step.** Shared elements go through `state.py shared`, products through worker result files merged with `state.py merge`. Never edit `state.json` by hand.
- **Images are out of scope.** Never try to create media; image URLs stay in `products.json` and are listed in the final report.
- **No bulk operation exists.** Do not search for one.
- **One data language.** Every name or label the migration writes that does not come verbatim from the export (categories, attribute definitions and options, metafield definition names and descriptions, the marker definition, collections, option labels...) is in the data language recorded in Phase 2 (`decisions.data_language`), never in English by default and never in the language of this conversation. The Shopify standard taxonomy (`Product Category`) is always exported in English: translate it.
- Only load a module reference when the developer chose that module.

## Start or resume

1. Check the MCP: call `list_locales`, `list_channels`, `list_countries`. Show the tenant (MCP URL from the agent's MCP configuration, or ask the developer for it; default locale, channels, countries). If the Flowkiwi tools are missing at first, the MCP may still be connecting: wait a few seconds (`sleep 5`) and search the tools again once before concluding. Still missing or not connected: stop and say so.
2. Ask the client slug. Workspace: `migrations/<client-slug>/` (layout in `references/state.md`).
3. If `migrations/<client-slug>/state.json` exists: run `state.py merge` then `state.py summary`, show it, and offer: **continue**, **add a module**, **retry failed**, **undo** (`references/undo.md`). Otherwise start Phase 1.

## Phase 1 - Collect

Read `references/collect.md`. Ask only for the core export first (Shopify product CSV into `exports/`). Then show the module menu and let the developer pick any, none, or come back later:

| Module            | Brings                                     | Effort      | Reference                        |
| ----------------- | ------------------------------------------ | ----------- | -------------------------------- |
| core (always)     | products, options, variants                | ~5 min      | `references/core.md`             |
| prices            | selling prices per country (TTC or HT)     | low         | `references/prices.md`           |
| costs-suppliers   | suppliers and purchase costs               | low         | `references/costs-suppliers.md`  |
| channels          | product to sales channel links             | low         | `references/channels.md`         |
| taxonomy          | categories, attributes, attribute values   | medium      | `references/taxonomy.md`         |
| collections       | collections and memberships                | medium/high | `references/collections.md`      |
| metafields        | metafield definitions and values           | medium/high | `references/metafields.md`       |
| translations      | other locales (only if tenant has several) | medium      | `references/translations.md`     |

Never push a module. For each chosen module, ask for its extra inputs as described in its `## Collect` section.

## Phase 2 - Consolidate

Work inside the workspace. `$S` = `python3 $SKILL/scripts`.

1. **Parse**: `$S/shopify_csv.py exports/products.csv products.json`. It prints counts and warnings grouped by code. It refuses to overwrite an existing `products.json` (it holds consolidation work): on a new export, see "New export on resume" below.
2. **State**: `$S/state.py init <dir> --client <slug> --mcp-url <url>`. After any `codes.py fix` that skips products, run `state.py sync <dir>` so skipped handles leave the worklists. Then the **data language**: detect it from a few titles and descriptions of `products.json`, compare it with the tenant default locale, and ask the developer to confirm (they differ: ask which one wins). Record `state.py decide <dir> --key data_language --value-json '"fr"'`. From now on, every name you propose (category tree, attribute names, option labels from slugs, metafield definition names) is written in that language, and the dry-run lists them for review. For a language `plan_shared.py` does not know (en, fr, de, es, it), also record `marker_labels` (`{"name": "...", "description": "..."}`) in that language.
3. **Status**: ask "Create products as DRAFT (recommended, you publish later) or keep the Shopify statuses?" and record `state.py decide <dir> --key status_mode --value-json '"draft"'` (or `'"shopify"'`).
4. **Fixes**: resolve the warnings with the developer, following `references/core.md` `## Map and consolidate`. Every rename or skip goes into `fixes.json` (format in `references/state.md`), applied with `$S/codes.py fix products.json fixes.json`, then `$S/shopify_csv.py --validate products.json` to refresh the warnings. Never edit `products.json` by hand.
5. **Codes**: `$S/codes.py preview products.json --prefix 2 --start <n> --sku-pattern "{handle}-{index}"` shows how many barcodes / SKUs are missing and the exact values (SKUs are uppercased). After validation, run `codes.py fill` with the same arguments, then record the format **and the printed `next_barcode_start`**: `state.py decide <dir> --key codes --value-json '{"prefix": "2", "next_start": 6, "sku_pattern": "{handle}-{index}"}'`. `<n>` is `codes.next_start` from the state when it exists, else 1.
6. **Tenant collisions** (after fill, so generated codes are checked too): `list_variants` with `query.sku[]` and with `query.barcode[]` (batches of 50) on all SKUs and barcodes, plus a few `list_products` by marker. Handle hits per `core.md`.
7. **Modules**: run `## Map and consolidate` of each chosen module, one question at a time. Record `modules` (list) and each module's choices under its own key with `state.py decide`.

### New export on resume

`$S/shopify_csv.py exports/<new>.csv products.new.json`, `$S/codes.py fix products.new.json fixes.json`, `$S/codes.py fill products.new.json --previous products.json --prefix <p> --start <codes.next_start> [--sku-pattern ...]` (keeps the codes generated earlier, generates only for new variants), `$S/shopify_csv.py --validate products.new.json`, review warnings, then replace `products.json` with it and run `state.py sync <dir>`. Update `codes.next_start`.

## Phase 3 - Create

1. **Blocking check**: `$S/shopify_csv.py --validate products.json`. Do not go further while any `duplicate_sku`, `missing_sku` or `missing_barcode` remains, or `short_option_name` (it would fail every affected product), or an `unknown_status` without a `status_map` entry when `status_mode` is `shopify`: they break the uniqueness lookups and parallel workers would race on the same SKU.
2. **Dry-run summary**: data language, counts per resource to create, shared elements to create or reuse (with every generated name: check none is left in another language than `data_language`), what is not migrated (images, variant weight, metafields missing from the export), with the metafields module the table of Shopify type vs Flowkiwi type per definition (each type validated explicitly: it cannot be changed later), 2-3 fully mapped sample products as MCP payloads (`$S/payload.py <dir> <handle> --dry-run`, which prints `<key IRI>` placeholders for shared elements not created yet; IRIs that only exist after an earlier call, like `<product IRI>`, stay placeholders). Wait for explicit approval.
3. **Shared elements**: `$S/plan_shared.py <dir>` prints the ordered plan from the decisions (core marker definition, categories parents first, attribute definitions and options, metafield definitions) with each body and state key, skipping keys already recorded. Create them in this session, or delegate to one dedicated worker (`references/core.md` `## Shared elements worker`, `{{WORK_DIR}}` = `<workspace>/tmp/shared/`), looking up before each creation; record them with `state.py shared <dir> --file <results.json>` (rerun `plan_shared.py --ready` for the entries whose parents are now recorded). Then the `## Shared elements` of the other chosen modules (prices, costs-suppliers, channels, collections), in menu order, recorded with `state.py shared`.
4. **Pilot**: `state.py worklists <dir> --workers 1 --pilot` (picks up to 5 representative products: several and single variants, with and without category, metafields, each status, longest description; or `--handles a,b,c` for an explicit choice), then `$S/verify.py sample <dir> --rate 0.1 --output <dir>/verify-sample.json` (pilot products are always in the sample), dispatch the worker, `state.py merge`. Show the pilot products (`get_product`) and ask the developer to check them in Flowkiwi. If something is wrong: undo the pilot (`references/undo.md`, scope `pilot`), fix the mapping, `state.py reset-deleted`, rerun. A wrong metafield type only: undo scope `shared:<key>` instead (`references/undo.md`).
5. **Full run**: `state.py worklists <dir> --workers 4` (default 4, at most 6: more workers hit the MCP rate limit), `verify.py sample` as above, dispatch the workers, `state.py merge`, repeat until no `pending` product remains.
6. **Final report** from `state.py summary`: created / reused / failed per kind, failures with errors and a retry offer (`worklists --retry-failed`), description checks (`verify` counts: ok / fixed / mismatch, with the mismatched handles to review by hand), lines rejected by `merge` (`results/rejected-*.jsonl`), generated barcodes and SKUs count, images not migrated (count; URLs in `products.json`), modules not run that could be added later.

## Running workers

Two modes, same files and same procedure:

- **Parallel** (the agent can spawn subagents, e.g. the Agent tool in Claude Code): one subagent per `worklists/worker-<n>.json`, all launched at once so they run in parallel.
- **Sequential** (no subagents, or the developer prefers it): always `state.py worklists <dir> --workers 1`, then **you** are the worker: follow the worker prompt below yourself for `worker-1.json`, appending one result line per product. Run `state.py merge` every 20 products so an interruption loses little. Keep tool outputs short (request only the fields you need). For large catalogs (several hundred products), suggest continuing in a fresh session from time to time: resuming is safe (merge, summary, continue).

In both modes:

- Build each prompt from the worker template in `references/core.md`: absolute paths for `{{SKILL}}`, `{{DIR}}` (the workspace), `{{WORKLIST_PATH}}`, `{{PRODUCTS_PATH}}`, `{{STATE_PATH}}`, `{{RESULT_PATH}}` (`results/worker-<n>.jsonl`) and `{{WORK_DIR}}` (`<workspace>/tmp/worker-<n>/`, one per worker), the full `started_at` timestamp for `{{MIGRATION_STARTED_AT}}`, `full` or `module-only` for `{{MODE}}`, core's mapping table (from `## Map and consolidate`) followed by its `## Per product` text for `{{CORE_PER_PRODUCT}}`, and for `{{MODULE_SECTIONS}}`, per chosen module (in menu order): its `## Per product` section, preceded by the mapping or conversion rules from its `## Map and consolidate` for the modules `payload.py` does not compute (prices, costs-suppliers, channels, collections, translations; workers never read the references).
- Workers (or you, in sequential mode) append one line per product to `results/worker-<n>.jsonl` with `state.py result` and return a 3-line summary only. Do not ask them for details: read the state instead.
- After all workers return (or after each sequential batch), run `state.py merge` before anything else. It rejects lines whose handle is not in that worker's worklist, or claimed by several workers with different IRIs, and writes them to `results/rejected-<stamp>.jsonl`: show them to the developer.

## Adding a module later

Run its Collect and Map sections, its shared elements (`plan_shared.py` covers taxonomy and metafields), then `state.py worklists <dir> --workers 4 --module <name>`: it lists `created` products that have no item of that module yet. Workers then execute only that module's `## Per product` section, using the product IRIs from `state.json`. Items are tagged with the module name so it can be undone on its own.
