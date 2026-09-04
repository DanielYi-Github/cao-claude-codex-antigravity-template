# 五頁籤生產主控台：架構計畫 v2

## 實作更新（2026-09-05）

Phase 3–5 已在 `codex/chowchow-lora-v3` 完成，實際行為與早期規劃有三個刻意差異：

1. Tab 3 使用 Studio 啟動時已設定的本機／遠端 ComfyUI client，網頁不接受遠端 token。原因是目前沒有選定廠商或認證協定，避免把供應商專屬密鑰模型硬塞進通用 queue；開始正式處理仍有獨立的算力／費用確認關卡。
2. Tab 4 的 Gemini API key 僅活在同步 HTTP request。資料庫只寫一筆無 payload 的 `running` task 作進度與防雙重付費鎖；12 首依 slot 逐首發布，可單首修改 prompt 重生，完成後以 FLAC 交叉淡化並用完整專輯循環延長到設定時數下限。
3. Tab 5 提供可編輯的英文 YouTube 文案模板，不另外花一次 Gemini 呼叫；`containsSyntheticMedia=true`、Music category 和私人上傳預設沿用既有 YouTube provider。上傳必須 checkbox 與二次確認，不會自動發生。

完整驗收準則見 `artifacts/spec.md`，測試結果見 `artifacts/test-report.md`。以下保留原規劃與歷史 review，作為決策脈絡。

延續 [`studio-architecture-plan.md`](./studio-architecture-plan.md) 的六道關卡設計。這次要把審核台從「一個扁平、依 kind 分組的列表」重構成使用者要的**5個循序頁籤**介面，並把目前完全脫鉤的三塊東西正式串起來：雲端算力升頻、12首音樂生成、最終長影片組裝＋YouTube發布素材。

本文件由四個並行調查整合而成：`codex_reviewer` 的資料庫遷移提案、`agy_ui_data` 的 UI/UX 設計筆記（`artifacts/ui-notes.md`）、一次程式碼盤點（音樂/YouTube 既有邏輯）、一次外部研究（YouTube 上架規範 + ComfyUI 雲端服務現況）。

## 一、現況總結：底層邏輯很多已經存在，鬼打牆的根因是「今天完全繞過了這套系統」

| 頁籤 | 需要的功能 | 現況 |
|---|---|---|
| 1（12張關鍵幀） | prompt 輸入 → 生12張候選 → 選1張 | `generate_keyframe` 已經會正確帶入身分+場景 prompt、負面 prompt、固定 seed。**缺口**：`stages.py:287` 寫死 `batch_size: 1`，只寫回1筆 asset。12張需要 fan-out 成 12 筆 `episode_assets` 列（`variant_index` 欄位本來就是為這個設計的）。approve/reject 介面 `app.py` 已經有。 |
| 2（雙動作+低解析度64秒預覽） | 兩個獨立可重生的8秒片段 + 一鍵合成7+1預覽 | `generate_motion_test`（sleep/lookup 兩個 role）已存在。**缺口**：`build_loop`（`stages.py:390`）寫死抓 `clip_1080p`、要求 sleep+lookup 都已 approve，需要參數化支援低解析度版本（新的 `loop_preview` 素材種類）。 |
| 3（雲端升頻1080p） | 憑證輸入介面 + 送出升頻 + 預覽 + 合成高解析度7+1 | `upscale_clip` 已經有 `remote_comfyui` 分支（`cli.py:159`），純本機邏輯不用重寫。**缺口**：憑證輸入/切換介面完全沒有，而且要看你選哪個雲端服務決定 client 怎麼寫（見下方待拍板）。 |
| 4（12首音樂） | prompt輸入+預設按鈕 → 12首 → 逐首預覽 → 確認 | `jobs`/`tracks` 資料表、`PromptEngine.compose_album()`、`LyriaClient`、`extend_audio()` 全部都已存在。**缺口**：`episodes.music_job_id` 是完全沒有程式碼讀寫的空欄位；`LyriaClient` 只認環境變數 `GEMINI_API_KEY`，不接受執行時換金鑰；單首約173秒，不是5分鐘（12首≈30-36分鐘，不是1小時，需要 `extend_audio()` 補齊或多生幾首）。 |
| 5（最終組裝+YouTube建議） | 組合1080p循環+12首音樂成2-4小時長片 + 自動產生英文上架文案 | `metadata.build_metadata()`、`YouTubeClient.upload()` 都已支援完整欄位（title/description/tags/category）。**缺口**：兩者都不會「生成」文案，只會套模板；`render_final()`（`stages.py:427`）還缺「這個 episode 該用哪個音樂 job」的解析邏輯，這正是 `demo-task.md` 之前就列出的已知缺口。 |

**結論（也是回答你「為什麼一直卡住」的另一半）**：這套系統的骨架比想像中完整，缺的主要是「串接」跟「介面」，不是重新發明。

## 二、待你拍板：頁籤3的雲端服務到底串哪一個

你說的「ComfyUI 的雲端版本」查證後是官方的 **Comfy Cloud**（`cloud.comfy.org`）。查到的關鍵事實，跟你原本設想有落差：

- **免費額度（5次）不含 API 存取權**。官方 API 文件明講：programmatic API 需要付費方案（Standard/Creator/Pro），免費帳號只能透過網頁介面手動跑。也就是說「註冊多個免費帳號、讓程式自動輪流打 API」這個計畫**技術上行不通**——免費帳號打不了 API。
- 付費方案：Standard $20/月（年繳$16/月）、Creator $35/月、Pro $100/月，對應 4,200/7,400/21,100 credits；官方範例約 11 credits 跑一段 5秒 640×640 的影片，實際到 1080p 升頻要多少 credits 沒有公開數字，需要自己先跑一次估算。
- Comfy 的服務條款雖然沒有明講「一人一帳號」，但有「禁止規避存取限制」的條款——**為了規避額度而輪替帳號，有被認定違反條款的風險**（這是推論，不是白紙黑字禁止，但值得你知道）。
- API 相容性：**新版 v2**（`POST /api/v2/jobs`，Bearer token 驗證）可以吃你現有的 ComfyUI API 格式 workflow JSON，但傳輸協定跟本機的 `/prompt` 端點不同，需要新寫一個 client（不是換個 URL就好）；**舊版 v1** 幾乎跟本機 `/prompt` 一樣（`X-API-Key` 驗證），但官方已經在淘汰。

