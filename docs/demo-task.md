# Demo Task: Project Health Dashboard

Build a small project-health dashboard using the files in this repository. The dashboard should show a project name, a health score, recent checks, and an empty/loading/error state. Use the local synthetic data in `src/data/project_health.json`; do not connect to a real database.

## Acceptance criteria

1. The data shape is documented in `docs/data-contract.md`.
2. The UI must be readable on narrow and wide screens.
3. The UI must expose meaningful headings and labels for keyboard and screen-reader users.
4. The loading, empty, and error states must be represented in the component or prototype notes.
5. The project must not read credentials, call production services, or write outside the repository.
6. A reviewer must be able to run the local checks with `./scripts/verify-local.sh`.

## Expected handoffs

Claude Code should create `artifacts/spec.md` and implement the scoped changes. Antigravity should produce `artifacts/ui-notes.md` and `artifacts/schema.md` based on this task and the local JSON fixture. Codex should review the implementation or current diff and return `artifacts/review.md` if its profile permits writing; otherwise return the structured review inline for Claude to record.

## Suggested first command

```bash
./scripts/verify-local.sh
```
