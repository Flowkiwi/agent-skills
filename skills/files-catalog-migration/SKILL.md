---
name: files-catalog-migration
description: Use when onboarding a Flowkiwi client whose catalog comes as spreadsheet files (xlsx, csv, old xls) rather than an e-commerce platform, to build that catalog in their Flowkiwi tenant through the Flowkiwi MCP, concept by concept, or to resume, retry, undo or run a later phase of such a migration after the client answered.
compatibility: Not validated yet. Written for any Agent Skills client with shell access and MCP (Claude Code, Codex, Cursor, Gemini CLI...); runs workers sequentially when the client cannot spawn subagents.
metadata:
  author: Flowkiwi
---

# Files catalog migration

Guide a Flowkiwi developer from client spreadsheet files (xlsx, csv, old xls) to a catalog created in a Flowkiwi tenant, **through the Flowkiwi MCP only**. Work concept by concept, starting from products and variants. Keep it simple for the developer: one question at a time, effort announced before each concept.

`$SKILL` below is this skill's folder: the base directory given when the skill loads (for example `.claude/skills/files-catalog-migration` or `.agents/skills/files-catalog-migration` in a project, the same under the home directory when installed globally, or a folder of the plugin cache when installed as a Claude Code plugin). Always use the base directory given at load time, never a guessed path. Scripts are run as `python3 $SKILL/scripts/<name>.py`. Migration workspaces are created under the current working directory, never inside `$SKILL`.

## Prerequisites

- An agent with shell access and an MCP client. Subagents are optional (see "Running workers").
- `python3` 3.8+ (scripts use the standard library only). Check once with `python3 --version`; if the command does not exist (often on Windows), use `python` or `py -3` instead everywhere this skill says `python3`, including in worker prompts.
- The Flowkiwi MCP server connected to the target tenant (`https://<tenant>.product-management.<env>.flowkiwi.net/api/mcp`, OAuth). If its tools are still missing after one retry (see "Start or resume") or ask for authentication, stop and give the developer the setup for their agent from `references/setup.md`.

## Hard rules

- **No write before confirmation.** Nothing is created in Flowkiwi before the developer approved the dry-run summary (Phase 3).
- **Check before create, check before retry.** Every creation is preceded by a lookup (state, then Flowkiwi). After any failed or timed-out write, look up before retrying. Never create a duplicate.
- **Never modify tenant items you did not create**, except reusing them as-is (recorded `reused`) or adopting an orphan, a product of this migration created before its `import.key` marker was written.
- **State after each step.** Shared elements go through `state.py shared`, products through worker result files merged with `state.py merge`. Never edit `state.json` by hand.
- **Images are out of scope.** Never try to create media; image URLs stay in `products.json` and are listed in the final report.
- **No bulk operation exists.** Do not search for one.
- **One data language.** Every name or label the migration writes that does not come verbatim from the source files (categories, attribute definitions and options, metafield definition names and descriptions, the marker definition, collections, option labels...) is in the data language recorded in Phase 2 (`decisions.data_language`), never in English by default and never in the language of this conversation.
- **Scripts compute, you propose.** Everything sent to Flowkiwi comes from a script output (`extract.py`, `payload.py`, `plan_shared.py`). Never clean, convert or retype a value yourself.
- **Never edit structured files by hand** (`products.json`, `concepts/*.json`, `state.json`): change `mappings/` or `fixes.json` and run the script again.
- **Client files are read-only.** They are copied into `sources/` by `intake.py`; never open a whole file in the conversation, read `inventory.json` samples instead.
- **Every decision has an origin** (`developer`, `agent`, `client`). A choice you make without the developer's answer is recorded `agent` with its reason, in the data language.
- **No magic.** Never present a proposed value, category or description as a fact from the data.
- **Half a day.** Group questions per concept, always with a default proposal; only ask about real ambiguities.
- Only load a concept reference when that concept is being worked on.

## Start or resume

Check the MCP first (`list_locales`, `list_channels`, `list_countries`), ask the client slug, then start Phase 1 or, when `migrations/<client-slug>/state.json` exists, offer to continue, retry failed, undo or run a later phase.

## Phase 1 - Intake

Copy the client files into the workspace without touching the originals, then inventory their sheets and columns.

## Phase 2 - Concepts

Take the concepts one by one in order, proposing a mapping from the inventory for each and recording every decision with its origin.

## Phase 3 - Create

Show a dry-run summary, wait for the developer's approval, then create the shared elements and the products.

## Running workers

Products are created by parallel subagents when the agent can spawn them, otherwise sequentially in the main session, with the same files and the same procedure.

## Undo

Only items created by this migration can be removed, and only after the developer confirmed the scope.
