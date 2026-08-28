# 調試與故障排除

## 先執行 Doctor

```bash
lyria-auto doctor
```

## 出現 400 content_blocked

**先確認：這個錯誤有相當比例是隨機的。** 實測用完全相同的 prompt 呼叫兩次，
一次被擋、一次成功，所以 log 裡出現零星 400 並不代表你的 Prompt 有問題。

程式的處理方式：先照一般錯誤重試（預設 3 次、指數退避）。只有每一次都被擋，
才視為內容真的違規，改寫成純器樂與純音樂屬性描述再試一次；不會降低或關閉平台安全機制。

只有**同一首連續多次都被擋**才需要調試：

1. 開啟 `workspace/job_XXXXXX/plan.json`。
2. 檢查是否出現人物、作品、品牌、歌詞、模仿或相似性要求。
3. 在 `config/prompts.yaml` 移除造成攔截的素材。
4. 先執行 `prompt-preview`。
5. 再以一支影片測試。

## Lyria 模型無權限或找不到

- 確認 Gemini API Key 所屬專案已開放 Lyria 3 Preview。
- 更新 SDK：`python -m pip install --upgrade google-genai`。
- 確認模型名稱仍為 `lyria-3-pro-preview`；Preview 型號可能變更。

## FFmpeg 失敗

執行：

```bash
ffmpeg -version
ffprobe -version
```

詳細命令錯誤會寫入 `logs/lyria-auto.log`。

## OAuth redirect / access_denied

- OAuth Client 必須是 Desktop app。
- 測試模式下，把你的 Google 帳號加入 Test users。
- 刪除舊 token 後重新執行 `authorize-youtube`。

## YouTube 403 quota / uploadRateLimitExceeded

- 等候配額恢復。
- 降低每次批次數量。
- 到 Google Cloud Console 查看 YouTube Data API 配額。

## 上傳成功但只能 private

新建立、未通過稽核的 YouTube API 專案可能被平台強制限制為 private。需依 YouTube 規則申請 API compliance audit。

## 斷線、當機或中途失敗後續跑

```bash
lyria-auto resume
```

已完成的曲目不會重新生成、不重複付費。單一曲目失敗不會中斷整批，全部跑完後
會自動對失敗的曲目重試一輪；重試後仍失敗才會中止，且**不會**產出缺料的影片。

每個 job 都有獨立資料夾與 SQLite 記錄，失敗輸出保留供診斷，不會覆蓋既有檔案。

常見訊息：

| 訊息 | 意思 |
|---|---|
| `找不到可續跑的工作` | 沒有未完成的正式工作（dry-run 工作不算） |
| `job N 已經完成` | 保護機制，避免重複產出與重複上傳 |
| `job N 不是 dry-run 工作` | 你對正式工作用了 `--from-job`，改用 `resume` |
| `N 首曲目生成失敗，可用 lyria-auto resume 續跑` | 重試後仍失敗，排除原因後跑 `resume` |
