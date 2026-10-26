# Module: costs and suppliers

## Effort

Low: ~2 min, data is in the product CSV (`Vendor`, `Cost per item`).

## Collect

Nothing new. Ask whether `Vendor` is the supplier or the brand. If it is the brand, ask for the supplier name to use (one for all, or per vendor).

## Map and consolidate

Flowkiwi purchase offers require `variant`, `supplier`, `country`, `tax`, `status` and `taxExcludedPrice`. Questions, recorded with `state.py decide <dir> --key costs --value-json '<json>'`:
1. Supplier mapping: distinct `vendor` values (case-insensitive, trimmed) -> supplier name. Supplier names need 3+ characters: propose a rename for shorter ones.
2. Country, tax and currency of the purchase offers (default: the first country of the prices module, or ask).
3. Is `Cost per item` tax-excluded? (Shopify cost is usually tax-excluded; if not, convert like the prices module.)
4. Offer status: `ACTIVE` by default.

Variants without `cost` are skipped and counted.

## Shared elements

For each supplier: look it up (`call_operation` search "list suppliers", filter by name), else `call_write_operation create_supplier` `{"body": {"name": "Acme"}}`. Record `supplier:<name>` with module `costs-suppliers`. Taxes and countries: same as the prices module, reuse their shared entries if they already exist.

## Per product

For each variant with a cost:
1. `call_operation list_purchase_offers` with `query.variant = <variant IRI>`: an offer from the same supplier and country exists -> record `reused`, skip.
2. Else `call_write_operation create_purchase_offer`:
   `{"body": {"variant": <iri>, "supplier": <iri>, "country": <iri>, "tax": <iri>, "status": <status>, "taxExcludedPrice": {"amount": "6.00", "currency": "EUR"}}}`
3. Record a `purchase_offer` item with module `costs-suppliers`.

## Verify

`list_purchase_offers` filtered by a pilot variant: amount and supplier match.

## Undo notes

Purchase offers are deleted with the products' children. Suppliers are deleted on a full or `module:costs-suppliers` undo if unused.
