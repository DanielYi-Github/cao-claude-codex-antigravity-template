# Optional Antigravity MCP Configuration

The first demo intentionally runs without an external MCP server. This keeps the download safe and reproducible. Add an MCP server only after the local three-agent loop works.

## Recommended boundary

Use a separate read-only credential for data inspection. The MCP server should return schema, aggregates, or redacted records rather than entire production tables. Keep the server command and non-secret configuration in your local CAO setup; keep passwords, tokens, and private endpoints outside the repository.

## Profile shape

CAO profiles accept an `mcpServers` object. A command-backed server can be declared like this, using your own local command and an environment variable name rather than a literal secret:

```yaml
mcpServers:
  project-data-readonly:
    type: stdio
    command: /absolute/path/to/your-readonly-mcp-server
    args: []
    env:
      DATABASE_URL: ${PROJECT_READONLY_DATABASE_URL}
    timeout: 30
```

The exact environment-variable expansion behavior depends on the MCP server and CAO version. If the server does not expand `${...}` itself, configure the secret through the server's supported keychain or local environment mechanism instead of putting it in this file.

## Antigravity-specific note

The current CAO Antigravity provider reads MCP servers through `~/.gemini/config/mcp_config.json`. CAO merges profile-defined servers at launch and forwards the active terminal identity to the CAO MCP server. Antigravity requires the tmux backend and a completed `agy` sign-in/onboarding before a CAO-launched worker can reliably start.

Do not add `cao-mcp-server` to `agy_ui_data` unless you intentionally want Antigravity to delegate to other agents. In this template, Claude is the coordinator; Antigravity is a specialist worker. Keeping the worker without coordinator tools makes the intended topology easier to understand.

## Verification checklist

1. Test the MCP server directly with synthetic data.
2. Confirm that the configured account cannot write, migrate, or delete data.
3. Start Antigravity outside CAO once and confirm `agy models` works.
4. Run `./scripts/verify-local.sh`.
5. Run the interactive Claude lead session and ask Antigravity to report the server name, fields observed, and query result without returning raw secrets or production records.
6. Remove or rotate local credentials if a worker accidentally prints one into a terminal log or CAO workflow output.
