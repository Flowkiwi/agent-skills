# Mappings: declarative field specs

A mapping says, for one concept (`products`, then taxonomy, collections, metafields...), which source columns give each Flowkiwi field and how their values are converted. It lives in `<dir>/mappings/<concept>.json` (UTF-8) and is read by `scripts/mapping.py`; `$S/extract.py <dir> <concept>` applies it (`$S/extract.py <dir> products` for the products).

> [!IMPORTANT]
> A mapping is the source of truth: to change an extracted value, change the mapping (or `fixes.json`) and re-extract. Never edit the output file.

## File layout

```json
{"version": 1, "concept": "products",
 "sources": {"main": {"file": "demo-catalogue.xlsx", "sheet": "Catalogue"},
             "prices": {"file": "demo-tarifs.csv", "join": {"column": "prices:SKU", "to": "main:sku_marchand"}}},
 "key": {"from": ["main:sku_marchand"]}}
```

- `version` is `1`, `concept` names the concept. The other top-level keys are the field specs of the concept.
- `sources`: one entry per file, keyed by a short source id (`main`, `prices`). Exactly one source has no `join`: the main source, one output row per non-empty data row.

| Source key    | Meaning                                           | Default                                                 |
|---------------|---------------------------------------------------|---------------------------------------------------------|
| `file`        | file name in `<dir>/sources/`                     | required                                                |
| `sheet`       | sheet name (a csv has a single table named `csv`) | first sheet                                             |
| `header_rows` | number of header rows above the data              | detected as in the inventory (2 when row 2 holds codes) |
| `join`        | `{"column": "<its ref>", "to": "<main ref>"}`     | none: this is the main source                           |

Leading empty rows above the headers are skipped. Row numbers in warnings are sheet row numbers (the first data row of a sheet with 2 header rows is row 3).

## Column references

A reference is `"<source id>:<column>"`. `<column>` is matched against the technical code (header row 2) first, then against the label (header row 1), both cleaned. `@<letters>` selects by column letter.

- Prefer technical codes: `main:poids_net` rather than `main:Poids net du produit` or `main:@G`.
- Use `@<letter>` when a label (or code) is used by several columns: the reference is rejected with an error naming the columns (`'main:Prix' matches several columns (P, T); use 'main:@P' or 'main:@T'`).
- An unknown column is an error too. Every reference of the mapping is checked when the sources are loaded, before any row is read.

## Field spec

All keys are optional except one of `from` or `value`.

```json
{"from": ["main:poids_net", "main:dim_poids_net"], "pick": "first", "sep": "\n",
 "map": {"Inox": "Acier inoxydable"}, "map_ignore_case": false,
 "transform": ["decimal", {"scale_above": [100, 1000]}], "default": null,
 "conflicts": "warn", "origin": "agent", "reason": "...", "question": "..."}
```

| Key               | Meaning                                                                                                  | Default |
|-------------------|----------------------------------------------------------------------------------------------------------|---------|
| `from`            | column references, in priority order                                                                     | -       |
| `value`           | a constant instead of `from` (string, list or `null`)                                                    | -       |
| `pick`            | `first`: first non-empty value; `all`: list of the non-empty values, deduplicated; `join`: joined values | `first` |
| `sep`             | separator of `pick: join`                                                                                | `"\n"`  |
| `map`             | value replacements, exact match; a `null` target drops the value                                         | none    |
| `map_ignore_case` | `map` keys and the value compared casefolded                                                             | `false` |
| `transform`       | conversion steps, applied in order (see below)                                                           | none    |
| `default`         | value used when the result is empty                                                                      | `null`  |
| `conflicts`       | `warn` or `ignore`: with `pick: first`, warn when another column gives a different value                 | `warn`  |
| `origin`          | who decided this spec: `developer`, `agent` or `client`                                                  | none    |
| `reason`          | why, in one sentence                                                                                     | none    |
| `question`        | the open question to ask the client about this field                                                     | none    |

Evaluation order: the cleaned cell values of `from` -> `pick` -> `map` -> each `transform` step -> `default` when the result is empty. A list value (from `pick: all` or `split`) is mapped and transformed element by element; empty elements are dropped.

With `pick: first` and `conflicts: warn`, every other non-empty column of `from` goes through the same `map` and `transform`. A different result adds a `conflict` warning naming each column and its value; the first column wins. Numbers are compared as numbers: `1,8` and `1.8` (or `29,90` and `29.9`) with `decimal` are the same value, no conflict.

`origin`, `reason` and `question` document the decision; they also apply to non-spec objects (the `group` of products). `reason` and `question` are written in the data language (French for a French catalog): they are shown to the client.

## Transforms

A step is a name, or a one-key object for the transforms that take an argument.

