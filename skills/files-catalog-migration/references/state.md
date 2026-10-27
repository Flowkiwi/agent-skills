# Workspace and state

## Layout

```
migrations/<client-slug>/
├── sources/            # client files copied by intake.py (never modified); previous/: replaced versions
├── inventory.json      # files, sheets, header rows, columns with samples and flags (inventory.py)
├── mappings/           # <concept>.json: validated mappings (the source of truth); cleaning.json
├── products.json       # products and variants (extract.py, then codes.py fill)
├── concepts/           # <concept>.json: structured files of the concepts (taxonomy, collections, metafields, metaobjects, prices)
├── fixes.json          # renames, skips, field fixes and generated keys, replayed on every extract
├── state.json          # decisions with their origin, shared elements, per-product progress
├── client-review.md    # questions for the client, choices made for them and missing data (client_review.py)
├── diff.json           # a later phase: the difference with what the last import wrote, and the conflicts (diff.py)
├── worklists/          # worker-<n>.json: handles assigned to each worker
├── results/            # worker-<n>.jsonl: one result line per product, written by workers
│                       # rejected-<stamp>.jsonl: lines refused by merge, with the reason; merged/: merged files
├── verify-sample.json  # handles whose description is checked after creation (verify.py sample)
├── tmp/                # worker-<n>/ and shared/: each worker's own temporary files; columns-proposal.json (columns.py);
│                       # remote/<handle>.json: Flowkiwi read-back snapshots of a later phase (diff.py conflicts)
└── undo/               # deleted-<timestamp>.json: IRIs deleted by an undo run
```

What is kept are the mappings and the decisions, never files changed by hand: `products.json` and `concepts/*.json` can be rebuilt at any time with `extract.py` (`all` for every mapped concept). All script commands below take the workspace folder as `<dir>`.

## Concept files

Every `concepts/<concept>.json` ends with a `provenance` block written by `extract.py`:

```json
{"...": "the concept keys (see its reference)", "warnings": [],
 "provenance": {"products": "<sha256>", "mapping": "<sha256>"}}
```

- `products`: sha256 of what the extraction read from `products.json`: the sha256 of each source file, then each product's handle and rows (sorted by handle) and, for `prices` only, the SKU, price and compare-at price of each variant. A new client file, added or removed products, a new grouping or (for prices) new SKUs change it.
- `mapping`: sha256 of `mappings/<concept>.json` at extraction.

`dryrun.py` recomputes both and blocks with `concept <concept> was extracted from another products.json` (or `from another mappings/<concept>.json`) and the `extract.py <dir> <concept>` to run again. So `codes.py fill` only blocks `prices` (extract it again after the fill), and a new client file blocks every concept. The block is part of the decision recorded by `state.py decide --value-file`, so a concept extracted again must be recorded again.

## inventory.json

```json
{"version": 1, "files": [{"file": "demo-catalogue.xlsx", "sha256": "a84d...", "sheets": [
  {"sheet": "Catalogue", "hidden": false, "header_rows": 2, "rows": 7, "merged": [],
   "columns": [{"letter": "H", "label": "[Dimensions] Poids Net (kg)", "code": "dim_poids_net", "filled": 5,
                "fill_rate": 0.71, "distinct": 4, "max_length": 3, "kind": "number", "samples": ["1.8", "2.6"],
                "flags": [{"flag": "similar_label", "with": "G", "conflicts": 1}]}]}]}]}
```

Kinds and flags are described in `references/sources.md`. `code` is the second header row (technical code) or `null`.

## mappings/

`<concept>.json`: one file per concept (`products.json` first), format in `references/mapping.md`. Every spec may carry `origin`, `reason` and `question`; `dryrun.py` lists the `agent` ones.

`cleaning.json` (optional): the cleaning rules changed for this client, applied by `extract.py` (the inventory always uses the default rules):

```json
{"disabled": ["dash"], "replacements": {"Ø": "diamètre "}}
```

`disabled` lists rule ids of `clean.py table` (or `custom` to turn the replacements off); `replacements` are exact text replacements applied before the rules.

## products.json

`{"version": 1, "products": [...], "warnings": [...], "cleaning": {"quote": 2}, "sources": {"main": {"file", "sheet", "sha256"}}, "metafield_columns": {}}`. One entry per product (`row` is its first sheet row, `rows` every row per source id):