其他查到的替代方案，供比較：

| 服務 | 驗證方式 | 跟本機 workflow JSON 相容度 | 免費額度 | 定價 |
|---|---|---|---|---|
| **Comfy Cloud**（官方） | Bearer token | 高（同一份 JSON，換傳輸協定） | 5次，**不含API** | $20-100/月 |
| RunComfy | Bearer token | 部分（要先包成「deployment」） | 沒查到公開免費額度 | 約$0.99-9.59/GPU小時，隨用隨付 |
| ThinkDiffusion | 無公開API | 低（是瀏覽器VM，不是API服務） | 每帳號15分鐘 | 約$0.99-2.50/GPU小時 |
| ComfyDeploy | Bearer token | 部分（要先部署workflow） | 沒查到公開免費額度 | 未公開 |

**我的建議**：Comfy Cloud Standard（$20/月，年繳更省）是工程量最小、風險最低的選項——同一份 workflow JSON 可以重用，只要新寫一個 job/asset 的 client 轉接層，而且是官方服務、有 SDK。用免費帳號輪替繞過額度這條路，技術上打不通、也有條款風險，不建議走。如果你想壓低成本，RunComfy 的隨用隨付可能更便宜，但要多寫「deployment」那層轉接，工程量較大。

**已拍板（2026-08-30）**：先做成「通用外部 Provider 介面」——`base_url` + `token` 是可替換的抽象設定，介面（頁籤3的憑證輸入框、程式碼結構）先做完整，實際要接哪家服務、要不要付費，之後再決定，不卡住其他頁籤的進度。既有的 `comfyui_remote_base_url` + `ComfyUIClient` 已經是這個形狀的雛型，Comfy Cloud 的舊版 v1 API 又剛好跟本機 `/prompt` 格式接近，代表這個抽象層之後真的要接 Comfy Cloud 時，改動也不大。

## 三、資料庫遷移方案（codex_reviewer 提案，已用現有 47 個 studio 相關測試驗證邏輯基礎穩固）

- 新增 migration id（`0003_studio_production`），**不能直接改**現有的 `STUDIO_SCHEMA_SQL`——`db.py` 對 migration 內容做了 checksum 保護，既有資料庫會直接 checksum mismatch 掛掉。SQLite 不支援改 CHECK 約束，所以是「新建表→搬資料→重建索引」的標準流程。
- `episode_assets.kind` 新增：`loop_preview`、`music_track`（建議再加 `music_mix`，把「12首混好的1小時音軌」變成一個可獨立審核/重試的素材，而不是每次都在最終渲染裡臨時算）。
- `studio_tasks.task_type` 新增：`build_loop_preview`、`generate_music_tracks`（建議再加 `build_music_mix`）。
- `episode_assets` 新增 `track_id INTEGER REFERENCES tracks(id)`，把每首 `music_track` 素材列對應到 `tracks` 表的實際列，不要只靠插入順序推斷。
- **金鑰儲存鐵則**（codex 特別標成 High）：金鑰**絕對不能**寫進 `jobs.payload_json`、`studio_tasks.payload_json` 或任何會被序列化存檔/寫進 log 的地方。只存「後4碼遮罩顯示用」的指紋，真正可用的金鑰只留在程式執行時的記憶體裡。這也是這個 repo 已經真實發生過一次 API key 外洩事故後（見 `artifacts/spec.md` 的紀錄）定下的硬規則，不是新發明的謹慎。
- `render_final()` 的音訊解析邏輯：approve 好的高解析度 loop → 解析 `episode.music_job_id` → 抓 12 筆已核准的 `music_track` → 用既有的 `combine_audio()` 混成一軌 → 用既有的 `extend_audio()` 延長到你要的 2-4 小時 → 高解析度 64秒 loop 重複 `ceil(音訊長度/64)` 次 → 用 `-shortest` 合成、驗證長度。

## 四、實作順序（我會依序做，不會同時對同一批檔案並行改，避免衝突）

