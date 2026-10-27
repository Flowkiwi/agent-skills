# Selling prices

Optional concept: selling prices (one sale offer per variant and target country) from `mappings/prices.json` to `concepts/prices.json`. Prices come from the variants of `products.json` (`variant.price` / `variant.compare_at_price` of the products mapping), or from per-country field specs (as in `references/mapping.md`) evaluated on the row of each variant. `$S` is `python3 $SKILL/scripts` and `<dir>` the workspace.

## Effort

Low: ~5 min to read the tax settings in Flowkiwi, a few questions.

## Map

Flowkiwi model: a price is a sale offer, not a variant field: a variant, a country, a tax, a status, a tax-excluded price with its currency, an optional discounted tax-excluded price, and optionally the channels it is restricted to.

Questions, one at a time, written to `mappings/prices.json`:
1. **Target countries**: `list_countries`, use the returned `@id` and the country code. A missing country is created first (`## Shared elements`), then its IRI goes into the mapping.
2. **Currency** per country (`EUR`).
3. **Tax** per country: `call_operation list_taxes` and match on **country IRI + numeric rate** (the API returns `"20.00"` for a tax created with `"20"`; names are free, e.g. `20%`). Propose the match (`tax` = its IRI), or a tax to create (`tax: null`, `tax_name` 3+ characters, `rate` like `"20"` or `"5.5"`).
4. **Do the file prices include tax?** `tax_included`; `rate` is then the rate used for the conversion.
5. **Which price column** per country: by default the products mapping `variant.price` / `variant.compare_at_price` (already in `products.json`). A country priced from another column (a price list per country, a column per currency) gets its own `price` spec, and `compare_at_price` spec if it has one, in its country entry. A missing column is added as a `join` source of `mappings/products.json`.
6. **Channels** on the offers: all channels (`"channels": null`, the field is omitted) or a list of channel IRIs.
7. **Offer status**: `/api/statuses/ACTIVE` (default) or `/api/statuses/DRAFT`.

The mapping has no `sources`: its columns are those of `mappings/products.json`.

```json
{"version": 1, "concept": "prices",
 "countries": [{"country": "/api/countries/019e9356-...", "code": "FR", "currency": "EUR", "tax": null, "rate": "20",
                "tax_name": "TVA 20 FR", "tax_included": true},
               {"country": "/api/countries/019e9357-...", "code": "BE", "currency": "EUR", "tax": "/api/taxes/019f84f8-...",
                "rate": "21", "tax_included": true, "price": {"from": ["main:prix_be"], "transform": ["decimal"]}}],
 "channels": null, "status": "/api/statuses/ACTIVE"}
```

| Key                | Meaning                                                                                              | Default                |
| ------------------ | ---------------------------------------------------------------------------------------------------- | ---------------------- |
| `country`          | country IRI from `list_countries`                                                                    | required               |
| `code`             | 2-letter uppercase country code, unique in the mapping (offers and tax keys use it)                  | required               |
| `currency`         | 3-letter uppercase currency code                                                                     | required               |
| `tax`              | tax IRI from `list_taxes`, or `null`: `plan_shared.py` creates `tax:<code>:<rate>`                   | required               |
| `rate`             | tax rate in percent, `"20"`, `"5.5"` (a JSON number is accepted)                                     | required               |
| `tax_name`         | name of the tax to create, 3+ characters (`TVA 20 FR`); required when `tax` is `null`                | `null`                 |
| `tax_included`     | `true` when the prices include the tax                                                               | required               |
| `price`            | field spec of the price for this country; without it, `variant.price` of `products.json`             | none                   |
| `compare_at_price` | field spec of the compare-at price, only with `price`; without `price`, `variant.compare_at_price`   | none                   |
| `channels`         | top level: `null` (all channels) or a list of channel IRIs                                           | `null`                 |
| `status`           | top level: status IRI of the offers                                                                  | `/api/statuses/ACTIVE` |

Price rules (amounts are strings with 2 decimals, `.` separator):
- Tax-excluded amount: `amount / (1 + rate / 100)` when the prices include tax, else the amount as is, rounded half-up to 2 decimals (`19.90` at 20% gives `16.58`, `5.00` at 5.5% gives `4.74`).
- If the compare-at price exists and is greater than the price: `taxExcludedPrice` = compare-at converted, `discountTaxExcludedPrice` = price converted (the discounted price, not the discount amount). Otherwise `taxExcludedPrice` = price converted, no discount (a compare-at price not above the price is ignored).
- A variant with no price for a country is skipped and counted (`missing_price`), never a failure.

## Record

Offers are keyed by SKU: fill the missing codes first (`$S/codes.py fill`, `references/products.md`), then extract the prices again whenever `products.json` changes.

