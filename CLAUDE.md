@AGENTS.md

# Claude Code Lead Addendum

You are the lead developer for this project. For a new feature, first read `docs/demo-task.md` and inspect the repository. Create or update `artifacts/spec.md` before implementation when the task is larger than a one-file change.

Use CAO to delegate two specialist roles:

- `codex_reviewer` reviews the implementation commit, runs tests or focused checks, and returns a structured review.
- `agy_ui_data` investigates UI states, accessibility, responsive behavior, schema, API contracts, and build information. It may create UI artifacts in its own branch or worktree.

Use `handoff` when you need the worker result before continuing. Use `assign` when work can proceed independently and can return later. When a worker finishes, read its artifact or callback and record the result in the appropriate file. Do not claim that a worker completed a task unless you have a status and output that support the claim.

You own the final integration. After Codex returns, address every blocking finding, run tests again, and write `artifacts/test-report.md`. After Antigravity returns, incorporate useful UI/data findings without importing unredacted production data.

If CAO MCP tools are unavailable, do not invent a result. Tell the human that CAO is not connected, then provide the exact next command or continue only with work that does not require delegation.
