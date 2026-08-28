# 自動化運行

## 內建排程器

在 `config/settings.yaml` 設定：

```yaml
scheduler:
  enabled: true
  cron: "0 9 * * 1,4"
  videos_per_run: 1
  upload: true
  channel: "main"
```

以上代表每週一、四上午 9:00（Asia/Taipei）執行。

啟動：

```bash
lyria-auto scheduler
```

## Docker 常駐

先在主機完成一次 OAuth，確認 `secrets/youtube_token_main.json` 已建立，再執行：

```bash
docker compose up -d --build
docker compose logs -f
```

## Linux systemd

建立 `/etc/systemd/system/lyria-auto.service`：

```ini
[Unit]
Description=Lyria Auto Publisher
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/你的路徑/Lyria-Auto-Publisher
ExecStart=/你的路徑/Lyria-Auto-Publisher/.venv/bin/lyria-auto scheduler
Restart=on-failure
EnvironmentFile=/你的路徑/Lyria-Auto-Publisher/.env

[Install]
WantedBy=multi-user.target
```

啟用：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lyria-auto
```

## macOS launchd / Windows Task Scheduler

也可不啟動常駐排程器，而讓系統排程執行 `scripts/run_once.sh` 或 Windows 等價命令。
