# 商用實景咖啡館影片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 將咖啡館循環影片專案改成以 Apache 2.0 模型為預設、避免真實品牌／地標／真人暗示，並提供正式生成與低成本預覽流程。

**Architecture:** 以 `FLUX.1 Schnell FP8 → WAN 2.2 I2V A14B FLF2V` 作為本地生成主線。下載腳本負責取得模型，Workflow 負責生成，Prompt library 與 README 負責商用內容規範，標準函式庫驗證腳本負責在無 GPU 的環境檢查靜態一致性。

**Tech Stack:** Bash、ComfyUI Workflow JSON、Markdown、Python 3 標準函式庫、Hugging Face 官方模型檔案。

---

## 檔案地圖

- Modify `scripts/download-models.sh`: 預設下載 Apache 2.0 的 FLUX.1 Schnell checkpoint 與 WAN 2.2 模型；保留部分下載續傳與 HTTP 失敗檢查。
- Modify `workflows/cafe-keyframe-flux.json`: 將 FLUX.1 Dev 改為 FLUX.1 Schnell，使用 4 steps／CFG 1.0 與虛構咖啡館 Prompt。
- Modify `workflows/cafe-flf2v-loop.json`: 使用無品牌、無地標、無可辨識人物的實景環境 Prompt。
- Create `workflows/cafe-flf2v-preview.json`: 正式 Workflow 的 81 幀／480p 預覽版，輸出前綴為 `video/cafe-loop-preview`。
- Modify `prompt-library.md`: 將 10 組真實城市場景改成虛構咖啡館場景，加入寫實與商用安全規則。
- Create `MODEL-LICENSES.md`: 記錄模型檔名、下載來源、授權、用途與最後確認日期。
- Create `scripts/validate_project.py`: 不依賴 ComfyUI 的靜態驗證工具。
- Create `tests/test_validate_project.py`: 驗證商用模型與 Workflow 規則的 unittest。
- Modify `README.md`: 更新模型、下載指令、預覽流程、YouTube 揭露與發布前檢查清單。

## 設計文件覆蓋表

- 「模型與授權決策」→ Task 1、Task 5。
- 「Workflow 設計」→ Task 2、Task 3。
- 「Prompt 與內容安全規則」→ Task 2、Task 3、Task 5。
- 「文件與發布流程」→ Task 5、Task 6。
- 「驗證策略」→ Task 4、Task 7。
- 「YouTube 風險邊界」→ Task 6、Task 7。

## Task 1: 更新模型下載腳本

**Files:**

- Modify `scripts/download-models.sh:8-90`

- [ ] **Step 1: 定義商用 keyframe 模型開關**

將目前的 `DOWNLOAD_FLUX` 改成 `DOWNLOAD_KEYFRAME="${DOWNLOAD_KEYFRAME:-1}"`。值為 `1` 時下載 `flux1-schnell-fp8.safetensors`；值為 `0` 時只下載 WAN 影片模型。腳本中不可再出現 `flux1-dev` 或 `DOWNLOAD_FLUX`。

- [ ] **Step 2: 更新 FLUX 下載目的地與官方來源**

使用下列目的地與 URL：

```bash
"${COMFYUI_BASE}/models/checkpoints/flux1-schnell-fp8.safetensors"
"https://huggingface.co/Comfy-Org/flux1-schnell/resolve/main/flux1-schnell-fp8.safetensors"
```

維持既有 `download_model()` 的 `--fail --location --retry --continue-at -` 與 `.part` 原子移動流程。

- [ ] **Step 3: 執行 Shell 語法檢查**

Run: `bash -n scripts/download-models.sh`  
Expected: exit code `0`，無語法輸出。

## Task 2: 修改關鍵影格 Workflow

**Files:**

- Modify `workflows/cafe-keyframe-flux.json:1-122`

- [ ] **Step 1: 替換 checkpoint 與 Schnell 參數**

將 `CheckpointLoaderSimple.widgets_values[0]` 設為 `flux1-schnell-fp8.safetensors`，將 `KSampler.widgets_values` 設為：

```json
[
  "__RANDOM_INT__",
  false,
  4,
  1.0,
  "euler",
  "simple"
]
```

- [ ] **Step 2: 寫入商用實景 Prompt**