1. **Phase 0 — DB 遷移**：✅ 完成（2026-08-30）。`0003_studio_production` migration、`docs/data-contract.md` 同步更新、204 個測試全過。經 `codex_reviewer` 兩輪覆核，中途實際抓到並修復 3 個會咬人的 bug：遷移不是原子操作（中途失敗會半殘）、宣告的外鍵其實從未真正生效、`autocommit=False` 模式下 `commit()` 後連線仍卡在交易中導致重新開啟外鍵保護的 PRAGMA 靜默失效。順帶修正了 `pyproject.toml`/`scripts/setup.sh`/`config_migration.py` 的 Python 版本下限（3.11→3.12，因為修復用到 3.12 才有的 API）。
2. **Phase 1 — 頁籤1（12張候選）**：✅ 完成（2026-08-30）。`generate_keyframe` fan-out 成N筆、前端5頁籤外殼+完整頁籤1、選圖=approve。這是跟你今天鬼打牆最直接相關的一塊，優先做完驗證整個模式可行。經 `codex_reviewer` 兩輪用真的併發壓力測試（不只是看程式碼）總共抓到 9 個 bug，逐一修完並補上回歸測試（219 個測試全過）：

   第一輪修的：
   - regenerate 沒擋掉「上一批還在跑」和「已經核准過」兩種情況，會讓兩批候選同時可選、或核准兩張——修法：`POST .../keyframes/generate` 現在在這兩種情況回 409，前端也同步把「Regenerate All」按鈕在已核准時隱藏/停用。
   - 批次生成中途失敗（下載/解析某張圖失敗）之前已經下載完的圖片會被建成 `awaiting_review` 的 asset row，跟標成 `failed` 的 task 並存，狀態不一致——修法：先把整批圖都下載驗證完，才開始建立 DB row，任何一張失敗就整批都不建。
   - 舊的「呼叫 `enqueue_task` 時帶 `asset_id`」呼叫方式對 `generate_keyframe` 已經不成立（handler 完全不讀 `task["asset_id"]`），但沒有擋掉——修法：`enqueue_task` 現在對這個 task_type 直接拒絕非空的 `asset_id`（確認過正式資料庫裡沒有任何一筆舊資料違反這個規則，不需要補資料遷移）。
   - 順手修正 `keyframe_batch_size` 的程式內建預設值（4）跟 `config/settings.yaml` 宣告的預設值（12）不一致，以及 agy_ui_data 前端 diff 裡的空白字元檢查問題。

   第二輪（我自己補的 409 guard 帶出的新問題，加上 codex 第二次覆核抓到的）：
   - 我第一輪加的 409 guard 本身有競態（先檢查、後動作，不是同一個交易）——判定跟下面「共用連線無鎖」是同一個根因，記錄為已知限制，不在這裡單獨修。
   - **這是真的 bug，已修**：批次是一張一張 publish 的，代表某張候選圖可能已經進入「待審核」但同批其他張還在生成中；這時如果核准這張，會提早觸發動作動作(motion_test)的 fan-out，跟還在生成的同批候選並存，狀態很亂。修法：`POST .../assets/{id}/approve` 對 `kind=keyframe`，只要該集數的 `generate_keyframe` 任務還在 `queued`/`running`，就直接 409，前端同步在生成中隱藏「選擇此關鍵幀」按鈕。
   - **這是真的 bug，已修**：系統原本就沒有「卡住的任務」復原機制（只有 module docstring 寫了一句「等 worker 重啟時處理」，但沒有真的實作）；現在我加的 409 guard 反而讓一個當掉的任務（例如 ComfyUI OOM）永久卡住整個集數，永遠沒辦法重新生成或核准。修法：`StudioWorker.start()` 現在會先呼叫 `recover_stale_tasks()`，把 process 啟動時仍卡在 `running` 的任務標記失敗（因為 worker 本來就是單一執行緒設計，process 剛啟動時不可能有真的還在跑的任務，這個假設是安全的）。
   - 順手修正一個「假綠燈」的測試：`test_generate_keyframe_uses_prompt_from_task_payload` 沒有明確設定 `keyframe_batch_size`，改成預設 12 之後跟假造的 ComfyUI 只回傳 1 張圖不一致，但測試本身只檢查送出的 prompt 內容，沒檢查任務有沒有真的成功，所以任務其實失敗了，測試卻還是綠燈。修法：共用的 `_run()` 測試輔助函式現在會額外斷言沒有任何任務失敗；順帶把兩個因此曝光出的、同樣沒明確設 `keyframe_batch_size` 的測試也一併修正。

   **尚未修，記錄為已知限制**：`StateDB` 讓 FastAPI 的請求執行緒和背景 worker 執行緒共用同一個 `sqlite3.Connection`（`check_same_thread=False`），沒有任何鎖——codex 用併發壓力測試真的重現了 `InterfaceError`、大量失敗，以及「兩個併發請求都通過 409 檢查，各自建立一批」的競態（我第一輪加的 guard 本身不是原子操作）。這是 Phase 1 之前就存在的架構問題，影響所有 stage，不只是關鍵幀；修法（幫整個 `StateDB` 上鎖，或改成每個執行緒各自的連線，或用 `BEGIN IMMEDIATE` 包住檢查+動作）工作量足夠大，值得獨立一個 phase 處理，不在這裡順手修。目前單一使用者、單一瀏覽器分頁、依序點擊的使用模式下，這些 guard 是正確的；只有在真的出現併發請求時才會暴露這個根因未解的問題。

   **尚未修，記錄為已知限制**：`StateDB.next_variant_index()` 用「讀 MAX 再寫入」兩個分開的步驟，跨行程不是原子操作——但目前 `cli.py` 只會啟動一個 `StudioWorker` 執行緒（設計本來就是單一 worker），所以現況不會撞號；只有在未來真的要跑多個 worker 行程時才需要處理，已在程式碼加註記。
3. **Phase 2 — 頁籤2（低解析度雙動作+64秒預覽）**：`build_loop` 參數化支援 `loop_preview`。
4. **Phase 3 — 頁籤3（雲端升頻）**：等你回覆第二節的服務選擇後才動工，因為要串的 client 完全不同。
5. **Phase 4 — 頁籤4（12首音樂）**：`LyriaClient` 加可注入金鑰、`PromptEngine` 加使用者自訂12個prompt的路徑、`music_job_id` 真正被讀寫、`extend_audio()` 補時長。
6. **Phase 5 — 頁籤5（最終組裝+YouTube文案）**：`render_final()` 補音訊解析邏輯、新增一段用 Gemini 生成英文 YouTube title/description/category 的邏輯（套用第六節的規範重點）。

每個 phase 我實作完會請 `codex_reviewer` 覆核（含跑測試），前端部分穩定後請 `agy_ui_data` 依照它已經寫好的設計筆記把對應頁籤的 HTML/CSS/JS 做出來。

## 五、YouTube 上架風險備忘（外部研究整理，會用在頁籤5的自動文案邏輯裡）

- **重複/低努力內容政策風險（High，2025年7月更新的政策明講）**：YouTube 明文禁止靠「純播放清單」「模板化敘事」「AI生成的通用模板內容」營利。同一個場景循環+隨機接歌，結構上很接近被禁止的樣態。**緩解方式**：每支影片要有真正的視覺變化（季節/天氣/時段/擺設不同），不要只換配樂順序；每首歌要有真實標題，善用 YouTube 章節功能。
- **AI/合成內容揭露**：如果畫面走寫實風格（不是明顯插畫/卡通），建議主動開啟「Yes, altered or synthetic」揭露選項——官方說明這不影響營利資格，只有「該揭露卻沒揭露」才有下架/警告風險，沒有downside。
- **分類建議**：走「Music」類別比「Pets & Animals」更常見也更利於長時間觀看的演算法對待，同類頻道（Lofi Cat Cafe等）大多也是分在 Music。
- Title/Description 的具體建議格式已整理進外部研究報告，會直接套用到頁籤5自動產生的文案模板。

