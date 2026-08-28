# 松獅犬影片工作室：整合架構計畫

## 已拍板決議（2026-08-12）

| 項目 | 決定 |
|---|---|
| 取捨 1 · ComfyUI JSON 格式 | **並存 `workflows/api/*.json` API 格式匯出**，程式只改具名參數的 API 版；驗證器加漂移檢查 |
| 取捨 2 · 解析度與升頻 | **768×432 動作試拍 → 1024×576 正式生成 → 純後製 ESRGAN 升頻至 1920×1080**（動作與核准版完全一致、無黑邊） |
| 取捨 4 · Web 框架 | **FastAPI + uvicorn**（換取 HTTP Range，影片可拖曳審核） |
| 控制台位置 | **放在 Lyria 專案內** `src/lyria_auto/studio/` |

## Context

目標：把 ComfyUI（畫面生成）、Lyria（音樂生成）、ffmpeg（合成）串成一條**全自動但每個節點都可人工攔截**的流水線，用網頁控制台呈現。核心訴求不是「無人值守」，而是「自動跑到下一個關卡就停下來等你點頭，不滿意可以只重做那一項」。

這份文件取代前一份 `chowchow-integration-plan.md` 的 Part C（手動交接）與 Part A2 的部分內容（manifest 改成資料庫）；Part A1（修復 comfyui 專案既有壞損）**原封不動仍然有效**，而且是這條路的前置條件。

範圍界線：YouTube 上架依你的指示維持人工，不納入自動化。

---

## 一、關鍵發現：現成基礎比預期多，但陷阱也在

### 好消息：Lyria 的資料庫已經有這套狀態機的骨架

`src/lyria_auto/db.py` 裡已經存在（為 Gemini/Veo 視覺管線設計，但形狀正確）：

- `jobs.status` 由 `state_catalog` 表加 SQLite trigger 強制約束，合法值已含 `awaiting_image_review`、`awaiting_video_review`、`generating_images`、`generating_videos`
- `visual_scenes.status` 有完整的審核循環：`awaiting_image_review` → `image_selected` → `image_approved` → `needs_regeneration` → `awaiting_video_review` → `needs_video_regeneration`
- `visual_assets` 有 `variant_index`（一個場景多個候選）、`status` 含 `ready`/`approved`/`rejected`/`superseded`
- 全表都有 `state_version` 樂觀鎖、`side_effect_leases` 租約機制、`events` 稽核表

**你要的「3(b) 單獨重製第 5 首歌」已經幾乎免費**：`tracks` 表有逐曲狀態，`pipeline.py:639` 的生成迴圈會呼叫 `_track_is_reusable()`（狀態為 `ready` + 檔案存在 + ffprobe 解得開，三者皆成立才跳過）。把第 5 首的 status 改掉、清空 `audio_path`，既有的 `resume` 就只會重生那一首。**不需要新的生成邏輯。**

### 壞消息：審核 UI 是空殼

`src/lyria_auto/review_server.py`（289 行）名字像現成審核台，實際上：

- 狀態只存在記憶體（`ReviewSession` dataclass），**完全沒接資料庫**，重啟即失憶
- **沒有任何路由能吐出圖片/影片的位元組**——CSS 寫了 `.asset img, .asset video` 樣式，但 JS 只產生檔名和 sha256 文字，畫面上看不到任何素材
- 要審核得手動打字輸入 asset ID
- 用 class 屬性注入全域狀態（`ReviewHandler._session = ...`），只能單一 session

結論：**資料層可以大量沿用，展示層要重寫。**

### 我決定不沿用 `visual_scenes` / `visual_assets`，理由

這兩張表形狀對，但語意綁死在**付費遠端生成**上：`estimated_cost_usd NOT NULL`、`pricing_snapshot_json NOT NULL`、外加 `paid_stage_authorizations` / `claim_paid_start` 這套「精確授權幾次付費呼叫」的機制。ComfyUI 是本機免費生成，硬塞進付費授權流程語意錯誤；而且那條管線 README 明講「實驗中、尚未付費端到端驗收」，兩者纏在一起，將來若真的開啟 `visual.enabled` 會互相干擾。

