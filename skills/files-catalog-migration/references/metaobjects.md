# Metaobjects

Optional concept: metaobjects ("cms" records shared by many products, such as a brand) from `mappings/metaobjects.json` (field specs as in `references/mapping.md`, evaluated on the first row of each product of `products.json`) to `concepts/metaobjects.json`. `$S` is `python3 $SKILL/scripts` and `<dir>` the workspace.

## Effort

~10 min of decisions, no extra file.

## When

A column holding "cms" data: a brand, a designer, an origin, a care guide. Each distinct value becomes one entry of a metaobject definition, and each product is linked to its entry by a reference metafield. Propose it, never impose it: show the candidate column with its distinct values and ask. A value that needs no record of its own stays a metafield (`references/metafields.md`) or an attribute (`references/taxonomy.md`). No metaobject at all is a valid answer: then skip this concept.

## Map

Flowkiwi model: a metaobject definition (`type`, `name`, `displayNameField`, typed `fieldDefinitions`) holds entries (`definition`, `handle`, `fields`, `status` `DRAFT` or `ACTIVE`). A product points at an entry through a metafield definition of type `metaobject_reference`, one metafield value per product.

The mapping has no `sources`: its columns are those of `mappings/products.json`.

```json
{"version": 1, "concept": "metaobjects", "reference_validation": false,
 "definitions": [{"type": "marque", "name": "Marque", "display_name_field": "nom", "entry_key": "nom", "status": "ACTIVE",
                  "fields": [{"key": "nom", "name": "Nom", "type": "single_line_text_field", "required": true,
                              "value": {"from": ["main:marque"], "map": {"toastine": "Toastine"}, "map_ignore_case": true}}],
                  "reference": {"metafield": "custom.marque", "name": "Marque"},
                  "origin": "agent", "reason": "La colonne Marque devient une fiche par marque reliée aux produits."}]}
```

| Key                    | Meaning                                                                                                      | Default              |
| ---------------------- | ------------------------------------------------------------------------------------------------------------ | -------------------- |
| `reference_validation` | the reference metafield definitions carry a validation on their metaobject definition                        | `false`              |
| `type`                 | definition type, 2-64 characters in `snake_case` (lowercase letters, digits, `_`, no hyphen)                 | required             |
| `name`                 | definition name, in `decisions.data_language`                                                                | required             |
| `display_name_field`   | key of the field shown as the entry name                                                                     | required             |
| `entry_key`            | key of the field whose value gives the entry handle (its slug), never a list type                            | `display_name_field` |
| `status`               | status of the entries: `ACTIVE` or `DRAFT`                                                                   | required             |
| `fields`               | `[{"key", "name", "type", "required", "target", "value"}]`, keys in `snake_case`, names in the data language | required             |
| `reference`            | `{"metafield": "<namespace>.<key>", "name"}`: the product metafield pointing at the entry                    | none                 |
| `origin`, `reason`     | who chose the definition and why, as in `references/mapping.md`                                              | none                 |

`reference_validation` is `false` by default: the MCP schema of `create_metafield_definition` only accepts a string for `validations.value` while the API wants an array, so a reference definition with a validation cannot be created. Set it to `true` once product-management accepts it. The field validations of the `metaobject_reference` fields inside a metaobject definition are not concerned.

1. **Definition**: one definition per "cms" column, `type` and field keys from the column code in `snake_case` (`marque`, `nom`), names in the data language (`Marque`, `Nom`). Field types are the metafield types (`references/metafields.md`) plus `metaobject_reference` and `list__metaobject_reference`, which need a `target`: the `type` of a definition placed earlier in `definitions`. Product and media references are not in this version.
2. **Entries**: the `entry_key` value of each product gives the entry handle (`Brioche & Co` -> `brioche-co`); products with the same handle share one entry. Spelling variants (`TOASTINE`, `Toastine`) give the same handle: map them to one spelling (`map` with `map_ignore_case`), else `entry_conflict` keeps the first one.
3. **Reference**: the product metafield `<namespace>.<key>` (the metafields rules: 3+ characters each, `import.key` refused), never a key of `mappings/metafields.json` too, its `name` in the data language. A definition without `reference` only holds entries referenced by other entries.

## Record

1. `$S/extract.py <dir> metaobjects` writes `concepts/metaobjects.json` and prints the preview: each definition with its reference metafield, entry and product counts and sample handles, the warnings grouped by code (with their row and handle) and the counts. Show it to the developer.
2. Settle the warnings through the mapping, then extract again:

