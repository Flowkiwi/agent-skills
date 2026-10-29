---
name: files-catalog-migration
description: Use when onboarding a Flowkiwi client whose catalog comes as spreadsheet files (xlsx, csv, old xls) rather than an e-commerce platform, to build that catalog in their Flowkiwi tenant through the Flowkiwi MCP, concept by concept, or to resume, retry, undo or run a later phase of such a migration after the client answered.
compatibility: Not validated yet. Written for any Agent Skills client with shell access and MCP (Claude Code, Codex, Cursor, Gemini CLI...); runs workers sequentially when the client cannot spawn subagents.
metadata:
  author: Flowkiwi
---

# Files catalog migration

Guide a Flowkiwi developer from client spreadsheet files (xlsx, csv, old xls) to a catalog created in a Flowkiwi tenant, **through the Flowkiwi MCP only**. Work concept by concept, starting from products and variants. Keep it simple for the developer: one grouped question round per concept, always with a default proposal, effort announced before each concept.

`$SKILL` below is this skill's folder: the base directory given when the skill loads (for example `.claude/skills/files-catalog-migration` or `.agents/skills/files-catalog-migration` in a project, the same under the home directory when installed globally, or a folder of the plugin cache when installed as a Claude Code plugin). Always use the base directory given at load time, never a guessed path. Scripts are run as `python3 $SKILL/scripts/<name>.py`. Migration workspaces are created under the current working directory, never inside `$SKILL`.

## Prerequisites

- An agent with shell access and an MCP client. Subagents are optional (see "Running workers").
- `python3` 3.8+ (scripts use the standard library only). Check once with `python3 --version`; if the command does not exist (often on Windows), use `python` or `py -3` instead everywhere this skill says `python3`, including in worker prompts.
- The Flowkiwi MCP server connected to the target tenant (`https://<tenant>.product-management.<env>.flowkiwi.net/api/mcp`, OAuth). A cold tenant is slow at first: the OAuth and the first reads can time out, so the start tries each read 3 times (see "Start or resume"). Not connected (tools missing after the retries, or an authentication asked): stop and give the developer the setup for their agent from `references/setup.md`. Connected but slow: wait and retry, the setup is fine.

## Hard rules

- **No write before confirmation.** Nothing is created in Flowkiwi before the developer approved the dry-run summary (Phase 3), and nothing is updated, archived or deleted before the dry-run of the later phase was approved (`references/phases.md`).
- **Check before create, check before retry.** Every creation is preceded by a lookup (state, then Flowkiwi). After any failed or timed-out write, look up before retrying. Never create a duplicate.
- **Never modify tenant items you did not create**, except reusing them as-is (recorded `reused`) or adopting an orphan, a product of this migration created before its `import.key` marker was written.
- **State after each step.** Shared elements go through `state.py shared`, products through worker result files merged with `state.py merge`. Never edit `state.json` by hand.
- **Images are out of scope.** Never try to create media; image URLs stay in `products.json` and are listed in the final report.
- **No bulk operation exists.** Do not search for one.
- **One data language.** Every name or label the migration writes that does not come verbatim from the source files (categories, attribute definitions and options, metafield definition names and descriptions, the marker definition, collections, option labels...) is in the data language recorded in Phase 2 (`decisions.data_language`), never in English by default and never in the language of this conversation.
- **Scripts compute, you propose.** Everything sent to Flowkiwi comes from a script output (`extract.py`, `payload.py`, `plan_shared.py`). Never clean, convert or retype a value yourself.
- **Never edit structured files by hand** (`products.json`, `concepts/*.json`, `state.json`): change `mappings/` or `fixes.json` and run the script again.
- **Client files are read-only.** They are copied into `sources/` by `intake.py`; never open a whole file in the conversation, read `inventory.json` samples instead, or the cells of one row with `$S/inventory.py <dir> --row <n>`.
- **Every decision has an origin** (`developer`, `agent`, `client`). A choice you make without the developer's answer is recorded `agent` with its reason, in the data language.
- **No magic.** Never present a proposed value, category or description as a fact from the data.
- **Half a day.** Group questions per concept, always with a default proposal; only ask about real ambiguities.
- Only load a concept reference when that concept is being worked on.

## Start or resume

`$S` below is `python3 $SKILL/scripts` and `<dir>` the workspace folder. Work inside the workspace.

