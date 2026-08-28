# Task Brief

Replace this file's content with the brief for your current task before you
launch the lead. This is what `AGENTS.md` calls "the human-readable task
brief" — the Claude lead, Codex reviewer, and Antigravity UI/data worker all
read this file first.

## What to include

1. One or two sentences describing what you want built, fixed, or
   investigated.
2. Acceptance criteria — a numbered list of concrete, checkable conditions.
3. Constraints — what must NOT happen (e.g. no production data, no writes
   outside the repo, no new dependencies without approval).
4. The command a reviewer should run to verify the result, e.g.
   `./scripts/verify-local.sh`.

## Expected handoffs

State explicitly which artifact each role should produce, for example:

- Claude Code creates `artifacts/spec.md` (for anything larger than a
  one-file change) and implements the scoped change.
- Antigravity produces `artifacts/ui-notes.md` and/or `artifacts/schema.md`
  if the task has a UI or data component.
- Codex reviews the implementation or current diff and returns
  `artifacts/review.md`, or an inline structured review per `AGENTS.md` if
  its profile does not permit writing files.

## Suggested first command

```bash
./scripts/verify-local.sh
```
