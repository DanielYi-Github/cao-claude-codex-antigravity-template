# 商用模型與授權紀錄

本專案預設使用下列模型。這份文件是工程與素材管理紀錄，不是法律意見；正式商用前仍應保存下載來源、版本、雜湊值與第三方素材授權。

| 模型 | 檔案 | 授權 | 官方來源 | 專案用途 |
|---|---|---|---|---|
| FLUX.1 Schnell FP8 | `flux1-schnell-fp8.safetensors` | Apache 2.0 | [Comfy-Org/flux1-schnell](https://huggingface.co/Comfy-Org/flux1-schnell) | 商用起始關鍵幀 |
| WAN 2.2 I2V A14B FP8 High Noise | `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors` | Apache 2.0 | [Wan 2.2 官方文件](https://docs.comfy.org/tutorials/video/wan/wan2_2) / [Comfy-Org repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) | FLF2V 高噪聲階段 |
| WAN 2.2 I2V A14B FP8 Low Noise | `wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` | Apache 2.0 | [Wan 2.2 官方文件](https://docs.comfy.org/tutorials/video/wan/wan2_2) / [Comfy-Org repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) | FLF2V 低噪聲階段 |
| UMT5 XXL FP8 | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | 依來源模型條款；下載自 Comfy-Org WAN repackaged | [Comfy-Org WAN 2.1 repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged) | WAN 文字編碼器 |
| WAN VAE | `wan_2.1_vae.safetensors` | 隨 WAN 模型來源條款 | [Comfy-Org WAN 2.2 repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) | 影像/影片編解碼 |

## Apple Silicon / Mac mini（MPS）替代關鍵幀模型組合

以下模型僅在使用 `workflows/cafe-keyframe-flux-mps.json` 時需要，取代上表的 FLUX.1 Schnell FP8 checkpoint。需另外安裝 [city96/ComfyUI-GGUF](https://github.com/city96/ComfyUI-GGUF) 自訂節點才能載入 GGUF 檔案。

| 模型 | 檔案 | 授權 | 官方來源 | 專案用途 |
|---|---|---|---|---|
| FLUX.1 Schnell GGUF Q4_K_S | `flux1-schnell-Q4_K_S.gguf` | Apache 2.0 | [city96/FLUX.1-schnell-gguf](https://huggingface.co/city96/FLUX.1-schnell-gguf) | MPS 相容量化 UNet |
| T5 XXL Encoder GGUF Q4_K_S | `t5-v1_1-xxl-encoder-Q4_K_S.gguf` | Apache 2.0 | [city96/t5-v1_1-xxl-encoder-gguf](https://huggingface.co/city96/t5-v1_1-xxl-encoder-gguf) | MPS 相容量化文字編碼器 |
| CLIP L 文字編碼器 | `clip_l.safetensors` | 依來源模型條款 | [comfyanonymous/flux_text_encoders](https://huggingface.co/comfyanonymous/flux_text_encoders) | FLUX 雙文字編碼器之一 |
| FLUX VAE | `ae.safetensors` | Apache 2.0（同 FLUX.1 Schnell，經 Comfy-Org 非閘控鏡像轉載） | [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo/blob/main/split_files/vae/ae.safetensors) | 影像編解碼 |

GGUF 量化模型是 FLUX.1 Schnell（Apache 2.0）的直接量化衍生版本，city96 的發布頁面同樣標示 Apache 2.0；量化本身不會改變原始模型授權條款，但仍建議在正式商用前自行核對來源頁面的最新授權標示。

## Apple Silicon（MPS）1080P 管線模型組合

生成工作流 `workflows/cafe-flf2v-wan22-mps.json`（試拍與正式生成共用同一個檔案）與後製升頻工作流 `workflows/cafe-upscale-1080p-mps.json` 合起來使用的完整模型組合。由 `DOWNLOAD_MPS_1080P=1 bash scripts/download-models.sh` 取得。

| 模型 | 檔案 | 授權 | 官方來源 | 專案用途 |
|---|---|---|---|---|
| WAN 2.2 I2V A14B GGUF Q5_K_M High Noise | `Wan2.2-I2V-A14B-HighNoise-Q5_K_M.gguf` | Apache 2.0 | [QuantStack/Wan2.2-I2V-A14B-GGUF](https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF) | FLF2V 高噪聲階段 |
| WAN 2.2 I2V A14B GGUF Q5_K_M Low Noise | `Wan2.2-I2V-A14B-LowNoise-Q5_K_M.gguf` | Apache 2.0 | [QuantStack/Wan2.2-I2V-A14B-GGUF](https://huggingface.co/QuantStack/Wan2.2-I2V-A14B-GGUF) | FLF2V 低噪聲階段 |
| UMT5 XXL Encoder GGUF Q8_0 | `umt5-xxl-encoder-Q8_0.gguf` | Apache 2.0 | [city96/umt5-xxl-encoder-gguf](https://huggingface.co/city96/umt5-xxl-encoder-gguf) | 文字編碼器（提示詞遵循度的關鍵） |
| WAN 2.2 Lightning 4-step LoRA (High/Low) | `wan2.2_i2v_A14B_{high,low}_noise_lightning_4step.safetensors` | Apache 2.0（發布頁標示） | [lightx2v/Wan2.2-Lightning](https://huggingface.co/lightx2v/Wan2.2-Lightning) → `Wan2.2-I2V-A14B-4steps-lora-rank64-Seko-V1/` | 4 步蒸餾，約 10 倍加速 |
| RIFE v4.26 | `rife_v4.26.safetensors` | MIT and Apache 2.0（Comfy-Org 標示 `license_name: mit-and-apache-2.0`） | [Comfy-Org/frame_interpolation](https://huggingface.co/Comfy-Org/frame_interpolation) | 2× 補幀 |
| Real-ESRGAN x4plus | `RealESRGAN_x4plus.pth` | **BSD-3-Clause** | [xinntao/Real-ESRGAN v0.1.0 release](https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth) | 4× 超解析至 1080P |
| WAN VAE | `wan_2.1_vae.safetensors` | 隨 WAN 模型來源條款 | [Comfy-Org WAN 2.2 repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) | 影像/影片編解碼 |

### ⚠️ 超解析模型的授權陷阱

社群最常推薦的兩個放大模型 **不可用於本專案**：

| 模型 | 授權 | 商用 |
|---|---|---|
| `4x_foolhardy_Remacri` | CC-BY-NC-SA-4.0 | ❌ 禁止 |
| `4x-UltraSharp` | CC-BY-NC-SA-4.0 | ❌ 禁止 |
| `RealESRGAN_x4plus` | BSD-3-Clause | ✅ 允許 |

兩者在 `uwg/upscaler` 這類彙整型 repo 裡會被概括標成 MIT，但那是 repo 的標籤，不是模型本身的授權（[OpenModelDB](https://openmodeldb.info/models/4x-Remacri) 標示為 CC-BY-NC-SA-4.0）。畫面看起來更銳利，但會讓整支成片變成不可商用。

`scripts/validate_project.py` 會掃描 `workflows/` 下所有檔案並攔截這些檔名。若要換其他放大模型，先確認授權再更新 `EXPECTED_MPS_UPSCALE_MODELS`。

若需要比 Real-ESRGAN 更好的細節且仍可商用，可考慮 [Phips/4xNomos8kDAT](https://huggingface.co/Phips/4xNomos8kDAT)（CC-BY-4.0，需標示出處）。

### Lightning LoRA 補充

`lightx2v/Wan2.2-Lightning` 的 HuggingFace 頁面標示 Apache 2.0。它是 Wan2.2（Apache 2.0）的蒸餾衍生權重。商用前建議自行到來源頁面複核最新標示，並記錄下載日期與檔案雜湊。

## ❌ 已評估並排除（1080P 用途）：MiniMax H3

`video_minimax_h3_t2v.json`（ComfyUI 官方範本，已於後續整理中移出 repo）曾用於下述 H3 評估。**H3 未被採用為本專案 1080P 成片路徑**，理由記錄如下（實測與推算過程見 [`README.md`](README.md#minimax-h3-能不能在這台機器跑)）。技術可行性後來被推翻過一次（GGUF 量化格式證實可行，且 360P 已實際產出可用成片），但 1080P 的結論不變——排除理由是速度與授權，不是「跑不起來」。

| 檔案 | 授權 | 來源 |
|---|---|---|
| `minimax_h3_fl2va_pruned_*.safetensors` / GGUF 量化版 | **MiniMax H3 Community License Agreement** | [MiniMaxAI/MiniMax-H3](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE) |
| `qwen3vl_32b_minimax_h3_*.safetensors` | 同上 | [Comfy-Org/MiniMax-H3](https://huggingface.co/Comfy-Org/MiniMax-H3) |
| `minimax_h3_{video,audio}_vae_*.safetensors` | 同上 | 同上 |

### 技術可行性：comfy_kitchen 原生量化不行，GGUF 可以（兩個解析度已實測）

- ❌ `int8_convrot`（21GB）—— 取樣時報 `torch._int_mm` 在 MPS 尚未實作（[pytorch#141287](https://github.com/pytorch/pytorch/issues/141287)）
- ❌ `fp8_scaled`（21GB）—— 取樣時報 MPS 完全不支援 `float8_e4m3fn` 這個資料型別（直接以 `torch.zeros(dtype=torch.float8_e4m3fn, device='mps')` 驗證過，PyTorch 2.12.1 對此丟出 `RuntimeError: Undefined type Float8_e4m3fn`，與 ComfyUI 無關，是 PyTorch MPS 後端本身的限制）。已刪除，確認無用。
- ✅ **GGUF Q3_K_M**（[Abiray/MiniMax-H3-GGUF](https://huggingface.co/Abiray/MiniMax-H3-GGUF)，15.6GB，社群量化）—— 用既有的 `UnetLoaderGGUF` 節點（WAN 管線同一套機制）實測兩次成功：
  - 864×480×192幀，2 步取樣，**150.44 秒/步**
  - 608×352×192幀，**完整 20 步**，**64.05 秒/步**，總耗時 **23.2 分鐘**，輸出正確的 8 秒成片（非黑畫面/NaN，與 [Comfy-Org/ComfyUI#15315](https://github.com/Comfy-Org/ComfyUI/issues/15315) 描述的 T2V 症狀不同）
  
  GGUF 在 CPU 端反量化成一般 f16/bf16 張量再搬到裝置上，全程不會出現 MPS 不支援的裝置端型別，這是它能跑而原生量化不能跑的根本原因。用這兩個真實測量點擬合出 H3 自己的成本曲線（指數 1.351，比先前借用 WAN 架構的 1.8 溫和）。

### 360P 是例外：技術、速度都可行，只剩授權是唯一障礙

以 H3 自己的曲線推算：360P 約 21 分（已實測 23.2 分）、480P 約 50 分、原生 1080P 約 **7.2 小時**（不可行）。**360P 這個規格下，H3 是可用的**——如果目標解析度就是 360P、且授權條件可接受，沒有理由不用。

### 1080P 排除理由（速度與授權，不是技術限制）

1. **無速度優勢** —— 現有 WAN 管線 40 分鐘完成含放大的完整 1080P 成片；H3 光是 480P 純取樣就要 50 分鐘，原生 1080P 更要 7.2 小時。
2. **授權與商用定位衝突** —— 非 Apache 2.0。年營收超過 US$20M 需另取書面授權；商用產品介面須顯著標示「MiniMax H3」；含全球適用的禁止蒸餾條款。
3. **地域限制** —— 授權的 Applicable Territory **排除美國、歐盟、英國、南韓**，這些地區不得在本地部署權重或使用其輸出（雲端 API 不受此限）。採用前必須依實際所在地與發布對象確認。

H3 唯一不可被取代的能力是**原生同步音訊**。若日後要做含環境音的版本，GGUF 路徑已證實可行，下一步是驗證音訊解碼分支（`VAEDecodeAudio`）與更高量化等級（Q5_K_M/Q6_K）的實際表現，同時需重新確認授權條款與地域限制。

## ⚠️ 實驗性評估中（尚未採用）：LTX-2.3

`models/unet/ltx-2.3-22b-distilled-1.1-Q3_K_S.gguf` 等 5 個檔案（共 24GB）是 2026-08-11 為了驗證「MPS 上跑不跑得起來」下載的測試檔，**不在本專案的預設下載清單或 `validate_project.py` 檢查範圍內**。保留原因：LTX-2.3 是少數原生支援「同步音訊＋視覺」的開源模型，若日後要做含環境音的咖啡館版本，這是候選之一。**目前未整合進任何 `workflows/` 生產流程。**

| 檔案 | 放哪 | 大小 | 來源 |
|---|---|---|---|
| `ltx-2.3-22b-distilled-1.1-Q3_K_S.gguf` | `models/unet/` | 13 GB | [QuantStack/LTX-2.3-GGUF](https://huggingface.co/QuantStack/LTX-2.3-GGUF) |
| `gemma-3-12b-it-qat-UD-Q4_K_XL.gguf` | `models/text_encoders/` | 6.9 GB | [unsloth/gemma-3-12b-it-qat-GGUF](https://huggingface.co/unsloth/gemma-3-12b-it-qat-GGUF) |
| `ltx-2.3-22b-distilled_embeddings_connectors.safetensors` | `models/text_encoders/` | 2.2 GB | [unsloth/LTX-2.3-GGUF](https://huggingface.co/unsloth/LTX-2.3-GGUF) |
| `ltx-2.3-22b-distilled_video_vae.safetensors` | `models/vae/` | 1.4 GB | 同上 |
| `ltx-2.3-22b-distilled_audio_vae.safetensors` | `models/vae/` | 352 MB | 同上 |

另安裝了 `custom_nodes/ComfyUI-KJNodes`（`VAELoaderKJ` 節點所需），MIT 授權，跟模型本身授權無關。

### 授權：`ltx-2-community-license-agreement`，非 Apache 2.0

不符合本專案「只將已審核的 Apache 2.0 模型列為預設」的規則。對照 MiniMax H3 被排除的邏輯，逐項檢查：

| 檢查項 | 條款 |
|---|---|
| 商用門檻 | 年營收 **≥ US$10,000,000** 需另簽付費商用授權（違反罰則為授權費雙倍） |
| 競品條款 | 禁止用於「與 Licensor（Lightricks）商業產品或服務直接競爭」的產品/服務，除非另簽商用授權——Lightricks 自家 LTX Studio 就是影片生成服務，本專案是否算「競爭」屬灰色地帶，商用前需法律複核 |
| 地域限制 | 標準出口管制／OFAC 制裁名單，**沒有**像 H3 那樣排除美/歐盟/英/南韓 |
| 內容揭露義務 | 強制要求「明確且可理解地」揭露輸出為機器生成——與 README 既有的「YouTube altered-content disclosure」步驟一致，不算新增負擔 |
| 衍生模型定義 | 涵蓋蒸餾/中間表徵方法，但**僅推論使用不受影響**，只在要微調/蒸餾 LTX-2.3 本身時才相關 |

來源：[Lightricks/LTX-2 LICENSE](https://github.com/Lightricks/LTX-2/blob/main/LICENSE)

### 技術可行性：MPS 相容（已驗證）

用 GGUF 量化（`UnetLoaderGGUF`，跟 WAN 管線同一套機制）在 M5 Pro/64GB 上實測：21 幀、512×288、8 步 distilled sampling，**140.45 秒完整跑完**，video VAE 與 audio VAE decode 皆成功，未觸發社群回報的 MPS 已知 bug（[Lightricks/ComfyUI-LTXVideo#386](https://github.com/Lightricks/ComfyUI-LTXVideo/issues/386) 提到僅 21/61 幀在 audio VAE decode 穩定，其他幀數可能觸發 `Output channels > 65536 not supported at the MPS device`——尚未測試其他幀數）。

### 目前不採用為視覺管線替代方案的理由（速度，非授權）

- distilled 版仍需 8 步（WAN2.2 + Lightning LoRA 只要 4 步）
- 22B 參數比 WAN2.2 A14B 更重，小尺寸（512×288×21幀）純 sampling 已要 80 秒，換算到專案實際目標（1024×576×65幀以上）不太可能贏過現有 40 分鐘全流程（含放大）
- 唯一不可被 WAN2.2 取代的能力是**原生同步音訊**——與 H3 案例相同的結論：技術可行，但目前沒有速度優勢，只有「日後要做含音訊版本」才有採用理由

### 下一步（若要正式評估含音訊版本）

1. 法律複核「競品條款」是否適用於本專案的發布形式（YouTube／授權銷售／SaaS 等）
2. 確認公司/專案年營收是否落在 $10M 門檻內
3. 驗證 LTX-2.3 原生的首尾幀（FLF2V）節點（`LTX_2.3 First And Last Frame.json`，[Lightricks/ComfyUI-LTXVideo](https://github.com/Lightricks/ComfyUI-LTXVideo/tree/master/example_workflows/2.3)）能否達成本專案「首尾嚴格循環」的要求
4. 用專案實際目標解析度/幀數重新測速，跟現有管線的 40 分鐘基準比較

## 商用控管規則

- 本專案只將已審核的 Apache 2.0 模型列為預設；若替換模型，必須先更新授權紀錄與驗證規則。
- 只使用虛構咖啡館場景。不要刻意生成真實城市、地標、品牌、商標、可辨識人物或名人肖像。
- 使用者自行上傳的起始/結束畫面、音樂、環境音、字型、素材與 LoRA 必須另行確認商用權利。
- Apache 2.0 通常允許商業使用，但仍須保留授權與 NOTICE 要求；不得把模型輸出或第三方素材的權利範圍誤寫成平台保證。
- 每次發布前記錄：模型來源 URL、下載日期、檔案 SHA-256、提示詞版本、輸入素材授權與成片審核結果。

## 建議發布紀錄欄位

```text
Project:
Model source URL:
Model file:
Model SHA-256:
Downloaded at:
Prompt library version:
Input image/music licenses:
Human review completed:
YouTube altered-content disclosure completed:
```
