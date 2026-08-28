# Validation Report

最後更新：2026-07-28

## 自動化測試

- Pytest：**42 passed**
- 涵蓋：Prompt 專輯一致性、安全預檢、metadata 排程、dry-run 整合、
  無縫循環長度、續跑不重複付費、CLI 錯誤訊息、上傳去重、頻道驗證。
- 所有測試以假的 Lyria client 執行，**不呼叫真實 API、不產生費用**。

## 真實 API 實測（已驗證）

- Lyria 3 Pro 生成：PASS（44.1 kHz 立體聲 MP3）
- 曲目長度：不指定秒數約 **173 秒**；指定「150 秒」只得 144 秒。
  計費按首計、與長度無關，故 Prompt 刻意不指定秒數。
- `400 content_blocked` 為**隨機**：完全相同的 Prompt 一次被擋、一次成功
  （179.7 秒）。故隨機失敗改為正常重試，不再立即改寫 Prompt。
- 完整 10 分鐘成品：PASS（1920×1080 H.264 + AAC 48 kHz，視訊與音訊長度精確對齊）

## FFmpeg 端到端

- FFprobe 驗證、loudnorm、acrossfade 串接、交叉淡化循環、靜態 MP4 編碼：PASS
- 48 軌 filter graph 壓力測試：PASS（92.7 分鐘輸出，耗時 140.5 秒）
- 循環長度實測：580.4 秒素材 → 目標 30 分鐘 → 實際 1737.1 秒
  （3 份完整循環；刻意不裁切以免切斷最後一首）
- 靜態影片長度對齊：`-shortest` 在低 fps 下會多出約 40 秒，已改為明確指定 `-t`

## 尚未驗證

- **真實 YouTube 上傳**：需要使用者自己的 OAuth client、首次瀏覽器授權與頻道。
  上傳路徑（`_upload`、重複發布防護、縮圖、播放清單）目前只有以假 client
  覆蓋的單元測試，未對真實 `googleapiclient` 分塊上傳路徑做過端到端驗證。
