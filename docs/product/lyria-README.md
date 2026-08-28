# Lyria Auto Publisher

以 Python 建立的 Lofi Jazz 長影片自動化流程：用 Google Gemini API 的 Lyria 3 產生原創音樂，透過 FFmpeg 做音訊品質檢查、正規化、交叉淡化與循環，再建立縮圖、影片 metadata，最後選擇性上傳並排程發布到 YouTube。

專案也包含 Gemini 圖片／Veo 視覺 pipeline 的實驗性元件。不過目前視覺流程仍是 opt-in、尚未完成正式環境驗收，預設保持關閉。

第一次使用請先讀 [`docs/00_START_HERE_zh-TW.md`](docs/00_START_HERE_zh-TW.md)。它是針對日常操作整理的完整指令手冊。

## 目前狀態

| 區域 | 狀態 | 說明 |
|---|---|---|
| 音樂生成與靜態影片 | 可使用 | Lyria 3 → FFmpeg → MP4，預設 `visual.enabled: false` |
| Dry-run / `--from-job` | 可使用 | 免費預覽後沿用同一份 Prompt、文案與縮圖生成 |
| SQLite 續跑 | 可使用 | 已完成曲目可重用，不重複生成或付費 |
| YouTube OAuth / 上傳 | 已實作，待使用者驗證 | 本機 fake client 測試完整；尚未用真實頻道做端到端上傳驗證 |
| Gemini / Veo 視覺 pipeline | 實驗中 | 有設定、資料庫、preflight、審核與渲染元件，但完整 job 流程仍不宜直接投入生產 |
| 自動化排程 / Docker | 已實作 | 排程預設關閉；視覺排程需要額外明確授權 |

最近一次本機基線驗證：`pytest -q` 為 **117 passed**，`ruff check src tests` 通過。測試使用 fake provider，不會呼叫真實付費 API。

## 功能

- 以結構化素材組合同一張「專輯」風格的 Lofi Jazz Prompt。
- 呼叫 Lyria 3 Pro 逐首生成音樂，遇到安全攔截時遵守平台安全機制重試或改寫。
- 以 FFprobe 驗證時長、取樣率與聲道，並用 FFmpeg 做 loudness normalization、串接與 crossfade。
- 以完整曲目循環補足長度，不從歌曲中間硬切；成品長度因此是接近目標值，而非保證精確相等。
- 產生 1920×1080 靜態畫面影片與 1280×720 JPEG 縮圖。
- 自動建立標題、說明、標籤、播放清單與 `publishAt` 排程資訊。
- 以 SQLite 保存 job、曲目與上傳結果；中途失敗可 `resume`。
- 支援多頻道 token profile、APScheduler、Docker，以及本機 metrics report。
- 實驗性支援 Gemini 圖片、Veo 影片、付費 watermark smoke test、素材審核與視覺時間軸渲染。

## 重要限制與費用

完整生成流程需要以下由操作者提供的資訊：

- `GEMINI_API_KEY`。Lyria 3 沒有免費額度，需使用已開通帳單的 Google 專案。
- YouTube OAuth Desktop App 的 `client_secret.json`。
- 第一次 OAuth 登入與授權。
- 可選的播放清單 ID。

YouTube 一般頻道不能用 Service Account 直接操作；第一次授權必須由你本人登入。之後 refresh token 會保存於本機，才能無人值守執行。

費用主要由 `generation.max_material_minutes` 決定。預設最多生成 60 分鐘原創素材，超過的影片長度用 crossfade 循環補足：

| 影片目標 | 原創素材上限 | 約需曲目 | Lyria Pro 估算 |
|---|---:|---:|---:|
| 30 分鐘 | 30 分鐘 | 約 11 首 | 約 $0.88 |
| 1 小時 | 60 分鐘 | 約 21 首 | 約 $1.68 |
| 2～4 小時 | 60 分鐘 | 約 21 首 | 約 $1.68 |

以上是目前文件中的價格估算，不是費用保證。視覺圖片與影片另外計費，且正式啟用前應先做小額、明確次數的 preflight。

## 系統需求

- Python `>=3.11,<3.15`
- FFmpeg 與 FFprobe
- 可存取 Gemini API 的 Google 帳號與已開通帳單的專案
- 要發布時，需要 YouTube 頻道、Google Cloud 專案與 OAuth Desktop App

### 安裝

macOS / Linux：

```bash
bash scripts/setup.sh
source .venv/bin/activate
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
Copy-Item .env.example .env
Copy-Item config/channels.example.yaml config/channels.yaml
.\.venv\Scripts\Activate.ps1
```

