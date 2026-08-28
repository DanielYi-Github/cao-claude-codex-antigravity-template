# Spec：Gemini 寫實循環場景與長影片視覺審核流程

- 建立日期：2026-07-29
- 狀態：已與使用者確認設計，待實作
- 適用專案：Lyria Auto Publisher
- 核心決策：Nano Banana 2 圖片、Veo 3.1 Fast 影片、付費 API 可見浮水印前置驗證、兩階段人工審核、最多四個場景循環

---

## 1. 目標

目前成片使用 `thumbnail.jpg` 搭配 FFmpeg `-loop 1`，整支影片只有一張靜態圖。新流程要為每張 Lyria 專輯建立一個照片級寫實、無人物的咖啡館世界，產生最多四個固定鏡位的 8 秒循環動畫，並以每 30 分鐘換景的方式組成 2～5 小時影片。

成功條件：

1. 四個場景屬於同一間咖啡館、同一時段與同一天氣，只改變鏡位及局部環境。
2. 鏡頭固定，只允許雨滴、蒸氣、火焰、窗簾、植物及光影等自然微動。
3. 每段動畫約 8 秒，首尾接縫在人工審核時不易察覺。
4. 2 小時後重複 `A → B → C → D`，因此 4～5 小時影片不增加 Gemini 視覺成本。
5. 圖片與影片各有一個人工審核關卡；未核准前不生成 Lyria 音樂、不渲染、不上傳。
6. 中斷後可從 SQLite 狀態續跑，不重做已成功、已下載或已核准的付費資產。
7. 預設視覺成本控制在每支長影片約 US$9，任何額外付費重生都需要明確確認。

---

## 2. 現況與接點

| 現有位置 | 現況 | 本設計的處理 |
|---|---|---|
| `src/lyria_auto/prompt_engine.py` | 一支影片共用 genre、scene、texture、production | 沿用為視覺 world bible 的來源 |
| `src/lyria_auto/pipeline.py::_plan` | 建立 tracks、metadata、`plan.json`、簡易縮圖 | 加入視覺規劃，但 dry-run 不呼叫付費視覺 API |
| `src/lyria_auto/media/thumbnail.py` | 本機背景或簡單漸層加文字 | 保留為 legacy；新工作改用核准場景衍生縮圖 |
| `src/lyria_auto/media/video.py` | 靜態圖片加音訊 | 保留 legacy 函式；新工作改走視覺時間軸渲染器 |
| `src/lyria_auto/db.py` | jobs、tracks、videos、events | 新增 visual_scenes、visual_assets |
| `src/lyria_auto/cli.py` | run、resume、status 等指令 | 新增 `review --job ID`，run/resume 依狀態主動暫停 |
| `src/lyria_auto/providers/youtube.py` | 已送出 `containsSyntheticMedia` | 維持 `true`，不隱瞞寫實 AI 視覺與 AI 音樂 |

`Pipeline` 仍是總協調者，但新的視覺責任必須拆成小模組，避免繼續擴大目前已包含規劃、音樂、渲染與上傳的單一檔案。

---

## 3. 已確認的產品決策

1. **人工審核模式**：每支影片上傳前，由使用者核准圖片與影片。
2. **預算**：每支影片視覺成本 US$5～10。
3. **場景關係**：同一間咖啡館的四個鏡位，不是四間不同咖啡館。
4. **人物**：完全不出現人物。
5. **循環長度**：接受 8 秒，優先確保接縫自然，不強求 10 秒。
6. **攝影機**：固定鏡頭，不推鏡、不平移、不縮放。
7. **審核介面**：`lyria-auto review --job ID` 啟動 localhost 頁面。
8. **審核階段**：先核准圖片，再核准影片。
9. **縮圖**：與第一場景同世界，但使用專用構圖和少量一致品牌文字。
10. **長片策略**：最多生成四個場景；每 30 分鐘換景，超過 2 小時後循環重用。
11. **時間邏輯**：四個場景維持同一時段與天氣，不做深夜到白天的線性推進，避免 D 回到 A 時出現時間倒退。
12. **模型備援**：Omni Flash 只能由人手動選擇，不能自動或靜默切換。
13. **浮水印前置驗證**：正式批次生成前，必須用目前設定的付費 Gemini Developer API 模型各產生一張原始圖片及一段 8 秒原始影片，由人工確認沒有右下角等肉眼可見的產品標誌。

