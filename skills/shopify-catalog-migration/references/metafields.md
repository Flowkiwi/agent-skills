# Module: metafields

## Effort

Medium to high: ~10-20 min to note the definitions and decide the type mapping.

## Collect

- `exports/metafield-definitions.csv` (`owner,namespace_key,name,type`) from Settings -> Custom data.
- Values: `metafields` in `products.json` (product-level columns of the CSV). Variant metafields are not in the product CSV: out of scope unless the developer provides them.

## Map and consolidate

Flowkiwi definitions: `namespace`, `key`, `name` (3+ chars each), `ownerType` (`/api/metafield_owner_types/products`), `type` (`/api/metafield_definition_types/<type>`). Check the current list with `call_operation list_metafield_definition_types`.

| Shopify type                       | Flowkiwi type                     |
| ---------------------------------- | --------------------------------- |
| `single_line_text_field`           | `single_line_text_field`          |
| `multi_line_text_field`            | `multi_line_text_field`           |
| `rich_text_field`                  | `json` (ask) or `multi_line_text_field` |
| `number_integer`                   | `number_integer`                  |
| `number_decimal`                   | `number_decimal`                  |
| `json`                             | `json`                            |
| `boolean`, `date`, `url`, `color`, `weight`, `dimension`, `volume`, `rating`, `money` | no direct type: propose `single_line_text_field` (or `json` for measures), the developer decides |
| `list.single_line_text_field`      | `list__single_line_text_field`    |
| `list.number_integer` / `list.number_decimal` | `list__number_integer` / `list__number_decimal` |
| `product_reference`, `list.product_reference` | out of scope unless asked (needs the Flowkiwi IRIs of the targets, run after all products) |
| `metaobject_reference`, `file_reference`, other references | out of scope (no media, metaobjects need their own migration) |

1. Join definitions and value columns on `namespace.key`; report columns without a definition and definitions without values.
2. Shopify namespaces/keys shorter than 3 characters: propose a rename.
3. Skip `shopify.*` standard metafields unless the developer wants them (they are often category metafields better handled by the taxonomy module).
4. Value formats: Shopify CSV list values look like `["a","b"]` or `a; b`; normalize to the Flowkiwi expected format (check with one `create_metafield` on the pilot).
5. Record with `state.py decide <dir> --key metafields --value-json '<json>'`: kept definitions with their Flowkiwi type and any value transform.

## Shared elements

For each kept definition: look up with `call_operation list_metafield_definitions`; else `create_metafield_definition` `{"body": {"namespace": "custom", "key": "material", "name": "Material", "ownerType": "/api/metafield_owner_types/products", "type": "/api/metafield_definition_types/single_line_text_field"}}`. Record `metafield_definition:<namespace>.<key>` with module `metafields`. Never touch the `shopify.handle` marker definition here.

## Per product

For each kept `namespace.key` with a non-empty value:
1. `call_operation list_metafields` on `products/<id>`: a metafield with this definition exists -> record `reused`, skip.
2. Else `call_write_operation create_metafield` `{"path": {"ownerResource": "products", "ownerId": <id>}, "body": {"definition": <iri>, "value": "<value>"}}`.
3. Record a `metafield` item with module `metafields`.

## Verify

`get_product` with metafields on a pilot product (or `list_metafields`), compare with `products.json`.

## Undo notes

Metafields are deleted with the product children; definitions on a full or `module:metafields` undo.
