# Spec：續跑機制與無縫循環

- 建立日期：2026-07-26
- 狀態：已與使用者確認設計，待實作
- 實作者：Sonnet
- 前置條件：專案已可正常執行（`./.venv/bin/lyria-auto run --videos 1` 已驗證成功）

---

## 1. 為什麼要做

使用者的目標是產出 2~4 小時的讀書伴讀長影片，策略是**只生成 1 小時的原創音樂，其餘用循環填滿**。

1 小時素材需要 25 軌，每軌約 $0.08、約 46 秒 API 時間 —— 單次執行約 **$2.00、20 分鐘**。

目前有兩個問題擋住這個規模：

1. **任何一軌失敗，整個 job 作廢。** `run_one` 把整個曲目迴圈包在同一個 `try` 裡；第 20 軌失敗會讓前 19 軌（$1.52）全部白費，且沒有任何續跑路徑（`create_job` 只有一個呼叫點，每次執行必建新 job）。
2. **循環接縫是硬切。** `extend_audio` 用 `-stream_loop -1`，接縫處沒有淡化。4 小時影片會有 3 個突兀的斷點，對專注情境傷害明顯。

---

## 2. 範圍

### 要做

1. 單軌失敗不中斷整批執行，跑完後統一重試
2. 新增 `lyria-auto resume` 指令
3. 拆分 `Pipeline.run_one`，讓 run 與 resume 共用生成／算圖邏輯
4. 循環改為交叉淡化（無縫）

### 明確不要做

- 不要改資料庫 schema（現有欄位已足夠）
- 不要改 Prompt 生成邏輯、`config/prompts.yaml`、`config/settings.yaml`
- 不要動 YouTube 上傳相關程式碼
- 不要為了「一致性」重構無關的檔案
- 不要新增設定項目，除非本文件明確要求

---

## 3. 現況（實作前請先讀過這些位置）

| 位置 | 現況 |
|---|---|
| `src/lyria_auto/pipeline.py:49-190` | `run_one`，140 行，同時做規劃／生成／算圖／上傳 |
| `src/lyria_auto/pipeline.py:105-139` | 曲目迴圈，無容錯 |
| `src/lyria_auto/pipeline.py:57` | `create_job` 唯一呼叫點 |
| `src/lyria_auto/media/audio.py:44-69` | `combine_audio`，acrossfade 串接（已壓測至 48 軌可用） |
| `src/lyria_auto/media/audio.py:71-87` | `extend_audio`，硬切循環 |
| `src/lyria_auto/cli.py:30-34` | `run` 子指令定義 |
| `src/lyria_auto/db.py:23-35` | `tracks` 表，已有 `status` / `audio_path` / `duration_seconds` / `sha256` |

**關鍵事實**：`tracks` 表已存了每軌的 `prompt`，`job_dir/plan.json` 已存了完整 metadata，`job_dir/thumbnail.jpg` 已存在。**續跑不需要重新規劃任何東西。**

---

## 4. 第 1 塊：單軌容錯與重試

改寫 `pipeline.py:105-139` 的曲目迴圈。

### 行為

每一軌各自包 `try`。失敗時：

```python
self.db.update_track(track_id, status="failed", error=str(exc))
self.db.event(job_id, "track_failed", f"track {idx}: {exc}", "ERROR")
# 不 raise，繼續下一軌
```

主迴圈跑完後，查詢本 job 中 `status='failed'` 的軌，**再跑一輪**（同樣的生成邏輯）。

`LyriaClient` 內部本來就有 `max_attempts=3` 的指數退避重試，所以這一輪等於是第 4~6 次機會。**不要在 pipeline 層再加額外的退避 sleep**，直接呼叫即可。

### 重試後仍失敗

- `self.db.update_job(job_id, "failed", <錯誤摘要>)`
- **不要進入算圖階段**，raise `GenerationError`，訊息需包含「可用 `lyria-auto resume` 續跑」
- 理由：寧可讓使用者續跑，也不要產出缺料的成品

### 注意

- `time.sleep(request_delay_seconds)` 只在**實際呼叫過 API 之後**執行；跳過的軌不 sleep
- `SafetyBlockedError` 的既有處理（`safe_rewrite_on_block`）維持不變，只是包進新的 try 裡

---

## 5. 第 2 塊：`lyria-auto resume`

### CLI

在 `cli.py` 新增子指令：

```python
resume = sub.add_parser("resume", help="續跑未完成的工作")
resume.add_argument("--job", type=int, default=None, help="指定 job id，省略則自動找最新一個")
resume.add_argument("--upload", action="store_true")
resume.add_argument("--channel", default="main")
```

處理邏輯放在 `main()` 末段建立 `pipeline` 之後的區塊（與 `run` / `status` 同層），呼叫 `pipeline.resume_one(args.job, args.upload, args.channel)`，輸出格式與 `run` 一致（`json.dumps(..., ensure_ascii=False, indent=2)`）。

### 找出可續跑的 job

