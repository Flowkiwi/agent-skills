# Client review

`client-review.md` is the document sent to the client after each phase: the questions still open, the choices made for them while waiting for their answer, and the missing data. It is written in the data language and must be sendable as is.

## Generate

After the final report of each phase (`references/create.md` step 6):

1. `$S/client_review.py <dir>` writes `<dir>/client-review.md` and prints `<n> questions, <n> choices, <n> missing data items`. A `warning: agent choice without reason` line names a `"origin": "agent"` left out of the document: give it a `reason` in the mapping (or record the decision again with `--reason`), then run it again.
2. Show the counts and review the document with the developer before anything is sent. It must not mention internal tooling: no script, file or MCP name, no IRI, no column id like `main:ref_com` (columns are named by the client's labels from `inventory.json`).
3. Tell the developer the file to send: `<dir>/client-review.md`.

## Content

One `##` section per concept, in the order products, taxonomy, collections, metafields, metaobjects, prices, then the other decisions, only when it has something to say. Each section has up to three parts:

| Part         | Comes from                                                                                                                                                                                   |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Questions    | every `question` of `mappings/*.json` whose `origin` is not `client`, in mapping order                                                                                                       |
| Choices      | every `"origin": "agent"` of the mappings and of the decisions in `state.json`, with its `reason` and the labels of the columns it reads                                                     |
| Missing data | products: keys generated for rows without reference (`fixes.json`), empty columns of the mapped sources, generated barcodes and SKUs, generated descriptions; prices: variants without price |

The missing prices are read from `concepts/prices.json`, so extract the prices again before generating the document when `products.json` changed.

`reason` and `question` texts are copied as they are: write them for the client, in the data language, one sentence each, without technical words. Typographic characters are replaced by plain ones (the cleaning rules of `clean.py`).

## Answers

The client answers under each point. Record each answer with origin `client`: in the mapping (`"origin": "client"`, the `reason` updated and the `question` removed) or with `state.py decide --origin client`. A choice or question recorded `client` is no longer listed, so the next document only holds what is still open.

## Other languages

Labels are built in for `fr` and `en`. For another data language, `client_review.py` exits 1 with `no labels for '<lang>': pass --labels`: write a JSON file with the keys of `LABELS["fr"]` in `scripts/client_review.py` translated (`title`, `intro`, `questions`, `choices`, `missing`, `column`, `concepts` with one label per section, and the count labels with their `{n}`, `{titles}`, `{labels}`, `{barcodes}`, `{skus}` placeholders kept). The `_one` keys are the singular forms used when the count is 1; they are optional. Then run `$S/client_review.py <dir> --labels <file.json>`. Keep the file in the workspace (`<dir>/review-labels.json`) for the next phases.