**改為在同一個 SQLite 檔內新增專用資料表**，沿用相同慣例（`state_version` 樂觀鎖、status CHECK 約束、寫 `events`），並直接沿用 `tracks`（音樂，完美吻合）與 `jobs`。Gemini 視覺管線完全不動。

---

## 二、架構決策

### 控制台放在 Lyria：`src/lyria_auto/studio/`

| 選項 | 判斷 |
|---|---|
| **放 Lyria**（建議） | 資料庫、音樂管線、ffmpeg 封裝、render 程式碼、套件結構全都現成；ComfyUI 視為「外部生成服務」由它驅動 |
| 放 comfyui repo | 該 repo 純標準庫、無資料庫、無狀態機，等於從零重建；音樂端還得跨行程呼叫 |
| 新開第三個 repo | 重複造一套已存在的狀態機，三個 repo 要同步，三個虛擬環境 |

代價：Lyria 這個名字會不再精確涵蓋它做的事（它變成整個工作室）。這是命名問題，不是架構問題。comfyui repo 的定位轉為**工作流範本 + 提示詞庫 + 靜態驗證器**的來源，由 Lyria 透過設定路徑讀取。

### 資料模型（新表，同一個 `workspace/state.sqlite3`）

```
episodes                 一支兩小時影片專案
  id, slug, title, status, state_version, music_job_id(FK jobs), ...

episode_assets           每一件產出，含版本與血緣
  id, episode_id, kind, role, variant_index, status,
  path, sha256, width, height, duration_seconds, fps,
  source_prompt, source_seed, comfyui_prompt_id,
  parent_asset_id,       ← 1080p 素材指向它的 480p 來源，血緣可追
  state_version, ...

  kind: keyframe | motion_test | clip | clip_1080p | loop | final
  role: shared | sleep | lookup
  status: queued|running|ready|awaiting_review|approved|rejected|superseded|failed

studio_tasks             背景工作器的佇列
  id, episode_id, asset_id, task_type, status, payload_json,
  attempts, error, created_at, started_at, finished_at
```

沿用不動：`tracks`（音樂逐曲）、`events`（稽核）、`jobs`（音樂工作）。

### 背景工作器：單執行緒、序列化、資料庫持久化

一條背景執行緒，從 `studio_tasks` 取 `queued` 的任務，**一次只跑一個**。刻意序列化的理由：ComfyUI 常駐約 22GB 模型、ESRGAN 升頻峰值約 18GB、兩小時 ffmpeg 編碼吃滿 CPU——這些東西不能同時跑。

所有狀態在資料庫、所有產出在磁碟，所以**關掉瀏覽器不影響進行中的工作**，隔天回來接著看。

### ComfyUI 串接：`src/lyria_auto/providers/comfyui.py`

```
POST /prompt            送出工作 → 取得 prompt_id
GET  /history/{id}      輪詢直到出現 → 取得輸出檔名
GET  /queue             取得排隊位置
GET  /view?filename=... 直接預覽（關鍵幀 4 張候選用這個，不必先複製）
```
核准後才把檔案從 ComfyUI 的 `output/` 複製進該 episode 的工作目錄（保留出處紀錄）。

---

## 三、三個必須先決定的技術取捨

### 取捨 1：ComfyUI 的兩種 JSON 格式（最大技術風險）

**這是整個計畫最容易踩雷的地方。** ComfyUI 的工作流有兩種格式：

- **UI 格式**：`{"nodes": [...], "links": [...]}`——「Save」產生的，repo 裡 `workflows/*.json` 全都是這種
- **API 格式**：`{"9": {"class_type": "...", "inputs": {"width": 832, ...}}}`——「Save (API Format)」產生的，**`POST /prompt` 只吃這種**

