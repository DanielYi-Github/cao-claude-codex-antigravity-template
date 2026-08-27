#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT_DIR/scripts/env.sh"

TASK="${*:-Read docs/demo-task.md and AGENTS.md. Analyze the task, delegate UI/data investigation to agy_ui_data and code review to codex_reviewer through CAO, then integrate the results and run ./scripts/verify-local.sh. Return a concise status report.}"

if ! curl -sf "http://${CAO_API_HOST}:${CAO_API_PORT}/sessions" >/dev/null 2>&1; then
  printf 'CAO server is not reachable at http://%s:%s\n' "$CAO_API_HOST" "$CAO_API_PORT" >&2
  printf 'Start it first with: ./scripts/start-server.sh\n' >&2
  exit 1
fi

exec cao launch \
  --agents claude_lead \
  --provider claude_code \
  --session-name claude-codex-agy \
  --working-directory "$ROOT_DIR" \
  "$TASK"
