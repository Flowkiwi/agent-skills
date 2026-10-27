# Products and variants

The mandatory concept: products, options, variants, SKUs and barcodes, from `mappings/products.json` (format in `references/mapping.md`) to `products.json` (format in `references/state.md`). Schemas below were checked on 2026-10-26. If a call is rejected for an unknown or missing field, run `describe_operation` on it and adapt; do not guess.

## Effort

~30-60 min of exchange with the developer: one grouped question round, then the warnings.

## Map

| `products.json`            | Flowkiwi                                                                                                                                                                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `title`                    | `create_product` `title` (max 255)                                                                                                                                                                                              |
| `description_html`         | `description` (HTML kept as is; omit when null, `minLength` is 1)                                                                                                                                                               |
| `tags`                     | `tags`: joined with `,` without spaces; omit when empty                                                                                                                                                                         |
| `status`                   | `status`: `/api/statuses/DRAFT` when `status_mode` is `draft`, `/api/statuses/ACTIVE` when it is `active`; with `source`, `decisions.status_map` first, then `active` -> `ACTIVE`, `draft` -> `DRAFT`, `archived` -> `ARCHIVED` |
| `options[].name`           | `create_product_option` `title` (min 3 chars), `position` = index                                                                                                                                                               |
| `options[].values[]`       | `create_option_value` `value`, `productOption`, `position` = index                                                                                                                                                              |
| `variants[]`               | `create_variant` `sku`, `barcode` (both required)                                                                                                                                                                               |
| `variants[].option_values` | `create_variant` `optionValues[]` (IRIs of the matching option values)                                                                                                                                                          |
| `handle`                   | marker metafield `import.key` on the product                                                                                                                                                                                    |

`payload.py` applies this table: workers copy its output instead of mapping by hand. The table stays for the dry-run and for review.

Kept in `products.json` but not created by this concept: images (no media through the MCP), `price` and `compare_at_price` (no prices concept in this version), `vendor` and `product_category` (for later concepts). Say it once in the dry-run.

## Identity

The handle, and so the `import.key` marker, comes from a client reference column, passed through a slug. It is the only link between a row of the files and a product of the tenant, on every later delivery.

- `key` lists the reference columns in this order of preference: merchant SKU, commercial reference, EAN (`{"from": ["main:sku_marchand", "main:ref_com", "main:ean"]}`): the first filled one of each row wins. **Never the title**: it changes from one delivery to the next.
- A row without any reference gets a generated key (`nokey-0001`), written once to `fixes.json` `generated_keys` (attached to its source row and title) and kept on every later extraction. It raises a `generated_key` warning: list these products for the client, who should add a reference.
- When such a row later gets a reference (the client filled its SKU), the product takes the new key and `extract.py` raises `generated_key_superseded` naming both keys. If the `nokey-` product was already created, undo it first (see the next bullet): `dryrun.py` blocks while a created handle is no longer in `products.json`, and while `products.json` and `state.json` differ (run `state.py sync`).
- Grouping: 1 row = 1 product by default (`"group": null`). Propose a `group` when several rows share a model (same commercial reference, same model name) or when a size or a color sits in a column or in the title. `group.key` gives the key of the grouped rows (its value is the handle), `options` give the variant values, and a `regex` on the title removes the variant suffix (worked example in `references/mapping.md`). Record it with `"origin"` and `"reason"`: the reason goes to the client.
- Changing the `key` or the `group` after products were created changes their keys: each created product whose handle changes becomes a removal plus a creation. Show it before applying: copy `products.json` to `tmp/products-before.json`, extract again, list the handles that disappear and the new ones, and ask for explicit confirmation. Refused: restore the previous mapping and copy `tmp/products-before.json` back (it holds the generated codes). Confirmed: this version cannot remove single products, so undo the created ones first (`references/undo.md`, scope `pilot` when only the pilot ran, else `all`), then `state.py reset-deleted <dir>` and `state.py sync <dir>`.

## Questions

One grouped round, in this order, each with a default proposal taken from the inventory:

1. **Key column**: the merchant SKU column, then the commercial reference, then the EAN (fill rates from the inventory).
2. **Grouping**: none, unless rows share a model or a variant axis (size, color) is visible in a column or the title.
3. **Title column**: the short product label (`Descriptif court`-like), with a fallback column when it is not always filled.
4. **Description column**: the richest HTML or text column, as is; `"transform": ["text_to_html"]` for plain text with line breaks. Poor descriptions: `## Descriptions`.
5. **Vendor**: the brand column.
6. **Images**: the main visual first, then the secondary ones (`"pick": "all"`). They are not created: their URLs are listed in the final report.
7. **Status**: `## Status`.
8. **Price columns**, if any: `variant.price` and `variant.compare_at_price` with `"transform": ["decimal"]`, kept in `products.json` for a later prices concept.
9. **Duplicate columns**: for each `similar_label` or `same_values` flag on a mapped field, which column wins (first in `from`), with its conflict count from the inventory.

