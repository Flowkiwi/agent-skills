# Module: translations

## Effort

Medium: ~10 min to export, then a locale mapping question.

Only offer it when `list_locales` returns more than one locale.

## Collect

`exports/translations.csv` from Translate & Adapt (`references/collect.md` `## Translations`). Expected columns: `Type`, `Identification`, `Field`, `Locale`, `Market`, `Status`, `Default content`, `Translated content`. If the headers differ, show them and adapt.

## Map and consolidate

1. Keep rows with `Type` `PRODUCT` (and `PRODUCT_OPTION` / `PRODUCT_OPTION_VALUE` when present) and a non-empty `Translated content`.
2. `Identification` is the Shopify product id, not the handle. Build `id -> handle` from the rows where `Field` is `handle` (their `Default content` is the handle). Rows whose product id has no handle row are matched on `Field` `title` + `Default content` = product title only if that title is unique; otherwise report them.
3. Fields: `title` -> product `title`, `body_html` -> `description`. Option names/values: matched on `Default content` within the product. Other fields (SEO, metafields) are out of scope unless asked.
4. Map each Shopify locale (e.g. `en`, `de`) to a tenant locale code (`list_locales`, e.g. `en`, `de-DE`). The tenant default locale is never written by this module (it holds the core content).
5. Report counts per locale and the unmatched rows. Record with `state.py decide <dir> --key translations --value-json '<json>'`: the locale mapping and fields kept.

## Shared elements

None. Missing tenant locales are not created by this module: ask the developer to add them first (or `create_locale` if they explicitly ask).

## Per product

For each target locale with translations for this handle:
1. `update_product` with `locale` = the tenant locale and `title` / `description`. The approved migration plan counts as the explicit confirmation `update_product` requires.
2. Option titles and option values: find their update operations with `search_operations` ("update product option", "update option value"), then call them with `locale`.
3. Record `{"kind": "translation_link", "iri": "<product iri>#translations-<locale>", "status": "created", "module": "translations"}` per locale.

## Verify

`get_product` with `locale` set on a pilot product, for each target locale.

## Undo notes

`translation_link`: `update_product` with `locale` and `title: null`, `description: null` (null clears a translation) when the product is kept.
