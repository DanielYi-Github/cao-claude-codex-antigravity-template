#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT_DIR/scripts/env.sh"

RUN_ID="${CAO_RUN_ID:-three-agent-demo-1}"
WORKFLOW="$ROOT_DIR/.cao/workflows/three_agent_demo.py"

if ! command -v cao >/dev/null 2>&1; then
  printf 'CAO is not installed. Run ./scripts/bootstrap.sh first.\n' >&2
  exit 1
fi

if ! curl -sf "http://${CAO_API_HOST}:${CAO_API_PORT}/sessions" >/dev/null 2>&1; then
  printf 'CAO server is not reachable at http://%s:%s\n' "$CAO_API_HOST" "$CAO_API_PORT" >&2
  printf 'Start it first with: ./scripts/start-server.sh\n' >&2
  exit 1
fi

printf '[CAO template] validating workflow\n'
cao workflow validate "$WORKFLOW"

printf '[CAO template] starting run: %s\n' "$RUN_ID"
cao workflow run "$WORKFLOW" \
  --run-id "$RUN_ID" \
  --wait \
  --input "project_dir=$ROOT_DIR"