```json
{
  "handle": "2230", "key": "2230", "row": 8, "rows": {"main": [8, 9]},
  "title": "Grille-pain Duo", "description_html": "<p>...</p>", "description_origin": "source",
  "vendor": "TOASTINE", "type": null, "product_category": "Catalogue/PEM/Grille-pain",
  "tags": [], "status": null, "published": null,
  "options": [{"name": "Couleur", "values": ["Noir", "Rouge"]}],
  "variants": [{"sku": "1005", "sku_generated": false, "barcode": "3601234560050", "barcode_generated": false,
                "option_values": ["Noir"], "price": "49.90", "compare_at_price": null, "cost": null,
                "grams": null, "taxable": null, "market_prices": {}}],
  "images": ["https://example.com/img/1005.jpg"], "metafields": {}
}
```

- `key` is the client reference value, `handle` its slug (the `import.key` marker value).
- `description_origin`: `source`, `generated` (approved generation; the poor one is kept in `description_source`), `fixed` (set by a `fields` fix of `fixes.json`, never regenerated) or `null`. `description_candidate`: the generated description waiting for approval.
- `cleaning`: number of replacements per cleaning rule, over the cells the mapping read.
- `sources`: the files read, with their SHA-256, to detect a new version.

Warning entry: `{"row": 6, "handle": "1004", "code": "conflict", "field": "variant.price", "message": "...", "stage": "extract"}`. `stage` is `extract` (mapping and extraction) or `validate` (recomputed by `catalog.py` on every run). Codes and how to settle them: `references/products.md` `## Warnings`.

## fixes.json

Every change that the mapping cannot express, replayed by `extract.py` on every extraction:

```json
{"skus": {"2228": {"OLD-SKU": "NEW-SKU"}}, "option_names": {"*": {"couleur": "Couleur"}},
 "skip": ["nokey-0002"], "skip_rules": [{"non_sellable": true}, {"handle_pattern": "^echantillon-"}],
 "fields": {"2228": {"title": "Grille-pain vintage crème"}},
 "generated_keys": [{"source": "main", "row": 7, "title": "Toaster sans référence", "key": "nokey-0001"}]}
```

- `skus`: SKU renames per handle. `option_names`: option renames per handle, `*` for every product (a handle entry wins).
- `skip`: handles left out. `skip_rules`: a product matching any rule is left out (all conditions of one rule must match): `non_sellable`, `metafield_prefix`, `handle_pattern`.
- `fields`: product values replaced after the renames, for `title`, `description_html`, `vendor`, `tags`, `product_category`, `status`: `tags` is a list of strings, the others a string or `null` (any other type is an error). They are cleaned like the cells (`references/mapping.md` `## Cleaning`); a `description_html` fix sets `description_origin` to `fixed` (`null` when the fix is `null`).
- `generated_keys`: written by `extract.py` only, never by hand: the keys given to rows without reference, matched again by source row and title. When such a row now has a real key, `extract.py` raises `generated_key_superseded` (`references/products.md` `## Identity`).

After a change that skips products, run `state.py sync <dir>`.

## state.json

```json
{
  "version": 1, "source": "files", "phase": 2, "client": "acme", "mcp_url": "https://...",
  "started_at": "2026-10-27T10:00:00Z",
  "phases": [{"n": 1, "started_at": "2026-10-27T10:00:00Z"}, {"n": 2, "started_at": "2026-11-12T09:00:00Z"}],
  "decisions": {"data_language": "fr", "status_mode": "draft", "codes": {"prefix": "2", "next_start": 6, "sku_pattern": "{handle}-{index}"}},
  "decision_meta": {"status_mode": {"origin": "developer", "reason": null, "phase": 1, "at": "2026-10-27T10:05:00Z"}},
  "shared": {"metafield_definition:import.key": {"iri": "/api/metafield_definitions/019f84f8-...", "status": "created",
                                                 "module": "core", "spec": {"type": "single_line_text_field", "name": "Clé d'import"}}},
  "products": {"2230": {
    "status": "created", "iri": "/api/products/019f...", "pilot": false, "error": null, "updated_phase": 2,
    "items": [{"kind": "variant", "iri": "/api/products/019f.../variants/019f...", "status": "created", "module": "core", "ref": "1005"}],
    "written": {"phase": 2, "payload": {"handle": "2230", "product": {"body": {"title": "Grille-pain Duo", "...": "..."}},
                                        "marker": {}, "options": [], "variants": [], "metafields": [], "prices": []}}
  }}
}
```

