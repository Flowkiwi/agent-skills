# Module: selling prices

## Effort

Low: ~5 min to read tax and market settings, a few questions.

## Collect

From `references/collect.md` `## Prices`: tax-inclusive or not, tax rate per target country, markets with their countries and currencies.

## Map and consolidate

Questions, one at a time, recorded with `state.py decide <dir> --key prices --value-json '<json>'`:
1. Target countries (Flowkiwi `list_countries`, use the returned `@id`; missing ones become shared elements).
2. Currency per country.
3. Tax per country: `call_operation list_taxes` and match on **country IRI + numeric rate** (the API returns `"20.00"` for a tax created with `"20"`; names are free, e.g. `20%`). Propose the match, or a tax to create (`country` required, `name` 3+ chars, `rate` like `"20"` or `"5.5"`).
4. Do Shopify prices include tax? If yes, the rate used for conversion.
5. Which price source per country: `base` (`price` / `compare_at_price`, even when `market_prices` also has values) or `market:<name>` (`market_prices.<name>`) when prices are fixed per market.
6. Channels on the offers: all channels (omit the `channels` field) or a list of channel IRIs.
7. Offer status: `ACTIVE` (default) or `DRAFT`.

Example decision: `{"countries": [{"country": "/api/countries/019e9356-...", "currency": "EUR", "tax": "/api/taxes/019f84f8-...", "rate": "20", "tax_included": true, "source": "base"}], "channels": null, "status": "/api/statuses/ACTIVE"}`.

Price rules (amounts are strings with 2 decimals, `.` separator):
- Tax-excluded amount: `round(amount / (1 + rate / 100), 2)` when prices include tax, else the amount as is.
- If `compare_at_price` exists and is greater than `price`: `taxExcludedPrice` = compare-at converted, `discountTaxExcludedPrice` = price converted (the discounted price, not the discount amount). Otherwise `taxExcludedPrice` = price converted, no discount. Confirm this reading on the pilot: `get_variant` `resolvedPrice` must equal the Shopify `price`; if it does not, stop and tell the developer.
- A variant with no price for a country is skipped and counted in the final report.

Show 3 converted examples (TTC -> HT) for validation before going further.

## Shared elements

- Missing countries: `create_country` (check `describe_operation` first). Record `country:<code>`.
- Taxes: reused ones are recorded `reused`; missing ones `call_write_operation create_tax` `{"body": {"name": "TVA 20 FR", "rate": "20", "country": <country IRI>}}`. Record `tax:<country code>:<rate>` with module `prices`.

## Per product

For each variant (IRI from the core step or `state.json` items) and each target country:
1. `call_operation list_sale_offers` with `query.variant = <variant IRI>` and `query.country = <country IRI>`. An offer exists: record it `reused`, skip.
2. Else `call_write_operation create_sale_offer`:
   `{"body": {"variant": <iri>, "country": <iri>, "tax": <iri>, "status": <status>, "taxExcludedPrice": {"amount": "16.58", "currency": "EUR"}, "discountTaxExcludedPrice": null}}`, plus `"channels": [...]` only when restricted.
3. Record a `sale_offer` item with module `prices`.

## Verify

On a pilot product: `get_variant` with `resolveContext[channel]` and `resolveContext[country]`, check `resolvedPrice` against the Shopify price.

## Undo notes

Sale offers are deleted first. Taxes and countries created by this module are only deleted on a full or `module:prices` undo, and only if nothing else uses them (a 409 is reported, not forced).
