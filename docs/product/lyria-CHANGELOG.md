# Changelog

## 1.1.0

### 新增

- `lyria-auto resume`：中途失敗可續跑，已完成的曲目不重新生成、不重複付費。
  單首失敗不再中斷整批，全部跑完後會自動重試失敗的曲目。
- `lyria-auto run --from-job N`：把 dry-run 的計畫實際生成，文案與縮圖一字不差。
- 上傳去重：已有 video ID 的工作再次上傳會跳過，避免重複發布。

### 變更

- **一支影片＝一張專輯**：曲風、場景、錄音質感、混音風格全片固定，
  只有樂器編制、情緒、速度、環境音逐首變化。先前每首獨立重抽全部屬性，
  25 首會橫跨 11 個場景、5 種曲風，且標題與縮圖只符合其中少數幾首。
- **曲目數改為自動決定**：逐首生成並累加實際長度，湊夠 `max_material_minutes`
  就停。移除 `tracks_per_video` 與 `target_track_seconds`，新增
  `max_material_minutes` 與 `max_tracks_per_video`。
- **Prompt 不再指定秒數**：實測不指定約得 173 秒，指定「150 秒」只得 144 秒；
  計費按首計與長度無關，指定較短秒數等於同樣的錢買到更少音樂。
- **循環改為交叉淡化且絕不中途切斷**：取代原本的 `-stream_loop` 硬切與
  `-t` 裁切，成品長度改為完整曲目的整數倍（因此是「大約」而非精確值）。
- **隨機 `400 content_blocked` 不再誤判為內容違規**：改為正常重試，
  只有每次都被擋才改寫 Prompt。

### 修正

- 靜態影片比音訊長約 40 秒（低 fps 下 `-shortest` 不足，改為明確 `-t`）。
- 縮圖字型在 macOS 15+ 退回點陣字（PingFang 已非一般檔案）。
- 標題與說明混入英文場景長句，改為 `en` / `zh` 雙欄。
- 無效 `--channel` 會在資料庫留下 failed 記錄（改為驗證後才建立 job）。

## 1.0.0

- Lyria 3 Interactions API provider
- Prompt combinator and local safety precheck
- SQLite job state
- FFmpeg audio QA, normalization, crossfade and video rendering
- Pillow thumbnail generation
- YouTube OAuth, resumable upload, thumbnail, playlist and publishAt scheduling
- Multi-channel token profiles
- APScheduler and Docker deployment
- Dry-run, doctor, status and test suite