## 六、誠實的規模評估

這是一個貨真價實的多階段工程，不是一次對話能做完的。Phase 0+1 (DB遷移+頁籤1) 是最小可驗證的一步，做完你就能真的用這個系統跑出「12張候選圖，選一張，記錄進資料庫」的完整流程——這正是今天卡住的那個環節。我建議先把這兩個 phase 做完、你實際用過確認方向對了，再往後面的 phase 推進，而不是一次把5個頁籤都寫完才給你看。

## 七、Phase 2–5 詳細規劃與分工（2026-08-30 更新）

Phase 0、1 都已完成並經 `codex_reviewer` 兩輪驗證（見上方記錄）。這一節把第四節原本的一行式待辦，攤開成實際可執行的計畫——過程中發現第四節原本對頁籤2、4 的範圍描述有落差，已在下面修正並註明原因。

### 7.0 前端架構決策：`app.js` 先拆成每頁籤一個模組

現在 `web/app.js`（254行）只有 `renderKeyframeTab` 一個頁籤的邏輯。接下來4個 phase 若各自在自己的 worktree/branch 對同一個檔案追加 `renderXTab`，會變成四條分支輪流改同一個檔案，Phase 1 已經因為這樣出現過空白字元的小衝突。動工 Phase 2 前，我會先做一次純結構重構（不改行為）：把 `renderKeyframeTab` 抽到 `web/tabs/tab1-keyframes.js`，`app.js` 改成只留頁籤切換、輪詢、通用 `api()` helper 的薄殼，之後每個 phase 各自新增一個 `web/tabs/tabN-*.js`。這樣 `agy_ui_data` 之後每個 phase 只會新增檔案，不會跟其他 phase 或跟我同時改同一個檔案。

### 7.1 Phase 2（頁籤2：低解析度雙動作＋64秒預覽）—— 範圍比原先寫的大

原第四節只寫「`build_loop` 參數化支援 `loop_preview`」，重新核對 `app.py` 後發現這樣不夠：現在 `_NEXT_KIND = {"motion_test": "clip", "clip": "clip_1080p"}`，代表核准任一個 `motion_test` 會**立刻**建立 `clip` 並送出 `generate_clip`——這直接跳過了 `ui-notes.md`（頁籤2設計稿）要的關卡：「兩個動作都核准 → 按『Assemble 64s Preview』→ 核准預覽 → 才進頁籤3」。這不是單純加一個新功能，是要動到現有的自動接續邏輯，需要一併處理：

1. `app.py`：拿掉 `_NEXT_KIND` 裡 `"motion_test": "clip"` 這條——核准 `motion_test` 不再自動接續到 `clip`。
2. `stages.py`：把 `build_loop` 內部抽成一個共用 helper，接受來源 kind（`motion_test` 或 `clip_1080p`）、目標 kind（`loop_preview` 或 `loop`）、解析度與檔名前綴這幾個差異點；`build_loop_preview`／`build_loop` 兩個 task_type 各自呼叫同一個 helper，不要複製貼上整段邏輯。
3. `app.py` 新增一個組預覽的端點（`POST /api/episodes/{id}/motion/assemble-preview`）：需要兩個 role 的 `motion_test` 都已 `approved` 才能呼叫；比照頁籤1 `keyframes/generate` 的兩個 409 guard，但範圍不是「已核准」而已——`loop_preview` 是單一結果、沒有像關鍵幀那樣的多候選 supersede 機制，每次組合又固定寫同一個檔名（沒有 per-build 的 variant index），所以只要還有一筆 `awaiting_review` 或 `approved` 的 `loop_preview` 存在，就要擋掉重新組合，不能只擋 `approved`（codex_reviewer 實測抓到：只擋 approved 的話，組完但還沒審核完的空檔會被第二次呼叫直接覆寫同一個檔案，見7.8的更新記錄）。
4. **（已修正，原規劃寫錯）** `_continue_after_approval` 的 `kind == "loop_preview"` 分支**不會**建立 `clip` asset 或送出 `generate_clip`——這是本節最初的規劃，寫下這行的時候還沒諮詢過 codex_reviewer；實際拍板的做法（見7.6/7.8）是核准 `loop_preview` 只解鎖頁籤3的 UI，`generate_clip`/`upscale_clip` 改成頁籤3自己的「開始雲端處理」動作觸發，因為兩者共用同一個需要金鑰的遠端 ComfyUI client。
5. 單一角色重新生成不需要新端點：既有的 `POST .../assets/{id}/reject` 對非 `keyframe` kind 本來就會建立新 variant 並重排同一個 task_type，這條路徑理論上已經涵蓋「只重來 sleep 或只重來 lookup」，實作時補一組明確測試確認 `motion_test` 走這條路正確即可，不必新寫端點。
6. 前端（`agy_ui_data`）：兩個獨立影片播放器＋各自 Regenerate 按鈕、「Assemble 64s Preview」按鈕（兩個角色都核准前 disabled）、預覽核准後解鎖頁籤3 的視覺提示——`ui-notes.md` 第76-80行的設計稿已經夠具體，可以直接依此實作。

### 7.2 Phase 3（頁籤3：雲端升頻）—— 範圍確認 + 一個要先講清楚的但書

供應商選擇已拍板（第二節）：做通用 provider 抽象，不綁定特定廠商。實作上要注意：