Positive Prompt 使用固定鏡位、虛構咖啡館、自然材質、柔和自然光與 16:9 構圖，並明確加入 `fictional cafe`, `no recognizable location`, `no logos`, `no readable text`, `no identifiable people`。Negative Prompt 加入 `brand logo`, `trademark`, `readable sign`, `celebrity`, `public figure`, `recognizable face`, `watermark`，同時保留解剖與畫質負面詞。

- [ ] **Step 3: 解析 JSON 並檢查模型與參數（先行檢查）**

Run: `python -c "import json; w=json.load(open('workflows/cafe-keyframe-flux.json', encoding='utf-8')); nodes={n['type']:n for n in w['nodes']}; assert nodes['CheckpointLoaderSimple']['widgets_values'][0]=='flux1-schnell-fp8.safetensors'; assert nodes['KSampler']['widgets_values'][2:4]==[4,1.0]"`  
Expected: exit code `0`; 完整專案驗證會在 Task 4 建立 `scripts/validate_project.py` 後執行。

## Task 3: 修改正式影片 Workflow 並建立預覽版

**Files:**

- Modify `workflows/cafe-flf2v-loop.json:1-280`
- Create `workflows/cafe-flf2v-preview.json`

- [ ] **Step 1: 更新正式影片 Prompt**

Positive Prompt 改為虛構咖啡館室內或戶外角落，使用固定鏡位與可物理解釋的低幅度動態：steam rising、rain sliding on glass、curtain moving slightly、leaves swaying gently、warm practical lights flickering subtly。加入 `no logos`, `no readable text`, `no recognizable location`, `no identifiable people`。

Negative Prompt 加入品牌、商標、文字、地標、名人、可辨識臉部、快速鏡頭運動、場景變形與不自然陰影等限制。

- [ ] **Step 2: 建立 81 幀預覽 Workflow**

以正式 JSON 為基礎建立預覽檔，僅修改 `WanFirstLastFrameToVideo.widgets_values` 的 length：

```json
[832, 480, 81, 1]
```

並將 `SaveVideo.widgets_values[0]` 改為 `video/cafe-loop-preview`。輸出鏈必須保持：`VAEDecode → CreateVideo → SaveVideo`。

- [ ] **Step 3: 驗證兩份影片 Workflow（先行檢查）**

Run: `python -c "import json; [json.load(open(p, encoding='utf-8')) for p in ['workflows/cafe-flf2v-loop.json','workflows/cafe-flf2v-preview.json']]"`  
Expected: exit code `0`; 正式版／預覽版幀數與輸出連線的完整檢查會在 Task 4 建立 `scripts/validate_project.py` 後執行。

## Task 4: 建立可重複的靜態驗證工具

**Files:**

- Create `scripts/validate_project.py`
- Create `tests/test_validate_project.py`

- [ ] **Step 1: 先寫驗證工具的失敗測試**

在 `tests/test_validate_project.py` 建立 `unittest.TestCase`，測試目前專案通過驗證，以及把暫存 Workflow 中的 `flux1-schnell-fp8.safetensors` 改成 `flux1-dev-fp8.safetensors` 後會回報錯誤。測試透過 `from scripts.validate_project import validate_project` 呼叫真實函式，不使用 mock。

- [ ] **Step 2: 執行測試確認紅燈**

Run: `python -m unittest tests/test_validate_project.py -v`  
Expected: FAIL，原因是 `scripts.validate_project` 尚未存在。

- [ ] **Step 3: 實作 JSON 結構檢查**

使用 `json`, `pathlib`, `sys`，逐一載入三份 Workflow，公開 `validate_project(root: Path) -> list[str]` 與 `main(argv=None) -> int`；檢查 JSON 可解析、node ID 不重複、每條 link 的來源與目的 node 存在。

- [ ] **Step 4: 實作商用模型與參數檢查**

驗證以下條件，失敗時回傳明確錯誤訊息並由 `main()` 以 exit code `1` 結束：

```text
keyframe checkpoint == flux1-schnell-fp8.safetensors
keyframe steps == 4
keyframe cfg == 1.0
all workflow text excludes flux1-dev
download script contains flux1-schnell-fp8.safetensors
```

- [ ] **Step 5: 實作影片輸出鏈與幀數檢查**

確認每份影片 Workflow 存在一個 `CreateVideo` 與一個 `SaveVideo`，且 `CreateVideo` 的 VIDEO 輸出 link 到 `SaveVideo` 的 VIDEO 輸入；正式版 length 必須是 `161`，預覽版 length 必須是 `81`。