| Code                   | How it is settled                                                                                                                                              |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `entry_conflict`       | two products give the same handle with other field values (first kept): map the variants to one spelling                                                       |
| `unknown_entry`        | a reference field value is not an entry of its target (dropped): fix the spec or add the missing entry's product                                               |
| `no_entry_key`         | a product has field values but no `entry_key` value: its other fields are not used; fill the column or ignore                                                  |
| `no_handle`            | the `entry_key` value has no letter or digit: map it to a real name or to `null`                                                                               |
| `missing_required`     | the entry lacks a `required` field: the product is not linked; fill the column, map it, or make the field optional                                             |
| `list_in_single_field` | several values for a field that holds one (first kept): `pick: first`, a `join`, or a list type                                                                |
| `not_a_number`         | a value that is not a number under a number type is dropped (`1,8` is read as `1.8`): fix the `value` spec, or change the type                                 |
| `too_long`             | a `single_line_text_field` value over 255 characters is kept whole and blocks the dry-run: switch to `multi_line_text_field`                                   |
| `group_conflict`       | a later row of a grouped product gives another field value than the first row (kept): accept it, or take the value from a column that is the same on every row |
| `conflict`             | the columns of a `from` list disagree: settle it as in `references/mapping.md`                                                                                 |

Brand and vendor: when a metaobject field reads the column that `product.vendor` also reads (a brand), and only the field has a `map` (spelling variants such as `TOASTINE` / `Toastine`), the dry-run prints `vendor reads <column> without the map applied to metaobject <type>.<field>: copy the map to product.vendor if the brand names should match`. This is a warning, not a blocker. Propose the copy to the developer (the same `map` and `map_ignore_case` in `mappings/products.json` `product.vendor`, then extract the products and the concepts again); never copy the map yourself without their answer.

3. `$S/state.py decide <dir> --key metaobjects --value-file <dir>/concepts/metaobjects.json --origin agent` (`--origin developer` when the developer asked for it).

`concepts/metaobjects.json`: `{"reference_validation": false, "definitions": [{"type", "name", "displayNameField", "status", "fields": [{"key", "name", "type", "required", "target"}], "reference": {"metafield", "name"}}], "entries": [{"definition", "handle", "fields": {"<key>": "<value>"}}], "values": {"<handle>": {"<type>": "<entry handle>"}}, "warnings": [...], "provenance": {"products": "<sha256>", "mapping": "<sha256>"}}` (`provenance`: what it was extracted from, checked by the dry-run, `references/state.md` `## Concept files`). `payload.py` and `plan_shared.py` read it: any change goes through the mapping and this file, never through worker instructions. Never edit it by hand.

## Shared elements

From `$S/plan_shared.py <dir>` (`references/create.md` step 3), in its order after the collections: the metaobject definitions, then their entries (definition order, then entry order), then the reference metafield definitions. Check each operation with `describe_operation` before its first call, look up before each creation, copy the body verbatim, record the key with module `metaobjects` and the `spec` of the plan entry. Names and values are in the data language: no `locale` argument.

- **Definition** `metaobject_definition:<type>`: `call_operation list_metaobject_definitions` with the type; else `call_write_operation create_metaobject_definition` `{"body": <entry body>}`. A definition found with other fields than the `spec` is a destructive change: stop and ask.
- **Entry** `metaobject:<type>:<handle>`: `call_operation list_metaobjects` with the definition IRI and the handle; else `call_write_operation create_metaobject` `{"body": {"definition": "<iri>", "handle": "toastine", "fields": [{"key": "nom", "value": "Toastine"}], "status": "ACTIVE"}}`. A reference field holds the IRI of the target entry (a list reference: a JSON array of IRIs as text), so an entry waits for its targets in `depends_on` (`plan_shared.py --ready`). This format is not verified yet: check it with `describe_operation` on the first entry with a reference field (a 422 there means the bare UUID rule of the product reference metafields, `## Per product`, applies here too).
- **Reference definition** `metafield_definition:<namespace>.<key>`: `call_operation list_metafield_definitions` with `ownerType` products (every page), then filter namespace and key client side (the operation filters neither); else `call_write_operation create_metafield_definition` with type `metaobject_reference`, and a `validations` entry naming the metaobject definition only with `"reference_validation": true`. If Flowkiwi rejects `validations` (`validations.value` must be an array for the API while the MCP schema takes a string), set `"reference_validation": false` back in `mappings/metaobjects.json`, extract again, `state.py decide` again, and rerun `plan_shared.py`.

## Per product

The reference metafield is an entry of the payload `metafields` (after the metafields of `references/metafields.md`): `payload.py` resolves the definition IRI and the entry from `shared`: the value is the bare UUID of the entry (the last segment of its IRI, `<metaobject:<type>:<handle> UUID>` with `--dry-run`), as the API answers 422 on the IRI. Same procedure as metafields: `list_metafields` on `products/<id>`, else `create_metafield` with the entry body copied verbatim. Record a `metafield` item with module `metaobjects` (the module of its shared definition in `state.json`), `"ref"` = `namespace.key` and `"shared"` = the entry `shared`. A product without a value for the definition gets no reference.

Module-only mode (metaobjects added to products already created, on the worklist the main session built for this module): `$S/payload.py <dir> <handle> --step metafields`, and only the entries whose `shared` key is recorded with module `metaobjects` (the other metafields belong to their own concept).

## Undo notes

Order of a full undo: the product metafields, the products, the entries (reverse creation order: an entry referencing another goes before its target), the reference metafield definitions, then the metaobject definitions. `module:metaobjects` removes the reference metafields, the entries and both kinds of definitions, and keeps the products. A 409 on a definition still used (entries or a metafield definition created outside this migration) is reported, not forced.
