# 商用實景咖啡館循環影片設計

日期：2026-08-04  
狀態：已由使用者確認設計方向，待文件審閱後進入實作計畫

## 目標

將專案調整為適合商業 YouTube 內容的本地生成流程：模型授權清楚、畫面接近實景、場景為虛構咖啡館，並降低品牌、地標、真人與大量模板化內容帶來的風險。

本設計不保證 YouTube 營利審核結果，也不替使用者取得第三方照片、音樂、音效或字型的授權。使用者仍須在發布前完成素材與平台政策檢查。

## 模型與授權決策

採用方案 A：

- 關鍵影格：`flux1-schnell-fp8.safetensors`，來源為 Comfy-Org 的 FLUX.1 Schnell checkpoint，Apache 2.0。
- 影片生成：WAN 2.2 I2V A14B FP8 scaled 模型，沿用現有原生 `WanFirstLastFrameToVideo` 流程；WAN 2.2 官方模型系列採 Apache 2.0。
- 移除 `flux1-dev-fp8.safetensors`。FLUX.1 Dev 不是本商用流程可接受的模型。
- 下載腳本預設安裝上述商用主線模型；若使用者只需要影片，也應能透過明確選項跳過關鍵影格模型。

模型來源與授權將寫入 `MODEL-LICENSES.md`，包含來源網址、檔名、用途、授權名稱與最後確認日期。這是模型授權紀錄，不代表法律意見。

參考：

- https://docs.comfy.org/tutorials/flux/flux-1-text-to-image
- https://huggingface.co/Comfy-Org/flux1-schnell/blob/main/flux1-schnell-fp8.safetensors
- https://docs.comfy.org/tutorials/video/wan/wan2_2

## Workflow 設計

### 關鍵影格

`workflows/cafe-keyframe-flux.json` 改用 FLUX.1 Schnell FP8 checkpoint，並使用官方 checkpoint 路線的 4 steps、CFG 1.0。Prompt 改為虛構咖啡館，禁止真實城市、地標、品牌、店名、可讀招牌、名人與可辨識真人。

### 正式影片

`workflows/cafe-flf2v-loop.json` 保留 WAN 2.2 FLF2V，維持 832×480、16fps、161 幀。影片 Prompt 只描述低幅度環境動態，例如蒸氣上升、雨滴滑落、窗簾輕動、葉片搖曳與燈光微閃；鏡頭固定，不使用快速推拉、旋轉或大幅變形。

### 預覽影片

新增 81 幀、480p 的預覽 Workflow，供正式生成前檢查循環接點、人物／物件變形與運動自然度。預覽通過後才使用 161 幀正式 Workflow。

## Prompt 與內容安全規則

`prompt-library.md` 的 10 組提示詞改為虛構場景分類，不再使用 Paris、Tokyo、Haussmann 等真實地點作為生成條件。每組提示詞加入：

- generic fictional cafe、no recognizable location、no logos、no readable text
- no celebrity、no public figure、no identifiable person
- locked camera、subtle ambient motion、stable composition
- photorealistic materials、natural light、physically plausible motion

負面 Prompt 會排除水印、商標、文字、招牌、變形臉部、額外肢體、漂浮物件與不可能的陰影。人物只放在遠景或以不可辨識剪影呈現。

## 文件與發布流程

README 將加入：

1. 商用模型安裝指令與磁碟需求。
2. 模型授權與第三方素材責任說明。
3. YouTube altered/synthetic content 揭露提醒。
4. 避免同模板大量批次生產的內容品質提醒。
5. 發布前檢查：授權、品牌／地標、畫面瑕疵、循環接點、音訊權利與 AI 揭露。

目前專案不自動生成或附帶音訊；音樂與環境音必須由使用者另行提供可商用素材。

## 驗證策略

- 使用 Python 標準函式庫解析兩份 Workflow JSON，檢查節點 ID、連結端點與模型檔名。
- 使用 `bash -n scripts/download-models.sh` 檢查下載腳本語法。
- 驗證正式影片鏈為 `VAEDecode → CreateVideo → SaveVideo`。
- 驗證 Workflow 中所有模型引用都能對應下載腳本或明確的使用者輸入。
- 以 81 幀預覽做人工畫面檢查，再以 161 幀正式生成。
- 本機無 ComfyUI、GPU 與模型權重，因此端到端生成需在 RunPod、Vast.ai 或其他具備相容 GPU 的環境完成。

## YouTube 風險邊界

逼真的 AI 場景通常需要在 YouTube Studio 中揭露 altered/synthetic content。揭露本身不等於失去營利資格，但 YouTube 的營利政策會關注重複、批量、缺乏原創價值的內容。因此專案只負責生成素材，使用者仍應為每支影片加入不同的場景設計、聲音、剪輯或其他原創價值。

參考：

- https://support.google.com/youtube/answer/14328491
- https://support.google.com/youtube/answer/1311392?hl=en-EN
