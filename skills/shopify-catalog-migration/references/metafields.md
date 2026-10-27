# Module: metafields

## Effort

Medium to high: ~10-20 min to note the definitions and decide the type mapping.

## Collect

- `exports/metafield-definitions.csv` (`owner,namespace_key,name,type`) from Settings -> Custom data.
- Values: `metafields` in `products.json` (product-level columns of the CSV). Variant metafields are not in the product CSV: out of scope unless the developer provides them.
- The product CSV export does not include `json` metafields, and may omit other types. Run `python3 $SKILL/scripts/metafields.py diff products.json exports/metafield-definitions.csv`: it lists declared definitions without a column (and columns without a definition). Show the missing ones: the developer gets their values another way (Shopify bulk editor, an app export) or accepts they are not migrated.
- Fallback without a definitions file: `python3 $SKILL/scripts/metafields.py infer products.json --output exports/metafield-definitions.csv` proposes one definition per metafield column (label as name, value count, suggested type from the values: newlines, HTML, integers, decimals, JSON, `["a","b"]` lists, length). The file then holds Flowkiwi type names directly; every type still needs the developer's validation.

## Map and consolidate

Flowkiwi definitions: `namespace`, `key`, `name` (3+ chars each), `ownerType` (`/api/metafield_owner_types/products`), `type` (`/api/metafield_definition_types/<type>`). Check the current list with `call_operation list_metafield_definition_types`, without pagination arguments (it rejects `itemsPerPage`).

| Shopify type                                                                          | Flowkiwi type                                                                                    |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| `single_line_text_field`                                                              | `single_line_text_field`                                                                         |
| `multi_line_text_field`                                                               | `multi_line_text_field`                                                                          |
| `rich_text_field`                                                                     | `json` (ask) or `multi_line_text_field`                                                          |
| `number_integer`                                                                      | `number_integer`                                                                                 |
| `number_decimal`                                                                      | `number_decimal`                                                                                 |
| `json`                                                                                | `json`                                                                                           |
| `boolean`, `date`, `url`, `color`, `weight`, `dimension`, `volume`, `rating`, `money` | no direct type: propose `single_line_text_field` (or `json` for measures), the developer decides |
| `list.single_line_text_field`                                                         | `list__single_line_text_field`                                                                   |
| `list.number_integer` / `list.number_decimal`                                         | `list__number_integer` / `list__number_decimal`                                                  |
| `product_reference`, `list.product_reference`                                         | out of scope unless asked (needs the Flowkiwi IRIs of the targets, run after all products)       |
| `metaobject_reference`, `file_reference`, other references                            | out of scope (no media, metaobjects need their own migration)                                    |

1. Join definitions and value columns on `namespace.key` (`metafields.py diff`); report columns without a definition and definitions without values.
2. Shopify namespaces/keys shorter than 3 characters: propose a rename (the decision is keyed by the Flowkiwi `namespace.key`, with `"source": "<shopify namespace.key>"`).
3. Skip `shopify.*` standard metafields unless the developer wants them (they are often category metafields better handled by the taxonomy module).
4. Value formats: Shopify CSV list values look like `["a","b"]` or `a; b`; pick the `transform` that gives the Flowkiwi expected format (check with one `create_metafield` on the pilot).
5. **Validate every type explicitly**: show a table with one row per kept definition (`namespace.key`, name, Shopify type or `inferred`, proposed Flowkiwi type, value count) and get the developer's approval per row. The type of a definition cannot be changed later: a wrong type means deleting the definition and its values (`references/undo.md`, scope `shared:<key>`), then recreating them. Show the table again in the dry-run summary.
6. Record the kept definitions with `state.py decide <dir> --key metafields --value-file metafields.json`, in this format (read by `payload.py` and `plan_shared.py`):
   `{"definitions": {"custom.ingredients": {"type": "multi_line_text_field", "name": "Ingredients", "transform": null}}}`.
   `type` is the Flowkiwi type, `name` defaults to the CSV column label (already in the store language; a name you write yourself, e.g. from a key without label, is in `decisions.data_language`), optional `source` is the Shopify `namespace.key` when renamed. `transform`: `null` (value as is), `list_json` (`["a","b"]` or `a; b` -> a JSON array string), `list` (same, as a JSON array), `number`, `json` (parsed), `strip_html` (HTML to plain text lines). Pick the list format with one `create_metafield` on the pilot.

## Shared elements

From `python3 $SKILL/scripts/plan_shared.py <dir>`, for each kept definition: look up with `call_operation list_metafield_definitions`; else `create_metafield_definition` `{"body": {"namespace": "custom", "key": "material", "name": "Material", "ownerType": "/api/metafield_owner_types/products", "type": "/api/metafield_definition_types/single_line_text_field"}}`. Record `metafield_definition:<namespace>.<key>` with module `metafields`. Never touch the `shopify.handle` marker definition here.

## Per product

For each entry of the payload `metafields` (`payload.py` keeps the decided definitions with a value, applies the transform and resolves the definition IRI):
1. `call_operation list_metafields` on `products/<id>`: a metafield with this definition exists -> record `reused`, skip.
2. Else `call_write_operation create_metafield` `{"path": {"ownerResource": "products", "ownerId": <id>}, "body": <entry body>}`, copied verbatim.
3. Record a `metafield` item with module `metafields` and `"shared"` = the entry `shared` (`metafield_definition:<namespace>.<key>`) (so `undo` scope `shared:<key>` can remove the values of one definition).

## Verify

`get_product` with metafields on a pilot product (or `list_metafields`), compare with `products.json`.

## Undo notes

Metafields are deleted with the product children; definitions on a full or `module:metafields` undo.
