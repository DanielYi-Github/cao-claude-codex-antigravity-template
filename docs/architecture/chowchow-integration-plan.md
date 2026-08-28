# 松獅犬吉祥物視覺＋音樂整合計畫

## Context

頻道要從「無主題咖啡館環境影片」轉為固定吉祥物場景：一隻可愛松獅犬趴在咖啡館地板上等主人工作結束。畫面規格已與使用者確認：64 秒巨集循環 = 8 個 8 秒片段（前 7 個「趴睡搖尾巴」完全相同、第 8 個「抬頭看一眼再趴回去」），主人不露臉／不入鏡。整個 64 秒序列本身也要能無限重播（第 8 段結尾要回到跟第 1-7 段起始完全相同的姿勢）。畫質策略已確認：**先用現有 480p 跑通全流程，1080p ESRGAN 升頻列為明確的後續階段**，不擋在這次工作前面。

技術路徑：`comfyui-cafe-loop-generator` 產生畫面（關鍵幀→動作片段→64秒序列），`Lyria-Auto-Publisher` 把這個循環影片當背景、疊上既有的 Lofi 音樂生成/發布流程。兩專案是完全獨立的 git repo，這次採手動交接（複製檔案＋一個設定值），不建自動化橋接。

**成本關鍵洞察**：因為前 7 段要求「完全相同」，只需要 2 次真正的 ComfyUI 生成（睡覺片段 1 次、抬頭片段 1 次），不是 8 次——重複播放同一個檔案即可。

## 已驗證的環境現況（動工前必須先處理）

- **ffmpeg/ffprobe 完全沒裝**（`which` 查無）。comfyui 專案目前 0 個 ffmpeg 依賴（用 ComfyUI 自己的 `CreateVideo` 節點），這次要引入 ffmpeg 是雙邊都需要的新依賴。
- **Lyria 端連 Python 版本都不符**：系統 `python3` 是 3.9.6，但 `pyproject.toml` 要求 `>=3.11,<3.15`，且 `pipeline.py` 用了 3.11 才有的 `datetime.UTC`，用 3.9 會直接 import 失敗。目前沒有 `.venv`。
- 需要：`brew install ffmpeg python@3.12`，然後用 `python3.12 -m venv .venv` 建立 Lyria 的虛擬環境（**不能**照 `scripts/setup.sh` 預設的 `python3 -m venv`，那會抓到 3.9）。

## 已驗證的 comfyui-cafe-loop-generator 現況

上一次 commit（`edd1642`「刪除不必要的東西」）留下的實際狀態（已用 `git show --stat` 和直接跑測試驗證，不是猜測）：

- `cafe-flf2v-1080p-mps.json` 被改名為 `cafe-flf2v-wan22-480p-mps.json` **並拿掉了 ESRGAN 升頻節點**——現在原生只輸出 832×480，不是 1920×1080。
- `cafe-keyframe-flux.json`、`cafe-flf2v-loop.json`、`video_minimax_h3_t2v.json` 被整個刪除。
- `cafe-flf2v-preview.json`／`cafe-flf2v-preview-mps-fast.json` 這兩個檔名被挪去裝完全不同的 H3／LTX2.3 內容。
- 連鎖後果（實際執行驗證過，不是推測）：`python3 scripts/validate_project.py` 現在噴 3 個「missing workflow」錯誤；`test_validate_project.py` 12 個測試裡 1 敗 9 錯；`test_staged_workflow.py` 5 個裡 2 個因為 `FileNotFoundError` 掛掉，因為 `staged_workflow_server.py` 的 `workflow_for()` 還在指向已經不存在的 `cafe-flf2v-1080p-mps.json`。**這些故障是既有的，不是我這次改動造成的**，但要讓審核台可用、要讓新的松獅犬素材通過驗證，必須先修。
- 額外細節：832×480 的長寬比是 26:15（≈1.733），跟 1920×1080 的 16:9（1.778）不完全一致，最終合成會有約 24px 的細窄黑邊。這是 480p 檔案沿用舊 preview 階段解析度造成的副作用，**先接受、留到 1080p 階段一起處理**，不在這次擋路。
- `console/`（Next.js/Cloudflare 那個資料夾）確認是無關的通用範本，`schema.ts` 是空的，完全沒有 "cafe" 相關內容——**不用動它**。真正的審核台是 `scripts/staged_workflow_server.py` 搭配 `console/local/` 的靜態頁面。
- **意外發現**：`.staged/state.json` 裡有一份先前手動實驗留下的松獅犬 motion prompt 草稿（品質不錯，可以直接改寫沿用），但引用的關鍵幀圖檔 `approved-Gemini_Generated_Image_cskc22cskc22cskc.png` 已經不存在（`/Users/danielyi/ComfyUI-Shared` 的 input/output 目錄目前是空的）。好消息：這台 ComfyUI 安裝的所有模型權重都已下載好（FLUX schnell GGUF、WAN2.2 高/低噪聲 GGUF、兩個 Lightning LoRA、RIFE、ESRGAN、文字編碼器全部就緒），不需要重新下載任何東西。