若系統的 `python3` 低於 3.11，不要直接使用 `scripts/setup.sh`，請指定可用版本建立環境：

```bash
python3.13 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
```

初始化設定：

```bash
cp .env.example .env
cp config/channels.example.yaml config/channels.yaml
# 編輯 .env，填入 GEMINI_API_KEY
# 將 Google Cloud OAuth 檔放到 secrets/client_secret.json
```

`.env`、`secrets/`、`workspace/` 與 `logs/` 已排除於 Git；不要把 API key、OAuth secret 或 token 寫入版本庫。

## 最快開始

```bash
lyria-auto doctor
lyria-auto authorize-youtube --channel main

# 免費：只建立 Prompt、metadata、縮圖與 SQLite job
lyria-auto run --dry-run --videos 1

# 使用 dry-run 的同一份計畫進行實際生成，不上傳
lyria-auto run --from-job <JOB_ID>

# 直接生成並選擇性上傳
lyria-auto run --videos 1
lyria-auto run --videos 1 --upload --channel main
```

`authorize-youtube` 只需要第一次執行。若暫時只想檢查 Prompt 或產出預覽，`prompt-preview` 與 `run --dry-run` 都不會呼叫 Lyria，也不會產生生成費用。

## CLI 指令

```text
lyria-auto doctor                         檢查環境、API SDK 與憑證
lyria-auto authorize-youtube --channel main
                                           第一次 YouTube OAuth 授權
lyria-auto prompt-preview --count 20      免費預覽 Prompt
lyria-auto run --dry-run --videos 1       免費建立計畫與縮圖
lyria-auto run --from-job <ID>            依既有 dry-run 計畫實際生成
lyria-auto run --videos 1 [--upload]      生成影片，選擇性上傳
lyria-auto resume [--job <ID>] [--upload] 續跑未完成 job
lyria-auto status --limit 20              查看近期工作
lyria-auto scheduler                      啟動常駐排程器
lyria-auto config-migrate --preview       檢查視覺設定遷移
lyria-auto config-migrate --apply         備份後套用設定遷移
lyria-auto config-migrate --enable-visual 將 visual.enabled 設為 true
lyria-auto visual-preflight ...           實驗性付費視覺 preflight
lyria-auto review --job <ID>              審核視覺素材
lyria-auto regenerate --job <ID>          重新生成視覺素材
lyria-auto report [--job <ID>]            輸出本機營運 metrics
```

### Dry-run 與 `--from-job`

正確流程是：

```bash
lyria-auto run --dry-run --videos 1    # 假設產生 job 9
open workspace/job_000009/thumbnail.jpg
lyria-auto run --from-job 9
```

不要看完預覽後直接重跑 `lyria-auto run`。每次 `run` 都會建立新 job，Prompt seed 也會改變，結果會變成「預覽 A、實際生成 B」；`--from-job` 才會沿用原本的 Prompt、文案、縮圖與曲目列。

### 續跑與去重

```bash
lyria-auto resume
lyria-auto resume --job 7
lyria-auto resume --job 7 --upload
```

曲目只有在以下條件都成立時才會重用：SQLite 狀態為 `ready`、檔案存在、FFprobe 能正確解析。單曲失敗不會立刻中斷整批，程式會在批次結束後重試失敗曲目；仍失敗才中止並保留進度。

已經有 YouTube video ID 的工作再次 `resume --upload` 會跳過上傳，避免重複發布。

## 設定

主要設定在 `config/settings.yaml`，頻道與 OAuth 路徑在 `config/channels.yaml`，Prompt 素材在 `config/prompts.yaml`。

### 目前重要預設值

| 區段 | 設定 | 預設 | 用途 |
|---|---|---:|---|
| `generation` | `model` | `lyria-3-pro-preview` | Lyria 3 Pro 完整曲 |
| `generation` | `max_material_minutes` | `60` | 費用主要控制項 |
| `generation` | `max_tracks_per_video` | `40` | 防止設定錯誤的安全上限 |
| `generation` | `max_generation_attempts` | `3` | 單首重試次數 |
| `quality` | `minimum_duration_seconds` | `20` | 音訊最低時長 |
| `quality` | `minimum_sample_rate` | `44100` | 最低取樣率 |
| `quality` | `require_stereo` | `true` | 是否要求立體聲 |
| `quality` | `loudness_target_lufs` | `-16` | 正規化目標 |
| `video` | `target_duration_minutes` | `120` | 成品目標長度；實際值可能有誤差 |
| `video` | `crossfade_seconds` | `2` | 曲目與循環接縫淡化時間 |
| `video` | `width` / `height` | `1920 / 1080` | 靜態影片解析度 |
| `video` | `fps` | `1` | 靜態畫面影片的影格率 |
| `video` | `background_directory` | `assets/backgrounds` | 自訂背景圖片目錄 |
| `scheduler` | `enabled` | `false` | 是否啟用常駐排程器 |
| `scheduler` | `cron` | `0 9 * * 1,4` | Asia/Taipei 下週一、四 09:00 |

