# Sources: intake and inventory

## Intake

Client files are copied into the workspace by `$S/intake.py`, never read from where they are and never rewritten.

- Files dropped in the conversation are attachments with a local path given by the client app. Run `$S/intake.py add <dir> <path>...` on those paths. Never paste their content and never write them with a heredoc.
- Supported sources: `.xlsx`, `.xlsm`, `.csv`, `.tsv`, `.txt`. A `.xls` or `.ods` is copied then converted next to the copy with LibreOffice (both files are kept in `sources/`).
- The command prints one line per file: `copied`, `already in sources` (same name and same content, nothing is copied) or `converted`. Errors go to stderr.

Exit codes:

| Code | Meaning                                                | What to do                                                                                     |
|------|--------------------------------------------------------|------------------------------------------------------------------------------------------------|
| 0    | done                                                   | continue                                                                                       |
| 1    | same name in `sources/` with other content             | ask the developer whether it is a new version of the file, then add it again with `--replace`  |
| 2    | unsupported format (or file not found)                 | PDF, images and URLs are not v1 sources: say so and continue with the tabular files            |
| 3    | conversion needed and LibreOffice missing or failed    | ask the developer to open the file and save it as `.xlsx`, then add the `.xlsx`                |

A new version of a file later: `$S/intake.py add <dir> <path> --replace`. The old file is moved to `sources/previous/<YYYYMMDD-HHMMSS>-<name>` first.

## Inventory

Run `$S/inventory.py <dir>` after every intake (and after every `--replace`). It reads every `.xlsx`, `.xlsm`, `.csv`, `.tsv` and `.txt` of `sources/` (not `sources/previous/`, not hidden files, not an original `.xls` kept next to its `.xlsx`), writes `<dir>/inventory.json` and prints one line per sheet then one line per flagged column. Exit code 1 means a file cannot be read: say which one and ask the developer for a clean `.xlsx` export.

For each sheet the inventory records the header rows (1, or 2 when the second row holds technical codes such as `poids_net`), the number of data rows (empty rows skipped), the merged cells, and for each column its letter, label, code, filled count and rate, distinct count, longest value, kind (`empty`, `boolean`, `date`, `number`, `url`, `html`, `text`) and the first distinct values (`--samples 5` by default). Values are cleaned with the rules of `$S/clean.py` before being counted.

A second header row of technical codes is the preferred column reference (`poids_net` rather than `Poids net du produit` or `G`).

Flags:

| Flag               | Meaning                                                                                                                              |
|--------------------|--------------------------------------------------------------------------------------------------------------------------------------|
| `empty`            | no value in any data row                                                                                                             |
| `same_values`      | same values as the column `with` on every row where both are filled (set on the later column)                                        |
| `similar_label`    | same label words as the column `with` (brackets, accents, stopwords ignored); `conflicts` = rows filled in both that differ          |
| `label_mismatch`   | the label names a link (`pdf`, `url`, `lien`, `link`, `visuel`, `image`, `photo`) but under half of the values are URLs (`matching`) |
| `suspect_scale`    | numbers above 100 times the median of the column, likely another unit or a typing error                                              |
| `boolean_variants` | a yes/no column spelling one answer several ways (`OUI`, `oui`, `Oui`)                                                               |
| `value_variants`   | distinct values equal once case and accents are ignored (`Inox`, `inox`)                                                             |

After the run, summarize to the developer, sheet by sheet: the empty columns, the duplicates (`same_values`, `similar_label` with their conflict counts), the columns whose content does not match the label, the suspect scales and the spelling variants.

The inventory is the input of every mapping. Never re-read a source file to answer a question the inventory answers; when a sample is not enough, run the inventory again with more `--samples`.