## Part 0 — 環境準備（手動，擋在最前面）

```bash
brew install ffmpeg python@3.12
cd Lyria-Auto-Publisher
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```
comfyui-cafe-loop-generator 那邊全部是標準庫寫的，不需要 venv。

## Part A — comfyui-cafe-loop-generator

### A1. 修復既有壞損（新內容的地基，必須先做）

**`scripts/validate_project.py`**
- 刪掉檢查已刪除檔案的區塊：`WORKFLOW_NAMES` 迴圈、NVIDIA keyframe 模型檢查、`cafe-flf2v-preview.json`/`cafe-flf2v-loop.json` 的 `_check_video_output`、`OPTIONAL_MPS_FAST_PREVIEW_WORKFLOW_NAME` 交叉檢查（`cafe-flf2v-preview-mps-fast.json` 現在裝的是 LTX2.3 內容，拿它跟一個不存在的「正式版」比對毫無意義）。
- 把仍然有價值的循環長度算術檢查（`_check_mps_1080p`，231-288 行附近）**改名並改標的**為 `_check_mps_480p_loop_arithmetic()`，指向 `cafe-flf2v-wan22-480p-mps.json`；保留 `WanFirstLastFrameToVideo`/`FrameInterpolate`/`ImageFromBatch`/`CreateVideo` 的幀數算術檢查（跟解析度無關，正是保護新素材要靠的東西），**刪除** `ImageScale`/`TARGET_OUTPUT_SIZE` 和 `ImageUpscaleWithModel` 存在性檢查（480p 本來就沒有這兩個節點）。留一行註解標記「未來 1080p 檔案回歸時在這裡加回升頻檢查」。
- 保留不動：`_check_mps_keyframe`（已經正確指向 `cafe-keyframe-flux-mps.json`）、非商用升頻器黑名單掃描（本來就跟檔案無關）。
- 建議加：`_check_start_end_frame_match()`，檢查 Start/End Frame 兩個 LoadImage 節點是否指向同一個檔名——把 README troubleshooting 章節裡「循環接點跳動→確認 Start/End 是同一檔案」這條人工提醒變成自動檢查，對其他 10 個既有咖啡館場景也有用。

**`tests/test_validate_project.py`**：刪除測試已刪除檔案行為的案例，把仍然有效的幀數算術測試（4 個案例，數值不用改，跟解析度無關）retarget 到新檔名。改完後 `test_current_project_passes_validation` 不需要另外改，壞損修好它就自動過。

**`scripts/staged_workflow_server.py`**：`workflow_for()` 的 `"preview"` 分支要重寫（不能只是換路徑）——舊邏輯會刪除 node 20/21 假設它們是升頻節點，但在新檔案裡 20/21 是 `CreateVideo`/`SaveVideo`，照舊邏輯跑會把影片組裝步驟整個刪掉。`"cloud"` 分支改成明確拋出「1080p 升頻已延後」的錯誤，不嘗試修復（反正這次不做 1080p）。

**`console/local/index.html` + `app.js`**：兩個檔案要一起改——如果拿掉「雲端 1080p」按鈕，`app.js` 裡對應的 `onclick` 綁定也要一起拿掉，否則 `querySelector` 對已刪除元素回傳 `null`，`null.onclick=...` 會在載入時直接拋錯、讓整個審核台頁面壞掉。

**文件小幅更新**：README 的目錄結構列表、MODEL-LICENSES.md 裡引用舊常數名的那一行——只做外科手術式修正，不重寫整份效能/成本論述。