成品長度不會從曲子中間裁切：素材已足夠時保留完整曲目，不足時以完整份數 crossfade 循環。因此 `target_duration_minutes` 是目標值，不是硬性精確值。

### Metadata 與 Prompt

`title_template`、`description_template` 可使用 `{scene}`、`{instrumentation}`、`{mood}`、`{duration_minutes}`、`{episode}`。Prompt 的場景採 `en` / `zh` 雙欄：英文餵給模型，中文顯示在標題與縮圖；`zh` 留空時會退回英文。

一支影片會固定曲風、場景、錄音質感與混音風格，只讓樂器編制、情緒、速度與環境音逐首變化，讓整支影片更像一張專輯。

### YouTube 頻道

```yaml
channels:
  main:
    privacy_status: private
    contains_synthetic_media: true
    publish:
      mode: scheduled
      delay_hours: 24
      spacing_hours: 24
```

`scheduled` 模式會使用 private 影片再設定 `publishAt`。播放清單 `id` 留空且 `create_if_missing: true` 時，程式會尋找同名播放清單，找不到就建立。

## 自動化流程

### 穩定的音樂／靜態影片流程

```text
排程或 CLI
  → 產生專輯級 Prompt 與 metadata
  → 本地安全預檢
  → 建立 plan.json、縮圖與 SQLite job
  → Lyria 3 逐首生成音訊
  → FFprobe 品質檢查
  → FFmpeg 正規化、串接與 crossfade 循環
  → 靜態背景 + 音軌 → MP4
  → YouTube OAuth 上傳、縮圖、播放清單、排程發布
  → SQLite 記錄狀態與 video ID
```

### 排程與 Docker

啟用前先修改：

```yaml
scheduler:
  enabled: true
  cron: "0 9 * * 1,4"
  videos_per_run: 1
  upload: true
  channel: main
```

```bash
lyria-auto scheduler
docker compose up -d --build
docker compose logs -f
```

Docker 會把 `config/`、`secrets/`、`workspace/`、`logs/` 與 `assets/` 掛載到容器；OAuth token 必須先在主機完成建立。Linux systemd、macOS launchd 與 Windows Task Scheduler 的範例請看 [`docs/03_AUTOMATION_zh-TW.md`](docs/03_AUTOMATION_zh-TW.md)。

## 視覺 pipeline（實驗性）

目前 `config/settings.yaml` 的設定是：

```yaml
visual:
  enabled: false
  provider: gemini-developer-api
  image_model: gemini-3.1-flash-image
  video_model: veo-3.1-fast-generate-preview
```

視覺流程會使用 Gemini 圖片與 Veo 影片建立世界設定、場景候選、人工審核與長片時間軸。它會產生付費 API 呼叫，因此不能只把 `visual.enabled` 改成 `true` 就投入無人值守。

### 付費 watermark preflight

第一次驗證視覺模型時，必須精確授權一張圖片與一段影片：

```bash
lyria-auto visual-preflight \
  --watermark-smoke-test \
  --allow-image-outputs 1 \
  --allow-video-outputs 1

# 完成後人工檢查 raw image / raw video，再核准或拒絕
lyria-auto visual-preflight --approve --run-id <RUN_ID>
lyria-auto visual-preflight --reject --run-id <RUN_ID>

# 若 polling 暫停或失敗，沿用既有 operation 續跑
lyria-auto visual-preflight --resume <RUN_ID>
```

Preflight 會保存模型、SDK、憑證 fingerprint、檔案 SHA-256 與 SQLite 狀態；付費 start 的不確定結果不會透明自動重試。設定中的 preflight 有效期目前為 30 天，價格是設定檔內的 2026-07-29 snapshot 估算。

### 目前不能假設的事情

- `visual.enabled` 預設關閉，且目前不建議直接開啟正式排程。
- 視覺 job 的 review wait、影片 normalize/QC 與部分 regenerate 接點仍在持續實作；相關程式與測試存在，不代表完整真實 API 流程已驗收。
- `visual-preflight --status` 目前只輸出指定 run ID 的提示；完整狀態請查看 SQLite 與 log。
- Visual pipeline 尚未做真實 Gemini / Veo API 的付費端到端驗收。