新增 `db.py`：

```python
def resumable_job(self, job_id: int | None = None) -> dict[str, Any] | None:
    if job_id is not None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    else:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE dry_run=0 AND status NOT IN ('complete','dry_run_complete') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None
```

找不到時，CLI 印出明確訊息並 `sys.exit(1)`：`找不到可續跑的工作。用 lyria-auto status 查看歷史。`

指定的 job 若 `dry_run=1`，拒絕並說明 dry-run 不需要續跑。

### 判斷哪些軌可以跳過

**三個條件全部成立**才跳過：

1. `status == 'ready'`
2. `audio_path` 非空且 `Path(audio_path).exists()`
3. `probe_audio(audio_path)` 不拋例外，且能取到 `format.duration`

第 3 條是關鍵：程式被 kill 時可能正好寫到一半，只信資料庫會拿到半殘檔。任一條不成立就重新生成該軌。

實作成一個獨立函式，方便測試：

```python
def _track_is_reusable(self, row) -> bool: ...
```

### resume 需要的其他狀態

| 需要什麼 | 從哪裡拿 |
|---|---|
| `job_dir` | `self.workspace / f"job_{job_id:06d}"` |
| 每軌 prompt | `tracks` 表的 `prompt` 欄 |
| metadata | `job_dir/plan.json` 的 `metadata` 欄，用 `Metadata(**d)` 還原 |
| thumbnail | `job_dir/thumbnail.jpg` |
| `video_row_id` | `SELECT id FROM videos WHERE job_id=? ORDER BY id DESC LIMIT 1` |

`plan.json` 不存在時，raise 並說明該 job 無法續跑（規劃階段就失敗了，請重新 `run`）。

**續跑不得重新生成 Prompt、不得重算 metadata、不得重產縮圖。** 成品必須與原本規劃的一致。

---

## 6. 第 3 塊：拆分 `Pipeline.run_one`

拆成四個私有方法。這是為了讓 run 與 resume 共用邏輯的必要拆分，不是順手重構。

```python
def _plan(self, channel_name, upload, dry_run, publish_offset_index) -> JobContext
def _generate(self, job_id, job_dir) -> list[Path]      # run 與 resume 共用
def _render(self, ctx: JobContext, tracks: list[Path]) -> Path
def _upload(self, ctx: JobContext, video_path: Path) -> str

def run_one(self, channel_name, upload, dry_run, publish_offset_index=0) -> dict
def resume_one(self, job_id: int | None = None, upload: bool = False,
               channel_name: str = "main") -> dict
```

`run_one` = `_plan` →（dry_run 則 return）→ `_generate` → `_render` → `_upload`
`resume_one` = 載入既有狀態組出 `JobContext` → `_generate` → `_render` → `_upload`

`JobContext` 定義成 `models.py` 裡的 frozen dataclass（**不要用 dict**），欄位：

```python
@dataclass(frozen=True)
class JobContext:
    job_id: int
    job_dir: Path
    metadata: Metadata
    video_row_id: int
    thumbnail: Path
    channel_cfg: dict
```

`resume_one` 與 `run_one` 都必須回傳相同結構的 dict（含 `job_id` / `directory` / `video` / `youtube_video_id` / `publish_at`），CLI 才能用同一段程式印出結果。

**要求**：
- `_generate` 只依賴 `tracks` 表的 `prompt` 欄，**不要依賴 `PromptPlan` 物件** —— 續跑時沒有那些物件。現行程式的 `zip(plans, track_rows)` 必須拿掉。
- 外層的 `try/except` 包住整個流程、把錯誤寫進 DB 的行為維持不變。
- `run_batch` 維持現有簽名與行為。

---

## 7. 第 4 塊：無縫循環

### `combine_audio` 新增選用參數

```python
def combine_audio(tracks, output_path, crossfade_seconds=2.0, target_seconds: float | None = None) -> Path
```

`target_seconds` 有值時，在輸出檔名前插入 `["-t", f"{target_seconds:.3f}"]`。其餘行為完全不變。

單一輸入的分支（`len(tracks) == 1`）也要套用 `-t`。

### 新增 `loop_audio`

```python
def loop_audio(input_path, output_path, target_seconds, crossfade_seconds=2.0) -> Path:
    duration = <ffprobe 取得>
    if crossfade_seconds >= duration:
        raise MediaError(...)
    step = duration - crossfade_seconds
    copies = max(2, math.ceil(target_seconds / step))
    return combine_audio([input_path] * copies, output_path, crossfade_seconds, target_seconds)
```

同一個檔案重複當多個 `-i` 輸入是合法的，`combine_audio` 現有的 filter 產生邏輯不需修改。

`audio.py` 目前沒有 import `math`，需要補上。

### `extend_audio` 改動

只改「不夠長」那個分支（`audio.py:82-86`）：把 `-stream_loop -1` 換成呼叫 `loop_audio`。

「太長要截斷」的分支（`audio.py:77-81`）與 `abs(duration - target) <= 1` 的提前返回**維持不變**。

