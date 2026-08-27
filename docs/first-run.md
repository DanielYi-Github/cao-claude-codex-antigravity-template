# First Run Guide

This template has two modes. Start with interactive mode so you can see how the three native CLIs behave. Use the repeatable workflow only after the three providers have authenticated and the basic session works.

## Interactive mode

Open three terminals:

```text
Terminal A: ./scripts/start-server.sh
Terminal B: ./scripts/launch-lead.sh
Terminal C: cao session list
```

In Terminal B, Claude Code is the lead. Ask it to read `docs/demo-task.md`, delegate one UI/data question to `agy_ui_data`, delegate one review question to `codex_reviewer`, and then summarize the returned results. The first run is intentionally small; do not start with a production repository or a task that can delete or migrate data.

If Claude says that CAO tools are unavailable, inspect whether `cao-mcp-server` is installed and whether the lead profile was installed under the same `CAO_HOME_DIR` as the server. If a worker appears idle but has no result, inspect the worker terminal output rather than immediately launching a duplicate worker.

## Repeatable workflow mode

The workflow is a sequential demo rather than a background daemon:

```bash
./scripts/run-demo.sh
```

It validates `.cao/workflows/three_agent_demo.py`, submits the run with an explicit run ID, and waits for the final Claude step. To follow a detached run later, use:

```bash
CAO_RUN_ID=three-agent-demo-2 ./scripts/run-demo.sh
cao workflow status three-agent-demo-2 --json
cao workflow result three-agent-demo-2 --json
```

The workflow writes only to the repository's `artifacts/` directory when an agent chooses to create reports. It uses a synthetic local JSON fixture, so no live database is needed for the first run.

## Manual recovery

If a provider is waiting for an approval or onboarding prompt, do not assume the workflow failed. Inspect its CAO session and terminal output:

```bash
cao session list
cao session status cao-claude-codex-agy --workers
```

Antigravity must be initialized once outside CAO with `agy`. Codex must be authenticated with `codex login` or its supported API-key flow. Claude Code must likewise be authenticated before the lead is launched.

If the run is no longer useful, stop only this project session:

```bash
cao shutdown --session cao-claude-codex-agy
```

Use `cao shutdown --all` only after confirming that no other project is using the same CAO runtime.