1. `$S/extract.py <dir> prices` writes `concepts/prices.json` and prints the preview: per country the currency, the tax (to create or reused), the offer and discount counts and the first 3 converted offers, then the warnings grouped by code (with their row and handle) and the totals.
2. **Show 3 converted examples for validation** before going further, each next to its source price (`products.json` variant, or the cell of the `price` column): `39.90 TTC, compare-at 49.90 -> 41.58 with discount 33.25`, `45 TTC -> 37.50`, one more with a discount or another rate. Wait for the developer to confirm the reading.
3. Settle the warnings through the mapping, then extract again:

| Code                   | How it is settled                                                                                                        |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `missing_price`        | no price for this SKU and country, offer skipped: fill the column, or accept it (listed for the client)                  |
| `not_a_number`         | the price is not an amount of 0 or more (offer skipped) or the compare-at price is not (ignored): fix the spec           |
| `zero_price`           | a price of 0: a `0.00` offer is created; check it with the developer (listed for the client) or map it to an empty value |
| `missing_sku`          | the variant has no SKU, offer skipped, blocks the dry-run: fill the codes, then extract again                            |
| `duplicate_sku`        | two variants of a product share a SKU, offer skipped: fix the SKU (`fixes.json`)                                         |
| `list_in_single_field` | a price spec gives several values (first kept): `pick: first` or a single column                                         |
| `conflict`             | the columns of a `from` list disagree: settle it as in `references/mapping.md`                                           |

4. `$S/state.py decide <dir> --key prices --value-file <dir>/concepts/prices.json --origin developer`.

`concepts/prices.json`: `{"countries": [{"country", "code", "currency", "tax", "rate", "tax_name", "tax_included"}], "channels": null, "status": "<iri>", "offers": {"<handle>": {"<sku>": {"<code>": {"taxExcludedPrice": "16.58", "discountTaxExcludedPrice": null}}}}, "warnings": [...], "provenance": {"products": "<sha256>", "mapping": "<sha256>"}}` (`provenance`: what it was extracted from, checked by the dry-run, `references/state.md` `## Concept files`): the mapping without its field specs (`rate` as text without trailing zeros), products and variants without any offer left out of `offers`. Every warning also carries `sku` (`null` for `missing_sku`) and `country`. `tax_name` is cleaned like the cells. `payload.py` and `plan_shared.py` read it: any change goes through the mapping and this file, never through worker instructions. Never edit it by hand.

## Shared elements

- **Countries** must exist before the mapping is written: `list_countries`. A missing one: `create_country` (check `describe_operation` first), record `country:<code>` with module `prices`, and put its IRI in the mapping.
- **Taxes** from `$S/plan_shared.py <dir>` (`references/create.md` step 3), at the end of its order: one entry per country with `"tax": null`, key `tax:<country code>:<rate>`. Look up first with `call_operation list_taxes` on the country IRI and the numeric rate (`20` matches `"20.00"`): found, record it `reused`; else `call_write_operation create_tax` `{"body": {"name": "TVA 20 FR", "rate": "20", "country": <country IRI>}}`. Record the key with module `prices` and the `spec` of the plan entry.

## Per product

`payload.py` builds one entry per variant and country in the payload `prices` (after the variants in full mode; `$S/payload.py <dir> <handle> --step prices` in module-only mode, after `$S/state.py worklists <dir> --module prices`): `{"sku", "country": "<code>", "body"}`, tax IRI from the country or from shared `tax:<code>:<rate>`. For each entry:
1. Replace `<variant IRI of <sku>>` in the body with the IRI of the variant with that SKU (the `sku -> variant IRI` map of the core step, or the `variant` items of the product in `state.json`, `ref` = SKU).
2. `call_operation list_sale_offers` with `query.variant = <variant IRI>` and `query.country = <country IRI>`. An offer exists: record it `reused`, skip.
3. Else `call_write_operation create_sale_offer` `{"body": <entry body>}`, copied verbatim: `{"variant": <iri>, "country": <iri>, "tax": <iri>, "status": <status>, "taxExcludedPrice": {"amount": "16.58", "currency": "EUR"}, "discountTaxExcludedPrice": null}`, plus `"channels": [...]` only when restricted.
4. Record a `sale_offer` item with module `prices` and `"ref"` = `<sku>:<code>` (`1001:FR`).

## Verify

On a pilot product: `get_variant` with `resolveContext[channel]` and `resolveContext[country]`, `resolvedPrice` must equal the file price (the discounted one when a compare-at price applies). If it does not, stop and tell the developer.

## Undo notes

Sale offers are undone first (no delete operation: archived, `references/undo.md`). Taxes and countries created by this module are only deleted on a full or `module:prices` undo, and only if nothing else uses them (a 409 is reported, not forced).