舊設定需要補上視覺區段時可先執行：

```bash
lyria-auto config-migrate --preview
lyria-auto config-migrate --apply
```

視覺設計、狀態機、成本保護與逐步 rollout 規則請看 [`docs/VISUAL_PIPELINE_IMPLEMENTATION_HANDOFF_zh-TW.md`](docs/VISUAL_PIPELINE_IMPLEMENTATION_HANDOFF_zh-TW.md)。

## 產出與資料位置

```text
workspace/
├── state.sqlite3                 工作狀態與上傳結果
├── metrics/                       本機 metrics events
├── visual_preflight/              視覺 preflight raw 檔與 snapshot
└── job_000001/
    ├── plan.json                  Prompt、metadata 與 job 計畫
    ├── metadata.json              最終標題／說明／排程資料
    ├── thumbnail.jpg              1280×720 縮圖
    ├── track_*_raw.mp3            Lyria 原始輸出
    ├── track_*_normalized.m4a     正規化後音訊
    ├── compilation.m4a            曲目串接結果
    ├── compilation_extended.m4a   循環補足後音訊
    └── <title>.mp4                最終靜態影片
```

常用位置：

| 內容 | 位置 |
|---|---|
| 執行紀錄 | `logs/lyria-auto.log` |
| 工作狀態 | `workspace/state.sqlite3` |
| API key | `.env` |
| YouTube 憑證 | `secrets/` |
| 自訂背景 | `assets/backgrounds/` |

## 安全設計

- 預設 YouTube 上傳為 `private`，排程公開需在頻道設定中明確開啟。
- 預設標記 `contains_synthetic_media: true`。
- 不使用歌手、樂團、歌曲、電影、遊戲、品牌或播放清單名稱來模仿既有內容。
- 不關閉或規避 Lyria 的安全過濾、recitation checking 或 artist intent checks。
- API key、OAuth secret、token、輸出音訊與工作資料不應進 Git。
- Docker 使用 volume 掛載 secrets，不把憑證 COPY 進 image。

詳細安全規則請看 [`docs/06_SECURITY_zh-TW.md`](docs/06_SECURITY_zh-TW.md) 與 [`docs/09_PROMPT_POLICY_zh-TW.md`](docs/09_PROMPT_POLICY_zh-TW.md)。

## 文件索引

- [`docs/00_START_HERE_zh-TW.md`](docs/00_START_HERE_zh-TW.md)：日常操作手冊
- [`docs/01_SETUP_zh-TW.md`](docs/01_SETUP_zh-TW.md)：安裝與初始化
- [`docs/02_USAGE_zh-TW.md`](docs/02_USAGE_zh-TW.md)：CLI 使用參考
- [`docs/03_AUTOMATION_zh-TW.md`](docs/03_AUTOMATION_zh-TW.md)：排程、Docker、systemd
- [`docs/04_YOUTUBE_OAUTH_zh-TW.md`](docs/04_YOUTUBE_OAUTH_zh-TW.md)：OAuth 與自動上傳
- [`docs/05_TROUBLESHOOTING_zh-TW.md`](docs/05_TROUBLESHOOTING_zh-TW.md)：故障排除
- [`docs/06_SECURITY_zh-TW.md`](docs/06_SECURITY_zh-TW.md)：憑證與安全
- [`docs/07_OPERATING_PROCESS_zh-TW.md`](docs/07_OPERATING_PROCESS_zh-TW.md)：完整運作過程
- [`docs/08_OFFICIAL_REFERENCES.md`](docs/08_OFFICIAL_REFERENCES.md)：官方技術依據
- [`docs/09_PROMPT_POLICY_zh-TW.md`](docs/09_PROMPT_POLICY_zh-TW.md)：Prompt 安全與拒絕原因
- [`docs/VISUAL_PIPELINE_IMPLEMENTATION_HANDOFF_zh-TW.md`](docs/VISUAL_PIPELINE_IMPLEMENTATION_HANDOFF_zh-TW.md)：視覺 pipeline 交接與 rollout 規則
- [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md)：歷史驗證報告與尚未驗證項目
- [`CHANGELOG.md`](CHANGELOG.md)：版本變更紀錄

## 開發驗證

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/ruff check src tests
./.venv/bin/python scripts/self_test_media.py
```

測試不會自動呼叫真實 Lyria、Gemini、Veo 或 YouTube API。真實 YouTube 上傳、視覺付費 preflight 與視覺完整 pipeline 必須由操作者自行授權並依 rollout 文件驗證。