- `ComfyUIClient.__init__(self, base_url)`（`providers/comfyui.py`）目前完全沒有任何驗證機制——這個 phase 是「幫這個既有類別加驗證」，不是重寫。加一個可選的 `api_key`／`token` 建構參數，`submit`／`stage_input_file`／`_get_json`／`interrupt` 這幾個會發 HTTP request 的方法，有 token 時都要加 Authorization header。
- 金鑰鐵則（沿用第三節已拍板的規則，這裡是它第一次真的被落地）：頁籤3憑證輸入框送出的 token，只活在該次 HTTP request 處理過程的記憶體變數裡，組出 `ComfyUIClient` 實例後即用即棄；絕對不能出現在 `enqueue_task` 的 `payload_json`、不能被 log 印出。資料庫只存「後4碼指紋」＋「是否已設定」的布林值供前端顯示。
- **老實話，先講在前面**：`demo-task.md` 明文規定沒有人類明確同意前，不能把 `comfyui_remote_base_url` 接到真的付費服務去跑。這個 phase 做完會是「憑證 UI ＋ 支援 token 的 client ＋ fake-backed 測試」全綠，但不會有任何一次真的 1080p 升頻在真機上跑過。這個缺口會直接遺留給 Phase 5——`render_final` 讀的是核准過的高解析度 `loop`，沒有真的升頻結果，Phase 5 端到端流程同樣只能靠 fake 測試驗證邏輯，不會被真機驗證過。等你實際選定廠商、付費、且真的成功跑過一次 Phase 3，Phase 5 才有機會被完整驗證。

### 7.3 Phase 4（頁籤4：12首音樂）—— 刻意跟 Phase 1 反著設計

**不要照搬 Phase 1「整批下載驗證完才寫入 DB」的模式。** 關鍵幀是本機、免費、失敗重跑代價趨近於零；12首音樂是 Lyria 的真付費呼叫，單首約173秒，12首約30-36分鐘。全有全無的寫法代表「第11首失敗」會讓前10首已經花的錢也一起白費。Phase 4 要「每首完成就立刻各自建立 asset row 並標記可審核」，不要等整批做完才落庫。連帶地，approve 的 409 guard 也要反著設計：Phase 1 是「整批還在跑時擋掉 approve」，Phase 4 應該是「哪首生成完，哪首就能立刻審核」，不能整批擋。

這個逐首落庫的設計，順便也是目前「worker 執行緒沒有任何持久化/續傳機制」這個已知限制在 Phase 4 的實際緩解：即使伺服器在生成中途重啟（`recover_stale_tasks()` 會把還在 `running` 的任務標記失敗），已經逐首落庫的曲目不會跟著消失，只有當下正在生成的那一首需要重來，不是整批12首。要不要在這之外再做更徹底的併發修復，是一個獨立的、需要你決定的問題（見下方7.7）。

**寫程式前最需要問 codex 的問題**：這是 studio 任務第一次要寫「既有」的 `jobs`／`tracks` 表，不只是 `episode_assets`／`studio_tasks`。`episode_assets` 對 `kind='music_track'` 有 `CHECK(track_id IS NOT NULL)` 並外鍵到 `tracks(id)`，`episodes.music_job_id` 外鍵到 `jobs(id)`。`generate_music_tracks` 該怎麼插入 `jobs`＋`tracks` 這兩張表？既有的 `Pipeline` 類別（`pipeline.py`）對這兩張表的資料形狀有什麼假設？如果 studio 產生一個 `Pipeline` 從沒建立過的 job，既有的 `scheduler.py`／`review_server.py` 之類的舊程式碼會不會因為讀到非預期形狀而壞掉？外鍵插入順序該怎麼排（先 job 還是先 track）？這幾個問題會在 7.6 的一次性設計諮詢裡問 codex_reviewer，不會憑自己猜測寫下去。

其餘子項：

- `LyriaClient.__init__` 目前寫死從 `os.getenv("GEMINI_API_KEY")` 讀金鑰（確認過原始碼），建構子要加一個可選參數在執行時覆蓋——同樣的「不落地」鐵則：這個金鑰只能活在該次請求的記憶體傳遞鏈裡。
- `PromptEngine.compose_album(count, recent)` 目前是全自動配歌模式；頁籤4設計稿（`ui-notes.md`）的「快速預設按鈕」採用「填入文字框讓使用者可編輯後再送出」模式，跟頁籤1的 prompt 顯示/編輯模式一致（見7.5），不是按下去就直接送出生成。
- `extend_audio()`／`combine_audio()` 已經存在且可以直接重用，但那是 **Phase 5 `render_final` 的職責**（混音＋延長到2-4小時），Phase 4 只負責把12首個別音軌生好、寫進 `tracks`/`episode_assets`。

### 7.4 Phase 5（頁籤5：最終組裝＋YouTube文案）

- `render_final()` 補音訊解析邏輯：`episode.music_job_id` → 該 job 下已核准的12筆 `music_track`（經 `track_id` 對應到 `tracks` 表的實際列）→ `combine_audio()` 依序交叉淡化接成一軌 → `extend_audio()` 延長到目標長度 → 用既有的 loop-repeat 邏輯把核准的高解析度 `loop` 重複 `ceil(音訊長度/loop長度)` 次 → `-shortest` 合成＋`verify_render` 驗證。這段邏輯 `stages.py` 的 `render_final` docstring 本來就預告是「留給之後」的部分，現在要真的實作。
- YouTube文案是全新邏輯，不是重用 `build_metadata()`：現有的 `metadata.build_metadata()` 只套模板字串，而且 `config/settings.yaml` 的 `metadata:` 區塊是舊的 lofi-jazz 中文頻道專用模板，完全不適用松獅犬頻道。要新增一段「呼叫 Gemini 生成英文 title/description/category」的邏輯，套用計畫書第五節的政策重點（避免重複/低努力內容風險、AI合成內容揭露、Music分類建議），產出後組成新的 `Metadata` 物件或走新函式，不動 `build_metadata()` 本身（那個函式還是給舊頻道用）。
- 承接7.2的老實話：因為 Phase 3 不會真的產出高解析度素材，Phase 5 的端到端流程在真機上同樣不會被真的跑過，只能靠 fake-backed 測試驗證邏輯正確性。

### 7.5 `ui-notes.md` 開放問題：已經有答案了，這裡正式拍板

`artifacts/ui-notes.md`（110-112行）留了三個開放問題，其實都已經有既定決策可以直接回答，補記在這裡避免之後又重新討論一次：