### A2. 新增松獅犬內容

**新檔案 `chowchow-prompts.md`**（獨立於 `prompt-library.md`，不混進那 10 個場景——架構不同：這邊是 1 個共用關鍵幀 + 2 組配對 motion prompt + 一份序列 manifest，不是「10選1」）：

- **關鍵幀**（兩個片段共用的 start=end 圖）：虛構咖啡館地板一角，松獅犬趴臥、頭低靠前腳、眼睛微閉，旁邊有拉開的空椅子＋發光筆電＋冒煙的馬克杯暗示主人存在但**完全不入鏡**。
- **Motion 1（睡覺搖尾巴，播 7 次）**：維持趴姿，尾巴輕搖幾下，呼吸起伏，環境微動（蒸氣、光線），結尾回到起始姿勢。
- **Motion 2（抬頭看一眼，播 1 次）**：從趴姿抬頭望向空椅子方向，停留片刻，再低頭回到跟起始完全相同的姿勢——這是讓整個 64 秒能無縫重播的關鍵，結尾必須精確對齊開頭。

保留一段簡短說明：為什麼「抬頭看一眼」被設計成 8 次裡只出現 1 次，而不是每次都有——避免長影片裡這個動作看起來重複/機械。

因為關鍵幀階段和動作階段都固定用 CFG 1.0（Lightning LoRA 的要求），negative prompt 在這個專案裡本來就是無效的（README 已經說明過）——「主人不露臉」這件事只能靠正向提示詞的寫法（讓主人完全不入鏡，而不是嘗試用負面詞排除模糊人影）加人工複檢來保證，不能指望模型自己排除。

**新目錄 `assets/chowchow/`**：`manifest.json`（進版控，記錄關鍵幀路徑、兩個片段路徑、重複次數、8 格播放順序、總秒數 64.0）+ `keyframe/`、`clips/` 兩個子目錄放實際生成的圖檔/影片檔（進 .gitignore，跟現有 `.staged/` 的模式一致）。

**新腳本 `scripts/build_chowchow_loop.py`**：讀 manifest → 用 ffprobe 檢查兩個實際片段的編碼/解析度/fps 是否一致（避免 concat 失敗）→ 寫暫存 concat list → `ffmpeg -f concat -safe 0 -i list -c copy` 無損拼接成 64 秒檔案 → 再 ffprobe 輸出確認總長度和串流數正確。這是這個 repo 第一次引入 ffmpeg 依賴，腳本要先檢查 `ffmpeg`/`ffprobe` 是否存在，缺少時給清楚的錯誤訊息。

**`validate_project.py` 再加一段**：只驗證 manifest 本身的結構/算術（8 格序列、每個 key 出現次數對得上 repeats、總秒數算術），不呼叫 ffprobe（維持這個檔案原本「不需要 GPU/外部工具」的設計原則）；真正碰檔案的驗證留給 `build_chowchow_loop.py`。

### A3. 審核台使用方式

不特地把場景參數化——沿用現有的單場景審核台，跑兩輪：先核准共用關�+鍵幀，接著分別輸入兩組 motion prompt、各跑一次動作預覽，每次跑完立刻把輸出檔手動搬到 `assets/chowchow/clips/` 對應檔名（因為兩次輸出共用同一個檔名前綴，ComfyUI 只靠自動遞增數字區分，不搬走下一輪會覆蓋/混淆）。

## Part B — Lyria-Auto-Publisher

**`src/lyria_auto/media/video.py`**：新增 `create_looping_video(background_video, audio_path, output_path, width, height, fps, audio_bitrate, preset)`，跟現有 `create_static_video` 並存（不改動它）。核心差異：`-stream_loop -1 -i <video>` 取代 `-loop 1 -i <image>`；`fps` 參數允許傳 `None`，此時**不強制**輸出幀率，直接沿用來源影片原生的 16fps——這點很重要，因為現有 `video.fps: 1` 是為靜態圖片調的，如果誤用在這裡會把狗的動作壓成 1fps，動態全毀。沿用 `probe_audio`/`run_command`，仿照 `media/timeline.py` 的 `create_timeline_video` 已有的「事後重新探測音訊長度」慣例（不要另外傳一個 duration 參數穿過呼叫鏈），並比照它的 `.partial` 原子寫入 + `verify_render` 驗證。