- `phase`: the current migration phase (1 for the first run), `phases` when each one started. `state.py phase <dir> --start` starts the next one (prints its number) and refuses while a result file is not merged (exit 1, `run state.py merge first`); `state.py phase <dir>` prints the current one. A decision belongs to the phase it was recorded in (`decision_meta.<key>.phase`): the phase decisions (`force_update`, `removed_action`, `confirmed_changes`, `confirmed_rekey`) also carry `{"phase": <n>}` in their value and only count when both equal the current phase.
- `decision_meta.<key>`: `origin` (`developer`, `agent` or `client`), `reason` (in the data language), `phase` and `at`, written by `state.py decide`.
- Product status: `pending`, `created`, `failed`, `deleted`, `archived` (archived in Flowkiwi by a later phase), `skipped` (handle no longer in `products.json`, set by `state.py sync` on a `pending` product or a `failed` one without any `created` item; back to `pending` if it reappears). `deleted` and `archived` products are never assigned to a creation worker nor counted as to create by `dryrun.py`.
- `removed`: `true` on a `created` or `archived` product whose handle is no longer in `products.json` (set by `state.py sync`, cleared when the handle comes back). A later phase asks once what to do with the `created` ones (archive or delete); a deleted product is never flagged again.
- `written`: what the last import wrote for the product, `{"phase": <n>, "payload": <payload.py output>}`, recorded by `state.py merge` for every `created` or `updated` line (only the last one is kept). A module-only line refreshes only its part of the previous snapshot (`taxonomy`: the body `category` and `attributes`; `collections`: the body `collections`; `metafields` or `metaobjects`: their entries of `metafields`; `prices`: `prices`), the rest stays as written; with no previous snapshot, none is recorded. An `updated` line leaves out the variants its run deferred (a SKU still held by another product: not created yet, with their prices and the option values only they use) and keeps the previous entries of the reused children it never wrote and of the variants it never deleted (diff.json `blocked_moves`: a variant still `created` in `items`, with its prices and the option values it uses), so the next `diff.py` still finds them. An `archived` line keeps the previous payload with the status `/api/statuses/ARCHIVED`, the only field the archive run wrote. When `payload.py` fails at merge time, `written` stays as it was and merge prints `warning: no written snapshot for <handle>: <error>`. A later phase compares it with the payload computed from the regenerated files.
- `updated_phase`: the phase of the last in-place update of the product.
- `verify`: `ok`, `fixed` or `mismatch` when the description was checked after creation (`verify.py`).
- `decisions.status_mode`: `draft`, `active` or `source`. `decisions.status_map`: `{"<source status in lowercase>": "<Flowkiwi status code>"}`, e.g. `{"hors ligne": "DRAFT"}`, applied before the default mapping when `status_mode` is `source`. Keys are always lowercase: the status is lowercased before the lookup (`catalog.py --check` and `payload.py`). The key `""` maps the products without a source status.
- `decisions.data_language`: language of every name or label generated by the migration (ISO code, e.g. `fr`). `decisions.marker_labels` (`{"name", "description"}`) overrides the marker definition labels for a language `plan_shared.py` does not know.
- `decisions.cleaning`: the cleaning rules validated in Phase 1. `decisions.description_generation`: `{"approved": true}` once generated descriptions are approved. `decisions.adopt`: `{"<handle>": "<tenant product IRI>"}` for products mapped to an existing tenant product.
- Shared entry `spec`: what was asked when the element was created (type, name...), copied from the `plan_shared.py` entry, to detect a later drift.
- Item status: `created` (made by this migration, undoable), `reused` (existed before, never deleted), `deleted`.
- Item `ref`: names the item in the product, so a later phase can match it: the SKU of a `variant`, the title of a `product_option`, `<option title>:<value>` of an `option_value`, `namespace.key` of a `metafield`, `<sku>:<country code>` of a `sale_offer`.
- Item `shared`: the shared key the item depends on (`metafield_definition:import.key` for the marker). Used by the undo scope `shared:<key>`. An item with a recorded `shared` key takes the module of that shared element (`state.py merge` sets it, whatever the result line said): `metaobjects` for a reference metafield (`metafield_definition:<namespace>.<key>` recorded with module `metaobjects`), so `worklists --module metaobjects` finds the products created before the metaobjects.
- `decisions.<concept>` (`taxonomy`, `collections`, `metafields`, `metaobjects`, `prices`): the `concepts/<concept>.json` file as recorded by `state.py decide --value-file`. `dryrun.py` blocks while a concept file differs from its decision (warnings aside) and prints the `state.py decide` to run again, with the workspace path and the origin and reason recorded before.
- Shared keys are `<kind>:<name>`, with their module: `metafield_definition:import.key` (`core`), `category:<path>`, `attribute_definition:<code>`, `attribute_option:<code>:<value>` (`taxonomy`), `collection:<path>` (`collections`), `metaobject_definition:<type>`, `metaobject:<type>:<entry handle>` and the reference metafield definitions `metafield_definition:<namespace>.<key>` (`metaobjects`), the other `metafield_definition:<namespace>.<key>` (`metafields`), `tax:<country code>:<rate>` and `country:<code>` (`prices`). Paths join their levels with ` > `. Item and shared kinds, in undo order: `sale_offer`, `purchase_offer`, `metafield`, `variant`, `option_value`, `product_option`, `*_link`, `product`, `collection`, `metaobject`, `attribute_option`, `attribute_definition`, `category`, `supplier`, `tax`, `metafield_definition`, `metaobject_definition`, `channel`, `country`; within a kind, the last created goes first.