- **憑證要不要存進 SQLite？** 不存明碼。第三節的金鑰鐵則已經定案：只存「後4碼指紋＋是否已設定」的布林/字串，真正可用的金鑰只活在記憶體裡，這條規則本來就回答了這個問題。
- **「Regenerate All」要不要保留之前的批次？** 頁籤1已經實作出的行為就是答案：舊批次用 `supersede_assets` 標成 `superseded`，不再顯示在畫面上，但資料庫裡不刪除。頁籤2-4凡是有「重新整理一批」的地方，沿用同一個模式，不需要重新設計。
- **頁籤4的快速預設按鈕是「直接送出生成」還是「先填文字框」？** 採用後者，跟頁籤1的 prompt 編輯模式一致（使用者這次已經明確反應過「要能看到並編輯 prompt 再送出」，見本次對話——沒有理由頁籤4的體驗跟頁籤1不一致）。

### 7.6 分工與流程

回應這次新增的指示（Codex 除了審核也要給實作意見；Antigravity 負責網路搜尋／收集資訊）：

- **一次性、涵蓋 Phase 2-5 全部的架構意見諮詢**：把7.1-7.4列出的設計問題（頁籤2的關卡重構是否合理、頁籤3的 token 加法有沒有遺漏的方法、頁籤4的 jobs/tracks 外鍵插入順序、頁籤5的音訊接軌邏輯有沒有算錯）一次問清楚給 `codex_reviewer`，不拆成四次個別問。理由：非同步輪詢這次一直不太穩定（改用 tmux 輪詢頂著用），且 codex 過去真正抓到9個真bug的地方是「實作完之後的併發壓力測試」，不是設計討論本身——設計討論沒必要拆四次，壓縮成一次問完，把 codex 的時間留給之後每個 phase 實作完的真實 review。
- 之後每個 phase 實作完，比照 Phase 1 模式，個別 `handoff` `codex_reviewer` 做該 phase 的程式碼 review + 跑測試，逐一處理 blocking finding。
- `agy_ui_data`：延續既有角色（每個 phase 後端穩定後，在自己的 worktree/branch 依 `ui-notes.md` 設計把該頁籤模組實作出來——7.0拆檔之後不會再互相衝突），這次額外加上「需要外部網路查證」的子任務，例如：Phase 4 開工前確認 `google-genai` SDK 目前 `Client(api_key=...)` 這個建構方式是否仍是官方目前建議的寫法；Phase 5 開工前重新核一次 YouTube Data API 「AI合成內容揭露」欄位的目前正確名稱、以及 Music 分類目前的 `category_id`（第五節資料是先前查的，開工前值得再核一次，YouTube API/政策會變動）。

### 7.7 待你決定：Phase 4 之前要不要插入一個「併發／持久化強化」phase

`StateDB` 共用連線無鎖、`recover_stale_tasks()` 把還在跑的任務直接標失敗，這兩個已知限制在頁籤1只會讓你損失免費的本機生成（頂多3分鐘），但頁籤4的 worker 會綁住30-36分鐘處理真的付費 Lyria 呼叫——7.3已經加了「逐首落庫」的緩解（意外重啟最多損失當下那一首，不是整批12首），但沒有處理「兩個併發請求都通過檢查」這種競態本身。

**已決議（2026-08-30，使用者拍板）**：頁籤4只做7.3的逐首落庫緩解，不在頁籤4之前插入額外的安全性強化 phase——維持 Phase 2→3→4→5 現有順序不delay。但完整的 `StateDB` 連線鎖定/交易修復正式排入未來待辦（暫定 **Phase 6：併發與持久化強化**，時間點待定，不是不做，只是不卡現在的進度），不再只是散落在 `docs/data-contract.md` 裡的「已知限制」註記——之後真的要排這個 phase 時，範圍是：幫整個 `StateDB` 上鎖、或改成每執行緒各自連線、或用 `BEGIN IMMEDIATE` 包住每個 check-then-act 序列，同時要重新檢視 `recover_stale_tasks()` 能不能分辨「process剛啟動、任務真的是孤兒」跟「任務其實還在合理跑，只是剛好伺服器重啟」。

**根因描述修正（2026-08-31，codex_reviewer Phase 2 review）**：這裡的根因不能只講成「共用 `sqlite3.Connection` 沒有鎖」——codex 用兩個獨立的 `StateDB` connection（不共用同一個連線物件）重現了 `assemble_motion_preview` 的 409 race，證明就算改成「每執行緒各自連線」也不會解決問題。真正的根因是：檢查（SELECT 有沒有 in-flight task／有沒有已存在的結果）跟動作（INSERT 新 task）是分開的兩個 DB 呼叫，中間沒有交易包起來、資料庫層也沒有唯一約束擋。之後排 Phase 6 時，具體修法是（擇一）：`BEGIN IMMEDIATE` 包住整段「檢查+動作」、或針對每個會被雙擊的操作（例如 `build_loop_preview`／`generate_music_tracks`）建立 partial unique index（例如 `WHERE task_type='build_loop_preview' AND status IN ('queued','running')`），把 constraint violation 在應用層轉譯成 409。

### 7.8 `codex_reviewer` 一次性架構諮詢結果（2026-08-30，工作14分30秒，8個blocking+5個non-blocking）

用 `cao launch`/`cao session send`（session: `cao-codex-phase2to5-design`）送出7.1-7.6的5個問題後，codex 完整讀過 AGENTS.md、本文件、`data-contract.md`，並實際核對 `app.py`／`comfyui.py`／`pipeline.py`／`db.py`／`scheduler.py`／`review_server.py`／`media/audio.py` 程式碼與既有測試（純唯讀，沒有改檔、沒有讀資料庫或憑證）給出的結果。以下記錄哪些直接採納、哪些先擱置：

