# Setup: connect the Flowkiwi MCP

Load this file only when the Flowkiwi MCP tools are missing or ask for authentication. Give the developer the block for their agent, with `<tenant>` and `<env>` filled in (ask for them), then ask them to restart the agent or reload its MCP servers.

The server is a remote MCP over streamable HTTP with OAuth: `https://<tenant>.product-management.<env>.flowkiwi.net/api/mcp`. Name it `flowkiwi`.

Every block sends the header `Flowkiwi-Api-Version: 2026-10` to pin the API version this skill was written for. Keep it: without it the tenant answers with its default version, which can change.

Only Claude Code has been validated end to end. The other blocks follow each agent's documentation (checked 2026-10-26) and have not been tested yet: if one fails, say so to the developer rather than improvising.

## Claude Code

`.mcp.json` at the project root:

```json
{
  "mcpServers": {
    "flowkiwi": {
      "type": "http",
      "url": "https://<tenant>.product-management.<env>.flowkiwi.net/api/mcp",
      "headers": { "Flowkiwi-Api-Version": "2026-10" }
    }
  }
}
```

Authenticate with `/mcp` in a session.

## Codex

`~/.codex/config.toml` (or `.codex/config.toml` in a trusted project):

```toml
[mcp_servers.flowkiwi]
url = "https://<tenant>.product-management.<env>.flowkiwi.net/api/mcp"
http_headers = { "Flowkiwi-Api-Version" = "2026-10" }
```

Authenticate with `codex mcp login flowkiwi` in a shell.

## Cursor

`.cursor/mcp.json` in the project (or `~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "flowkiwi": {
      "url": "https://<tenant>.product-management.<env>.flowkiwi.net/api/mcp",
      "headers": { "Flowkiwi-Api-Version": "2026-10" }
    }
  }
}
```

Authenticate from Cursor settings, MCP section (Connect / Login on the `flowkiwi` server).

## Gemini CLI

`.gemini/settings.json` in the project (or `~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "flowkiwi": {
      "httpUrl": "https://<tenant>.product-management.<env>.flowkiwi.net/api/mcp",
      "headers": { "Flowkiwi-Api-Version": "2026-10" },
      "oauth": { "enabled": true }
    }
  }
}
```

Authenticate with `/mcp auth flowkiwi` in a session.

## Other agents

Any MCP client that supports remote streamable HTTP servers with OAuth (dynamic client registration) should work: declare the URL above under the name `flowkiwi` with the `Flowkiwi-Api-Version: 2026-10` header and run its OAuth login.
