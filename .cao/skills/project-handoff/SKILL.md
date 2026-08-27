---
name: project-handoff
description: Keep multi-agent project handoffs short, durable, and verifiable
---

# Project Handoff

Use this skill when one agent passes work to another agent.

## Required handoff fields

Every handoff must state the task, the repository path, the branch or commit, the acceptance criteria, the requested output format, and the command used to verify the result. Point to a durable artifact whenever the content is longer than a short paragraph.

## Artifact convention

Use `artifacts/spec.md` for the lead specification, `artifacts/review.md` for review findings, `artifacts/ui-notes.md` for UI guidance, `artifacts/schema.md` for data observations, and `artifacts/test-report.md` for verification results. Create the `artifacts/` directory before writing to it.

## Completion rule

A worker must report one of `completed`, `blocked`, or `needs-human-input`. It must include changed paths, tests or checks executed, and unresolved questions. Never report success only because a command was launched; report the observed output or the reason it could not be verified.

## Safety rule

Do not put secrets, tokens, private data, or hidden reasoning in handoff files. Share only the minimum project context needed for the next agent to act.
