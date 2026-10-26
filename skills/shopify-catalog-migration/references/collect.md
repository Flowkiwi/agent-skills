# Collect: what to get from the Shopify backoffice

Give the developer one instruction at a time, with the expected time. Files go into `migrations/<client-slug>/exports/`. Backoffice labels change over time: if the developer cannot find a menu, ask what they see instead of insisting.

## Core (always, ~5 min)

1. Shopify admin -> **Products** -> **Export**.
2. Choose **All products** and **CSV for Excel, Numbers, or other spreadsheet programs**.
3. Shopify emails the file (or downloads it for small catalogs). Save it as `exports/products.csv`.

This single file also feeds prices, costs, channels, taxonomy and metafield values. If the store has more than one language, it only contains the default one.

## Prices (low, ~5 min)

No extra file. Ask the developer to read in the backoffice:
- **Settings -> Taxes and duties**: is "All prices include tax" enabled? Tax rate(s) for the target countries.
- **Settings -> Markets**: the markets, their countries and currencies, and whether prices are fixed per market (then the CSV has `Price / <market>` columns).

## Costs and suppliers (low, ~2 min)

No extra file: `Cost per item` and `Vendor` are in the product CSV. Ask whether `Vendor` really is a supplier (sometimes it is the brand).

## Channels (low, ~5 min)

Ask the developer to list the sales channels in **Settings -> Apps and sales channels** (Online Store, POS, marketplaces...), and how they should map to Flowkiwi channels. The product CSV only tells whether a product is published on the online store (`Published`).

## Taxonomy (medium, ~15-30 min of decisions)

No extra file: `Type`, `Product Category` and tags come from the product CSV. The time goes into validating the proposed category tree and attributes.

## Collections (medium/high, ~5 min per collection)

The product CSV does not contain collections. For each collection in **Products -> Collections**:
- Note its title and type (manual or smart, with its conditions for smart ones).
- Get its products: open the collection, or filter the product list by collection and export that selection.

Ask the developer to fill `exports/collections.csv`:

```csv
collection_title,handle
Summer,tshirt-basic
Summer,cap
```

Optional `parent_title` column for nested collections.

## Metafields (medium/high, ~10-20 min)

1. **Settings -> Custom data -> Products** (and **Variants** if used): for each definition, note name, `namespace.key` and type. Save as `exports/metafield-definitions.csv` with columns `owner,namespace_key,name,type`.
2. When exporting products, the CSV includes metafield columns named `Label (product.metafields.<namespace>.<key>)`. If they are missing, re-export and make sure metafields are included.
3. The product CSV export does not include every metafield type: `json` metafields are left out, and other types may be. After parsing, compare the declared definitions with the columns (`metafields.py diff`, `references/metafields.md`) and list the missing ones to the developer: get them another way (Shopify bulk editor, an app export) or accept they are not migrated.
4. No definitions file (the developer cannot open Custom data)? `metafields.py infer` proposes one from the CSV columns.

## Translations (medium, ~10 min)

Only when the tenant has several locales (`list_locales`) and the store is multilingual.
1. Open the **Translate & Adapt** app -> **Export** (or **Settings -> Languages -> Export**).
2. Select the languages and the **Products** resource, export as CSV.
3. Save as `exports/translations.csv`.