**`src/lyria_auto/pipeline.py`**：只改 `_render()`（約 684-711 行）——讀到 `video_cfg.get("background_video")` 有值就走新的 `create_looping_video` 分支，否則完全比照現在的 `create_static_video` 呼叫，一行不改。**不動** `visual.enabled` 那條實驗性 Gemini/Veo 付費視覺管線的程式碼——兩者是平行的、互不相關的路徑，混在一起容易誤觸付費 API。

**`config/settings.yaml`**：`video:` 底下加兩個新的可選 key：`background_video: ''`（相對路徑，空字串＝維持現有靜態圖行為）、`background_video_fps: null`。配置本身沒有 schema/驗證層（純字典 `.get()` 讀取），加新 key 不需要動 `config_migration.py`。

**README.md**：比照 `background_directory` 那一行格式，新增對應的設定說明列。新建 `assets/background_video/` 目錄（不用現有的 `assets/backgrounds/`，那個目錄的縮圖產生邏輯只認圖片副檔名，混進 mp4 會被默默忽略，目錄用途會搞混）。

**新測試 `tests/test_video.py`**：目前 `media/video.py`完全沒有專屬測試。比照 `test_visual_render.py`/`test_loop.py` 的慣例：一個真跑 ffmpeg 的煙霧測試（確認輸出長度跟音訊對齊、確認真的有循環超過來源片段原長）+ 一個 mock `run_command` 斷言指令參數含 `-stream_loop -1` 且不含 `-loop 1` 的測試。

## Part C — 交接（手動，不做跨 repo 自動化）

1. comfyui 專案跑 `scripts/build_chowchow_loop.py`，產出 `assets/chowchow/chowchow-64s-loop.mp4`。
2. 複製（不做 symlink，避免兩個獨立 repo 的路徑互相依賴）到 `Lyria-Auto-Publisher/assets/background_video/chowchow-64s-loop.mp4`。
3. 在 Lyria 的 `config/settings.yaml` 設定 `video.background_video: assets/background_video/chowchow-64s-loop.mp4`。
4. 其餘一切（音樂生成、安全機制、metadata、上傳）完全不動。

## 執行順序與人力介入點

| # | 步驟 | 需要你親自坐在機器前？ |
|---|---|---|
| 0 | `brew install` 環境準備 | 是（一次性） |
| 1 | 修復 comfyui 專案既有壞損 | 否，純程式改動 |
| 2 | 新增松獅犬 prompt/manifest/build script/驗證測試（片段還沒生成也能先做） | 否，純程式改動 |
| 3 | 用審核台在 ComfyUI 實際生成關鍵幀＋兩個動作片段 | **是，必須**（生成+人工複檢，無法用程式代勞） |
| 4 | 跑 `build_chowchow_loop.py` 拼接成 64 秒檔 | 否，但要等步驟 3 完成 |
| 5 | Lyria 端程式改動 | 否，可以跟步驟 3 平行進行 |
| 6 | 手動交接：複製檔案＋改一行設定 | 是，一次操作 |
| 7 | 端到端驗證：跑一次真的生成，實際播放檢查 | 是，最終視覺/聽覺確認 |

## 驗證方式

comfyui 端：`python3 scripts/validate_project.py`、`python3 -m unittest discover -s tests -v`。
Lyria 端：`.venv/bin/python -m pytest -q`、`.venv/bin/python -m pytest tests/test_video.py -v`。
端到端：`lyria-auto run --dry-run --videos 1` 確認設定被接受，接著跑一次真實生成（`target_duration_minutes` 先調小方便快速檢查），手動播放輸出檔確認 8 秒內循環和 64 秒巨集循環都不明顯跳動、音畫時長一致。

### 主要異動檔案
- `comfyui-cafe-loop-generator/scripts/validate_project.py`
- `comfyui-cafe-loop-generator/scripts/staged_workflow_server.py`
- `comfyui-cafe-loop-generator/scripts/build_chowchow_loop.py`（新增）
- `comfyui-cafe-loop-generator/chowchow-prompts.md`、`assets/chowchow/manifest.json`（新增）
- `Lyria-Auto-Publisher/src/lyria_auto/pipeline.py`
- `Lyria-Auto-Publisher/src/lyria_auto/media/video.py`
- `Lyria-Auto-Publisher/config/settings.yaml`
