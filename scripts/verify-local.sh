#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

printf '[verify] Python fixture check\n'
python3 src/health_check.py

printf '[verify] unit tests\n'
PYTHONPATH="$ROOT_DIR" python3 -m unittest discover -s tests -p 'test_*.py' -v

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
