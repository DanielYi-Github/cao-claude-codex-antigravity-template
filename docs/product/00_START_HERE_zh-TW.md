# 從這裡開始：日常操作手冊

> 本文件針對 **danielyi 這台 Mac** 的實際環境撰寫，所有指令都已實機驗證過。
> 與 `01_SETUP` / `02_USAGE` 的差異：那兩份是通用說明，本文件是「照著打就會動」的版本。

---

## 0. 最重要的一件事：不要用系統的 python3

這台機器的預設 `python3` 是 **3.9.6**，但本專案要求 **3.11 以上**。
所以 **`scripts/setup.sh` 不能直接跑**（它寫死用 `python3`，會安裝失敗）。

環境已經用 `python3.13` 建好在 `.venv/`，你不需要重建。
萬一哪天要重建：

```bash
cd ~/Downloads/Lyria-Auto-Publisher
rm -rf .venv
python3.13 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -e ".[dev]"
```

---

## 1. 每次啟動：三行指令

開啟「終端機」App，貼上：

```bash
cd ~/Downloads/Lyria-Auto-Publisher
source .venv/bin/activate
lyria-auto doctor
```

看到 `(.venv)` 出現在提示字元最前面，就代表環境進去了。

`doctor` 應該全部 `[OK]`（YouTube 兩項在你完成 OAuth 前會是 `[FAIL]`，不影響生成音樂）。

### 懶人版：不想每次 activate

直接用完整路徑，效果一樣：

```bash
cd ~/Downloads/Lyria-Auto-Publisher
./.venv/bin/lyria-auto doctor
```

> **一定要先 `cd` 到專案根目錄。** 程式讀的 `config/`、`workspace/`、`.env` 都是相對路徑，
> 在別的目錄執行會找不到設定檔。

---

## 2. 常用指令對照表

| 想做什麼 | 指令 | 花費 | 耗時 |
|---|---|---|---|
| 檢查環境 | `lyria-auto doctor` | $0 | 1 秒 |
| 只看會產生什麼文案，不生成音樂 | `lyria-auto run --dry-run --videos 1` | **$0** | 3 秒 |
| 預覽 20 組 Prompt | `lyria-auto prompt-preview --count 20` | $0 | 1 秒 |
| **生成完整成品（不上傳）** | `lyria-auto run --videos 1` | **$0.32** | 約 4 分 |
| 生成並上傳 YouTube | `lyria-auto run --videos 1 --upload` | $0.32 | 約 6 分 |
| **中途失敗後續跑** | `lyria-auto resume` | 只付缺的部分 | 視剩餘量 |
| 查看歷史工作紀錄 | `lyria-auto status --limit 10` | $0 | 1 秒 |
| 常駐排程器 | `lyria-auto scheduler` | 依設定 | 持續 |

### 預覽再生成的正確做法

`--dry-run` 免費產出完整的標題／說明／縮圖。滿意之後**要用 `--from-job` 轉正，
不能只是把 `--dry-run` 拿掉重跑**：

```bash
lyria-auto run --dry-run --videos 1    # 免費，假設產生 job 9
open workspace/job_000009/thumbnail.jpg   # 看縮圖
lyria-auto run --from-job 9            # 生成 job 9 那一份，一字不差
```

**⚠️ 為什麼不能直接重跑 `run`：** Prompt 的隨機種子取自 `job_id`，每跑一次就建新 job、
換新種子，抽出的場景組合完全不同；dry-run 寫入的曲目簽章還會進入「避免重複」清單，
把下一次的結果推得更遠。所以直接重跑會變成**預覽 A、生成 B**，等於白花錢。

---

## 3. 產出檔案放在哪裡

每跑一次會建一個獨立資料夾，編號遞增：