- [ ] **Step 6: 執行測試確認綠燈**

Run: `python -m unittest tests/test_validate_project.py -v`  
Expected: 所有測試 PASS。

- [ ] **Step 7: 執行驗證工具**

Run: `python scripts/validate_project.py`  
Expected: 顯示所有檢查通過並 exit code `0`。

## Task 5: 更新 Prompt Library 與授權文件

**Files:**

- Modify `prompt-library.md`
- Create `MODEL-LICENSES.md`

- [ ] **Step 1: 將場景改為虛構分類**

移除 Paris、Tokyo、Vienna、New York、Kyoto、Barcelona、Rome、London、Seoul、Montreal 等真實城市名稱與具體地標描述，改成室內木質咖啡館、雨天窗邊、溫室咖啡館、街角但不可辨識的咖啡館等虛構分類。

- [ ] **Step 2: 保留英文生成 Prompt 並加入商用規則**

每組 Prompt 必須包含穩定構圖、自然材質、無品牌／文字／地標／可辨識人物與低幅度環境動態；新增「生成後人工檢查」段落。

- [ ] **Step 3: 建立授權清單**

`MODEL-LICENSES.md` 至少包含：

```markdown
| Model | File | License | Source | Use |
|---|---|---|---|---|
| FLUX.1 Schnell FP8 | flux1-schnell-fp8.safetensors | Apache 2.0 | Comfy-Org/flux1-schnell | Commercial keyframes |
| WAN 2.2 I2V A14B FP8 | wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors | Apache 2.0 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged | Commercial video |
| WAN 2.2 I2V A14B FP8 | wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors | Apache 2.0 | Comfy-Org/Wan_2.2_ComfyUI_Repackaged | Commercial video |
```

加上「模型授權不涵蓋第三方輸入圖片、音樂、音效、字型或商標」的責任說明。

## Task 6: 更新 README 與發布檢查

**Files:**

- Modify `README.md`

- [ ] **Step 1: 更新安裝說明**

將預設指令改為直接下載商用 keyframe 模型：

```bash
bash /workspace/comfyui-cafe-loop-generator/scripts/download-models.sh
```

補充 `DOWNLOAD_KEYFRAME=0` 只下載影片模型的選項，並更新模型檔名、磁碟容量與授權說明。

- [ ] **Step 2: 加入預覽與正式流程**

明確說明先載入 `cafe-flf2v-preview.json` 做 81 幀檢查，通過後才載入 `cafe-flf2v-loop.json`。

- [ ] **Step 3: 加入 YouTube 發布清單**

列出 AI 揭露、場景差異化、畫面瑕疵、品牌／地標／真人、音訊授權與循環接點檢查；明確說明本專案不保證 YouTube 營利審核結果。

## Task 7: 全面驗證與交付

**Files:**

- Verify `scripts/download-models.sh`
- Verify `scripts/validate_project.py`
- Verify all three Workflow JSON files, `prompt-library.md`, `MODEL-LICENSES.md`, and `README.md`

- [ ] **Step 1: 執行所有靜態驗證**

Run:

```bash
bash -n scripts/download-models.sh
python -m unittest tests/test_validate_project.py -v
python scripts/validate_project.py
```

Expected: all three commands exit `0`。

- [ ] **Step 2: 檢查禁用字串與 Markdown 結構**

Run: `rg -n "flux1-dev|FLUX\.1 Dev|Paris|Tokyo|Haussmann|celebrity|public figure" scripts workflows prompt-library.md README.md MODEL-LICENSES.md`  
Expected: 不出現 `flux1-dev`、`FLUX.1 Dev`、真實地點或被禁止的商用內容條件；`celebrity` 與 `public figure` 僅可出現在 negative Prompt 或政策說明中。

- [ ] **Step 3: 執行端到端環境測試**

在具備最新 ComfyUI、NVIDIA GPU 與模型權重的雲端環境中，先用 81 幀預覽，再用 161 幀正式版；記錄是否能生成可播放影片、VRAM 使用量、生成時間與循環接點瑕疵。沒有雲端執行環境時，交付報告必須明確標示此項未完成。

- [ ] **Step 4: 報告結果**

交付修改檔案、靜態驗證輸出、模型授權清單，以及仍需在雲端人工確認的項目。當前資料夾不是 Git repository，因此不執行 commit。
