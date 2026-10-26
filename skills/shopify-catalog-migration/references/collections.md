# Module: collections

## Effort

Medium to high: ~5 min per Shopify collection to list its products.

## Collect

`exports/collections.csv` filled by the developer (`references/collect.md` `## Collections`): `collection_title,handle[,parent_title]`.

## Map and consolidate

1. Read the file, report: collections and product counts, handles that are not in `products.json` (typo or product not exported), collection titles shorter than 3 characters.
2. Smart collections: Flowkiwi collections are plain lists. They are created with their membership **at export time**; say so to the developer once.
3. Status of the created collections: `ACTIVE` by default.
4. Record with `state.py decide <dir> --key collections --value-json '<json>'`: `{"status": "...", "membership": {"<handle>": ["<collection title>", ...]}}`.

## Shared elements

Parents first. Look up with `list_collections` `query.name`; else `call_write_operation create_collection` `{"body": {"name": "Summer", "parent": <iri or null>, "status": "/api/statuses/ACTIVE"}}`. Record `collection:<title>` with module `collections`.

## Per product

- `full` mode: `collections: [<iris>]` in the `create_product` body.
- `module-only` mode: `get_product`, then `update_product` with `collections` = existing + new (send the union).
- Record `{"kind": "collection_link", "iri": "<product iri>#collections", "status": "created", "module": "collections"}`. Products with no collection get no link item.

## Verify

`list_products` with `query.collections = <iri>` for 2 collections, compare counts with the file.

## Undo notes

`collection_link`: `update_product` removing this migration's collections when the product is kept. Collections are deleted children before parents.