## Status

Ask "Create products as DRAFT (recommended, you publish later), ACTIVE, or from the <column> column?" and record `$S/state.py decide <dir> --key status_mode --value-json '"draft"' --origin developer` (`'"active"'` or `'"source"'`).

With `source`, map `product.status` from that column to `active`, `draft` or `archived` with a `map`, for example `{"from": ["main:statut"], "map": {"en ligne": "active", "hors ligne": "draft"}, "map_ignore_case": true}`. Any other value, and an empty status (empty cell, no status column), raises `unknown_status`, blocking until the `map` (or a `default`) covers it or `status_map` decides it (key `""` for the empty status): `$S/state.py decide <dir> --key status_map --value-json '{"hors ligne": "DRAFT"}' --origin developer`. Its keys are the source values **in lowercase** (`Hors Ligne` is matched by `hors ligne`): `catalog.py` and `payload.py` both compare the lowercased status.

## Descriptions

A description comes from one column, as is after cleaning. It is **poor** when its plain text (tags removed, spaces collapsed) is shorter than `min_length` characters or equal to the title (case ignored).

When poor descriptions are frequent, propose a `generate` block on `product.description_html` (the only field that accepts it), recorded `"origin": "agent"` with its `reason` in the data language:

```json
"description_html": {"from": ["main:descriptif"],
  "generate": {"when": "poor", "min_length": 80, "intro": "{title} de la marque {vendor}.", "separator": " : ",
               "features": [{"label": "Matière", "from": ["main:matiere"], "map": {"inox": "Inox"}},
                            {"label": "Poids net", "from": ["main:poids_net"], "transform": ["decimal"], "suffix": " kg"}],
               "origin": "agent", "reason": "Descriptions absentes ou réduites au titre : description construite avec les caractéristiques du fichier."}}
```

| Key                | Meaning                                                                                                                                                                              | Default  |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| `when`             | only `poor`                                                                                                                                                                          | `poor`   |
| `min_length`       | plain text length under which a description is poor (integer, 0 or more)                                                                                                             | `80`     |
| `intro`            | first paragraph; only the `{title}` and `{vendor}` placeholders are allowed (values after the `fields` fixes; one of them empty: no candidate for that product); empty: no paragraph | `""`     |
| `separator`        | between a feature label and its value                                                                                                                                                | `": "`   |
| `features`         | non-empty list of field specs (`from`, `map`, `transform`...) with a `label` and an optional `suffix` after the value; an empty feature is left out                                  | required |
| `origin`, `reason` | who proposed it and why, in the data language                                                                                                                                        | none     |

The result is `<p>intro</p><ul><li>label separator value suffix</li>...</ul>`, HTML-escaped and cleaned; nothing is generated when every feature is empty or a placeholder of the intro is empty. It states only the intro placeholders and values read from the files: never add a claim that is not in the data. A description set by a `fields` fix (`description_origin: fixed`) is never replaced.

Generated descriptions are created content and need an explicit approval:

1. Without it, the poor description stays, the generated one is stored in `description_candidate` and a `poor_description` warning (not blocking) is raised.
2. The dry-run (`dryrun.py`) shows 3 examples, poor and candidate side by side. Show them and ask whether the rule applies to the whole catalog.
3. Only after the developer said yes for the whole catalog: `$S/state.py decide <dir> --key description_generation --value-json '{"approved": true}' --origin agent --reason "<reason in the data language>"`, then extract again. Poor descriptions are replaced (`description_origin: generated`, the previous one kept in `description_source`).
4. No: record nothing (or remove the `generate` block); the poor descriptions stay.

## Codes

1. `$S/codes.py preview <dir>/products.json --prefix 2 --start <n> --sku-pattern "{handle}-{index}"` shows how many barcodes / SKUs are missing and the exact values (SKUs are uppercased). Default barcode: EAN-13, prefix `2` (internal range), sequential.
2. After validation, run `$S/codes.py fill` with the same arguments, then record the format **and the printed `next_barcode_start`**: `$S/state.py decide <dir> --key codes --value-json '{"prefix": "2", "next_start": 6, "sku_pattern": "{handle}-{index}"}' --origin developer`. `<n>` is `codes.next_start` from the state when it exists, else 1.

Re-extracting keeps generated codes: `extract.py` copies them from the previous `products.json` onto the same variants (same handle and option values). Only new variants miss codes then: run `codes.py fill` again with `--start <codes.next_start>` and record the new `next_start`.

