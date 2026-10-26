# Agent skills

Flowkiwi skills for agent collaboration. Every skill lives in its own folder under `skills/` and can be installed on its own.

## Skills

| Skill                                                           | Use it to                                                                                                  | Validated with | Should work with (untested) |
| --------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------- | -------------- | --------------------------- |
| [`shopify-catalog-migration`](skills/shopify-catalog-migration) | Rebuild a Shopify store catalog in a Flowkiwi tenant through the Flowkiwi MCP, from backoffice CSV exports | Claude Code    | Codex, Cursor, Gemini CLI   |

"Should work with" means the skill only relies on the Agent Skills format, a shell and an MCP client, but nobody has run it with that agent yet. Please report what breaks.

### shopify-catalog-migration

- Ask your agent to migrate a Shopify catalog, for example "migrate the Shopify catalog of acme into Flowkiwi". The skill guides you through the Shopify exports, the consolidation questions and a dry-run before writing anything.
- When the agent can spawn subagents (Claude Code, Cursor), products are created by parallel workers; otherwise they are created sequentially in the main session, which is slower and better split across sessions for large catalogs.
- If the Flowkiwi MCP is not connected, the skill shows the setup for your agent (`references/setup.md`).

> [!WARNING]
> Migration workspaces are created in `migrations/<client-slug>/` under the current directory. They contain client data: keep them out of git.

## Install a skill

### As a Claude Code plugin (recommended)

Each skill is a plugin of the `flowkiwi` marketplace. In Claude Code (2.1.275 or later):

```text
/plugin install <skill-name> --marketplace Flowkiwi/agent-skills
```

Or in two steps, which also works on older versions and from the shell (`claude plugin ...`):

```text
/plugin marketplace add Flowkiwi/agent-skills
/plugin install <skill-name>@flowkiwi
```

Updates come with `/plugin marketplace update flowkiwi`. As a plugin, a skill is invoked as `/<skill-name>:<skill-name>`, or simply by asking for what it does.

### With the `skills` CLI (any agent)

With [`skills`](https://github.com/vercel-labs/skills) (Node.js 22.20+), which copies the skill where each agent looks for it. Pick the agent with `-a`, or omit it to choose interactively:

```bash
# In the current project
npx skills add Flowkiwi/agent-skills --skill <skill-name> -a claude-code
npx skills add Flowkiwi/agent-skills --skill <skill-name> -a codex
npx skills add Flowkiwi/agent-skills --skill <skill-name> -a cursor
npx skills add Flowkiwi/agent-skills --skill <skill-name> -a gemini-cli

# For every project: add -g
npx skills add Flowkiwi/agent-skills --skill <skill-name> -a codex -g
```

> [!WARNING]
> The CLI is `skills` (plural). `npx skill` (singular) is an unrelated npm package.

Restart the agent (or open a new session) so it picks up the new skill.

## Requirements

Each skill lists its own requirements in its `SKILL.md`. Most of them need:

- `python3` 3.8+ for the helper scripts (standard library only).
- The Flowkiwi MCP server (`https://<tenant>.product-management.<env>.flowkiwi.net/api/mcp`, streamable HTTP with OAuth), declared under the name `flowkiwi`:

| Agent       | Config file             | Entry                                                          | Authenticate               |
| ----------- | ----------------------- | -------------------------------------------------------------- | -------------------------- |
| Claude Code | `.mcp.json`             | `"flowkiwi": {"type": "http", "url": "<url>"}`                 | `/mcp` in a session        |
| Codex       | `~/.codex/config.toml`  | `[mcp_servers.flowkiwi]` then `url = "<url>"`                  | `codex mcp login flowkiwi` |
| Cursor      | `.cursor/mcp.json`      | `"flowkiwi": {"url": "<url>"}`                                 | Cursor settings, MCP       |
| Gemini CLI  | `.gemini/settings.json` | `"flowkiwi": {"httpUrl": "<url>", "oauth": {"enabled": true}}` | `/mcp auth flowkiwi`       |

## Add a skill to this repository

1. Create `skills/<name>/SKILL.md` with the frontmatter below, plus any `references/`, `scripts/` or `assets/` it needs. Keep the folder self-contained: it is installed alone.

   ```yaml
   ---
   name: <name>
   description: Use when ...
   compatibility: Validated with Claude Code. <other agents and their status>
   metadata:
     author: Flowkiwi
   ---
   ```

2. Add a plugin entry to `.claude-plugin/marketplace.json` (marketplace name `flowkiwi`), with `"source": "./"`, `"strict": false` and `"skills": ["./skills/<name>"]`, so the plugin loads only this skill.
3. Add a row to the skills table above.
4. Run `python3 scripts/check_skills.py`, `claude plugin validate . --strict` and `npx skills add . --list`. The CI runs the same checks plus every skill's script tests on Python 3.8 and 3.13.
5. Only list an agent as validated after a real run with it.

Helper script tests of a skill:

```bash
cd skills/<name> && python3 -m unittest discover -s scripts/tests
```

### Allowed paths

The repository is public. Only these paths may be committed, and the CI rejects any other file:

| Path                                 | Content                                                              |
| ------------------------------------ | -------------------------------------------------------------------- |
| `skills/<name>/SKILL.md`             | the skill entry point                                                |
| `skills/<name>/references/`          | reference files loaded on demand                                     |
| `skills/<name>/scripts/`             | helper scripts and their tests (`scripts/tests/`, fixtures included) |
| `skills/<name>/assets/`              | static files the skill needs                                         |
| `.claude-plugin/marketplace.json`    | the Claude Code marketplace                                          |
| `.github/`                           | CI, Dependabot, PR template                                          |
| `scripts/`                           | repository tooling and its tests                                     |
| `README.md`, `LICENSE`, `.gitignore` | repository files                                                     |

Data files (fixtures, samples, assets) must be invented demo data, never a real client's export, migration workspace or MCP output. To allow a new path, update this table and `ALLOWED_PATHS` in `scripts/check_skills.py` in the same PR.

## License

[MIT](LICENSE)
