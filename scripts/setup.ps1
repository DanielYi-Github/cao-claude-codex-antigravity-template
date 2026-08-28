$ErrorActionPreference = "Stop"
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
New-Item -ItemType Directory -Force workspace, logs, "assets/backgrounds", secrets | Out-Null
if (-not (Test-Path config/channels.yaml)) { Copy-Item config/channels.example.yaml config/channels.yaml }
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Write-Host "安裝完成。請編輯 .env，再執行 .\.venv\Scripts\Activate.ps1"
