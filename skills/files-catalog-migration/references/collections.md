# Collections

Optional concept: collections (plain lists of products, possibly nested) from `mappings/collections.json` (field specs as in `references/mapping.md`, evaluated on the rows of each product of `products.json`) to `concepts/collections.json`. `$S` is `python3 $SKILL/scripts` and `<dir>` the workspace.

## Effort

~10 min of decisions, no extra file.

## Map

Flowkiwi model: a collection has a `name`, an optional `parent` collection and a `status`; a product has `collections[]`, any number of them. Collections are plain lists: their membership is set on the products when they are created, never by a rule.

The mapping has no `sources`: its columns are those of `mappings/products.json` (a grouping column held by another file is added there as a `join` source).

```json
{"version": 1, "concept": "collections",
 "value": {"from": ["main:gamme"], "map": {"ete": "Été"}, "map_ignore_case": true},
 "separator": null, "assign": {"2226": ["Petit déjeuner"]}, "status": "/api/statuses/ACTIVE"}
```

| Key         | Meaning                                                                                                          | Default                |
| ----------- | ---------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `value`     | field spec of the grouping column, or `null` when the collections only come from `assign`                        | `null`                 |
| `separator` | splits each value into nested levels (`/`, `>`); `null`: each value is one collection at the root                | `null`                 |
| `assign`    | `{"<handle>": ["<path>", ...]}`, paths with ` > ` between levels, added to the column ones of that product       | `{}`                   |
| `status`    | status IRI of the created collections                                                                            | `/api/statuses/ACTIVE` |

A product is in every collection its rows give (the value may be a list: `pick: all`, a `split` transform), in column then `assign` order, without duplicates. `map` and `transform` of the spec rename or drop values (a `null` map target: no collection).

1. **Grouping column**: a column holding a range, a universe, a season, a line or a theme (`Gamme`, `Univers`, `Saison`, `Collection`: names and samples in `inventory.json`, role in `$S/columns.py propose <dir>`). A column already used as the category tree (`references/taxonomy.md`) is not a collection too unless the developer asks for it. Several candidates: show their samples and ask which ones the developer wants as collections.
2. **Names**: names follow `decisions.data_language`. Codes, abbreviations or names in another language are translated through the `map` of the spec; names under 3 characters raise `short_collection_name`. Rerun `$S/extract.py <dir> collections` after each change: it prints the collections with their product counts.
3. **No grouping column**: propose a light set from the extracted products (titles, vendor, `product_category` in `products.json`): `"value": null`, the products in `assign`, at most 5 collections, names in the data language, recorded `"origin": "agent"` with its `reason` in the data language (`"reason": "Aucune colonne de regroupement : collections proposées à partir des titres."`). Show it as a proposal, never as data from the files: the developer validates or corrects it, and the dry-run lists it with the agent choices. No collection at all is a valid answer: then skip this concept.

## Record

1. `$S/extract.py <dir> collections` writes `concepts/collections.json` and prints the preview: the collections (nested ones indented) with their product counts, the warnings and the collection count. Show it to the developer.
2. Settle the warnings through the mapping, then extract again:

| Code                    | How it is settled                                                                          |
| ----------------------- | ------------------------------------------------------------------------------------------ |
| `short_collection_name` | a collection name under 3 characters: translate or rename it through `map`                 |
| `unknown_handle`        | `assign` names a handle that is not in `products.json`: fix the handle or remove the entry |

3. `$S/state.py decide <dir> --key collections --value-file <dir>/concepts/collections.json --origin developer` (`--origin agent` for a proposal).

`concepts/collections.json`: `{"status": "<iri>", "collections": [{"path": "A > B", "name": "B", "parent": "A"}], "membership": {"<handle>": ["A > B", "C"]}, "warnings": [...], "provenance": {"products": "<sha256>", "mapping": "<sha256>"}}` (`provenance`: what it was extracted from, checked by the dry-run, `references/state.md` `## Concept files`), collections sorted by path, parents first, products without collection left out of `membership`. `payload.py` and `plan_shared.py` read it: any change goes through the mapping and this file, never through worker instructions. Never edit it by hand.

## Shared elements

From `$S/plan_shared.py <dir>` (`references/create.md` step 3), in its order (after the taxonomy entries), parents first, looking up before each creation with `list_collections` `query.name` and the same parent; else `call_write_operation create_collection` `{"body": {"name": "Été", "parent": <iri or null>, "status": "/api/statuses/ACTIVE"}}`. Key `collection:<full path>`, recorded with module `collections` (`state.py shared`, or `--file` for a batch).

## Per product

`payload.py` resolves the collection IRIs from the decision and `shared`: never map them by hand.
- `full` mode (the products are created with this concept): the payload `product.body` already holds `collections: [<iris>]` for the `create_product` call.
- `module-only` mode (the concept is added to products already created, `state.py worklists <dir> --module collections`): `$S/payload.py <dir> <handle> --step collections` prints `{"handle": "<handle>", "collections": [<iris>]}`; `get_product`, then `update_product` with `collections` = existing + new (send the union: merge-patch replaces the array).
- Record `{"kind": "collection_link", "iri": "<product iri>#collections", "status": "created", "module": "collections"}`. Products with no collection get no link item.

## Undo notes

`collection_link`: `update_product` removing this migration's collections when the product is kept. Collections are deleted children before parents.
