# 完整運作過程

## 初始化階段（只做一次）

1. 安裝 Python、FFmpeg 與專案依賴。
2. 建立 Gemini API Key（**須為已開通帳單的付費專案**，Lyria 3 無免費額度）。
3. 建立 Google Cloud OAuth Desktop App。
4. 把 OAuth JSON 放入 `secrets/`。
5. 執行一次 `authorize-youtube`。
6. 使用 `doctor` 驗證。
7. 使用 `run --dry-run` 驗證本地輸出，再用 `run --from-job N` 轉正。
8. 使用 private 模式上傳一支測試影片。

## 每次自動工作

**規劃階段**

1. Scheduler 根據 cron 觸發。
2. Prompt Engine 抽取素材：曲風、場景、錄音質感、混音風格**全片固定一次**，
   樂器編制、情緒、速度、環境音逐首變化，讓成品像一張專輯而非隨機播放清單。
3. SQLite 檢查近期 signature，降低與前幾支影片的組合重複。
4. 本地 Safety 檢查。
5. 建立標題、說明、標籤、排程時間與縮圖，寫入 `plan.json`。
   （縮圖與文案在生成音樂**之前**就完成，所以中途失敗可原樣續跑。）

**生成階段**

6. Lyria 3 Pro 逐首生成音訊，**累加實際長度，湊夠 `max_material_minutes` 就停** ——
   不預先算曲目數，規劃了但用不到的 Prompt 不會送出、不花錢。
7. FFprobe 驗證時長、取樣率與聲道。
8. FFmpeg loudnorm 正規化。
9. 單首失敗不中斷整批；全部跑完後對失敗的曲目重試一輪。仍失敗則中止並保留進度，
   可用 `lyria-auto resume` 續跑，已完成的曲目不重做。

**算圖與發布階段**

10. 以 crossfade 串接所有曲目，必要時**以完整份數交叉淡化循環**補足目標長度
    （絕不從曲子中間切斷，所以成品長度是「大約」）。
11. 將靜態圖片與音訊編碼為 MP4，長度精確對齊音訊。
12. 使用 resumable upload 上傳 YouTube；已上傳過的工作會偵測既有 video ID 並跳過，
    不會重複發布。
13. 設定縮圖與播放清單。
14. 把 video ID、狀態與錯誤寫入 SQLite。

## 人工抽查建議

完全無人值守可行，但音樂生成具有隨機性。正式公開前，仍建議先以 private 模式累積一批，人工抽查音質、內容與頻道一致性，再開啟 scheduled 模式。