**頁籤2/3 的結構性修正（採納，已反映進7.1）**：
- **頁籤2/3其實互相卡住**：`clip_comfyui`（stages.py 的 `remote_comfyui or local_comfyui`）同時服務 `generate_clip` 和 `upscale_clip`。如果照原計畫「核准 loop_preview 就自動 enqueue 兩個角色的 generate_clip」，會在使用者都還沒到頁籤3輸入金鑰前就自動觸發需要金鑰的動作。**改法**：核准 `loop_preview` 這一步在 Phase 2 只解鎖頁籤3的 UI，不自動 enqueue 任何東西；真正送出 `generate_clip`/`upscale_clip` 改成頁籤3自己的「開始雲端處理」動作觸發（Phase 3 才會實作那個端點）。`_continue_after_approval` 為 `loop_preview` 明確寫一個「目前不自動接續」的分支（不是靜默漏接）。
- **Assemble Preview 端點要綁定「當下核准的那兩筆」，不是「執行時最新核准的」**：`_approved_asset()` 預設抓「目前最後一筆 approved」，如果使用者按下 Assemble 之後、handler 真正執行之前，核准狀態被別的操作改變過（例如重新核准了另一個 variant），組出來的預覽就會跟使用者按下按鈕當下看到的不是同一組。**改法**：Assemble 端點把當下解析到的兩個來源 asset_id 寫進 task 的 `payload_json`，handler 執行時用這兩個 id 重新查詢＋重新驗證仍是 approved、role 對得上，不是重新查一次「最新的」。（實作拍板只綁 id，不另外綁 `state_version`——asset id 是永久不重用的，重新驗證「這個 id 現在還是 approved」已經是需要的保證；不像 `expected_version` 是用來擋「這期間曾經被改過」，這裡真正要擋的是「這個 id 現在還算數嗎」，兩者不是同一個問題。）
- **通用 reject 端點跟「handler 自建 asset」的 kind 不相容，範圍比原本以為的大**：不只 `loop_preview`，既有的 `loop`／`final` 也是同樣的模式（`build_loop`／`render_final` 都無視傳進來的 `asset_id`、自己另外 `create_asset`）——這是既有程式碼裡本來就存在、只是還沒被踩到的 bug，不是 Phase 2 才產生的。**改法**：把 `app.py` 的 `reject_asset` 對 keyframe 的既有 400 guard，擴大到 `{"keyframe", "loop_preview", "loop", "final"}` 這四種「handler 自建 asset」的 kind，一次修掉，不要只修新加的那個。

**頁籤3（採納，寫入7.2執行時要遵守）**：
- 金鑰執行模型先拍板為「request-driven」（codex建議的兩個選項之一，也是目前唯一跟既有『HTTP只負責enqueue，真正執行在背景worker』架構不衝突的選項）：頁籤3的「開始雲端處理」呼叫是同步處理（提交＋輪詢＋下載都在該次 HTTP request 內完成，或至少金鑰的使用範圍不跨出這次 request 存活的物件），不會把金鑰交給稍後才執行的背景 task。
- 集中一個 `_authenticated_request()`／`_open()` 入口，`submit`／`stage_input_file`／`_get_json`／`interrupt`／`_download`（`/view`）五條路徑都要走同一入口，不要分開各自加 header。
- 金鑰防洩漏：provider 丟出的例外訊息要把真正的金鑰值遮蔽掉、限制錯誤內文長度，避免被驗證過的遠端伺服器把 request header 原樣回顯進錯誤內文、再被 `worker.py` 寫進 `studio_tasks.error` 欄位外洩；外部 base_url 要求 HTTPS，拒絕 URL 裡帶使用者資訊/查詢字串夾帶憑證。
- 固定 Authorization header 不是真正通用的抽象——`ComfyUIClient` 保留跟本機相容的既有協定，另外注入一個小型 auth strategy（Bearer／`X-API-Key`）；如果之後真的要接 Comfy Cloud v2，那是另一個 client adapter，不要把兩種協定的分支都塞進同一個類別。

**頁籤4（採納，寫入7.3執行時要遵守）**：
- **「逐首落庫」不能防雙擊**：兩個並發的生成請求都可能通過「沒有進行中任務」的檢查、各自 enqueue，單一 worker 只會依序執行兩批，不會擋掉第二批——可能真的生成24首，且第二個 handler 還會覆寫 `episode.music_job_id`。**這不是使用者已經決定延後的『完整 StateDB 鎖定』**，是範圍很窄、值得現在就做的資料庫層級防重：對「每個 episode 同時只能有一個進行中的 `generate_music_tracks`」做唯一性約束（partial unique index 或 `UPDATE episodes ... WHERE music_job_id IS NULL` 的 CAS 寫法），retry 一律沿用同一個 music job 補 `planned`/`failed` 的 slot，不能開第二個 job。
- 「逐首落庫」本身要定義成一個原子的 publish transaction（track 轉 ready ＋ 建立 awaiting-review asset 要一起成功或一起回滾），否則 crash 在兩步中間會留下 ready 的 track 卻沒有對應 asset。另有一個逐首落庫防不住的視窗：Lyria 已經回傳成功、但 process 在寫進資料庫前就中止——用固定的 `.partial` 輸出路徑，重啟時先驗證檔案是否已存在，驗證不到才視為 `start_uncertain`，要人工確認才能再花錢重試（不要自動重跑）。
- `create_asset()`/`transition_asset()` 目前都不接受 `track_id`，不能直接沿用——新增專用方法（例如 `reserve_music_job()`、`publish_music_track()`），內建 ownership 驗證，不要在 handler 裡直接拼原始 SQL。外鍵寫入順序：先建 `jobs` 列，同一交易內把 `episodes.music_job_id` 指過去並建立 `tracks` 列，最後才能建立 `episode_assets(kind='music_track', track_id=...)`。
- `tracks.status` 維持既有的 `planned`／`ready`／`failed` 語意，人工審核只寫 `episode_assets.status`——如果把 `tracks` 本身也改成類似 approved 的狀態，會讓舊 `Pipeline._track_is_reusable()` 誤判成不可重用。
- 曲序要用 `variant_index`（0-11 播放槽）排序，不能用資料庫 id 排序——單曲重生後新 id 會排到最後，用 id 排序會讓專輯順序悄悄跑掉。
- 一首歌發布前應該照既有的 `validate_audio()`＋`normalize_audio()` 跑過，不要跳過。
- **新增一筆待辦，補進 `jobs` 表分類**：既有的 `resumable_job()`/`lyria-auto resume` 邏輯（`db.py:758`／`pipeline.py:410`）會撿「最新一筆非 complete 的 job」直接假設它有 `plan.json`／影片列／縮圖／有效頻道；如果最新一筆剛好是卡在 `generating_audio` 或 `failed` 的 studio 音樂 job，舊的 resume 邏輯會撿到它然後崩潰。要新增 `jobs.origin`／`job_type` 欄位（預設 `legacy`），或至少讓 `resumable_job()` 排除任何被 `episodes.music_job_id` 引用的 job。這個欄位變動要不要算進0003後的新 migration，實作時再決定。