---

## 4. 模型選擇與成本

### 圖片

- 主模型：`gemini-3.1-flash-image`（Nano Banana 2）
- 輸出：2K、16:9、無文字
- 一張 world anchor
- 每個場景三張候選，四個場景共十二張
- 第一場景核准後，額外衍生一張無文字的縮圖背景
- 縮圖文字由 Pillow 本機疊加，不要求模型生成文字

以 2026-07-29 官方價格估算，2K 圖片約 US$0.101／張。十四張約 US$1.41。

### 影片

- 主模型：`veo-3.1-fast-generate-preview`
- 輸出：8 秒、1920×1080、16:9、24 fps
- 每個場景兩段候選，共八段
- 同一張核准圖片同時作為 first frame 與 last frame
- 提示詞要求 single continuous shot、locked-off camera、no people、no cuts
- Veo 產生的音軌下載後立即移除，不進入最終成片

以 1080p 每秒 US$0.12 估算，`8 段 × 8 秒 × US$0.12 = US$7.68`。

### 總額

預設第一輪約 US$9.09。這是價格快照，不是永久價格保證；真正的硬性保護採「資產數量上限」，而不是只依賴美元估算。

### 浮水印與 API 來源

Gemini 圖片和 Veo 影片的官方 API 文件都說明輸出包含 SynthID；Google 將 SynthID 定義為肉眼不可見的數位浮水印。Gemini Developer API 文件沒有把右下角可見標誌列為付費 API 原始輸出的固定行為。相對地，Google Flow 官方文件明確說明部分消費者方案會加上可見的「made with Veo」標誌。因此本專案只能使用 Gemini Developer API 回傳的原始位元組，不能把 Gemini 網頁版、Flow、Google Photos、AI Studio 預覽畫面或經其他下載介面處理的檔案當成正式來源。

在第一支正式視覺工作前執行一次付費 smoke test：

1. 使用目前設定的 image model 生成一張 2K、16:9 圖片。
2. 使用目前設定的 video model 生成一段 8 秒、1080p 影片。
3. 不裁切、不修補、不重新編碼，直接保存 API 原始回應及 SHA-256。
4. 保存 provider、完整 model ID、endpoint、計費專案識別、SDK 版本、時間與價格快照。
5. 由使用者查看原始檔，分別標記 `passed_no_visible_mark` 或 `failed_visible_mark`。
6. 只有兩個項目都通過，才允許建立正式付費視覺資產。

這項測試預估為 `US$0.101 + 8 秒 × US$0.12 = US$1.061`，獨立於單支長影片的 US$5～10 預算。它有自己的一張圖片及一段影片硬上限，失敗時不自動重試。

任何一項改變時，先前核准立即失效並要求重新測試：

- provider 或 API endpoint
- image model 或 video model 的完整 ID
- 計費專案
- 產出經過的產品介面

preview model 的底層版本可能在相同 alias 下更新，因此最後一次通過超過 30 天時，`doctor` 必須警告；scheduler 不得建立新的視覺工作，直到重新核准。若原始 API 輸出出現可見標誌，系統停止並保存樣本供查證，不提供自動裁切、遮蓋、修補或破壞 SynthID 的功能。

官方參考：

- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Veo 3.1 video generation](https://ai.google.dev/gemini-api/docs/video)
- [Gemini image generation](https://ai.google.dev/gemini-api/docs/image-generation)
- [Verify Google AI-generated images with SynthID](https://support.google.com/gemini/answer/16722517?hl=en)
- [Flow visible watermark behavior](https://support.google.com/flow/answer/16353333?hl=en&rd=1)
- [Google Generative AI Prohibited Use Policy](https://policies.google.com/terms/generative-ai/use-policy?gl=US&hl=en-US)

---

## 5. 世界設定與提示詞策略

`VisualPlanner` 從專輯的 `PromptPlan` 取得 scene、mood、genre、texture 與 production，產生一份不可在同一 job 內漂移的 world bible。至少固定：

- 建築格局與空間大小
- 木材、牆面、窗框、桌椅、燈具材質
- 主色、色溫、時間與天氣
- 窗外城市／山景／海景特徵
- 桌面道具的種類與位置原則
- 無人物、無可辨識品牌、無可讀文字

先用 world bible 生成一張 `world_anchor.png`，後續十二張候選都把它當參考圖。四個鏡位角色固定為：

| 場景 | 鏡位目的 | 可用微動 |
|---|---|---|
| A | 窗邊主視覺，亦為縮圖世界來源 | 雨滴、咖啡蒸氣、遠處散景 |
| B | 書牆或閱讀角 | 壁燈微光、書頁或窗簾極小幅移動 |
| C | 吧台或咖啡器具區 | 蒸氣、吊燈反射、細微陰影 |
| D | 壁爐或深處座位 | 火焰、植物葉片、暖光變化 |

提示詞不得要求人物、對話、運鏡、景別切換、招牌文字或戲劇性事件。四個場景的變化來自鏡位，而非從夜晚走到白天。

---

## 6. 元件與責任邊界

### `visual_planner.py`

定義 world bible、四個 scene plans、圖片提示詞、影片提示詞和縮圖背景提示詞。輸出只包含規劃，不直接呼叫 API。

### `providers/gemini_visual.py`

提供 `generate_image`、`start_video`、`poll_video`、`download_video`。每個方法只負責 Gemini API 及回應正規化，不處理 DB 狀態機。model ID 全部由設定檔傳入，不寫死於業務邏輯。

### `media/visual_quality.py`

以 FFprobe、FFmpeg 與 Pillow 檢查檔案完整性、解析度、比例、影片長度、fps、黑畫面、首尾影格差異及運動量。它只回傳量測結果，不自行重生資產。

### `review_server.py`

啟動綁定 `127.0.0.1` 的隨機 port，顯示候選資產並保存核准結果。使用 job-scoped 隨機 token 防止其他本機頁面誤送請求。頁面點擊本身絕不呼叫付費 API。

### `media/timeline.py`

根據實際最終音訊長度，建立 `A → B → C → D → A…` 的 30 分鐘區段，並呼叫 FFmpeg 渲染。它不關心候選或 Gemini，只接收四個已核准的本機影片路徑。

### `Pipeline`

只負責依 job 狀態呼叫上述元件、在人工關卡停止、組合既有音樂流程、渲染和上傳。

---

## 7. CLI 使用流程

```bash
# 1. 免費規劃，不呼叫 Gemini 圖片／影片或 Lyria
lyria-auto run --dry-run --videos 1

# 首次使用或驗證已失效時，明確授權一張圖片及一段影片的付費測試
lyria-auto visual-preflight --watermark-smoke-test \
  --allow-image-outputs 1 --allow-video-outputs 1

# 查看命令輸出的本機原始檔路徑後，記錄人工判定
lyria-auto visual-preflight --approve RUN_ID

# 假設 dry-run 產生 job 12；驗證通過後開始正式視覺流程，生成圖片候選後停止
lyria-auto run --from-job 12

# 2. 在本機網頁核准各場景圖片
lyria-auto review --job 12

# 3. 生成影片候選後停止
lyria-auto resume --job 12

# 4. 在本機網頁核准各場景影片
lyria-auto review --job 12

# 5. 視覺核准後才生成音樂、渲染與上傳
lyria-auto resume --job 12 --upload
```

`review` 指令根據 job 狀態自動顯示圖片或影片，不需要額外 mode 參數。完成核准後它只更新 DB；使用者必須明確執行下一次 `resume` 才會產生新費用。

若某一組被拒絕，額外付費重生使用明確的數量授權：

```bash
# 圖片階段：只為 A 場景增加三張圖片
lyria-auto resume --job 12 --regenerate-scene A --allow-extra-image-outputs 3

# 影片階段：只為 A 場景增加兩段影片
lyria-auto resume --job 12 --regenerate-scene A --allow-extra-video-outputs 2
```

兩個 `--allow-extra-*-outputs` 不能同時使用；指定數量必須與即將送出的輸出數完全相等，否則在呼叫 API 前失敗。未指定 `--regenerate-scene` 時，普通 `resume` 不得超出預設資產上限。

排程器可把新工作推進到 `awaiting_image_review`，但不得跨過任何人工關卡，也不得在未核准視覺時公開上傳。

`visual-preflight --approve` 會逐一要求使用者確認原始圖片及影片沒有肉眼可見的產品標誌，不接受非互動式預設同意。若選擇失敗，該 run 保留為 `failed_visible_mark`，不能改寫成通過；修正 API 來源或設定後必須建立新的 run。

---

## 8. 狀態機

```text
valid visual preflight
  ↓
planned
  → generating_images
  → awaiting_image_review
  → generating_videos
  → awaiting_video_review
  → generating_audio
  → rendering
  → rendered
  → uploading
  → complete
```

失敗狀態不另建一套平行狀態；job 保留發生錯誤前的階段，並把錯誤寫入 `jobs.error` 與 `events`。`resume` 先依資產狀態判斷可否重用，再從該階段接續。

人工關卡行為：

- `awaiting_image_review`：所有有效 scene 都必須有一個核准 image asset。
- `awaiting_video_review`：所有有效 scene 都必須有一個核准 video asset，且縮圖預覽必須核准。
- 若目標影片短於 2 小時，唯一場景數為
  `min(4, ceil(target_duration_minutes / scene_interval_minutes))`。
- 目標影片為 2 小時以上時，唯一場景數固定為 4。

---

## 9. SQLite 與檔案

新增三張表。

### `visual_preflight_runs`

這是專案層級的付費 API 來源驗證，不屬於個別 job。核心欄位：

- `id`
- `provider`
- `endpoint`
- `image_model`
- `video_model`
- `billing_project_id`（API 可安全取得時）
- `credential_fingerprint`（使用安裝期 secret 計算 HMAC，不儲存 API key）
- `sdk_version`
- `image_operation_id`
- `video_operation_id`
- `raw_image_path`
- `raw_video_path`
- `image_sha256`
- `video_sha256`
- `image_result`
- `video_result`
- `estimated_cost_usd`
- `pricing_snapshot_json`
- `status`
- `approved_at`
- `expires_at`
- `created_at`

`status` 只允許 `running`、`awaiting_review`、`passed_no_visible_mark`、`failed_visible_mark` 或 `failed_generation`。失敗狀態不可改回通過。若無法取得計費專案識別，就以 credential fingerprint 變更作為重新驗證條件；fingerprint 只供比對，不能反推出或取代憑證。

### `visual_scenes`

核心欄位：

- `id`
- `job_id`
- `position`（0～3）
- `label`
- `world_json`
- `image_prompt`
- `motion_prompt`
- `selected_image_asset_id`
- `selected_video_asset_id`
- `status`
- `approved_at`
- `created_at`
- `updated_at`

`UNIQUE(job_id, position)` 防止 resume 重複建立場景。

### `visual_assets`

核心欄位：

- `id`
- `job_id`
- `scene_id`（world anchor／thumbnail 可為 null）
- `asset_type`（world_anchor、scene_image、scene_video、thumbnail_background）
- `variant_index`
- `provider`
- `model`
- `operation_id`
- `prompt`
- `path`
- `mime_type`
- `width`
- `height`
- `duration_seconds`
- `seam_score`
- `motion_score`
- `sha256`
- `estimated_cost_usd`
- `pricing_snapshot_json`
- `status`
- `error`
- `created_at`
- `updated_at`

API 一旦回傳 operation ID 就立即 commit，之後才開始 polling。下載完成後必須先寫暫存檔、驗證可解碼、計算 SHA-256，再原子移到正式路徑並標記 `ready`。

`plan.json` 增加 `visual_plan_version: 1` 及可讀的視覺規劃快照。SQLite 是執行狀態來源，JSON 是人工稽核與除錯用途。

舊 job 沒有 `visual_plan_version`，一律維持 legacy 靜態縮圖流程，避免升級後改變未完成舊工作的語意。

---

## 10. 審核頁面

圖片審核：

- 依 A～D 分組，每組顯示三張 16:9 候選。
- 顯示模型、variant、解析度、自動 QC 結果與估計成本。
- 每組只能核准一張。
- 可拒絕整組；拒絕不會立刻重生，只把 scene 標記為 `needs_regeneration`。
- 所有有效 scene 都選好後，需要一次「確認核准」。

影片審核：

- 影片靜音、自動循環播放，可並排比較兩個候選。
- 可切換「顯示循環接點」，從結尾連播到開頭。
- 顯示 duration、fps、seam score、motion score 與 QC 原因。
- 自動 QC 失敗的候選預設不可選；進階開關可供人工檢查，但仍不可核准。
- 同頁顯示已套用 Pillow 文字的縮圖預覽；縮圖未核准時不能完成本階段。
- 所有有效 scene 都選好且縮圖核准後，需要一次「確認核准」。

額外重生必須離開 review 頁面，改由 CLI 明確執行並顯示新增資產數與估計成本。審核伺服器不得持有呼叫付費模型的程式路徑。

---

## 11. 成本保護

預設硬上限：

```yaml
visual:
  enabled: true
  max_unique_scenes: 4
  scene_interval_minutes: 30
  image_candidates_per_scene: 3
  video_candidates_per_scene: 2
  max_world_anchor_images: 1
  max_thumbnail_backgrounds: 1
  max_image_outputs: 14
  max_video_outputs: 8
  estimated_budget_usd: 10.00
  image_2k_estimated_usd: 0.101
  video_1080p_second_estimated_usd: 0.12
  pricing_snapshot_date: "2026-07-29"
  seam_max_normalized_mae: 0.06
  motion_min_normalized_mae: 0.002
  motion_max_normalized_mae: 0.10
  duration_tolerance_seconds: 0.25
```

規則：

1. 開始付費階段前列出即將產生的資產數與價格快照估算。
2. transient network error 可重送尚未建立 operation 的同一請求。
3. 已建立 operation 後只 polling，不建立第二個請求。
4. operation 明確失敗、被安全阻擋或產出 QC 不合格時，不自動補生。
5. 超出預設輸出上限時，要求獨立 CLI 參數確認額外數量；不能只靠設定檔意外放大。
6. Omni Flash 不在自動 fallback chain。
7. 根據價格快照算出的預估總額若高於 `estimated_budget_usd`，在任何付費呼叫前停止。價格快照是人工維護的保守估算；`doctor` 顯示日期，超過 30 天時警告使用者先核對官方價格。
8. preflight 預算與正式 job 預算分開計算；每個 run 最多一張圖片及一段影片，且必須由 CLI 精確授權。
9. 沒有有效且未過期的 `passed_no_visible_mark` preflight 時，正式 image/video API 呼叫和 scheduler 視覺工作都必須停止。

---

## 12. 品質檢查

### 圖片機械檢查

- 可由 Pillow 解碼
- 16:9
- 至少 1920×1080 等效像素
- 無全黑、全白或近乎單色輸出
- SHA-256 與檔案大小有效

### 影片機械檢查

- FFprobe 可解析
- 1920×1080、16:9
- 24 fps
- duration 與要求的 8 秒相差不超過 0.25 秒
- 無長時間黑畫面
- 首尾影格的 normalized RGB mean absolute error 不超過 0.06
- 以每秒一張取樣的相鄰影格 normalized MAE 中位數介於 0.002 與 0.10
- 移除音軌後只剩視訊 stream

normalized MAE 以每 channel 0～255 的平均絕對差除以 255，值域為 0～1。上述數字是初始機械門檻；人眼核准仍是最終標準，不能因分數通過而跳過人工審核。

### 人工檢查

以下項目不交給不可靠的自動分數做最終決策：

- 家具、窗框、杯子、書本是否變形
- 是否出現幽靈人物或人形倒影
- 四個鏡位是否真的像同一間咖啡館
- 雨滴、蒸氣、火焰是否符合物理直覺
- 循環接點是否被肉眼察覺
- 是否含錯誤文字、商標或突兀物件

---

## 13. 長影片時間軸與渲染

場景函式：

```text
scene_index(t) = floor(t / 1800 seconds) mod active_scene_count
```

範例：

| 影片長度 | 場景序列 |
|---|---|
| 2 小時 | A B C D |
| 2.5 小時 | A B C D A |
| 4 小時 | A B C D A B C D |
| 5 小時 | A B C D A B C D A B |

渲染規則：

- 每個 8 秒 asset 在自己的 30 分鐘區段內重複。
- 最後不足 30 分鐘的區段依實際音訊結束。
- 30 分鐘邊界使用約 2 秒視訊交叉淡化，邊界中心仍落在 `00:30:00`、`01:00:00` 等位置。
- 最終時長完全跟隨 `compilation_extended.m4a`，視訊可以在任意 frame 結束，但不得裁切音樂。
- 輸出為 H.264、1920×1080、24 fps、`yuv420p`、AAC 256k、`+faststart`。
- renderer 以實際音訊長度產生任意長度時間軸；即使音樂稍微超出設定，仍繼續 A～D 循環，不要求增加場景。

Veo 候選下載後先移除原生音軌，再產生供審核使用的 normalized loop asset。若首尾仍有微小差異，可在 loop asset 內使用極短交叉淡化；原始 Veo 檔保留作稽核。

---

## 14. 縮圖

第一場景核准後，以該圖和 world anchor 作參考，生成一張同世界但更適合縮圖的無文字背景：

- 主體靠右或置中，左側保留乾淨負空間
- 不含模型生成文字、招牌或品牌
- 保留第一場景的材質、天氣、色溫與主要物件

之後沿用 Pillow 疊加品牌文字，但改成較少且固定的資訊層級，例如：

- 小型系列標記：`COZY JAZZ · NO. 012`
- 主標題：場景中文短名
- 次標：`Study · Read · Relax`

文字內容、字型、位置與安全邊界由程式控制，避免 AI 圖片常見的錯字和每支影片排版漂移。縮圖只影響 YouTube 包裝，影片本體不疊字。

---

## 15. 失敗與續跑

| 情況 | 行為 |
|---|---|
| 429／5xx 且尚未取得 operation ID | 指數退避後重送同一請求 |
| 已取得 operation ID | 只恢復 polling 或下載，不建新 operation |
| 安全阻擋 | 保存原提示詞和原因，停止該候選，不自動改寫 |
| 下載中斷 | 保留 operation ID，刪除無效暫存檔，下次重新下載 |
| 自動 QC 不合格 | 保存資產與原因，禁止核准，不自動補生 |
| 人工拒絕 | 只把該 scene 標記為需重生 |
| 模型不存在／已停用 | 明確失敗，要求更新設定；不靜默換模型 |
| review server 關閉 | DB 不變，重新執行 `review --job` 即可 |
| FFmpeg 渲染失敗 | 沿用核准影音，只重跑 render |
| YouTube 上傳中斷 | 沿用現有 callback 保存的 video ID，避免重複上傳 |

Veo 雲端產物有保存期限；成功後必須立即下載。續跑的可靠來源是本機檔案與 SHA-256，不是日後仍可存取的遠端 URI。

---

## 16. YouTube 揭露與營利風險

`containsSyntheticMedia` 維持 `true`。寫實 AI 場景與 AI 音樂都應揭露。YouTube 官方說明指出，揭露標籤本身不會限制觀眾或影響營利資格；不能因此推論影片一定能通過 YPP。

主要風險是頻道整體看起來大量模板化、低變化或批量生產。緩解方式：

1. 每支影片建立新的 world bible、world anchor、四個場景和縮圖背景。
2. 音樂維持一支影片一張連貫但原創的專輯。
3. 人工核准視覺，不讓明顯瑕疵或不相關片段自動發布。
4. metadata 描述實際場景與音樂，不使用誤導性標題。
5. 保存 prompts、models、approvals、資產雜湊與 job 紀錄，證明創作流程。

相關官方文件：

- [Disclosing use of GenAI content](https://support.google.com/youtube/answer/14328491?hl=en)
- [YouTube channel monetisation policies](https://support.google.com/youtube/answer/1311392?hl=en-GB)
- [What kind of content can I monetise?](https://support.google.com/youtube/answer/2490020?hl=en)

本設計降低「單張靜態圖＋大量同模板影片」的風險，但不承諾或保證營利審核結果。

---

## 17. 測試

所有自動測試使用 fake Gemini clients，不呼叫真實付費 API。

### 規劃與時間軸

1. 相同 seed 產生相同 world bible 與四個 scene plans。
2. 四個 scene 共用世界屬性，但鏡位角色不重複。
3. 提示詞包含無人物、固定鏡頭、無剪接與自然微動限制。
4. 2、2.5、4、5 小時分別產生正確的 A～D 序列。
5. 影片超過 2 小時仍只有四個唯一場景。
6. 短於 2 小時時只生成實際需要的唯一場景數。

### DB、狀態與成本

1. 新表建立具冪等性，舊 DB 可直接開啟。
2. resume 不重複建立 visual scene 或 ready asset。
3. operation ID 在 polling 前已落盤。
4. 兩個人工關卡沒有完整核准時，pipeline 必須停止。
5. review 寫入選擇時驗證 asset 屬於同一 job 與 scene。
6. 預設最多十四張圖片、八段影片；超額需顯式授權。
7. 舊 job 沒有 visual plan 時仍走 legacy 路徑。
8. preflight 未通過、失敗或過期時，正式付費視覺工作不能開始。
9. provider、endpoint、model ID 或計費專案改變時，既有 preflight 核准失效。
10. preflight 的一張圖片及一段影片上限不能由普通重試突破。

### API adapter

1. 圖片生成成功、阻擋、429、5xx 與無效回應。
2. Veo operation 建立、poll、成功下載、失敗與中斷恢復。
3. 已成功下載並通過雜湊的資產不再呼叫 API。
4. Omni 不會被自動選用。
5. preflight 保存未經轉碼的 API 原始回應、完整呼叫來源資料及雜湊。
6. preflight 已建立 operation 後只 polling，不因中斷產生第二段付費影片。

### 媒體

1. 使用 FFmpeg 合成短測試片，驗證解析度、fps、duration 和 stream。
2. seam checker 能區分相似首尾與明顯不連續首尾。
3. motion checker 能抓出完全靜止及劇烈運動畫面。
4. Veo 音軌被移除。
5. 縮短場景間隔後，renderer 正確產生 A→B→C→D→A。
6. 最終視訊長度跟隨音訊且不裁切音樂。
7. 既有靜態影片、音樂循環及 YouTube 防重複上傳測試全部維持通過。

### 人工驗收

先完成 watermark smoke test：

- 原始圖片及影片都沒有右下角或其他肉眼可見的 Google、Gemini、Veo 或產品標誌。
- 驗證檔案未經裁切、修補或轉碼。
- 改用另一個 model ID、endpoint 或計費專案後，正式流程會再次被 preflight gate 擋下。
- 人工判定失敗的 run 無法被改寫成通過。

通過後，再各產出一支 private 的 2 小時與 5 小時影片：

- 檢查四種 8 秒循環接點。
- 檢查每 30 分鐘換景點。
- 檢查 2 小時後 D→A 的循環。
- 檢查四個場景是否屬於同一空間。
- 檢查縮圖與第一場景的一致性及文字正確性。
- 確認 YouTube 完成 1080p 處理、AI 揭露正確且沒有重複上傳。

兩支 private 驗收通過後，才允許 scheduler 建立視覺工作；scheduler 仍不得跨過人工核准或自動公開。

---

## 18. 不在本次範圍

- 不建立雲端審核後台、帳號系統或多人權限。
- 不生成或維持跨影片的固定角色。
- 不讓 AI 自動判斷「一定能營利」。
- 不用 Veo 產生的音樂或環境音。
- 不在一支影片中生成超過四個唯一場景。
- 不自動改寫被安全系統阻擋的視覺提示詞。
- 不自動切換到 Omni、Veo Standard 或其他更昂貴模型。
- 不重構與視覺流程無關的音樂、OAuth、排程或 metadata 程式。

---

## 19. 實作完成的定義

只有同時符合以下條件才算完成：

1. 所有新增及既有測試通過。
2. dry-run 保持零付費 API 呼叫。
3. fake-client 整合測試證明兩個人工關卡會停止與續跑。
4. 預設資產上限無法被普通重試意外突破。
5. 付費 API 原始圖片及影片通過可見浮水印 preflight，且 gate 的失效規則有自動測試。
6. 2 小時 private 影片通過人工驗收。
7. 5 小時 private 影片通過人工驗收。
8. YouTube 上傳仍送出 `containsSyntheticMedia: true` 且不重複建立影片。
9. 操作文件、設定範例、故障排除與成本說明同步更新。
