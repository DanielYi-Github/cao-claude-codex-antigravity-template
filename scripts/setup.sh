#!/usr/bin/env bash
set -euo pipefail

command -v ffmpeg >/dev/null || { echo "找不到 ffmpeg，請先安裝。"; exit 1; }
command -v ffprobe >/dev/null || { echo "找不到 ffprobe，請先安裝。"; exit 1; }

# pyproject.toml requires Python >=3.11,<3.15. Plain `python3` on macOS is
# frequently the system 3.9 (import fails at 3.9 with obscure errors, e.g.
# datetime.UTC not existing) -- pick the newest interpreter that actually
# satisfies the constraint instead of assuming `python3` does.
PYTHON=""
for candidate in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    major="${version%%.*}"
    minor="${version#*.}"
    if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ] && [ "$minor" -lt 15 ]; then
      PYTHON="$candidate"
      break
    fi
  fi
done
if [ -z "$PYTHON" ]; then
  echo "找不到符合 Python >=3.11,<3.15 需求的直譯器（試過 python3.11-3.14 與 python3）。" >&2
  echo "請先安裝，例如：brew install python@3.12" >&2
  exit 1
fi
echo "使用 $PYTHON（$("$PYTHON" -c 'import sys; print(sys.version.split()[0])')）建立虛擬環境"

"$PYTHON" -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
mkdir -p workspace logs assets/backgrounds secrets
[ -f config/channels.yaml ] || cp config/channels.example.yaml config/channels.yaml
[ -f .env ] || cp .env.example .env

echo "安裝完成。下一步：編輯 .env，並執行 source .venv/bin/activate && lyria-auto doctor"
