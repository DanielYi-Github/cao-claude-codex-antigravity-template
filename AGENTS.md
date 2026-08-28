# Multi-Agent Project Contract

This file is the canonical project contract for Claude Code, Codex CLI, and Antigravity CLI. Keep it concise, commit it with the project, and do not create a competing source of truth in another harness-specific file.

## Roles

Claude Code is the lead developer. It owns requirement analysis, architecture decisions, implementation, integration, test execution, and the final response to the human. Claude should delegate review and UI/data investigation instead of silently assuming those tasks were completed.

Codex CLI is the reviewer and debugger. It reads the requested commit or working tree, reproduces failures, runs safe checks, and returns findings. Codex should not modify the lead worktree unless the task explicitly asks it to create a separate patch or worktree. A review is not complete until it states what was checked and what remains uncertain.

Antigravity CLI is the UI and data worker. It explores UI composition, accessibility, responsive behavior, API contracts, schema, and build/deployment information through approved MCP tools. It may create UI artifacts in its own branch or worktree. Database access must be read-only unless the human explicitly approves a narrowly scoped write.

## Source-of-truth files

The human-readable task brief belongs in `docs/demo-task.md`. The lead should keep durable decisions in `artifacts/spec.md` and `artifacts/architecture.md`. Codex findings belong in `artifacts/review.md`. Antigravity findings belong in `artifacts/ui-notes.md` and `artifacts/schema.md`. Test results belong in `artifacts/test-report.md`.

Do not use chat history as the only handoff channel. A handoff must mention the relevant absolute or repository-relative artifact path, the commit or branch under review, the exact acceptance criteria, and the command used to verify the result.

## Git safety

Do not let Claude, Codex, and Antigravity write the same working tree concurrently. Use one of the following patterns:

1. The lead writes in the main feature branch, while Codex is read-only and Antigravity writes in a separate worktree.
2. Every writing agent uses a dedicated branch; the lead reviews and merges only after tests pass.

Never force-push, reset another agent's branch, delete unmerged work, or rewrite history without human approval. Never commit secrets, `.env` files, credentials, private keys, cookies, or generated CAO logs.

## Review contract

Codex must return a structured review with these headings:

```text
Summary
Blocking findings
Non-blocking findings
Tests executed
Suggested patch
Confidence and unknowns
```

Each finding must include a severity, file and line or symbol when possible, why it matters, and a concrete reproduction or fix suggestion. If no issue is found, Codex must say which checks were performed; “looks good” alone is not sufficient.

## UI and data contract

Antigravity must return a structured note with these headings:

```text
UI goals
Screens and states
Accessibility and responsive notes
Data sources and schema observations
MCP tools used
Artifacts and paths
Open questions
```

Do not copy production data into the repository. Redact personal data from screenshots and examples. Prefer synthetic fixtures over production data.

## Lead completion checklist

Before reporting completion, Claude must read the latest `artifacts/review.md`, respond to every blocking finding, read the latest UI/data notes, run the project tests, and write `artifacts/test-report.md`. If a worker timed out or returned an uncertain result, Claude must report it explicitly rather than claiming that the workflow succeeded.

## Communication style

Use short, explicit task messages. Ask workers to return paths, commit IDs, commands, and unknowns. Do not ask a read-only reviewer to write a file; ask it to return findings inline or use a designated writable report path only when its profile permits writing. Never include credentials in task prompts or artifact files.
