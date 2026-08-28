#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

VENV_PY="$ROOT_DIR/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  printf '[verify] .venv not found at %s\n' "$VENV_PY" >&2
  printf '[verify] run: python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"\n' >&2
  exit 1
fi

printf '[verify] pytest\n'
"$VENV_PY" -m pytest -q

printf '[verify] ruff\n'
"$VENV_PY" -m ruff check src tests

printf '[verify] comfyui-assets validator\n'
"$VENV_PY" comfyui-assets/scripts/validate_project.py

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
