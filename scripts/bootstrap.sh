#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_DIR="$ROOT_DIR/.cao/profiles"
SKILL_DIR="$ROOT_DIR/.cao/skills"
# shellcheck source=/dev/null
source "$ROOT_DIR/scripts/env.sh"

say() {
  printf '\n[CAO template] %s\n' "$*"
}

need_command() {
  local name="$1"
  local hint="$2"
  if ! command -v "$name" >/dev/null 2>&1; then
    printf 'Missing command: %s\nInstall hint: %s\n' "$name" "$hint" >&2
    return 1
  fi
}

say "Checking local prerequisites"
need_command python3 "Install Python 3.10+ from your operating system package manager"
need_command git "Install Git from your operating system package manager"
need_command tmux "Install tmux 3.3+ from your operating system package manager"
need_command uv "Install uv from https://docs.astral.sh/uv/"
need_command curl "Install curl from your operating system package manager"

if ! command -v cao >/dev/null 2>&1; then
  say "CAO is not installed; installing the current upstream main branch with uv"
  uv tool install git+https://github.com/awslabs/cli-agent-orchestrator.git@main --upgrade
fi

need_command cao "Run: uv tool install git+https://github.com/awslabs/cli-agent-orchestrator.git@main --upgrade"

say "Checking the three native provider CLIs"
provider_missing=0
for pair in \
  "claude|Install Claude Code using Anthropic's official instructions" \
  "codex|Run: npm install -g @openai/codex" \
  "agy|Install Antigravity CLI using https://antigravity.google/cli/install.sh"; do
  IFS='|' read -r command_name install_hint <<<"$pair"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Provider CLI missing: %s\nInstall hint: %s\n' "$command_name" "$install_hint" >&2
    provider_missing=1
  fi
done
if [ "$provider_missing" -ne 0 ]; then
  printf '\nInstall the missing provider CLIs, then run this script again.\n' >&2
  exit 1
fi

say "Registering the project profiles with CAO"
for profile in "$PROFILE_DIR"/*.md; do
  cao install "$profile"
done

say "Registering project skills with CAO"
# CAO expects a JSON value for list-valued settings. The path is absolute so the
# setting remains valid regardless of the directory from which cao is launched.
skill_json="[\"$SKILL_DIR\"]"
cao config set skills.extra_dirs "$skill_json"

mkdir -p "$ROOT_DIR/artifacts"
cat > "$ROOT_DIR/artifacts/README.md" <<'EOF'
# Runtime Artifacts

This directory is intentionally empty in the template. Runtime agents may write
review notes, UI notes, schema observations, specifications, and test reports
here. Do not commit secrets, production data, credentials, cookies, or CAO logs.
EOF

say "Bootstrap complete"
printf '%s\n' \
  "Next: run ./scripts/verify-local.sh" \
  "Then authenticate claude, codex, and agy separately before starting cao-server."
