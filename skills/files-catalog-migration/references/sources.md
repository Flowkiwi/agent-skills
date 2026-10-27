# Sources: intake

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