```
workspace/job_000004/
├── plan.json                       ← 完整計畫：4 段 Prompt + 全部 metadata
├── metadata.json                   ← 標題／說明／標籤／排程時間
├── thumbnail.jpg                   ← ★ 縮圖 1280×720
├── track_01_raw.mp3                ← Lyria 原始輸出（4 首）
├── track_01_normalized.m4a         ← 響度正規化後
├── ...（track_02 ~ track_04 同上）
├── compilation.m4a                 ← 4 首交叉淡化串接後
├── compilation_extended.m4a        ← 補足到目標長度
└── 壁爐書牆閱讀室-咖啡廳爵士-....mp4  ← ★ 最終成品影片
```

**你最常要找的兩個檔案：**

- **縮圖** → `workspace/job_000004/thumbnail.jpg`
- **影片** → `workspace/job_000004/` 裡副檔名 `.mp4` 的那個（檔名是中文標題）

### 快速打開

```bash
open workspace/job_000004/              # 在 Finder 開啟資料夾
open workspace/job_000004/*.mp4         # 直接播放影片
open workspace/job_000004/thumbnail.jpg # 直接看縮圖

open workspace/                         # 看全部歷史工作
```

### 其他位置

| 東西 | 位置 |
|---|---|
| 執行紀錄 / 錯誤訊息 | `logs/lyria-auto.log` |
| 工作狀態資料庫 | `workspace/state.sqlite3` |
| 之前生成的試聽音檔 | `workspace/samples/` |
| API Key | `.env` |
| YouTube 憑證 | `secrets/` |

---

## 4. 我會得到怎樣的結果？

跑完 `lyria-auto run --videos 1`，終端機會印出：

```json
[
  {
    "job_id": 4,
    "directory": ".../workspace/job_000004",
    "video": ".../壁爐書牆閱讀室-咖啡廳爵士-讀書-專注-工作音樂-no-004.mp4",
    "youtube_video_id": null,
    "publish_at": "2026-07-27T..."
  }
]
```

- `youtube_video_id: null` → 代表**沒有上傳**（因為沒加 `--upload`），檔案只在本機。
- 成品是一支 **10 分鐘、1920×1080** 的 MP4：靜態縮圖畫面 + 串接好的爵士樂。
- 音樂是 **4 首各約 144 秒的原創曲**，用 2 秒交叉淡化接起來。

### 想改長度

**只要改一個數字。** 曲目數不用你算 —— 程式會一首一首生成、累加實際長度，
湊夠素材目標就停，規劃了但用不到的 Prompt 不會送出、也就不花錢。

```yaml
video:
  target_duration_minutes: 120   # 想要幾小時就填幾
```

| 想要的長度 | 素材目標 | 約需曲目 | 費用 | 說明 |
|---|---|---|---|---|
| 30 分鐘 | 30 分 | 約 11 首 | 約 $0.88 | 全新曲 |
| 1 小時 | 60 分 | 約 21 首 | 約 $1.68 | 全新曲 |
| 2 小時 | 60 分 | 約 21 首 | **約 $1.68** | 1 小時素材循環 1 次 |
| 3 小時 | 60 分 | 約 21 首 | **約 $1.68** | 循環 2 次 |
| 4 小時 | 60 分 | 約 21 首 | **約 $1.68** | 循環 3 次 |

**2～4 小時費用完全相同** —— 素材上限是 `generation.max_material_minutes`（預設 60 分），
超過的長度靠交叉淡化循環補足（不是硬切，聽不出接縫）。

想要更多不重複的內容，就把 `max_material_minutes` 調高，費用才會跟著上升。

### 長度是「大約」，不會剛好

**成品絕不會從曲子中間切斷**，最後一首一定完整播完。所以實際長度會跟設定值差幾十秒到幾分鐘：

- 素材已超過目標 → 原樣沿用，**你付費生成的曲目全部保留**，成品會略長於設定值。
- 素材不足 → 以完整份數循環，取最接近目標的份數，可能略短或略長。

實測（用 9.7 分鐘素材）：設 30 分鐘得到 29.0 分、設 1 小時得到 57.9 分、設 4 小時得到 241.0 分。

素材越長、循環份數越少，誤差比例就越小。真的想更貼近目標，就把 `max_material_minutes` 調高。

### 每首曲子會有多長？

