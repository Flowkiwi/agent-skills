# Module: taxonomy

## Effort

Medium: no extra export, but ~15-30 min of decisions on the category tree and attributes.

## Collect

Nothing new: `product_category`, `type`, `tags` and option names from `products.json`.

## Map and consolidate

Flowkiwi model: categories form a tree (`parent`); attribute definitions (`code`, `name`, `type` `list` or `string`, `multiple`) are attached to categories and inherited by every descendant; `list` definitions hold attribute options. A product has one `category` and `attributes[]` values (`definition` + `option` for list, `definition` + `value` for string).

1. **Category source**, ask which one: `product_category` (Shopify standard taxonomy, split on ` > ` into a path), `type` (flat), or a custom mapping table the developer edits. Show the resulting tree with product counts per node, then the products without a category.
2. **Trim the tree**: propose to cut levels the developer does not want (e.g. keep 2 levels). Names need 3+ characters.
3. **Attributes**: propose candidates and let the developer keep, rename or drop each:
   - tag patterns `key:value` (e.g. `color:red`) -> `list` definition `color` with options,
   - recurring `metafields` keys with few distinct values -> `list`, many values -> `string`,
   - variant option names are **not** attributes (they stay product options) unless the developer asks.
   Codes: lowercase, `[a-z0-9]` with `-` or `_`, 2-64 chars. Attach each definition to the highest category that needs it.
4. Record with `state.py decide <dir> --key taxonomy --value-json '<json>'`: the tree (path -> parent path), the product -> category rule, the definitions (code, name, type, multiple, categories, source) and per-product value extraction rule.

## Shared elements

In this order, looking up before each creation (`list_categories` by name and parent, `call_operation list_attribute_definitions` by code, `list_attribute_options` by value):
1. Categories, parents first: `call_write_operation create_category` `{"body": {"name": "T-shirts", "parent": <iri or null>, "status": "/api/statuses/ACTIVE"}}`. Key `category:<full path>`.
2. Attribute definitions: `create_attribute_definition` `{"body": {"code": "color", "name": "Color", "type": "list", "multiple": false, "categories": [<iris>]}}`. Key `attribute_definition:<code>`.
3. Options of list definitions: `create_attribute_option` `{"path": {"definitionId": <id>}, "body": {"value": "Red", "position": 0}}`. Key `attribute_option:<code>:<value>`.

Record each with `state.py shared ... --module taxonomy`.

## Per product

- `full` mode: add `category` and `attributes` to the `create_product` body: `{"definition": <iri>, "option": <option iri>}` for list, `{"definition": <iri>, "value": "..."}` for string. Only definitions available on the product's category (or its ancestors) are accepted.
- `module-only` mode: `get_product`, then `update_product` with `category` and `attributes` (send the full attributes array: merge-patch replaces it).
- Record `{"kind": "taxonomy_link", "iri": "<product iri>#taxonomy", "status": "created", "module": "taxonomy"}`.

## Verify

`list_products` with `query.category` on 2 categories, compare counts; `get_product` on a pilot product to check its attribute values.

## Undo notes

`taxonomy_link`: `update_product` with `category: null` and `attributes: []` when the product is kept. Shared elements are deleted options -> definitions -> categories (children before parents). The API rejects deleting a definition or option still in use (409): report it.
