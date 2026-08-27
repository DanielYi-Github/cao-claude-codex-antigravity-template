#!/usr/bin/env bash
# Source this file from the project scripts or your shell before using cao.
set -euo pipefail

CAO_TEMPLATE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Keep this project's CAO database, installed profiles, skills, logs, FIFOs, and
# workflow journal together. The workflow directory must live below CAO_HOME_DIR
# for the current CAO validator. Respect an explicit caller override when present.
export CAO_HOME_DIR="${CAO_HOME_DIR:-$CAO_TEMPLATE_ROOT/.cao}"
export CAO_API_HOST="${CAO_API_HOST:-127.0.0.1}"
export CAO_API_PORT="${CAO_API_PORT:-9889}"
export CAO_TERMINAL_BACKEND="${CAO_TERMINAL_BACKEND:-tmux}"

# Do not put provider tokens here. Authenticate each native CLI separately.
export CAO_TEMPLATE_ROOT
