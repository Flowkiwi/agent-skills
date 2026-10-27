# Metafields

Optional concept: product metafields (extra fields that fit neither the core product fields, the taxonomy nor the collections) from `mappings/metafields.json` (field specs as in `references/mapping.md`, evaluated on the rows of each product of `products.json`) to `concepts/metafields.json`. `$S` is `python3 $SKILL/scripts` and `<dir>` the workspace.

## Effort

~15 min to choose the retained columns and validate their types, no extra file.

## Map

Flowkiwi model: a metafield definition (`namespace`, `key`, `name`, `ownerType` products, `type`) is created once, then each product holds one metafield value per definition. Namespace and key are 3+ characters (lowercase letters, digits, `_`, starting with a letter); `import.key` is reserved for the import marker and refused. The mapping has no `sources`: its columns are those of `mappings/products.json`.

```json
{"version": 1, "concept": "metafields", "definitions": {
  "custom.poids_net": {"name": "Poids net (kg)", "type": "number_decimal",
                       "value": {"from": ["main:dim_poids_net", "main:poids_net"], "transform": ["decimal"]}},
  "custom.composition": {"name": "Composition", "type": "list__single_line_text_field",
                         "value": {"from": ["main:matiere", "main:coloris"], "pick": "all"}}}}
```

| Key                | Meaning                                                                                           | Default       |
| ------------------ | ------------------------------------------------------------------------------------------------- | ------------- |
| `name`             | name of the definition, 3+ characters, in `decisions.data_language`                               | required      |
| `type`             | one of the Flowkiwi types below                                                                   | required      |
| `value`            | field spec of the column(s) (`from`, `pick`, `map`, `transform`...) or a literal `{"value": ...}` | required      |
| `transform`        | how `payload.py` writes the value: `null` (as is), `list_json`, `list`, `number`, `json`          | from the type |
| `origin`, `reason` | who chose the definition and why, as in `references/mapping.md`                                   | none          |

1. **Retained columns**: the rows of `$S/columns.py propose <dir>` with role `metafield` (numbers, dates, technical specifications with a unit), with the proposed type from the values. Show them and ask which ones to keep; the others stay out of the catalog (or become attributes, `references/taxonomy.md`, when the developer wants to filter on them).
2. **Namespace and key**: namespace `custom` by default, key from the technical code of the column (`dim_poids_net` -> `custom.poids_net`), lowercase, 3+ characters. Never reuse a key of another definition.
3. **Name**: in `decisions.data_language`, with the unit when the column has one (`Poids net (kg)`).
4. **Values**: the value of a product comes from its first row. A column with several values per product (`pick: all`, a `split` transform) needs a list type; a list type wraps a single value in a list.

## Validate types

Flowkiwi types (check the current list with `call_operation list_metafield_definition_types`, without pagination arguments: it rejects `itemsPerPage`):

| Values in the column                                | Flowkiwi type                                                        | Default `transform` |
| --------------------------------------------------- | -------------------------------------------------------------------- | ------------------- |
| short text (255 characters at most)                 | `single_line_text_field`                                             | `null`              |
| text with line breaks, HTML, or over 255 characters | `multi_line_text_field`                                              | `null`              |
| integers                                            | `number_integer`                                                     | `number`            |
| decimals (`1.8` or `1,8`)                           | `number_decimal`                                                     | `number`            |
| a JSON object or array to keep as is                | `json`                                                               | `null`              |
| several text values per product                     | `list__single_line_text_field`                                       | `list_json`         |
| several integers / decimals per product             | `list__number_integer` / `list__number_decimal`                      | `list_json`         |
| booleans, dates, URLs, measures with a unit         | no direct type: `single_line_text_field` (booleans: or an attribute) | `null`              |

Booleans have no type: keep them as `single_line_text_field` (`Oui` / `Non` through a `boolean` transform) or make them an attribute (`references/taxonomy.md`).

**Validate every type explicitly**: show one table with one row per kept definition (`namespace.key`, name, proposed type, value count, sample value) and get the developer's approval per row. The type of a definition cannot be changed later: a wrong type means deleting the definition and its values (`references/undo.md`, scope `shared:<key>`), then recreating them. Show the table again in the dry-run summary.

List values: the format Flowkiwi expects for a list metafield is checked with one `create_metafield` on the pilot, then the `transform` is set in the mapping (`list_json`: a JSON array as text, the default; `list`: a JSON array) and the concept is extracted again.

## Record

1. `$S/extract.py <dir> metafields` writes `concepts/metafields.json` and prints the preview: each definition with its type, transform, product count and a sample value, the warnings and the definition count. Show it to the developer.
2. Settle the warnings through the mapping, then extract again:

| Code                   | How it is settled                                                                                                                                       |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `not_a_number`         | a value that is not a number under a number type is dropped: fix the `value` spec (a `decimal` transform strips units such as `kg`), or change the type |
| `too_long`             | a `single_line_text_field` value over 255 characters is kept whole: switch to `multi_line_text_field`                                                   |
| `list_in_single_field` | several values for a type that holds one (first kept): `pick: first`, a `join`, or a list type                                                          |
| `conflict`             | the columns of a `from` list disagree: settle it as in `references/mapping.md` (order, `conflicts: ignore`)                                             |

3. `$S/state.py decide <dir> --key metafields --value-file <dir>/concepts/metafields.json --origin developer` (`--origin agent` when you chose the columns).

`concepts/metafields.json`: `{"definitions": {"<namespace.key>": {"type", "name", "transform"}}, "values": {"<handle>": {"<namespace.key>": "<value>" or ["<value>", ...]}}, "warnings": [...]}`. `payload.py` and `plan_shared.py` read it: any change goes through the mapping and this file, never through worker instructions. Never edit it by hand.

## Shared elements

From `$S/plan_shared.py <dir>` (`references/create.md` step 3), after the collections: for each kept definition, look up with `call_operation list_metafield_definitions` (namespace, key, owner type products); else `call_write_operation create_metafield_definition` `{"body": {"namespace": "custom", "key": "poids_net", "name": "Poids net (kg)", "ownerType": "/api/metafield_owner_types/products", "type": "/api/metafield_definition_types/number_decimal"}}`. Record `metafield_definition:<namespace>.<key>` with module `metafields`. Never touch the `import.key` marker definition here.

## Per product

For each entry of the payload `metafields` (`payload.py` keeps the definitions the product has a value for, applies the transform and resolves the definition IRI):
1. `call_operation list_metafields` on `products/<id>`: a metafield with this definition exists -> record `reused`, skip.
2. Else `call_write_operation create_metafield` `{"path": {"ownerResource": "products", "ownerId": <id>}, "body": <entry body>}`, copied verbatim.
3. Record a `metafield` item with module `metafields` (`metaobjects` for a reference metafield, `references/metaobjects.md`), `"ref"` = `namespace.key` and `"shared"` = the entry `shared` (`metafield_definition:<namespace>.<key>`), so that `undo` scope `shared:<key>` can remove the values of one definition.

## Undo notes

Metafields are deleted with the product children; definitions on a full or `module:metafields` undo. The `import.key` marker definition is never part of this concept: it goes with `module:core` or `all` only.
