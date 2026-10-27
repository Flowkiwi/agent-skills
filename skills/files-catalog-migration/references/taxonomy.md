# Taxonomy

Optional concept: categories (a tree) and attributes, from `mappings/taxonomy.json` (field specs as in `references/mapping.md`, evaluated on the rows of each product of `products.json`) to `concepts/taxonomy.json`. `$S` is `python3 $SKILL/scripts` and `<dir>` the workspace.

## Effort

~20-40 min of decisions on the category tree and the attributes, no extra file.

## Map

Flowkiwi model: categories form a tree (`parent`); attribute definitions (`code`, `name`, `type` `list` or `string`, `multiple`) are attached to categories and inherited by every descendant; `list` definitions hold attribute options. A product has one `category` and `attributes[]` values (`definition` + `option` for list, `definition` + `value` for string).

The mapping has no `sources`: its columns are those of `mappings/products.json` (a category held by another file is added there as a `join` source).

```json
{"version": 1, "concept": "taxonomy",
 "categories": {"value": {"from": ["main:categorie"]}, "separator": "/", "drop_levels": 1, "depth": null,
                "map": {"PEM > Grille-pain": "Petit électroménager > Grille-pain",
                        "PEM > Bouilloire": "Petit électroménager > Bouilloire"},
                "rename": {}, "assign": {}, "status": "/api/statuses/ACTIVE"},
 "attributes": []}
```

| Key           | Meaning                                                                                                     | Default                |
| ------------- | ----------------------------------------------------------------------------------------------------------- | ---------------------- |
| `value`       | field spec of the category column, or `null` when the tree only comes from `assign`                         | `null`                 |
| `separator`   | splits a value into levels (`/`, `>`, `\|`); `null`: the whole value is one level                           | `null`                 |
| `drop_levels` | levels removed at the start of every path (a generic root such as `Catalogue`)                              | `0`                    |
| `depth`       | levels kept after `drop_levels`; `null`: all                                                                | `null`                 |
| `map`         | `{"<path>": "<new path>" or null}` on the path joined with ` > ` after the steps above; `null`: no category | `{}`                   |
| `rename`      | `{"<full path>": "<new name>"}`, one node renamed (path after `map`); also applied to `assign` paths        | `{}`                   |
| `assign`      | `{"<handle>": "<path>"}`, overrides the column for that product                                             | `{}`                   |
| `status`      | status IRI of the created categories                                                                        | `/api/statuses/ACTIVE` |

A product takes the category of its first row that gives one; a later row of the same product giving another one raises `group_conflict`.

1. **Category column**: the column holding a category, a range or a family (`Catégorie`, `Famille`, `Rayon`: names and samples in `inventory.json`, role in `$S/columns.py propose <dir>`). Several candidate columns: show their samples and ask which one structures the catalog.
2. **Separator and levels**: the separator seen in the samples; propose `drop_levels` when every value starts with the same generic root, `depth` when the tree is deeper than the developer wants. Rerun `$S/extract.py <dir> taxonomy` after each change: it prints the tree with product counts and the category count of the dry-run. Names need 3+ characters (`short_category_name`).
3. **Language**: category names follow `decisions.data_language`. Codes or abbreviations (`PEM`) and names in another language are translated through `map`, whole path to whole path (`{"PEM > Grille-pain": "Petit électroménager > Grille-pain"}`); show the translated tree. Same for names typed or deduced by you.
4. **Renames**: `rename` for a single node (`{"Petit électroménager": "Électroménager"}`), `assign` for a product the column puts in the wrong place.
5. **No structuring column**: propose a tree from the extracted products (titles, `product_category` and vendor in `products.json`): `"value": null`, every product in `assign`, at most 2 levels, names in the data language, recorded `"origin": "agent"` with its `reason` in the data language (`"reason": "Aucune colonne catégorie : arbre proposé à partir des titres."`). Show it as a proposal, never as data from the files: the developer validates or corrects it, and the dry-run lists it with the agent choices. Products left out stay without category (`uncategorized`).

## Attributes

Not built yet by this concept: keep `"attributes": []`.

## Record

1. `$S/extract.py <dir> taxonomy` writes `concepts/taxonomy.json` and prints the preview: the tree with the products per node (descendants included), the products without category, the attributes, the warnings and the category count. Show it to the developer.
2. Settle the warnings through the mapping, then extract again:

| Code                   | How it is settled                                                                              |
| ---------------------- | ---------------------------------------------------------------------------------------------- |
| `short_category_name`  | a category name under 3 characters: `rename` it or translate it through `map`                  |
| `unknown_handle`       | `assign` names a handle that is not in `products.json`: fix the handle or remove the entry     |
| `group_conflict`       | the rows of one product give different categories, the first wins: check it with the developer |
| `list_in_single_field` | the category spec gives several values (`pick: all`, `split`): give it a single value          |

3. `$S/state.py decide <dir> --key taxonomy --value-file <dir>/concepts/taxonomy.json --origin developer`.

`concepts/taxonomy.json`: `{"source": "mappings/taxonomy.json", "category_status": "<iri>", "categories": [{"path": "A > B", "name": "B", "parent": "A"}], "products": {"<handle>": "A > B"}, "attributes": [...], "uncategorized": ["<handle>"], "warnings": [...]}`, categories sorted by path, parents first. `payload.py` and `plan_shared.py` read it: any change goes through the mapping and this file, never through worker instructions. Never edit it by hand.

## Shared elements

From `$S/plan_shared.py <dir>` (`references/create.md` step 3), in its order, looking up before each creation (`list_categories` by name and parent, `call_operation list_attribute_definitions` by code, `list_attribute_options` by value):
1. Categories, parents first: `call_write_operation create_category` `{"body": {"name": "T-shirts", "parent": <iri or null>, "status": "/api/statuses/ACTIVE"}}`. Key `category:<full path>`.
2. Attribute definitions: `create_attribute_definition` `{"body": {"code": "color", "name": "Color", "type": "list", "multiple": false, "categories": [<iris>]}}`. Key `attribute_definition:<code>`.
3. Options of list definitions: `create_attribute_option` `{"path": {"definitionId": <id>}, "body": {"value": "Red", "position": 0}}`. Key `attribute_option:<code>:<value>`.

Record each with module `taxonomy` (`state.py shared`, or `--file` for a batch).

## Per product

`payload.py` resolves the category and attribute IRIs from the decision and `shared`: never map them by hand.
- `full` mode (the products are created with this concept): the payload `product.body` already holds `category` and `attributes` (`{"definition", "option"}` for list, `{"definition", "value"}` for string). Only definitions available on the product's category (or its ancestors) are accepted.
- `module-only` mode (the concept is added to products already created, `state.py worklists <dir> --module taxonomy`): `$S/payload.py <dir> <handle> --step taxonomy`, `get_product`, then `update_product` with that `category` and `attributes` (send the full attributes array: merge-patch replaces it).
- Record `{"kind": "taxonomy_link", "iri": "<product iri>#taxonomy", "status": "created", "module": "taxonomy"}` when the product got a category or attributes.

## Undo notes

`taxonomy_link`: `update_product` with `category: null` and `attributes: []` when the product is kept. Shared elements are deleted options -> definitions -> categories (children before parents). The API rejects deleting a definition or option still in use (409): report it.