## Tenant collisions

After `codes.py fill`, so generated codes are checked too:

- **SKUs or barcodes already in the tenant**: `call_operation list_variants` with `query.sku[]` on all the SKUs, then with `query.barcode[]` on all the barcodes, generated ones included (batches of 50): both checks, a barcode can clash while its SKU does not. These filters are **partial matches** (`TS-S` also returns `TS-S-2`): only exact `sku` / `barcode` equality is a collision. `list_variants` does not accept `expand`: each hit carries its `product` IRI, read it with `get_product` to show the product. The developer picks: skip the product (`skip` fix), rename the SKU (`skus` fix), or, when it is the same product, adopt it: `$S/state.py decide <dir> --key adopt --value-json '{"<handle>": "<product IRI>", ...}' --origin developer` (the whole map each time). A clashing barcode cannot be renamed through `fixes.json`: skip the product, adopt it when it is the same product, or ask the client for a corrected file (added with `intake.py add --replace`).
- **Products already migrated**: `list_products` with `query.metafield["import.key"]` for a few handles. Hits on a fresh workspace mean a previous migration ran on this tenant: warn and let the developer decide (reuse is automatic per product).

## Warnings

`extract.py` prints them grouped by code. Settle each through the mapping or `fixes.json` (`references/state.md`), then extract again. Blocking codes stop Phase 3 (`catalog.py --check`).

| Code                                               | Blocking      | How it is settled                                                                                                                                                                             |
| -------------------------------------------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `conflict`                                         | no            | columns of one `from` disagree, the first wins: pick the winner by reordering `from`, or `"conflicts": "ignore"` when the first one is right                                                  |
| `group_conflict`                                   | no            | rows of one product give different product values, the first row wins: check the grouping, or take the field from another column                                                              |
| `suspect_unit`                                     | no            | a number above 100 times the median of its field (another unit, a typing error): add `{"scale_above": [<threshold>, <divisor>]}` after `decimal`, e.g. `[100, 1000]` for grams in a kg column |
| `scaled`                                           | no            | a value divided by `scale_above`: check the list with the developer                                                                                                                           |
| `not_a_number`, `not_a_boolean`, `no_match`        | no            | a transform could not convert a value (it becomes null): adapt the `map` or the transform, or a `fields` fix                                                                                  |
| `too_long`                                         | yes           | a title over 255 characters, never truncated silently: a `fields` fix with a shorter title validated by the developer, or another title column                                                |
| `missing_title`                                    | yes           | a fallback title column in `from`, else a `fields` fix with the title given by the developer                                                                                                  |
| `generated_key`                                    | no            | kept (the key stays in `fixes.json`), listed for the client in the final report                                                                                                               |
| `generated_key_superseded`                         | no            | a row with a generated key now has a real key: `## Identity`                                                                                                                                  |
| `duplicate_key`, `duplicate_handle`                | yes           | two rows with the same key, or two keys giving the same handle (the later row is dropped): change the key (another column first) or the grouping                                              |
| `missing_option_value`                             | yes           | a variant row has no value for an option (`payload.py` refuses it): fill the option column with the client, or check the grouping                                                             |
| `duplicate_option_values`                          | yes           | two variants with the same option values: an option column is missing from `options`, or the grouping is too wide                                                                             |
| `list_in_single_field`                             | yes           | several values for a single-value field (a `map` target written as a list): give the `map` a single target                                                                                    |
| `short_option_name`                                | yes           | an option name under 3 characters: rename it in `options[].name`                                                                                                                              |
| `option_name_case`                                 | no            | the same option name with different cases: use the majority form in the mapping, or an `option_names` fix under `*`                                                                           |
| `duplicate_sku`                                    | yes           | show both products: rename one (`skus` fix, e.g. suffix `-2`) or skip one (`skip` fix)                                                                                                        |
| `missing_sku`, `missing_barcode`                   | yes           | `## Codes`                                                                                                                                                                                    |
| `non_sellable`                                     | no            | no price and no SKU from the files (notes, samples): ask whether to skip them all with `"skip_rules": [{"non_sellable": true}]`, settle it before the codes                                   |
| `unknown_status`                                   | with `source` | `## Status` (an empty status too)                                                                                                                                                             |
| `poor_description`                                 | no            | `## Descriptions`                                                                                                                                                                             |
| `join_duplicate`, `join_unmatched`, `join_missing` | no            | a secondary file does not match the main one row for row: show the counts and check the join column with the developer                                                                        |

After a `skip` or `skip_rules` change, run `state.py sync <dir>` so skipped handles leave the worklists.
