# ComfyUI 商用寫實咖啡館循環影片生成器

在 **Apple Silicon 本機**生成 **1920×1080 / 8.00 秒 / 首尾嚴格循環**的寫實咖啡館環境影片。

方向是「虛構場景 + 寫實攝影感 + 小幅度自然運動」，適合長時間觀看的環境影片。不代表每次輸出都可以不經人工審核直接發布。

---

## 目錄

- [核心觀念：1080P 不是「生成」出來的](#核心觀念1080p-不是生成出來的)
- [步驟 0 — 一次性安裝](#步驟-0--一次性安裝)
- [步驟 1 — 生成關鍵幀](#步驟-1--生成關鍵幀)
- [步驟 2 — 動作預覽（約 4 分鐘）](#步驟-2--動作預覽約-4-分鐘)
- [步驟 3 — 正式 1080P 成片（約 40 分鐘）](#步驟-3--正式-1080p-成片約-40-分鐘)
- [步驟 4 — 發布前人工檢查](#步驟-4--發布前人工檢查)
- [調參手冊：畫面不對時改哪裡](#調參手冊畫面不對時改哪裡)
- [MiniMax H3 能不能在這台機器跑](#minimax-h3-能不能在這台機器跑)
- [本地驗證](#本地驗證)
- [目錄結構](#目錄結構)
- [商用原則與授權](#商用原則與授權)

---

## 核心觀念：1080P 不是「生成」出來的

### 實測數據（M5 Pro / 64GB / MPS / ComfyUI 0.31.1 / torch 2.12.1）

WAN 2.2 的成本由 latent token 數決定：`tokens = (寬/16) × (高/16) × ((幀數-1)/4 + 1)`。

本機實測每次模型前向（model evaluation）的耗時：

| 解析度 × 幀數 | tokens | 實測 s/eval |
|---|---|---|
| 640×368 × 33 | 8,280 | 27.5 |
| 832×480 × 33 | 14,040 | **64.6** |
| 832×480 × 81 | 32,760 | **335** |

擬合出來的成本模型（線性層 ∝ N，注意力 ∝ N²，三點誤差都在 8% 內）：

```
t = 6.109e-04 × N  +  2.933e-07 × N²
```

### 為什麼不能直接生 1080P

| 設定 | tokens | 4-step 總時間 |
|---|---|---|
| 832×480 × 33（預覽） | 14,040 | **4 分** |
| 1024×576 × 65（日常） | 39,168 | **32 分** |
| 1280×720 × 65（高品質） | 61,200 | **76 分** |
| **1920×1088 × 129（原生 1080P）** | **269,280** | **23.8 小時** |

原生 1080P 8 秒要跑將近一天，而且注意力矩陣在那個 token 數會先把記憶體吃爆。

**所以 1080P 的像素不由擴散模型產生，而是後製放大出來的。**

### 管線：三個獨立工作流，動作核准後畫質才升頻

生成與升頻是**兩個分開的工作流檔案**，不是同一份工作流裡的接續步驟：生成一次決定「怎麼動」並產出成品，升頻只碰像素、完全不重新擴散，所以核准過的動作保證逐格不變。

```
   步驟 1                步驟 2 / 3a（cafe-flf2v-wan22-mps.json）
┌───────────┐      ┌──────────────────────────────────────────────┐
│   FLUX    │      │  WAN 2.2 FLF2V   解析度為參數                │
│  schnell  │─────▶│  Lightning 4-step LoRA · CFG 1.0             │  ← 唯一昂貴的一步
│ 1280×720  │ 同一 │  768×432＝試拍 / 1024×576＝正式  ↓ 約32分（正式）│
└───────────┘ 張圖 │  RIFE ×2      65 幀 → 129 幀                  │
              進   │                          ↓                    │
           start   │  丟掉最後一幀  129 → 128 幀（接點去重）        │
           & end   │                          ↓                    │
                   │  16 fps → 128 ÷ 16 = 正好 8.00 秒（原生解析度）│
                   └──────────────────────────────────────────────┘
                                        │ 核准後的成品檔
                                        ▼
                   步驟 3b（cafe-upscale-1080p-mps.json，純後製）
                   ┌──────────────────────────────────────────────┐
                   │  LoadVideo → 解回影格                         │
                   │  ESRGAN ×4    → Lanczos 縮到 1920×1080        │
                   │  16 fps 重新封裝，動作與幀數完全不變  ↓ 約7分  │
                   └──────────────────────────────────────────────┘
```

RIFE 和 ESRGAN 都是 **ComfyUI 內建節點**（`FrameInterpolate`、`ImageUpscaleWithModel`），不用裝任何 custom node。`LoadVideo`／`GetVideoComponents` 是較新版本才有的內建影片節點，本文驗證於 0.31.1。

> 這條管線已在本機端到端跑通驗證過（縮小版：33 幀 → RIFE → 65 → 保留 64 → 8 fps），輸出檔實測為 **1920×1080 / 64 幀 / 8.0 fps / 正好 8.000 秒**，Lightning LoRA 也確認能正常掛在 GGUF 模型上。

### 三個關鍵設計

**1. 首尾同圖 → 場景由關鍵幀決定，WAN 只負責「動」**

這代表「不能有招牌／商標／可辨識人臉」這些商用約束，**必須在步驟 1 的關鍵幀階段把關**，不是在影片階段。影片階段的 negative prompt 只需要管動態瑕疵。

**2. CFG 1.0 → negative prompt 完全不生效**

Lightning LoRA 要求 CFG 1.0。CFG 1.0 時模型只跑一次前向，根本不會評估 negative 分支。工作流裡仍保留 negative prompt，是為了你調高 CFG 時能用。

> 同理：`cafe-keyframe-flux-mps.json` 用的 FLUX schnell 也固定 CFG 1.0，**它的 negative prompt 一樣是無效的**。關鍵幀的內容管控只能靠正向提示詞 + 人工複檢。

**3. 生成 65 幀再補幀，而不是直接生 129 幀**

成本接近 token 數的平方，65 幀比 129 幀便宜約 3 倍。65 幀是 4 秒的動作，補幀後以 8 秒播放 = 半速慢動作 —— 對咖啡館氛圍片反而更好看。

### 注意力後端：**不要**改（實測反直覺）

ComfyUI 在 MPS 上預設 `sub quadratic optimization for attention`，啟動時會提示你換別的。**實測換掉會更慢**：

| 注意力後端 | 832×480 × 33 幀 |
|---|---|
| sub-quadratic（預設） | **64.6 s/eval** |
| `--use-pytorch-cross-attention`（SDPA） | **204.9 s/eval（慢 3.2 倍）** |

MPS 的 SDPA 在這種長序列（14k tokens）下沒有 flash-attention 快速路徑，會把完整注意力矩陣攤開，導致記憶體反覆搬運。**保持預設就好，不要加任何注意力參數。**

---

## 步驟 0 — 一次性安裝

### 需求

- Apple Silicon Mac，建議 32GB 以上統一記憶體（本文數據來自 M5 Pro / 64GB）
- ComfyUI（本文驗證於 0.31.1）
- [city96/ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) 自訂節點（提供 `UnetLoaderGGUF` / `CLIPLoaderGGUF`）
- 磁碟：1080P 模型組約 **30 GB**

### 下載模型

```bash
MODEL_DIR=/你的/ComfyUI \
DOWNLOAD_KEYFRAME=0 \
DOWNLOAD_MPS_KEYFRAME=1 \
DOWNLOAD_MPS_1080P=1 \
bash scripts/download-models.sh
```

`DOWNLOAD_MPS_1080P=1` 會下載這些檔案，每一個的理由如下：

| 檔案 | 放哪 | 大小 | 為什麼是這個版本 |
|---|---|---|---|
| `Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf` | `models/unet/` | 10.8 GB | Q3_K_S 對材質與光影的損失很明顯，正好是寫實場景最吃的部分。高低噪聲模型是**輪流**載入的，64GB 綽綽有餘。（舊的 `DOWNLOAD_MPS_ANIMATION` Q3 選項已移除，避免重複下載 16 GB 品質更差的權重） |
| `Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf` | `models/unet/` | 10.8 GB | 同上 |
| `umt5-xxl-encoder-Q8_0.gguf` | `models/clip/` | 6.0 GB | **提示詞理解發生在文字編碼器**。Q3_K_S 等於一開始就把提示詞讀糊了。文字編碼一輪只跑一次而且在 CPU 上，升級幾乎沒有速度代價 |
| `wan2.2_i2v_A14B_{high,low}_noise_lightning_4step.safetensors` | `models/loras/` | 1.2 GB ×2 | 20 步 × CFG 3.5 = 40 次前向，壓成 4 步 × CFG 1.0 = 4 次。**約 10 倍加速**，是整套方案成立的關鍵 |
| `rife_v4.26.safetensors` | `models/frame_interpolation/` | 23 MB | ComfyUI 官方 `Comfy-Org/frame_interpolation`，內建節點直接支援 |
| `RealESRGAN_x4plus.pth` | `models/upscale_models/` | 64 MB | **BSD-3-Clause，可商用**。社群愛用的 `4x_foolhardy_Remacri` 與 `4x-UltraSharp` 看起來更銳利，但兩者都是 CC-BY-NC-SA-4.0（**禁止商用**），本專案不能用。Real-ESRGAN 也偏柔和，這對影片是優點：過度銳化會變成逐幀閃爍 |

同時建議跑 `DOWNLOAD_MPS_KEYFRAME=1` 取得 FLUX schnell GGUF 關鍵幀組（約 9 GB）。

### 不要加啟動參數

不要加 `--use-pytorch-cross-attention` 或 `--use-split-cross-attention`（見上方實測）。

---

## 步驟 1 — 生成關鍵幀

### 三階段審核控制台（建議）

如果希望強制執行「圖片核准 → 動作試拍核准」流程，可啟動本專案的本機控制台：

```bash
python3 scripts/staged_workflow_server.py
```

然後開啟 `http://127.0.0.1:8787`。控制台會：

1. 提供每次生成 4 張候選圖的 FLUX 圖片 workflow，並將核准圖片從 ComfyUI `output/` 複製到 `input/`。
2. 產生固定使用同一張首尾圖的 **768×432 / 128 幀 / 16 fps / 8 秒**動作試拍 workflow（`cafe-flf2v-wan22-mps.json` 把節點 9 暫時改成 768×432）。

動作試拍核准後，控制台**不再**提供雲端 1080p 打包——1080p 現在是本機後製升頻（步驟 3b），不是重新生成，所以核准過的動作不會因為升頻而改變。控制台上「雲端 1080P」按鈕會回傳說明訊息，指向步驟 3a／3b 的手動流程。

**這一步決定畫面「是什麼」。後面兩步只決定它怎麼動。**

1. ComfyUI 載入 `workflows/cafe-keyframe-flux-mps.json`
2. 從 [`prompt-library.md`](prompt-library.md) 挑一組場景，貼進 **Positive Prompt**
3. Queue Prompt。每次輸出 **4 張**候選圖；Apple Silicon workflow 的解析度為 **1280×720**，4 steps / CFG 1.0。四張會以同一批次生成，因此記憶體與時間會高於原本的單張設定
4. 輸出在 `output/cafe-keyframe/`

> 為什麼是 1280×720 而不是 1024×576？1280×720 是 0.92MP，落在 FLUX schnell 的甜蜜點（1024×576 只有 0.59MP，構圖與細節會變差）。`WanFirstLastFrameToVideo` 會自動縮到影片解析度，同一張圖也能直接用於 1280×720 的高品質檔。

### 這一步必須人工檢查（因為 negative prompt 無效）

- [ ] 沒有招牌、商標、包裝文字、可讀字樣
- [ ] 沒有可辨識人臉或名人
- [ ] 手部、桌椅、建築結構沒有結構性錯誤
- [ ] 構圖穩定、適合鎖定鏡位

有問題就換 seed 重生成。**不要把有問題的關鍵幀帶進步驟 2**，因為 FLF2V 會忠實保留它。

滿意後，把檔案複製到 ComfyUI 的 `input/` 目錄。

---

## 步驟 2 — 動作試拍（約數分鐘）

**這一步只驗證「動得對不對」，不驗證畫質，也不是最終素材。**

1. 載入 `workflows/cafe-flf2v-wan22-mps.json`（跟步驟 3a 正式生成同一個檔案）
2. **Start Frame 和 End Frame 都選同一個檔案** ← 這是循環的來源，選錯就不會循環
3. 把節點 9 `WanFirstLastFrameToVideo` 的寬高**暫時**改成 `768 × 432`（16:9，比正式的 1024×576 便宜、跑得快，幀數算術不變）
4. 改 **Positive Prompt** 的動作描述（`steam rising`、`leaves swaying`、`rain sliding` 之類）
5. Queue Prompt

用三階段審核控制台的話，「核准圖片」後下載的預覽 workflow 已經自動把節點 9 設成 768×432，不用手動改。

### 看什麼

- 動作幅度對不對？（環境影片要「幾乎不動」，不是「明顯在動」）
- 有沒有物體變形、突然出現／消失、畫面漂移？
- 鏡頭有沒有亂動？

**一次只改一到兩個動作描述**，改多了無法判斷是哪個造成的。這一步跑一次只要幾分鐘，值得多試幾輪。滿意後記下這組 Positive Prompt，步驟 3a 要原封不動貼過去——正式生成要 30 分鐘以上，不適合在那邊邊跑邊調。

---

## 步驟 3a — 正式生成（約 32-34 分鐘）

1. 同一個檔案 `workflows/cafe-flf2v-wan22-mps.json`
2. 節點 9 的寬高改回（或維持）**`1024 × 576`**
3. Start / End Frame 維持**同一張**關鍵幀
4. 把步驟 2 核准的 Positive Prompt 原封不動貼過來
5. Queue Prompt

輸出：`output/video/cafe-loop-wan22_*.mp4`，**1024×576 / 128 幀 / 16 fps / 正好 8.00 秒**，尚未升頻。**這一版就是「核准了動作就不會再變」的版本**——下一步的升頻只碰像素，不碰動作，所以現在核准的東西保證是最終出貨的動作。

### 節點在做什麼

| 節點 | 動作 |
|---|---|
| 9 `WanFirstLastFrameToVideo` | 1024×576，65 幀 |
| 10 / 12 `LoraLoaderModelOnly` | 掛 Lightning 4-step LoRA |
| 14 / 15 `KSamplerAdvanced` | 高噪聲 0→2 步、低噪聲 2→4 步，CFG 1.0 |
| 18 `FrameInterpolate` | RIFE ×2 → 129 幀 |
| 19 `ImageFromBatch` | 保留前 128 幀（丟掉與第一幀重複的最後一幀） |
| 20 `CreateVideo` | 16 fps，原生解析度，不升頻 |
| 21 `SaveVideo` | 輸出，檔名前綴 `video/cafe-loop-wan22` |

> **為什麼要丟最後一幀**：首尾同圖時第 1 幀和第 129 幀是同一張，直接輸出會在循環接點卡一格。丟掉後 128 ÷ 16 = 剛好 8.00 秒。

### 時間預算（本機實測，取樣階段）

| 階段 | 時間 |
|---|---|
| 文字編碼 + FLF2V 取樣（4 步） | 約 32 分 |
| VAE 解碼 + RIFE 補幀 | 約 2 分 |
| **合計** | **約 34 分** |

---

## 步驟 3b — 本機升頻到 1080P（約 7 分鐘，跟動作調整無關，可以晚點再做）

**這一步完全是後製，不重新擴散、不改動作。** 步驟 3a 核准的動作，升頻後逐格保證一樣，這正是把生成和升頻拆成兩個工作流檔案的原因。

1. 載入 `workflows/cafe-upscale-1080p-mps.json`
2. 節點 1 `LoadVideo` 選步驟 3a 輸出的檔案
3. Queue Prompt

輸出：`output/video/cafe-loop-1080p_*.mp4`，**1920×1080 / 128 幀 / 16 fps / 8.00 秒**，動作與步驟 3a 逐格相同。

### 節點在做什麼

| 節點 | 動作 |
|---|---|
| 1 `LoadVideo` | 載入步驟 3a 的輸出 |
| 2 `GetVideoComponents` | 解回影格＋音訊（目前素材沒有音訊軌） |
| 3 `UpscaleModelLoader` | RealESRGAN x4plus |
| 4 `ImageUpscaleWithModel` | ESRGAN ×4 → 4096×2304 |
| 5 `ImageScale` | Lanczos → 1920×1080 |
| 6 `CreateVideo` | 16 fps（跟來源一致，硬編碼，不是動態偵測） |
| 7 `SaveVideo` | 輸出 |

### 時間預算

| 階段 | 時間 |
|---|---|
| ESRGAN ×4 + Lanczos + h264 編碼 | 約 7 分 |

（沿用拆分前 832×480 / 64 幀實測 144.5 秒、按 1024×576 / 128 幀等比放大推得的舊數字，尚未在拆分後的新檔案上重新實測——第一次跑請留意實際耗時是否吻合，不符再回來更新這個表。）

### 想要更高畫質（改用 1280×720 生成）

1280×720 是 WAN 2.2 A14B 的原生訓練解析度，材質細節明顯更好，只需 1.5 倍超解析。步驟 3a 節點 9 改成 **`1280 × 720`** 即可，其他都不用動——幀數算術與解析度無關，RIFE、trim、fps 全部維持原值，仍然是 8.00 秒。升頻工作流（步驟 3b）完全不用改，`LoadVideo` 照樣讀，只是 ESRGAN 輸入變大。驗證器只鎖幀數、循環長度與升頻後的最終尺寸，生成解析度是自由的。

### 記憶體最緊的一點在放大階段

ESRGAN 一次處理整批。128 幀 float32 的中間張量大小：

| 生成解析度 | ×4 後 | 峰值張量 |
|---|---|---|
| 1024×576 | 4096×2304 | 約 18 GB |
| 1280×720 | 5120×2880 | 約 28 GB |

64GB 機器兩者都跑得動，但 1280×720 已經接近上限。若遇到記憶體不足，**在 `cafe-upscale-1080p-mps.json` 裡刪掉節點 3、4，把節點 2 的 `images` 輸出直接接到節點 5**：Lanczos 從 1280×720 放大到 1920×1080 只有 1.5 倍，畫面稍軟但完全不佔額外記憶體，而且不會有超解析造成的閃爍。

---

## 步驟 4 — 發布前人工檢查

- [ ] 沒有品牌標誌、商標、可讀招牌或意外文字
- [ ] 沒有可辨識名人、真實人物肖像或未授權輸入照片
- [ ] 首尾接合自然，沒有跳接、閃爍、物體突然消失或幾何變形
- [ ] 咖啡杯、蒸氣、雨滴、植物與布料的運動符合物理直覺
- [ ] 超解析沒有造成逐幀閃爍（尤其是細紋理、葉緣、文字狀圖案）
- [ ] 有自己的剪輯、環境音、旁白、設計或敘事價值，不只是批量更換提示詞
- [ ] 已保存模型來源、模型檔 SHA-256、提示詞版本與輸入素材授權
- [ ] 若畫面屬於逼真的 AI 生成／合成場景，已完成 YouTube altered content 揭露

---

## 調參手冊：畫面不對時改哪裡

| 症狀 | 改哪裡 |
|---|---|
| 幾乎完全靜止 | 節點 11 / 13 `ModelSamplingSD3` 的 shift `8` → `5`（lightx2v 官方對 Lightning 的建議值）。或在 prompt 裡把動作講得更具體 |
| 動太多／鏡頭在漂 | shift `8` → `12`；prompt 裡強化 `locked-off camera, stable composition` |
| 物體變形、morphing | 降低動作描述數量，一次只留一種運動 |
| 不聽提示詞 | 先確認用的是 `umt5-xxl-encoder-Q8_0.gguf` 不是 Q3。仍不夠的話把 CFG 從 `1.0` 提到 `1.5~2.0` 並把步數從 `4` 提到 `6`（速度會變慢約 3 倍，因為 CFG>1 要跑兩次前向） |
| 循環接點有跳動 | 確認 Start / End 是**同一個檔案**；確認節點 19 `ImageFromBatch` 的 length 是 128 不是 129（`cafe-flf2v-wan22-mps.json`） |
| 放大後畫面在閃 | 在 `cafe-upscale-1080p-mps.json` 裡直接刪掉節點 3/4，只留節點 5 的 Lanczos 放大（畫面較軟但完全不閃） |
| 記憶體不足（生成階段） | `cafe-flf2v-wan22-mps.json` 節點 9 的幀數 `65` → `49`，同時節點 19 的 length 改成 `96`，fps 改成 `12`（96 ÷ 12 = 8.00 秒） |
| 想要更長／更短 | 保持 `保留幀數 ÷ fps = 目標秒數`。驗證器會擋掉算錯的組合 |

---

## MiniMax H3 能不能在這台機器跑

`workflows/video_minimax_h3_t2v.json` 是 ComfyUI 官方的 MiniMax H3 範本。H3 很吸引人：`MiniMaxH3ImageToVideo` 節點**原生就吃 `first_frame` + `last_frame`**（正是本專案需要的嚴格循環），還帶原生同步音訊，原生解析度到 1344×768。

本節的實測結論見下方「實測結果」。

### 為什麼量化格式是關鍵

ComfyUI 用 `comfy_kitchen` 執行量化權重，在這台機器上後端狀態是：

```
cuda   : 不可用（PyTorch 沒有 CUDA runtime）
hip    : 不可用
triton : 不可用（沒裝 triton）
eager  : ✅ 可用
```

`eager` 後端具備 `dequantize_int8_convrot_weight`、`dequantize_nvfp4`、`int8_linear` 等能力，也就是說**這些格式在 MPS 上會走「反量化成 bf16 再做一般矩陣乘法」的路徑**，而不是直接失敗。代價是每次前向都要反量化一次。

（本專案先前的筆記寫「int8_convrot 需要 CUDA cu130、nvfp4 是 Blackwell 專用，MPS 跑不了」—— 就 ComfyUI 0.31.1 的 eager fallback 而言，這個說法需要修正。）

### 實測結果：兩種量化格式在取樣階段都直接失敗

本機（M5 Pro / 64GB / MPS / ComfyUI 0.31.1 / torch 2.12.1）逐一實測三個檔案：

**✅ 文字編碼器 `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors`（15.7 GB）可以用**

```
[INFO] Found quantization metadata version 1
[INFO] Using MixedPrecisionOps for text encoder
[INFO] Requested to load MiniMaxH3TEModel_
[INFO] loaded completely;  14960.20 MB loaded, full load: True
```

載入 14.96 GB，`MiniMaxH3ImageToVideo` 完整執行（含文字編碼與首尾幀 VAE 編碼），43.5 秒完成。這比官方唯一的 MPS「安全」替代品 `qwen3vl_32b_minimax_h3_bf16.safetensors`（51.5 GB）省了 36 GB。

**❌ 擴散模型 `minimax_h3_fl2va_pruned_int8_convrot.safetensors`（21 GB）—— 缺運算子**

模型載入成功，取樣一啟動就中斷：

```
NotImplementedError: The operator 'aten::_int_mm' is not currently implemented
for the MPS device.
  comfy_kitchen/backends/eager/quantization.py:754 in fast_int8_mm
    return torch._int_mm(lhs, rhs)
```

`eager` 後端有 `dequantize_int8_convrot_weight`，權重讀得進來；但矩陣乘法呼叫 `torch._int_mm`，**PyTorch 在 MPS 上還沒實作這個 op**（[pytorch#141287](https://github.com/pytorch/pytorch/issues/141287)）。`PYTORCH_ENABLE_MPS_FALLBACK=1` 不是可行解，會把每一層線性層丟到 CPU，等於整個 20B 模型跑在 CPU 上。

**❌ 擴散模型 `minimax_h3_fl2va_pruned_fp8_scaled.safetensors`（21 GB）—— 資料型別本身不存在**

換成 fp8 版本重跑，錯誤更根本：

```
TypeError: Trying to convert Float8_e4m3fn to the MPS backend but it does not
have support for that dtype.
```

直接用 PyTorch 驗證（跳過 ComfyUI）：

```python
>>> torch.zeros(4, dtype=torch.float8_e4m3fn, device='mps')
RuntimeError: Undefined type Float8_e4m3fn
```

**這不是缺一個運算子，是 PyTorch 2.12.1 的 MPS 後端根本無法建立 float8_e4m3fn 張量。** 追進 `comfy/model_management.py` 可以看到這是設計上的：`supports_fp8_compute()` 直接判斷 `is_nvidia()`，MPS 一律回傳 `False`。不是 ComfyUI 忘記適配，是 PyTorch 目前根本沒有這個 dtype 的 MPS 實作，沒有任何命令列參數能繞過。

> **注意**：本專案先前的筆記寫「int8_convrot 需要 CUDA cu130、nvfp4 是 Blackwell 專用，MPS 都跑不了」——實測後這個說法需要修正為更精確的版本：nvfp4 文字編碼器可以跑；int8_convrot 擴散模型卡在缺 MPS 運算子；fp8 擴散模型卡在 MPS 根本不支援這個資料型別。三個原因都不一樣，都已直接驗證。

### 小結：comfy_kitchen 原生量化都不行，GGUF 可以

| 元件 | 檔案 | 能否在 MPS 執行 |
|---|---|---|
| 文字編碼器 | `qwen3vl_32b_..._nvfp4_awq`（15.7 GB） | ✅ 可以 |
| 影片 / 音訊 VAE | `minimax_h3_{video,audio}_vae_*` | ✅ 可以 |
| 擴散模型 int8_convrot（21 GB） | ❌ 缺 `_int_mm` 運算子 |
| 擴散模型 fp8_scaled（21 GB） | ❌ MPS 不支援 float8_e4m3fn dtype |
| 擴散模型 GGUF Q3_K_M（15.6 GB） | ✅ **可以**（見下） |

兩種 comfy_kitchen 原生量化格式**都已下載並實測**，都在 `SamplerCustomAdvanced`（取樣節點）直接報錯。文字編碼與 VAE 都能跑，唯獨佔算力最重的擴散模型這一步過不去——直到換成 GGUF。

### ✅ 但 GGUF 量化格式可以跑 —— 突破口不在精度，在載入路徑

comfy_kitchen 的 fp8/int8 是把整個張量以該資料型別**原生存放在 MPS 裝置記憶體上**，MPS 對這件事本身有硬性限制（見上）。**GGUF 是完全不同的機制**：llama.cpp 格式的量化區塊在 CPU 端讀入、反量化成一般的 f16/bf16 張量，才搬到裝置上 —— 全程不會出現 float8_e4m3fn 這種 MPS 不支援的裝置端型別。這正是本專案 WAN 管線全程在用的路徑（`UnetLoaderGGUF`），已經被證實在這台機器上完全可靠。

H3 開源一週後，社群就釋出了 GGUF 量化版本（[Abiray/MiniMax-H3-GGUF](https://huggingface.co/Abiray/MiniMax-H3-GGUF)、[joeygambino/MiniMax-H3-GGUF](https://huggingface.co/joeygambino/MiniMax-H3-GGUF) 等）。用 `MiniMax-H3-FL2VA-Q3_K_M.gguf`（15.6 GB）搭配既有的 `UnetLoaderGGUF` 節點實測：

```
[INFO] loaded completely;  15341.98 MB loaded, full load: True
  0%|          | 0/2 [00:00<?, ?it/s] 50%|█████     | 1/2 [02:30<02:30, 150.73s/it]
100%|██████████| 2/2 [05:00<00:00, 150.49s/it]
```

**取樣正常跑完，輸出 192 張影格，每張都是正確的寫實畫面**（同一張咖啡館關鍵幀當 first/last frame，只跑 2 步，蒸氣、光影、構圖都正常，首尾幀構圖幾乎一致）——不是 GitHub Issue #15315 描述的黑畫面或 NaN。

**這推翻了本節先前「H3 在本機無法產出任何影片」的結論。** GGUF 量化的 H3 擴散模型可以在這台機器上正確運行。

### 已校準的成本模型：兩個真實測量點，H3 自己的曲線

跑過兩次完整（20 步）GGUF Q3_K_M 生成，兩個解析度都是真實測量，不是外推：

| 解析度 | 幀數 | 步數 | 實測每步 | 實測總取樣時間 | 實測總耗時（含載入/解碼/編碼） |
|---|---|---|---|---|---|
| 608×352（360P） | 192 | 20 | **64.05 秒** | 21 分 21 秒 | **23.2 分**（1390.6 秒） |
| 864×480（480P） | 192 | 2（外推 20 步） | 150.44 秒 | — | — |

用這兩個真實點擬合出 H3 自己的成本曲線（`t = k·N^p`），不再借用 WAN 的指數：

```
p = 1.351   （先前借用 WAN 架構的指數是 1.8，明顯偏高——H3 的實際擴大成本比 WAN 溫和）
```

用這條校準過的曲線重新推算：

| 解析度 | tokens | 20 步總計 |
|---|---|---|
| 608×352（360P，實測） | 10,672 | **21 分**（實測） |
| 864×480（480P，實測） | 20,080 | **50 分** |
| 1280×736 | 44,800 | 2.5 小時 |
| 1920×1088（原生 1080P） | 98,560 | **7.2 小時**（先前用 WAN 指數估的是 14.6 小時，明顯高估） |

原生 1080P 依然不可行，但比之前兩次推算都更準——這次用的是 H3 自己的真實擴大曲線，不是跨架構借用的指數。360P 的 8 秒完整成片（含模型載入、文字編碼、VAE 解碼、h264 編碼）**實測 23.2 分鐘**，輸出規格 608×352 / 192 幀 / 24fps / 正好 8.000 秒，首尾幀構圖幾乎一致，循環乾淨——這是用你要求的室外露台咖啡廳關鍵幀跑出來的真實成片，不是基準測試。

> Q3_K_M 是有損量化，實測畫面乾淨銳利，看不出明顯的量化瑕疵。更高量化等級（Q5_K_M 23.9GB、Q6_K 28.2GB）品質理論上更好，但速度差異未實測——本專案在 WAN 上觀察到 Q3→Q5 每步只差約 5%，H3 是否也是這樣還沒驗證過。

### ⚠️ 授權：即使能跑，也不符合本專案的商用控管規則

| 項目 | 內容 |
|---|---|
| 授權 | **MiniMax H3 Community License Agreement**（非 Apache 2.0，非 OSI 認可） |
| 營收上限 | 年營收超過 **US$20M** 需另外取得 MiniMax 書面授權 |
| 強制標示 | 商用產品介面須顯著標示「MiniMax H3」 |
| 地域限制 | **美國、歐盟、英國、南韓不在 Applicable Territory** —— 這些地區不得在本地部署權重或使用其輸出（雲端 API 不受此限） |
| 禁止蒸餾 | 不得用 H3 輸出訓練競品模型（全球適用） |

本專案 [`MODEL-LICENSES.md`](MODEL-LICENSES.md) 的控管規則寫明「只將已審核的 Apache 2.0 模型列為預設」。H3 不符合，且地域限制需要你依自己所在地與發布對象自行確認。

### 結論

**H3 用 GGUF 量化格式可以在這台機器上正確產出影片，360P 甚至相當實用，但撐不到 1080P：**

| 面向 | 結果 |
|---|---|
| 技術可行性 | ✅ **GGUF 可以**（Q3_K_M 已實測兩次，畫面正確，360P 已產出完整成片） |
| 360P（608×352） | ✅ **實測 23.2 分鐘**，8 秒整，可用 |
| 480P（864×480） | 約 50 分鐘（純取樣，實測校準推算） |
| 原生 1080P | ❌ 約 7.2 小時（H3 自己曲線校準後的推算，比先前估計準確） |
| 授權 | ❌ MiniMax H3 Community License，非 Apache 2.0，US$20M 營收上限、強制標示「MiniMax H3」、地域排除美/歐盟/英/南韓 |

**360P 這個規格是本專案唯一一個「H3 實際比從頭生更快」的甜蜜點**——23.2 分鐘產出 608×352 的 8 秒成片，如果目標解析度就是 360P，不需要額外放大步驟。但本專案的目標是 1080P，這條路徑撐不過去；技術可行性的翻案不改變「不建議作為本專案 1080P 成片路徑」的結論。授權這關若對你的用途無妨礙，360P 場景反而是一個真實可用的選項。

它唯一無法被 WAN 管線取代的是**原生同步音訊**（voice / SFX / 音樂在單次前向中一起生成）。如果哪天要做「有環境音的版本」，GGUF 路徑已經證實可行，下一步是驗證音訊解碼分支（`VAEDecodeAudio`）與更高量化等級（Q5/Q6）的實際表現。

---

## 本地驗證

以下檢查不需要模型、不需要 ComfyUI、不需要 GPU：

```bash
bash -n scripts/download-models.sh
python -m unittest tests/test_validate_project.py -v
python scripts/validate_project.py
```

`validate_project.py` 會檢查 JSON 結構、node/link 端點、商用模型檔名、FLUX schnell 的 steps/CFG、生成工作流的**循環長度算術**，以及升頻工作流的輸出尺寸／幀率：

```
RIFE 後幀數 = (生成幀數 - 1) × 倍率 + 1
保留幀數   = RIFE 後幀數 - 1        ← 去掉重複的接點幀
保留幀數 ÷ fps = 8.00 秒            ← 必須剛好
```

改了幀數、倍率或 fps 其中任何一個而沒同步改其他的，驗證器會直接指出來。

---

## 目錄結構

```text
comfyui-cafe-loop-generator/
├── workflows/
│   ├── cafe-keyframe-flux-mps.json      # 步驟 1：FLUX schnell GGUF 關鍵幀（1280×720）
│   ├── cafe-flf2v-wan22-mps.json        # 步驟 2/3a：WAN2.2 FLF2V 生成，解析度為參數（768×432 試拍／1024×576 正式）
│   ├── cafe-upscale-1080p-mps.json      # 步驟 3b：純後製 ESRGAN+Lanczos 升頻到 1920×1080，不重新擴散
│   ├── cafe-flf2v-ltx23-360p-mps.json   # LTX 2.3 對照組（360P）
│   └── cafe-flf2v-h3-360p-mps.json      # MiniMax H3 對照組（360P，見下方章節；授權不符合本專案商用規則）
├── scripts/
│   ├── download-models.sh               # 模型下載器
│   ├── staged_workflow_server.py        # 圖片→動作試拍二階段人工審核本機控制台
│   └── validate_project.py              # 不需 GPU 的靜態驗證器
├── tests/
├── prompt-library.md                    # 10 組虛構寫實場景
├── MODEL-LICENSES.md                    # 模型授權與發布紀錄模板
└── README.md
```

先前保留的 NVIDIA GPU 版工作流（`cafe-keyframe-flux.json`、`cafe-flf2v-preview.json`、`cafe-flf2v-loop.json`）與 `video_minimax_h3_t2v.json` 官方範本已在後續整理中移除；目前 repo 裡只有 Apple Silicon MPS 這一套管線。若要租用雲端 GPU，需要另外重建對應工作流。

---

## 商用原則與授權

- 起始關鍵幀使用 **FLUX.1 Schnell**，來源標示為 Apache 2.0
- 動畫使用 **WAN 2.2 I2V A14B**，官方文件標示 Apache 2.0
- **Lightning LoRA 來自 [lightx2v/Wan2.2-Lightning](https://huggingface.co/lightx2v/Wan2.2-Lightning)，商用前請自行確認其授權條款**
- RIFE 與 ESRGAN 放大模型各有獨立授權，商用前請自行確認
- 場景一律為虛構咖啡館，不使用真實城市、地標、品牌、商標、名人或可辨識人物
- 模型授權不涵蓋你上傳的照片、音樂、環境音、字型或其他第三方素材
- 揭露 AI 不等於一定能獲得收益；大量重複、模板化的影片仍可能不符合 YouTube 營利政策

詳細紀錄請看 [`MODEL-LICENSES.md`](MODEL-LICENSES.md)。

## 官方參考

- [ComfyUI WAN 2.2 文件](https://docs.comfy.org/tutorials/video/wan/wan2_2)
- [ComfyUI FLUX.1 文字生圖文件](https://docs.comfy.org/tutorials/flux/flux-1-text-to-image)
- [lightx2v/Wan2.2-Lightning](https://huggingface.co/lightx2v/Wan2.2-Lightning)
- [QuantStack/Wan2.2-I2V-A14B-GGUF](https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF)
- [city96/umt5-xxl-encoder-gguf](https://huggingface.co/city96/umt5-xxl-encoder-gguf)
- [Comfy-Org/frame_interpolation](https://huggingface.co/Comfy-Org/frame_interpolation)
- [YouTube：AI 生成或合成內容揭露](https://support.google.com/youtube/answer/14328491)
