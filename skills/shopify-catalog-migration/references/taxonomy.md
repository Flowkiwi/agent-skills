# Module: taxonomy

## Effort

Medium: no extra export, but ~15-30 min of decisions on the category tree and attributes.

## Collect

Nothing new: `product_category`, `type`, `tags` and option names from `products.json`.

## Map and consolidate

Flowkiwi model: categories form a tree (`parent`); attribute definitions (`code`, `name`, `type` `list` or `string`, `multiple`) are attached to categories and inherited by every descendant; `list` definitions hold attribute options. A product has one `category` and `attributes[]` values (`definition` + `option` for list, `definition` + `value` for string).

`T` = `python3 $SKILL/scripts/taxonomy.py`. Every option below goes to both `preview` and `build`.

1. **Category source**, ask which one: `product_category` (Shopify standard taxonomy, split on ` > ` into a path), `type` (flat), or a custom mapping table the developer edits (`--map mapping.json`: `{"<source value>": "<target path>" or null}`). Show `$T preview products.json --source <source>`: the tree with product counts per node, the products without a category with a suggested one (from their `type`, or a tag matching a category name), and the category count.
2. **Trim the tree**: propose to cut levels the developer does not want (`--depth 2`), to drop the first level (`--drop-root`) and to rename nodes (`--rename 'Old path=New name'`, repeatable, paths after the other options). Names need 3+ characters (`preview` warns). Rerun `preview` after each change; its final count line is the category count of the dry-run. With the Shopify standard taxonomy as source, the roots are generic (`Food, Beverages & Tobacco`, `Apparel & Accessories`, `Home & Garden`...): propose `--drop-root` when every product shares one root, or `--rename 'Food, Beverages & Tobacco=Food'` to keep a shorter name.
3. **Attributes**: `preview` lists candidates from tags `key:value` (e.g. `format:gros-format` -> `list` definition `format`, option labels proposed from slugs: `Gros format`), with their product counts, `multiple` when a product has several values, and the category they attach to (the lowest common ancestor of their products). Let the developer keep (`--attribute <key>`, repeatable), rename or drop each (`--no-attributes` for none). Other sources (recurring `metafields` keys: few distinct values -> `list`, many -> `string`) are added by hand to the built file. Variant option names are **not** attributes (they stay product options) unless the developer asks. Codes: lowercase, `[a-z0-9]` with `-` or `_`, 2-64 chars. Products without a category cannot hold attribute values (`preview` warns).
4. Record: `$T build products.json <same options> --output taxonomy.json`, review or edit the file with the developer, then `state.py decide <dir> --key taxonomy --value-file taxonomy.json`. Format: `{"categories": [{"path": "A > B", "name": "B", "parent": "A"}], "products": {"<handle>": "A > B"}, "attributes": [{"code", "name", "type": "list"|"string", "multiple", "categories": ["<path>"], "options": [{"value", "source"}], "values": {"<handle>": ["<option value or string>"]}}]}`. `payload.py` and `plan_shared.py` read it: any change goes through this file, never through worker instructions.

## Shared elements

From `python3 $SKILL/scripts/plan_shared.py <dir>` (SKILL.md Phase 3 step 3), in its order, looking up before each creation (`list_categories` by name and parent, `call_operation list_attribute_definitions` by code, `list_attribute_options` by value):
1. Categories, parents first: `call_write_operation create_category` `{"body": {"name": "T-shirts", "parent": <iri or null>, "status": "/api/statuses/ACTIVE"}}`. Key `category:<full path>`.
2. Attribute definitions: `create_attribute_definition` `{"body": {"code": "color", "name": "Color", "type": "list", "multiple": false, "categories": [<iris>]}}`. Key `attribute_definition:<code>`.
3. Options of list definitions: `create_attribute_option` `{"path": {"definitionId": <id>}, "body": {"value": "Red", "position": 0}}`. Key `attribute_option:<code>:<value>`.

Record each with module `taxonomy` (`state.py shared`, or `--file` for a batch).

## Per product

`payload.py` resolves the category and attribute IRIs from the decision and `shared`: never map them by hand.
- `full` mode: the payload `product.body` already holds `category` and `attributes` (`{"definition", "option"}` for list, `{"definition", "value"}` for string). Only definitions available on the product's category (or its ancestors) are accepted.
- `module-only` mode: `python3 $SKILL/scripts/payload.py <dir> <handle> --step taxonomy`, `get_product`, then `update_product` with that `category` and `attributes` (send the full attributes array: merge-patch replaces it).
- Record `{"kind": "taxonomy_link", "iri": "<product iri>#taxonomy", "status": "created", "module": "taxonomy"}` when the product got a category or attributes.

## Verify

`list_products` with `query.category` on 2 categories, compare counts; `get_product` on a pilot product to check its attribute values.

## Undo notes

`taxonomy_link`: `update_product` with `category: null` and `attributes: []` when the product is kept. Shared elements are deleted options -> definitions -> categories (children before parents). The API rejects deleting a definition or option still in use (409): report it.