## Worker result line

```json
{"handle": "2230", "status": "created", "iri": "/api/products/019f84f8-...", "error": null, "verify": "ok",
 "items": [{"kind": "product", "iri": "/api/products/019f84f8-...", "status": "created", "module": "core"},
           {"kind": "variant", "iri": "/api/products/019f84f8-.../variants/019f84f9-...", "status": "created", "module": "core", "ref": "1005"}]}
```

`status` is `created` or `failed`, and in a later phase `updated` (the product stays `created`, `updated_phase` is set) or `archived` (the product becomes `archived`). `iri` is required except on `failed`. On failure, still list the items created before the failure so they can be reused or undone; a `failed` line on a product already `created` or `archived` (an update run) only records the error. Optional `verify`: `ok`, `fixed` or `mismatch`. Optional `deleted`: the IRIs of the items the run deleted, marked `deleted` (the product IRI marks the product `deleted`).

`state.py result` rejects a line that does not follow these rules: missing keys, unknown statuses, a missing `iri`, an IRI (item or `deleted`) that is not `/api/<resource>/<uuid>` (a `#<relation>` suffix only for `*_link` items), an empty `ref`, a `shared` that is not a shared key.

## Commands

```
$S = python3 $SKILL/scripts
$S/intake.py add <dir> <path>... [--replace]                         # copy client files into sources/
$S/inventory.py <dir> [--samples 5]                                  # writes inventory.json
$S/clean.py table                                                    # the cleaning rules
$S/extract.py <dir> products                                         # writes products.json (and new generated keys in fixes.json)
$S/extract.py <dir> taxonomy|collections|metafields|metaobjects|prices  # writes concepts/<concept>.json, prints its preview
$S/extract.py <dir> all                                              # products, then every concept with a mappings/<concept>.json
$S/columns.py propose <dir>                                          # column triage, writes tmp/columns-proposal.json
$S/catalog.py --validate <dir>/products.json                         # refresh and print the warnings
$S/catalog.py --check <dir>/products.json [--state <dir>/state.json] # exit 1 on blocking warnings
$S/codes.py preview <dir>/products.json --prefix 2 --start 1 [--sku-pattern "{handle}-{index}"] [--previous <old products.json>]
$S/codes.py fill <dir>/products.json --prefix 2 --start 1 [--sku-pattern "{handle}-{index}"] [--previous <old products.json>]
$S/codes.py fix <dir>/products.json <dir>/fixes.json                 # one-off check: extract.py applies fixes itself
$S/state.py init <dir> --client <slug> --mcp-url <url>
$S/state.py sync <dir>                                               # new or skipped handles from products.json
$S/state.py decide <dir> --key <name> --value-json '<json>' | --value-file <file.json> --origin developer|agent|client [--reason "<text>"]
$S/state.py shared <dir> --key <kind:name> --iri <iri> --status created|reused --module <module> [--spec-json '<json>']
$S/state.py shared <dir> --file <dir>/tmp/shared/shared.json         # [{"key", "iri", "status", "module", "spec"}, ...]
$S/state.py worklists <dir> --workers 4 [--pilot [--limit 5]] [--handles a,b,c] [--retry-failed] [--module <name>]
$S/state.py worklists <dir> --workers 4 --update [--handles a,b,c] [--retry-failed]   # a later phase: next pass of diff.json
$S/state.py result <dir>/results/worker-1.jsonl --json-file <dir>/tmp/worker-1/line.json   # or --json '<line>', --json - (stdin)
$S/state.py merge <dir>                                              # also records the written snapshots
$S/state.py phase <dir> [--start]                                    # print the current phase, or start the next one
$S/state.py undo-plan <dir> --scope all|pilot|module:<name>|shared:<key>|product:<handle>
$S/state.py deleted <dir> --iris-file <dir>/undo/deleted-<timestamp>.json
$S/state.py reset-deleted <dir>                                      # every deleted product back to pending, with no items
$S/state.py reset-deleted <dir> --handle <handle>                    # only this deleted product (repeatable); an error if it is not deleted
$S/state.py summary <dir>                                            # counts per status, failed, verify, decision origins, phase, removed
$S/dryrun.py <dir>                                                   # exit 1 on blocking warnings, state drift, payload errors, concept files from another products.json or mapping, stale concept decisions or missing decisions; in a later phase also diff.json, snapshots and phase decisions (references/phases.md)
$S/plan_shared.py <dir> [--ready]
$S/plan_shared.py <dir> --changes                                    # a later phase: destructive changes and updates of the shared elements
$S/payload.py <dir> <handle> [--step product|options|variants|metafields|taxonomy|collections|prices|all] [--dry-run]
$S/payload.py <dir> <handle> --update                                # a later phase: the update operations of one product of diff.json
$S/diff.py <dir>                                                     # a later phase: writes diff.json
$S/diff.py conflicts <dir>                                           # compares the snapshots of tmp/remote/ with what was written
$S/client_review.py <dir> [--labels <file.json>]                     # writes client-review.md
$S/verify.py sample <dir> --rate 0.1 --output <dir>/verify-sample.json
$S/verify.py <dir> <handle> --field description|title --readback-file <dir>/tmp/worker-1/readback.txt   # exit 0 match, 1 mismatch
```

