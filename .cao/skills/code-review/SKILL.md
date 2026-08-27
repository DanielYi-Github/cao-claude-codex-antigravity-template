---
name: code-review
description: Review a scoped change with evidence, severity, and safe reproduction steps
---

# Code Review

Review the requested commit or working tree; do not review the whole repository unless asked. Start with the acceptance criteria and inspect the diff before reading unrelated files.

Report findings in severity order. A blocking finding prevents merge because it causes incorrect behavior, data loss, a security issue, a reproducible failure, or a missing acceptance criterion. A non-blocking finding is useful but can be handled later.

For each finding, include the file and line or symbol, the observed behavior, why it matters, a safe reproduction or test, and the smallest credible fix. State explicitly which tests and commands were executed and which were not possible.

Never read secrets or modify the lead worktree. Prefer `git diff`, static inspection, targeted tests, dry-run commands, and synthetic fixtures. If the reviewer discovers a serious issue, return it even if the implementation otherwise looks polished.