UI 格式的參數存成位置陣列 `widgets_values: [832, 480, 65, 1]`，沒有欄位名稱；名稱要向 ComfyUI 查 `/object_info` 才知道。

**建議**：在 repo 裡並存 `workflows/api/*.json`（人工在 ComfyUI 按一次「Save (API Format)」匯出並提交），程式只改 API 格式。理由：API 格式的參數是**具名的**，改起來比改位置陣列更安全；而且不需要執行期依賴 `/object_info`。工作流很少變動，同步成本低。再於 `validate_project.py` 加一條檢查，確保 UI 版與 API 版的關鍵參數不會悄悄漂移。

（已評估並否決的替代方案：寫 UI→API 轉換器。要查 `/object_info` 把位置參數對回名稱，約 150-250 行，且與 ComfyUI 版本耦合。）

### 取捨 2：「擴大為 1080p」到底是什麼意思 — 需要你拍板

你的規劃是「確認 480p 影片無誤後，將其擴大為 1080p」。這句話有兩種實作，結果差很多：

| 做法 | 動作是否保留 | 畫質 |
|---|---|---|
| **(i) 純後製升頻**：拿你核准的那支 480p 檔，跑 ESRGAN 放大 | ✅ 完全一致，你核准什麼就出什麼 | 受限於原生成解析度 |
| **(ii) 換解析度重新生成**：用同樣提示詞在較高解析度重跑擴散 | ❌ **動作會變**，核准等於作廢 | 原生細節較好 |

擴散模型換解析度會產生不同結果，即使種子相同。人工審核制度下 (ii) 是矛盾的——你核准的東西不會出貨。

**另有一個順帶要解決的問題**：現在的 832×480 是 26:15（≈1.733），不是 16:9（1.778），升到 1920×1080 會有細窄黑邊。

**建議方案（兩段式，全部 16:9）**：

- **動作試拍 768×432**（16:9 精確、可被 16 整除、約 22k tokens 最便宜）——用來反覆調動作提示詞，**不是成品**
- **正式生成 1024×576**（16:9 精確，正是本專案原本的「日常」設定）——這支才是你核准的素材
- **升頻**：ESRGAN ×4 → 4096×2304 → Lanczos → 1920×1080，**純後製，動作與核准的完全相同，且無黑邊**

這也正好呼應本專案 README 原本的三步設計（關鍵幀 → 便宜預覽 → 正式成片）。

需新增一支「純升頻」工作流（`LoadVideo` → `ImageUpscaleWithModel` → `ImageScale` → `CreateVideo`）。**待驗證**：ComfyUI 0.31.1 是否有 `LoadVideo` 核心節點；若無，退路是用 ffmpeg 的 Lanczos 放大（較軟但零風險）。

### 取捨 3：兩小時最終渲染——有個能省下數十倍時間的做法

樸素做法是 `-stream_loop -1 -i loop.mp4 -c:v libx264`，等於重新編碼 **115,200 張** 1080p 影格，每次迭代可能要跑一小時以上。

但那支 64 秒循環檔**本身已經是編好的 1080p h264**。改用 concat demuxer 把同一個檔案列 113 次 + `-c:v copy` 串流複製，再把兩小時音軌 mux 進去、`-shortest` 收尾——**幾分鐘就好，不是幾小時**。因為每份複本位元完全相同（同樣的 SPS/PPS、開頭都是 IDR 關鍵幀），串流複製是安全的。

樸素重編碼保留為退路（若時間戳出問題）。這個差別對「不滿意就重做」的迭代速度影響非常大。

### 取捨 4：Web 框架（順帶）

建議 **FastAPI + uvicorn**（新增 2 個依賴，目前有 9 個）。決定性理由：`http.server` 的 `SimpleHTTPRequestHandler` **不支援 HTTP Range 請求**，而瀏覽器的 `<video>` 要能拖曳進度條就需要 Range——審核兩小時成品時這是必需的。FastAPI 的 `FileResponse` 內建處理，另外還帶來 async 背景任務與進度串流。