`payload.py` prints the exact MCP bodies of one product (status from `status_mode`, tags joined with `,`, category, attributes and collections from the concept decisions, the `import.key` marker, options with positions, variants with their option value names, metafields and metaobject references, sale offers). `--step taxonomy|collections|metafields|prices` prints one concept's part for a module-only run (`references/create.md` `## Concept added after creation`). Non-ASCII letters stay literal; invisible or special whitespace characters are printed as `\uXXXX` escapes. A missing decision or shared IRI is an error (`--dry-run`: a `<key IRI>` placeholder), and so is a variant without a value for one of its options.

`plan_shared.py` prints the shared elements to create, in order, with `key`, `module`, `operation`, `lookup`, `path`, `body`, `spec` and `depends_on` (keys not recorded yet, shown as `<key IRI>` placeholders in the body). Keys recorded `created` or `reused` are skipped; `deleted` ones are planned again. `--ready`: only the entries with no pending dependency. `state.py shared --file` checks every entry before recording any.

`verify.py` compares a read-back value with `products.json` after normalizing line endings, Unicode composition and outer whitespace, and prints both lengths, both SHA-256 and the first differing position with its context. `sample` lists ~`rate` of the handles (deterministic hash), every handle whose description holds special whitespace or non-ASCII punctuation, and the pilot products.

`worklists` first merges any unmerged results, then writes fresh worklists. Products sharing the same title always go to the same worker, so the orphan rule can never adopt another worker's product.
- `--handles a,b,c`: explicit selection (combine with `--pilot`). An unknown handle, or one that is not `pending` (`failed` too with `--retry-failed`), is an error and the previous worklists are kept.
- `--pilot` without `--handles`: a representative selection of up to `--limit` products (default 5) covering, when present: several variants, a single variant, a category, no category, metafields, a generated description, each source status, the longest description. The rest is filled in order. The `pilot` flag of products not `created` is cleared first, so only the new selection is flagged.
- `--module <name>`: `created` products with no live item of that module (a concept added after creation).
- `--update`: a later phase, the next pass of the update run of `diff.json` (deletions of moved or regrouped SKUs, then updates, archives and deletions), printing the `pass` and the `skipped` handles with their reason (`references/phases.md` `## Update worker`).

`undo-plan --scope product:<handle>`: only the `created` items of that product, children first, without any shared element (used to delete a removed product in a later phase). An unknown handle is an error.

## Resume rules

- Resuming = invoking the skill again: `merge`, then `summary`, then continue.
- A product with no result line (worker died mid-product) stays `pending`. The next worker run finds it by marker (or adopts the orphan) before creating anything, per `references/create.md`.
- A `failed` product is only retried with `--retry-failed`. Its earlier items are in the state: the worker reuses them.