| Step                             | Effect                                                                                                                                       | Example                                            |
|----------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------|
| `"decimal"`                      | removes spaces and a trailing `€`, `EUR` or `%`, `,` becomes `.`; must then be a plain number, else `not_a_number` and `null`                | `1 299,90 €` -> `1299.90`                          |
| `"integer"`                      | as `decimal`, then a whole number, else `not_a_number` and `null`                                                                            | `6.0` -> `6`, `6.5` -> `null`                      |
| `{"boolean": ["<yes>", "<no>"]}` | `oui yes y true vrai 1 x` (any case) give `<yes>`, `non no n false faux 0` give `<no>`, else `not_a_boolean` and `null`                      | `non` -> `Non` with `["Oui", "Non"]`               |
| `{"split": "<sep>"}`             | list of the stripped non-empty parts                                                                                                         | `A / B` -> `["A", "B"]` with `"/"`                 |
| `{"scale_above": [100, 1000]}`   | a number above the threshold is divided by the divisor (trailing zeros removed) and a `scaled` warning is added                              | `1964` -> `1.964`                                  |
| `{"regex": "<pattern>"}`         | group 1 of the first match, or the whole match without group; no match gives `no_match` and `null`                                           | `^(.+?)(?: - Noir)?$` on `Duo - Noir` -> `Duo`     |
| `"text_to_html"`                 | escapes `&`, `<`, `>`; blank-line separated paragraphs become `<p>...</p>`, a single newline `<br>`                                          | `a & b\nc\n\nd` -> `<p>a &amp; b<br>c</p><p>d</p>` |
| `"strip_html"`                   | `<br>`, `</p>`, `</li>`, `</hN>` become newlines, other tags removed, entities decoded, empty lines dropped, then cleaned (`&rsquo;` -> `'`) | `<p>a<br/>b</p>` -> `a\nb`                         |

Put `decimal` before `scale_above`. Steps after a step returning `null` are skipped.

## Joins

A secondary source with `"join": {"column": "prices:SKU", "to": "main:sku_marchand"}` attaches to each main row the first secondary row whose cleaned `SKU` equals the cleaned `sku_marchand`. Its columns are then referenced like the main ones (`prices:Prix public TTC`); they are empty for a main row without match.

| Warning          | Meaning                                                        | `row`                     |
|------------------|----------------------------------------------------------------|---------------------------|
| `join_duplicate` | several secondary rows share one key, the first one is used    | the ignored secondary row |
| `join_unmatched` | a secondary row matched by no main row (its values are unused) | the secondary row         |
| `join_missing`   | a main row with a non-empty key and no secondary row           | the main row              |

Join warnings have `field: null` and `handle: null`. Every warning is `{"row", "handle", "code", "field", "message", "stage": "extract"}`.

## Cleaning

Cell values are cleaned with the rules of `scripts/clean.py` (and `mappings/cleaning.json`) when the mapping first reads them, each cell once. The cleaning counts of the output cover exactly the cells the mapping reads. Header labels and codes are cleaned for matching but not counted.

## Worked example: `mappings/products.json`

The products mapping of the demo files (`demo-catalogue.xlsx` with 2 header rows, `demo-tarifs.csv` joined on the SKU):

```json
{"version": 1, "concept": "products",
 "sources": {"main": {"file": "demo-catalogue.xlsx", "sheet": "Catalogue"},
             "prices": {"file": "demo-tarifs.csv", "join": {"column": "prices:SKU", "to": "main:sku_marchand"}}},
 "key": {"from": ["main:sku_marchand", "main:ref_com", "main:ean"]},
 "group": {"key": {"from": ["main:ref_com", "main:sku_marchand"]}, "origin": "agent",
           "reason": "Les lignes de même référence commerciale sont les coloris d'un même modèle."},
 "product": {"title": {"from": ["main:accroche"], "transform": [{"regex": "^(.+?)(?: - (?:Noir|Rouge))?$"}]},
             "description_html": {"from": ["main:descriptif"]},
             "vendor": {"from": ["main:marque"]},
             "product_category": {"from": ["main:categorie"]},
             "images": {"from": ["main:visuel_princ"], "pick": "all"}},
 "options": [{"name": "Couleur", "value": {"from": ["main:coloris"]}}],
 "variant": {"sku": {"from": ["main:sku_marchand"]}, "barcode": {"from": ["main:ean"]},
             "price": {"from": ["prices:Prix public TTC", "main:prix"], "transform": ["decimal"]},
             "compare_at_price": {"from": ["prices:Prix barré"], "transform": ["decimal"]}}}
```

- `key` identifies a row: the SKU, else the commercial reference, else the EAN.
- `group` gathers the rows of one product (the two colors of `Grille-pain Duo` share `ref_com` 2230); its `reason` goes to the client.
- `title` drops the color suffix of the variant rows with a `regex`.
- `price` prefers the price file and falls back to the catalog column: for SKU 1004 the files disagree (`27,90 €` and `29,90`), which adds a `conflict` warning and keeps `27.90`.
- SKU 9999 of the price file gives `join_unmatched`; SKUs 1005 and 1006 have no price line and give `join_missing`.

Products keys: `key`, `group` (`null` or `{"key": <spec>, "origin"?, "reason"?}`), `product` (`title`, `description_html`, `vendor`, `tags`, `status`, `product_category`, `images`), `options` (`[{"name", "value": <spec>}]`) and `variant` (`sku`, `barcode`, `price`, `compare_at_price`).