1. Check the MCP: call `list_locales`, `list_channels`, `list_countries`. The tools may be deferred: load them first (ToolSearch in Claude Code, client prefix such as `mcp__flowkiwi__`). Try each read 3 times, with `sleep 5`, then `sleep 15` between attempts: the tenant may be cold (the OAuth and the first calls can time out). Then tell the developer which case it is:
   - **Not connected**: the tools are still missing, or the MCP asks for authentication (OAuth): stop and give the setup for their agent from `references/setup.md`.
   - **Connected but slow**: the tools are there but the reads time out or answer 5xx: the setup is fine, the tenant is waking up: wait a minute and try again, before suspecting the setup.

   Once the reads answer, show the tenant (MCP URL from the agent's MCP configuration, or ask the developer for it; default locale, channels, countries).
2. Ask the client slug. Workspace: `migrations/<client-slug>/` (layout in `references/state.md`).
3. If `migrations/<client-slug>/state.json` exists: run `$S/state.py merge <dir>` then `$S/state.py summary <dir>`, show it, and offer: **continue** (at the first step not done), **add a concept** (Phase 2 menu, then `references/create.md` `## Concept added after creation` when the products already exist), **retry failed** (`references/create.md`, `--retry-failed`), **undo** (`references/undo.md`), **later phase** (the client answered or sent new files: "Later phases" below). Otherwise start Phase 1 (with files already in `sources/`, go on from the inventory).

## Phase 1 - Intake

~5 min. Read `references/sources.md`.

1. **Files**: ask for the client files (dropped in the conversation, or their paths). `$S/intake.py add <dir> <path>...` copies them into `sources/` (exit codes and conversion of old `.xls` files in `references/sources.md`). A new version of a file later: add it again with `--replace`.
2. **Inventory**: `$S/inventory.py <dir>`, then summarize the defects to the developer, sheet by sheet (`references/sources.md` `## Inventory`). Never open a source file: `inventory.json` answers the questions (`--samples 10` when more values are needed, `--row <n>` to see one row).
3. **Cleaning**: show `$S/clean.py table` once and ask "Keep these rules for this client?". Changes go to `<dir>/mappings/cleaning.json` (format in `references/state.md`). The answer is recorded once the state exists (Phase 2 step 5).

## Phase 2 - Concepts

Products always come first. Then offer the other concepts in menu order, one question at a time (one concept per question, with its effort), and never push one: "no" is a valid answer, and a concept can be added later (Start or resume, **add a concept**). Announce the effort before each concept and only load its reference then.

| Concept           | Brings                                      | Effort     | Reference                   |
| ----------------- | ------------------------------------------- | ---------- | --------------------------- |
| products (always) | products, options, variants                 | ~30-60 min | `references/products.md`    |
| taxonomy          | categories, attributes from columns         | ~20-40 min | `references/taxonomy.md`    |
| collections       | collections and memberships                 | ~10 min    | `references/collections.md` |
| metafields        | metafield definitions and values            | ~15 min    | `references/metafields.md`  |
| metaobjects       | "cms" entries (brand...) linked to products | ~10 min    | `references/metaobjects.md` |
| prices            | selling prices per country (TTC or HT)      | ~5-10 min  | `references/prices.md`      |

- **Column triage**, once, before taxonomy and metafields: `$S/columns.py propose <dir>` and show its table (every column of the mapped sources with a role: `core`, `attribute`, `metafield`, `description` or `ignore`, a type and the reason, sorted by role; long cells are cut in the table, whole in `tmp/columns-proposal.json`), so one validation covers attributes, metafields, descriptions and ignored columns. Each concept then starts from the rows of its role.
- **Prices last**: offers are keyed by SKU, so the prices are extracted after `codes.py fill` (a products step); extract them again whenever `products.json` changes.
- **After a products change** (new mapping, fixes, new file): `$S/extract.py <dir> all` extracts the products, then every concept with a `mappings/<concept>.json`, in menu order. Record each changed concept again (`state.py decide`): the dry-run blocks while a `concepts/<concept>.json` differs from its decision, or was extracted from another `products.json` or mapping than the current ones.

Per concept:

1. **Read** its reference.
2. **Propose** the mapping from `inventory.json`: for each field, the columns in priority order, the conversions and the defaults. Detect the data language from the samples (titles, descriptions). Ask the questions of the reference `## Questions` in **one grouped round**, each with its default proposal: the developer only answers what they want to change.
3. **Write** `<dir>/mappings/<concept>.json` (format in `references/mapping.md`). A choice made without the developer's answer carries `"origin": "agent"` and its `reason`, in the data language.
4. **Extract**: `$S/extract.py <dir> <concept>`. It prints the counts, the cleaning counts and the warnings grouped by code. Exit 1 is a mapping error naming the field: fix the mapping and run it again.
5. **State**, after the first extract only: `$S/state.py init <dir> --client <slug> --mcp-url <url>`. Then the **data language**: compare the detected language with the tenant default locale and ask the developer to confirm (they differ: ask which one wins), then `$S/state.py decide <dir> --key data_language --value-json '"fr"' --origin developer`. From now on, every name you propose (option labels, marker labels, later categories and attributes) is written in that language, and the dry-run lists them for review (`Concepts` section, `references/create.md` step 2). For a language `plan_shared.py` does not know (en, fr, de, es, it), also record `marker_labels` (`{"name": "...", "description": "..."}`) in that language. Then the **cleaning** answer of Phase 1: `$S/state.py decide <dir> --key cleaning --value-file <dir>/mappings/cleaning.json --origin developer` (`--value-json '{}'` when the rules are kept as they are). Later, after any extract that adds or drops products: `$S/state.py sync <dir>`.
6. **Warnings**: settle them as the reference `## Warnings` says, by changing the mapping or adding a `fixes.json` entry (format in `references/state.md`), then extract again. `extract.py` always rebuilds `products.json` from the source files, replaying `fixes.json` and keeping the codes generated earlier: never edit `products.json`.
7. Run the remaining steps of the reference (products: status, descriptions, codes, tenant collisions; other concepts: their `## Record` steps, ending with `$S/state.py decide <dir> --key <concept> --value-file <dir>/concepts/<concept>.json --origin developer|agent`).

## Phase 3 - Create

Read `references/create.md` and follow it: blocking check, dry-run summary and explicit approval, shared elements, pilot, full run, final report. Before asking for approval, tell the developer the MCP time from the `MCP effort` line of `dryrun.py` (about 8 s per call, for one worker: parallel workers split the products, not the shared elements).

End every phase with the client review: `$S/client_review.py <dir>` writes `client-review.md`, the questions, choices and missing data to send to the client (`references/review.md`).

## Later phases

When the client answered `client-review.md` or sent new or corrected files after products were created: read `references/phases.md` and follow its `## Procedure`. The answers are recorded with origin `client`, the structured files regenerated, and `diff.py` compares them with what the last import wrote: changed products are updated in place, removed products archived or deleted with one question for the whole phase, a grouping or key change is shown as removals plus creations and confirmed, products modified in Flowkiwi since the last import are left as they are unless the developer forces them, and destructive changes on shared elements are confirmed with the list of their products. Only what this migration created is ever written. The phase ends like the first one, with the final report and the client review.

## Running workers

Two modes, same files and same procedure:

- **Parallel** (the agent can spawn subagents, e.g. the Agent tool in Claude Code): one subagent per `worklists/worker-<n>.json`, all launched at once so they run in parallel. 4 workers by default, at most 6: more workers hit the MCP rate limit.
- **Sequential** (no subagents, or the developer prefers it): always `$S/state.py worklists <dir> --workers 1`, then **you** are the worker: generate the prompt of worker 1 (`$S/prompts.py <dir> worker --worker 1`) and follow it yourself, appending one result line per product. Run `$S/state.py merge <dir>` every 20 products so an interruption loses little. Keep tool outputs short (request only the fields you need). For large catalogs (several hundred products), suggest continuing in a fresh session from time to time: resuming is safe (merge, summary, continue).

In both modes:

- Generate each prompt with `$S/prompts.py <dir> worker --worker <n>` after `state.py worklists` (add `--module <concept>` for a concept added after creation, `--update` for the update run of a later phase; `$S/prompts.py <dir> shared` for the shared elements worker; `--python <command>` when `python3` is not the command). It fills the templates of `references/create.md` and `references/phases.md` with the absolute paths, the `started_at` of `state.json`, the MCP rules and the procedure, writes the prompt to `<dir>/tmp/prompts/<name>.md` and prints that path. Give each worker the content of its file verbatim: never fill a template by hand, never shorten or rewrite the prompt. Workers never read the references: everything they need is in that prompt.
- Workers (or you, in sequential mode) append one line per product to `results/worker-<n>.jsonl` with `state.py result`, every item with its `ref` (SKU, option title, `<option>:<value>` or `namespace.key`), and return a 3-line summary only. Do not ask them for details: read the state instead.
- After all workers return (or after each sequential batch), run `$S/state.py merge <dir>` before anything else. It rejects lines whose handle is not in that worker's worklist, or claimed by several workers with different IRIs, and writes them to `results/rejected-<stamp>.jsonl`: show them to the developer.

## Undo

Read `references/undo.md`. Only items recorded `created` by this migration are removed, after the developer confirmed the scope (`pilot`, `module:<name>`, `shared:<key>`, `product:<handle>` or `all`).
