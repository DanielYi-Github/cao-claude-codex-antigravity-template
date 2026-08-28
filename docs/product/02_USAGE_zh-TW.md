# 使用方式

> 日常操作的完整流程請看 [`00_START_HERE_zh-TW.md`](00_START_HERE_zh-TW.md)，本文件是各指令的參考。

## Prompt 預覽

```bash
lyria-auto prompt-preview --count 20
```

只組合 Prompt，不呼叫 Lyria、不產生費用。

同一支影片的曲目共用同一個曲風、場景、錄音質感與混音風格（讓成品像一張專輯），
只有樂器編制、情緒、速度、環境音逐首變化，所以預覽時看到大量重複欄位是正常的。

## Dry run

```bash
lyria-auto run --dry-run --videos 1
```

會建立：

- `plan.json`
- `thumbnail.jpg`
- SQLite 工作記錄

不呼叫音樂 API，也不上傳 YouTube。

> ⚠️ **看完 dry-run 要用 `--from-job` 轉正，不能只是把 `--dry-run` 拿掉重跑。**
> Prompt 的隨機種子取自 `job_id`，重跑會建立新 job、換新種子，抽到完全不同的組合，
> 等於預覽 A、生成 B。

## 把 dry-run 的計畫實際生成

```bash
lyria-auto run --dry-run --videos 1    # 假設產生 job 9
lyria-auto run --from-job 9            # 就生成 job 9 那一份，一字不差
```

`--from-job` 沿用該工作既有的 `plan.json`、縮圖與曲目列，完全不重新規劃，
也沿用同一個 job 編號（標題裡的集數綁定它）。

`--from-job` 不能與 `--dry-run` 併用；對已是正式工作的 job 使用會被擋下並提示改用 `resume`。

## 真正生成，但不上傳

```bash
lyria-auto run --videos 1
```

輸出位於 `workspace/job_XXXXXX/`。

## 生成並上傳

```bash
lyria-auto run --videos 1 --upload --channel main
```

## 一次建立多支影片

```bash
lyria-auto run --videos 3 --upload --channel main
```

每支影片的預定發布時間會依 `spacing_hours` 往後遞增。

## 中途失敗後續跑

```bash
lyria-auto resume            # 自動接續最近一個未完成的工作
lyria-auto resume --job 7    # 指定工作編號
lyria-auto resume --upload   # 續跑並上傳
```

已完成的曲目不會重新生成，不重複付費。判斷可否重用的三個條件必須同時成立：
資料庫狀態為 `ready`、音檔仍在磁碟上、且能被 `ffprobe` 正確解析
（第三項是為了擋掉程式被中斷時寫到一半的半殘檔）。

已上傳過的工作再 `resume --upload` 不會重複發布，會偵測到既有的 video ID 並跳過。

## 查看狀態

```bash
lyria-auto status --limit 30
```

## 影片長度與曲目數

**曲目數不需要你指定。** 程式會一首一首生成、累加實際長度，湊夠
`generation.max_material_minutes`（預設 60 分）就停；規劃了但用不到的 Prompt
不會送出，也就不花錢。

只要設定影片長度：

```yaml
video:
  target_duration_minutes: 120
```

素材不足的部分會以交叉淡化循環補足，**且絕不從曲子中間切斷** ——
最後一首一定完整播完，所以成品長度是「大約」而非精確值。

因為超過 `max_material_minutes` 的部分一律靠循環，2 小時與 4 小時的影片費用相同。
想要更多不重複的內容，就調高 `max_material_minutes`；
`max_tracks_per_video` 是防止設定失誤的安全上限，通常不用動。

## 大量產出前的建議

先以 1～3 支 private 影片測試，確認成本、模型配額、影片品質與頻道限制後，再提高批次數。
`videos.insert` 每天有上限（預設配額約 100 支/日），排程大量產出前請先確認配額。
