# Module: channels

## Effort

Low: ~5 min to list the Shopify sales channels and map them.

## Collect

From `references/collect.md` `## Channels`: Shopify sales channels in use. The CSV only has `published` (online store).

## Map and consolidate

1. Show Flowkiwi channels (`list_channels`).
2. Ask the rule, recorded with `state.py decide <dir> --key channels --value-json '<json>'`. Usual rules:
   - `published: true` -> channel X (e.g. `B2C`), `published: false` -> no channel.
   - every product -> channels X and Y.
3. Channels that do not exist yet become shared elements (with the developer's approval of the name).

Example: `{"published": ["/api/channels/<id>"], "unpublished": []}`.

## Shared elements

`call_write_operation create_channel` for each new channel (check `describe_operation create_channel` for required fields). Record `channel:<name>` with module `channels`.

## Per product

- `full` mode: pass `channels: [...]` in the `create_product` body (core step 5).
- `module-only` mode: `get_product`, then `update_product` with `channels` = existing channels + the new ones (merge-patch replaces the whole array, so always send the union). The developer's approval of the migration counts as the confirmation `update_product` requires.

In both modes, record a link item (a link is not a deletable resource, the item tells undo and `worklists --module` that it was done): `{"kind": "channel_link", "iri": "<product iri>#channels", "status": "created", "module": "channels"}`.

## Verify

`list_products` with `query.channels = <channel IRI>` and compare the count with the expected number.

## Undo notes

`channel_link` items are not deletable resources: undo of this module means `update_product` with the channels removed (send the remaining ones). Channels created by this module are deleted on a full or `module:channels` undo if unused.