**程式刻意不指定秒數。** 實測不提長度會拿到約 **173 秒**（接近模型上限），
而要求「150 秒」只拿到 144 秒 —— 計費按「首」算與長度無關，
指定較短的秒數等於同樣的錢買到更少音樂。

### 中途失敗不用重來

跑 25 首大約 20 分鐘。萬一中途失敗（網路、API 暫時性錯誤、電腦睡眠），
**已經生成好的曲目不會重做、不會重複付費**：

```bash
lyria-auto resume        # 自動接續最近一個未完成的工作
```

程式會逐首檢查：資料庫狀態為 `ready`、檔案還在、且能被 ffprobe 正常讀取 —— 三個條件
都成立才跳過。所以就算是「寫到一半被中斷」的半殘檔，也會被抓出來重新生成。

---

## 5. 想改文案／場景

- **標題與說明格式** → `config/settings.yaml` 的 `title_template` / `description_template`
- **場景、氛圍、樂器** → `config/prompts.yaml`

場景是 `en` / `zh` 兩欄：`en` 餵給 Lyria（必須英文），`zh` 顯示在標題和縮圖上。

```yaml
scenes:
  - en: "a rainy bookstore café beside a large window"   # 給 AI 讀
    zh: "雨天窗邊書店"                                     # 給人看
```

自己加場景時，`zh` 忘了填也不會壞，會自動退回顯示英文。

### 一支影片 = 一張專輯

同一支影片裡，這幾項**全片固定**，讓整支影片聽起來是同一張專輯：

| 全片固定 | 逐首變化 |
|---|---|
| `genres` 曲風 | `instrumentations` 樂器編制 |
| `scenes` 場景（同時決定標題與縮圖） | `moods` 情緒 |
| `textures` 錄音質感 | `tempos` 速度 |
| `productions` 混音風格 | `atmospheres` 環境音 |

所以**想換曲風主要是改 `genres`**，而不是期待它在一支影片內變化。
每支影片會從 `genres` 隨機挑一個當主軸，整支沿用。

想讓專輯內部更豐富，就增加右欄那四類的選項；想讓不同影片之間差異更大，
就增加左欄的選項。

改完先跑 `lyria-auto run --dry-run --videos 1` 看效果，免費。

---

## 6. 出問題時

| 症狀 | 原因 | 解法 |
|---|---|---|
| `command not found: lyria-auto` | 沒 activate 或沒 cd 到專案 | 重跑第 1 節三行指令 |
| `429 ... limit: 0` | API Key 在免費層 | Lyria 沒有免費額度，需到 AI Studio 開通帳單 |
| `找不到設定檔` | 不在專案根目錄 | `cd ~/Downloads/Lyria-Auto-Publisher` |
| 縮圖文字變成小方塊 | 字型沒載到 | 已修復；若復發檢查 `media/thumbnail.py` 的字型清單 |
| 跑到一半失敗 | 網路／API 暫時性錯誤 | 跑 `lyria-auto resume` 續跑，已完成的曲目不會重做 |
| `N 首曲目生成失敗，可用 lyria-auto resume 續跑` | 重試後仍失敗 | 看 `logs/lyria-auto.log` 找原因，排除後跑 `resume` |
| `job N 已經完成，不需要續跑` | 對已完成的工作下 `resume --job N` | 正常保護機制，避免重複產出與重複上傳 |

### 續跑機制怎麼運作

- 單一曲目失敗**不會**中斷整批，全部跑完後會自動對失敗的曲目重試一輪。
- 重試後仍失敗才會中止，且**不會**產出缺料的影片。
- `lyria-auto resume` 會沿用原本的 Prompt、文案與縮圖，成品與原規劃一致。
- 已上傳過的工作再 `resume --upload` 不會重複發布 —— 會偵測到既有的 video ID 並跳過。

**曲目數不再需要保守設定。** 25 首（約 1 小時素材）跑起來很安全，
中途出事就 `resume`，不會白花錢。

### 查看歷史與診斷

```bash
lyria-auto status --limit 10       # 各工作的狀態與錯誤訊息
cat logs/lyria-auto.log            # 完整執行紀錄
```
