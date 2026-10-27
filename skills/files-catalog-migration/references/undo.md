# Undo

Removes what this migration created. Never touches items recorded `reused` or tenant items without a state entry.

## Available operations (checked 2026-10-26)

| Kind                   | How                                                                                                                                              |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `sale_offer`           | **no delete operation**: `update_sale_offer` with `status: /api/statuses/ARCHIVED`, unless its variant is deleted in the same run (then skip it) |
| `purchase_offer`       | **no delete operation**: `update_purchase_offer` with `status: /api/statuses/ARCHIVED`, same rule                                                |
| `metafield`            | `delete_metafield`                                                                                                                               |
| `variant`              | `delete_variant`                                                                                                                                 |
| `option_value`         | `delete_option_value`                                                                                                                            |
| `product_option`       | `delete_product_option`                                                                                                                          |
| `*_link`               | `update_product` removing the relation; skip when the product is deleted in the same run                                                         |
| `product`              | `delete_product`                                                                                                                                 |
| `collection`           | `delete_collection`                                                                                                                              |
| `attribute_option`     | `delete_attribute_option`                                                                                                                        |
| `attribute_definition` | `delete_attribute_definition`                                                                                                                    |
| `category`             | `delete_category`                                                                                                                                |
| `supplier`             | `delete_supplier`                                                                                                                                |
| `tax`                  | `delete_tax`                                                                                                                                     |
| `metafield_definition` | `delete_metafield_definition`                                                                                                                    |
| `channel`              | `delete_channel`                                                                                                                                 |
| `country`              | `delete_country`                                                                                                                                 |

This version only creates products, options, option values, variants, the `import.key` marker metafields and the marker definition; the other kinds come with later concepts. Check each operation with `describe_operation` before the first call (path argument names).

## Cascades (verified 2026-10-26)

- Deleting a **variant** also deletes its sale offers. Purchase offers were not verified: check with `get_purchase_offer` after the first variant deletion.
- Deleting a **product** is refused with 409 `product.cannot_delete_with_variants` while it has variants. Once its variants are gone, deleting it also deletes its options, option values and metafields (the `import.key` marker included).
- So, for a product in the plan: delete its variants, then the product, and record **all** its items as deleted (offers, metafields, options, option values included).
- A 404 on a later step means the item already went with its parent: count it as deleted.

## Procedure

1. Ask the scope: `pilot` (pilot products and their children), `module:<name>` (one module's items and shared elements, `module:core` for every product of this version and the marker definition), `shared:<key>` (one shared element and the product items tied to it, never the marker definition), or `all`.
2. `python3 $SKILL/scripts/state.py merge <dir>` then `python3 $SKILL/scripts/state.py undo-plan <dir> --scope <scope>`.
3. Show counts per kind and what will be archived instead of deleted. Wait for explicit confirmation: this is destructive.
4. Walk the plan in order (it is sorted children first). For each step:
   - Skip children whose product is also in the plan **only if** a read shows they are already gone after the product deletion (some deletions cascade, some are refused with 409 while children exist: then delete the children first, which the plan order already does).
   - Run the operation from the table. A 404 means it is already gone: count it as deleted.
   - A 409 (still in use) or any other error: record it, do not force, continue.
5. Write the IRIs actually deleted or archived to `<dir>/undo/deleted-<YYYYMMDD-HHMMSS>.json` (a JSON array), then `python3 $SKILL/scripts/state.py deleted <dir> --iris-file <that file>`.
6. Report: deleted, archived, already gone, refused (with reason). Products whose `product` item was deleted are now `deleted` in the state; a later run does not recreate them.

Items tied to a shared element carry its key in `"shared"` (the marker metafields record `metafield_definition:import.key`). With `shared:<key>` the plan lists the created items with that key, then the shared element itself if this migration created it. The marker definition `metafield_definition:import.key` is deleted last, and only on a full undo (`all` or `module:core`): without it, no product of this migration can be found again by marker and a later run would duplicate them. `state.py undo-plan` refuses the scope `shared:metafield_definition:import.key` (exit 1, use `all` or `module:core`).

Expected flow after a bad pilot: undo `pilot`, fix the mapping or the decision (`state.py decide`) and extract again, `python3 $SKILL/scripts/state.py reset-deleted <dir>` (deleted products become `pending` again, with no items), then rerun the pilot.
