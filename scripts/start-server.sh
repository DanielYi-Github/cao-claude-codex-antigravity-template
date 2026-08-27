#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT_DIR/scripts/env.sh"

mkdir -p "$CAO_HOME_DIR"
printf '[CAO template] server data: %s\n' "$CAO_HOME_DIR"
printf '[CAO template] server URL: http://%s:%s\n' "$CAO_API_HOST" "$CAO_API_PORT"
printf '[CAO template] Press Ctrl-C to stop the server.\n'

exec cao-server --terminal "$CAO_TERMINAL_BACKEND"