### 效能預期

1 小時素材做 4 小時 → 5 份輸入 → 編碼約 5 小時音訊。以 48 軌壓測的實測值（92.7 分鐘 / 140.5 秒）推算約 **7~8 分鐘**。這是預期行為，不是效能問題。

---

## 8. 測試

新增 `tests/test_resume.py` 與 `tests/test_loop.py`。**所有測試都不得呼叫真實 API。**

用 monkeypatch 替換 `pipeline.LyriaClient`，做一個假的 client：記錄呼叫次數，並寫出一個真實可播放的音訊檔。

產生測試音訊用這一行（**用 `sine` 不要用 `anullsrc`** —— 純靜音會讓 `loudnorm` 行為異常）：

```python
subprocess.run([
    "ffmpeg", "-y", "-f", "lavfi",
    "-i", "sine=frequency=440:sample_rate=44100:duration=25",
    "-ac", "2", "-c:a", "libmp3lame", str(out)
], check=True, capture_output=True)
```

25 秒 / 44.1kHz / 立體聲，剛好通過 `validate_audio` 的三道門檻（`minimum_duration_seconds: 20`、`minimum_sample_rate: 44100`、`require_stereo: true`）。

測試用的 `target_duration_minutes` 請在測試中覆寫成小值（例如 2 分鐘），避免每次測試都編碼 10 分鐘音訊。

### 必要測試案例

1. **單軌失敗不中斷**：假 client 在第 2 軌拋例外一次，之後成功 → job 完成，第 2 軌最終 `status='ready'`
2. **重試後仍失敗會中止**：假 client 對第 2 軌永遠失敗 → raise，job `status='failed'`，且**沒有產出 MP4**
3. **resume 不重複呼叫 API**（最重要）：先讓 job 在第 3 軌失敗，記錄呼叫次數 N₁；`resume` 後記錄 N₂。斷言 **N₂ 等於剩餘軌數**，而非總軌數 —— 直接驗證「不重複付費」
4. **半殘檔會被重新生成**：把某個 `status='ready'` 的檔案截斷成 0 bytes → resume 時該軌被重新生成
5. **resume 沿用原文案**：resume 前後 `plan.json` 的 metadata 完全一致
6. **無縫循環長度正確**：60 秒素材做 200 秒 → 產出長度 200±1 秒
7. **`loop_audio` 拒絕不合理輸入**：crossfade ≥ 素材長度時 raise `MediaError`

### 既有測試

`tests/` 現有 5 個測試必須全部維持通過。

---

## 9. 驗收指令

實作完成後，依序執行並確認輸出：

```bash
cd ~/Downloads/Lyria-Auto-Publisher

# 1. 全部測試通過
./.venv/bin/python -m pytest -q

# 2. 環境檢查沒退步
./.venv/bin/lyria-auto doctor

# 3. dry-run 仍可用，文案未改變
./.venv/bin/lyria-auto run --dry-run --videos 1

# 4. resume 在沒有可續跑工作時給出明確訊息（不是 traceback）
./.venv/bin/lyria-auto resume

# 5. 無縫循環實跑（用既有素材，不花錢）
./.venv/bin/python -c "
import sys; sys.path.insert(0,'src')
from lyria_auto.media.audio import loop_audio
p = loop_audio('workspace/job_000004/compilation.m4a', '/tmp/loop_test.m4a', 1800, 2.0)
print(p)
"
ffprobe -v error -show_entries format=duration -of csv=p=0 /tmp/loop_test.m4a   # 應為 1800.0
```

**證據要求**：宣稱完成前，上述每一條都要有實際輸出佐證。測試沒跑就寫「未跑」，失敗就貼失敗輸出。

---

## 10. 已知地雷

實作時容易踩到的，先寫在這裡：

1. **`-t` 是輸出選項**，必須放在輸出檔名之前。放錯位置會變成輸入選項，語意完全不同。
2. **`-shortest` 不足以對齊視訊與音訊長度**。低 fps 下 x264 的 lookahead 佇列會被沖出，導致視訊多出約 40 秒。`video.py` 已修（明確加 `-t`），**不要把它改回去**。
3. **`slugify` 有保留中文**（`一-鿿`），檔名含中文是正常的，不要「修正」成 ASCII。
4. **`.venv` 是用 `python3.13` 建的**。系統預設 `python3` 是 3.9.6，跑不動本專案。一律用 `./.venv/bin/python`。
5. **不要跑 `lyria-auto run`（不含 `--dry-run`）來測試** —— 那會真的呼叫 API 花錢。所有驗證都用 dry-run 或假 client。

---

## 11. 未納入本次範圍

以下是已知但這次不處理的，不要順手做：

- 分批 checkpoint（目前設計是整批跑完才算圖，中途死掉靠 resume 救）
- 平行生成多軌（Lyria 有速率限制，未確認可平行度）
- YouTube OAuth 與上傳流程的驗證
- 標題文案模板的進一步優化
