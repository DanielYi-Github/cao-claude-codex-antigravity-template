#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Add your project's own build/lint/type-check commands here as they exist.

printf '[verify] unit tests\n'
set +e
PYTHONPATH="$ROOT_DIR" python3 -m unittest discover -s tests -p 'test_*.py' -v
test_status=$?
set -e
# Python 3.12+ exits 5 for "no tests were collected" (e.g. a fresh skeleton
# with no test_*.py yet); treat that as informational, not a failure.
if [ "$test_status" -ne 0 ] && [ "$test_status" -ne 5 ]; then
  exit "$test_status"
fi

printf '[verify] Git whitespace check\n'
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git diff --check
else
  printf '[verify] no Git repository yet; skipping diff check\n'
fi

printf '[verify] required template files\n'
for required in \
  AGENTS.md \
  CLAUDE.md \
  GEMINI.md \
  docs/demo-task.md \
  docs/data-contract.md \
  .cao/profiles/claude_lead.md \
  .cao/profiles/codex_reviewer.md \
  .cao/profiles/agy_ui_data.md \
  .cao/workflows/three_agent_demo.py; do
  test -f "$required"
done

printf '[verify] OK\n'
