#!/usr/bin/env bash
set -euo pipefail

command -v python3 >/dev/null || { echo "找不到 python3"; exit 1; }
command -v ffmpeg >/dev/null || { echo "找不到 ffmpeg，請先安裝。"; exit 1; }
command -v ffprobe >/dev/null || { echo "找不到 ffprobe，請先安裝。"; exit 1; }

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
mkdir -p workspace logs assets/backgrounds secrets
[ -f config/channels.yaml ] || cp config/channels.example.yaml config/channels.yaml
[ -f .env ] || cp .env.example .env

echo "安裝完成。下一步：編輯 .env，並執行 source .venv/bin/activate && lyria-auto doctor"