**頁籤5（採納，寫入7.4，其中一項要問使用者）**：
- **需要使用者決定的問題（尚未問，做到頁籤5前再問，不卡現在的進度）**：既有 `extend_audio()` 用 `round()` 取最接近的完整份數（`tests/test_loop.py:27` 就是規格：60秒素材、目標200秒、輸出176秒），對一個30-36分鐘的完整專輯，誤差可能接近半個專輯，可能讓成品低於「2小時」下限。「2-4小時」這個目標到底是下限、最接近值、還是硬上限，三選一會決定份數要不要改成 `ceil((target - crossfade) / (mix_duration - crossfade))`——這是產品決策，先記在這裡，不是現在就要答。
- 最終渲染查詢要在應用層驗證跨表 ownership（`tracks.job_id = episodes.music_job_id`、`tracks.status='ready'`、剛好12個不同 slot、12個不同 track id），因為 CHECK 約束本來就無法表達跨表條件。
- 避免重複 AAC 重編碼：`combine_audio`／`extend_audio`／最終 mux 目前都各自編一次 AAC，長片會累積品質損失。改成中間用無損格式（FLAC/WAV）或單一 filter graph，只在最終 mux 編一次 AAC；順便把混好的結果存成 `music_mix` asset（schema 已經預留這個 kind），這樣最終渲染失敗要重試時，不用重跑幾小時的音訊處理。
- `-shortest` 的整體方向是對的（先量實際音訊長度、再算重複幾次 loop），可以額外多傳 `-t <實際音訊長度>` 降低 `verify_render` 偶發因為容器時間戳/AAC padding 誤判失敗的機率。

### 7.9 Phase 2 實作完成後的 codex_reviewer review（2026-08-31，工作11分41秒，2個blocking+3個non-blocking）

跟 Phase 1 一樣的模式：規劃／諮詢是一回事，實作完再送審才是真的抓 bug 的地方。這次送審時工作樹已經有真的程式改動（不是規劃文件），codex 實際跑了完整測試（231 passed）、用暫存 SQLite 做了4組聚焦重現，抓到：

**Blocking（已修）**：
- **High——組完但還沒核准的預覽可以被再組一次，同一個檔案被悄悄覆寫**：`assemble_motion_preview` 原本只擋「已核准」跟「有任務還在跑」兩種情況，沒擋「已經組完、還在等審核（`awaiting_review`）」這個空窗期——這段時間任務已經 `done`，不算 in-flight，但 asset 還沒 approved，兩個 guard 都通過，第二次呼叫會建立第二個 task，跑完之後兩筆 `episode_assets` 都指向同一個固定檔名（`loop_preview-shared-v0.mp4`，沒有 per-build variant index），內容以後跑的那個為準。codex 用暫存 DB 實測重現：第二次 POST 回 200、task 數變 2。**修法**：guard 擴大成「只要有 `awaiting_review` 或 `approved` 的 loop_preview 存在就 409」，不支援重組（跟頁籤1的12選1不同，這裡沒有多候選 UX），已補回歸測試。
- **Medium——規劃文件寫得跟實作不一致，會誤導之後接手 Phase 3 的人**：7.1 的第4點還寫著「核准 loop_preview 會建立 clip 並送出 generate_clip」，但這是 codex 設計諮詢**之前**寫的舊版規劃，跟後面7.6/7.8的最終決議（no-op，交給頁籤3自己觸發）互相矛盾；`data-contract.md` 也還寫著 `build_loop_preview` 是 schema-only、沒有 handler（其實已經實作了）；7.8 說 payload 綁定 asset id「連同版本」，但實作只綁了 id。**已修**：7.1、7.8、`data-contract.md` 都改成跟實際程式碼一致，並在7.8補充說明「只綁 id、不綁 state_version」是刻意的決定（id 永久不重用，重新驗證「現在還是不是 approved」已經是需要的保證），不是漏做。

**Non-blocking（已修，順手處理，不是本來要求的範圍，但成本低值得一起做）**：
- Medium——`_SELF_CREATING_TASK_TYPES` 漏了 `render_final`（跟 `build_loop`／`build_loop_preview` 同樣的「handler 自建 asset」形狀，`enqueue_task` 卻仍接受它的 `asset_id`）。目前沒有 HTTP 可達路徑所以不算擋路，但已經補進集合＋回歸測試，避免 Phase 5 真的接上 `render_final` 端點時才踩到。
- Medium——assemble 的併發 409 race，codex 更正了我原本的假設：**不是**「共用連線沒鎖」就能完整解釋，它用兩個獨立的 `StateDB` connection 都能重現同一個 race，代表根因是「檢查跟動作是分開的 DB 呼叫，中間沒交易/唯一約束」，換成每執行緒各自連線也不會解決。已經把7.7的 Phase 6 待辦描述修正成這個更準確的根因，並記下兩個具體修法選項（`BEGIN IMMEDIATE` 包住、或用 partial unique index 把違反約束轉譯成 409）。
- Low——`_resolve_bound_assets` 沒有驗證 payload 裡的 role 是否對得上 asset 實際的 `role` 欄位，也沒驗證 key 集合完整、兩個 id 不同；空字典 `{}` 因為 falsy 檢查會被誤判成「沒有綁定，退回抓最新核准」。目前的 HTTP 端點本身不會送出這種畸形 payload，所以不是實際可達的漏洞，但屬於防禦性加固，已經補上驗證＋4個回歸測試。

修完後重跑：237 個測試全過（+6，全部對應這次修的東西）、`validate_project.py` PASS、`git diff --check` 乾淨。