替代方案：純標準庫自己實作 Range（約 40 行），維持零新依賴，但等於重造輪子。

前端用單頁原生 HTML/JS（比照現有 `console/local/app.js`），**不引入建置工具**。順帶一提：comfyui repo 裡那個 Next.js `console/` 是無關的範本殘留，建議直接刪除而非改造。

---

## 四、六道人工關卡的具體設計

| # | 關卡 | 自動做的事 | 你在畫面上做的事 | 局部重做的粒度 |
|---|---|---|---|---|
| 1a | 關鍵幀 | FLUX 一次生 4 張候選 | 看圖選一張，或全部退回換種子 | 整批重生 |
| 1b | 動作試拍 | 768×432 兩支各一次 | 看動作對不對 | **單獨重做 sleep 或 lookup** |
| 1c | 正式生成 | 1024×576 兩支 | 確認成品動作 | **單獨重做某一支** |
| 2 | 升頻 | ESRGAN → 1920×1080 | 確認無閃爍、毛髮細節 | 單獨重升某一支 |
| — | 拼接 | 7×sleep + 1×lookup → 64 秒 | 確認接縫 | 自動重拼 |
| 3a | 音樂提示詞 | `run --dry-run` 產生約 21 首的提示詞 | **逐條看語意、可編輯** | 改文字重生計畫 |
| 3b | 音樂成品 | 逐首生成 | 逐首試聽 | **單獨重製第 N 首** |
| 4 | 最終合成 | 循環 + 混音 → 兩小時 | 播放確認 | 重新合成 |

每道關卡的機制一致：工作器跑完 → episode 狀態設為 `*_review` → 停下 → 等 HTTP 核准/退回 → 退回時帶「退哪一項」，只把那一項重新排進佇列。

---

## 五、需要動的程式碼

### comfyui-cafe-loop-generator

- **前置**：套用前一份計畫的 Part A1（修復 `validate_project.py`、`staged_workflow_server.py` 的失效檔名與節點假設）——這是地基，不修的話驗證器和審核台都是壞的
- 新增 `workflows/api/*.json`（API 格式匯出）+ 驗證器的漂移檢查
- 新增純升頻工作流
- `chowchow-prompts.md`（提示詞庫，沿用 `prompt-library.md` 慣例）
- **改變**：原計畫的 `manifest.json` 與 `build_chowchow_loop.py` 不再需要——序列定義移進資料庫，拼接變成工作器裡的一個函式
- 刪除無用的 Next.js `console/`
- 舊的 `staged_workflow_server.py` 在新控制台上線後功成身退（保留或刪除再議）

### Lyria-Auto-Publisher

新增 `src/lyria_auto/studio/`：
- `schema.py` 新資料表
- `worker.py` 背景工作器與任務分派
- `stages.py` 各階段的實作
- `app.py` FastAPI 路由（含 `/artifact/{id}` 帶 Range 的媒體服務）
- `web/` 單頁前端

新增 `src/lyria_auto/providers/comfyui.py`（送單、輪詢、取檔）
新增 `src/lyria_auto/media/concat.py`（串流複製拼接 + 兩小時合成）
`cli.py` 新增 `lyria-auto studio` 啟動控制台

**兩個已確認的小缺口**：
1. `StateDB.update_track` 的白名單是 `{audio_path, duration_seconds, sha256, status, error}`，**`prompt` 不在其中**——關卡 3a 若要編輯提示詞需要放寬（一行）或加新方法
2. 逐曲重生（3b）**不需要新的生成程式碼**，沿用既有 `_track_is_reusable()` 機制即可

不動：Gemini/Veo 視覺管線、`review_server.py`、音樂生成與安全機制、YouTube 上傳。

---

## 六、分階段實作

| 階段 | 內容 | 可否先驗證 | 狀態 |
|---|---|---|---|
| 0 | 環境：`brew install ffmpeg python@3.12`、建 venv | — | ✅ 完成（ffmpeg 8.1.2 / Python 3.12.13） |
| 1 | comfyui repo 修復 + API 格式匯出 + 升頻工作流 | 驗證器與測試轉綠 | ✅ 完成，17/17 測試通過（API 格式匯出留待 Stage 3 人工在 ComfyUI GUI 做） |
| 2 | studio 骨架：資料表、佇列、工作器、FastAPI、episode CRUD | **用假的生成器就能全程測**，不碰 ComfyUI | ✅ 完成，`lyria-auto studio` 可啟動，147/147 測試通過，六道關卡的核准/退回/自動接續已用假生成器端到端跑通 |
| 3 | ComfyUI provider + 關卡 1a/1b/1c/2 | 需真的跑 ComfyUI | 待進行——需要你在機器前操作 |
| 4 | 拼接 + 音樂關卡 3a/3b（接既有 Lyria 管線） | 音樂端可用 fake provider 測 | 待進行 |
| 5 | 最終合成（串流複製最佳化）+ 關卡 4 | 端到端 | 待進行 |
| 6 | 打磨：進度顯示、錯誤復原、稽核紀錄呈現 | | 待進行 |

**Stage 2 實作細節**（供之後銜接 Stage 3 參考）：
- 新表 `episodes` / `episode_assets` / `studio_tasks` 加在 `db.py` 既有的 `StateDB`，沿用 `state_version` 樂觀鎖與 `transition_preflight` 的寫法；順手修正了原本 migration runner 的一個 bug（舊碼在 visual migration 已套用時會提前 `return`，導致之後永遠不會套用第二個 migration——已改成不會互相阻擋）。
- `src/lyria_auto/studio/worker.py`：單執行緒輪詢，handler 用 dict 注入，方便測試用假生成器跑全程。
- `src/lyria_auto/studio/app.py`：核准會自動接續下一關（keyframe 核准後 fan-out 出 sleep/lookup 兩個 motion_test 任務；兩個 clip_1080p 都核准才 fan-in 出 build_loop），退回會建立新 variant 重新排隊，不會覆蓋原本失敗的紀錄。Artifact 服務走 FastAPI 的 `FileResponse`，已驗證 HTTP Range（206 Partial Content）真的有作用。
- `lyria-auto studio` 指令目前用空的 handler map 啟動 worker——建立集數後關鍵幀任務會立刻顯示「no handler registered」，這是預期行為，不是錯誤，等 Stage 3 接上真的 ComfyUI provider 後就會換成真的 handler。

階段 2 特別值得強調：**整條流水線的骨架可以在完全不碰 ComfyUI 和不花任何 API 費用的情況下測完**，用假素材走完六道關卡。這讓後面每一步的除錯範圍都很小。

---

## 七、誠實的規模評估

這不是小工程。粗估新增 **2,500–4,000 行**程式碼（含測試），六個階段。好消息是資料層、音樂管線、render 程式碼、ffmpeg 封裝都能沿用，真正從零寫的是控制台、工作器、ComfyUI provider 這三塊。

風險排序：
1. **ComfyUI API 格式**（取捨 1）——沒處理好整條自動化不會動
2. **升頻語意**（取捨 2）——沒講清楚會做出「核准的不是出貨的」矛盾系統
3. `LoadVideo` 節點是否存在——影響升頻走 ComfyUI 還是 ffmpeg
4. 兩小時渲染時間——已有解方，但要實測驗證串流複製可行

---

## 待你拍板

1. **取捨 2 的解析度方案**（768×432 試拍 → 1024×576 正式 → 純後製升頻）是否同意？這是內容品質決定，不是純技術決定。
2. **FastAPI 兩個新依賴**可否接受？（換取影片可拖曳審核）
3. 控制台放在 Lyria 專案內是否同意？（Lyria 的名字會不再精確涵蓋它的職責）
