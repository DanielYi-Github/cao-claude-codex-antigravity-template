# 視覺 Pipeline、長片時間軸與安全上線 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把已測試的視覺規劃、圖片／影片審核和 Veo loops 接入既有 Lyria pipeline，以每 30 分鐘 A→B→C→D 循環渲染 2～5 小時影片，並保留舊 job、resume、YouTube 防重複與人工揭露行為。

**Architecture:** `Pipeline` 仍是 orchestrator，但視覺細節委派給 planner、workflow、repository、render preflight、timeline renderer、originality gate 與 resumable uploader。普通 `resume` 只恢復同一份既有工作，不建立 regeneration；任何付費 start 都採 at-most-once。`media.timeline` 依實際最終音訊長度建立全域 scene windows，於每個 30 分鐘邊界 `[B−1s, B+1s]` 執行 2 秒 `xfade`，最後強制與音訊時長一致。沒有 `visual_plan_version` 的舊 job 仍走原本 static video 路徑。

**Tech Stack:** Python 3.11+、FFmpeg／FFprobe、SQLite、google-genai、argparse、APScheduler、pytest、YouTube Data API。

---

## DX review 核准決策索引（D1–D30）

本表是四份計畫的正式決策帳本；若後文舊範例與本表衝突，以本表及標示的
owner plan 為準，實作前必須先修正範例，不得自行選擇較寬鬆行為。

| ID | 核准決策 | Owner／驗證入口 |
|---|---|---|
| D1 | 採 continuous checkpoint；每個 task 的 RED／GREEN／gate 均留下可續跑證據 | 全計畫／各 completion gate |
| D2 | 產品是私人 operator CLI＋automation，不做公開 SaaS | Plan 4／CLI E2E |
| D3 | 主要 persona 是技術型單人 Mac operator；可用 CLI/YAML，但不要求每日看 code、SQLite 或 raw log | Plan 1、4／Mac smoke |
| D4 | 採用既定 empathy narrative：費用、長時間等待、crash 後不確定性是主要焦慮 | 全計畫／error UX |
| D5 | 免費、零 paid call 的首次成功體驗 TTHW < 2 分鐘 | Plan 2／`visual-demo` test |
| D6 | `lyria-auto visual-demo` 使用獨立 DB/workspace，輸出 `SAMPLE / NOT FOR UPLOAD` | Plan 2／demo isolation |
| D7 | DX polish 等級為 POLISH，不接受只靠 log 的操作流 | 全計畫／CLI、review、report |
| D8 | 文件以 macOS bash `\` 多行與安全單行命令為主 | Plan 1、4／docs command tests |
| D9 | 鎖定可驗證的最低 `google-genai`，doctor 只做 capability checks、零 paid call | Plan 1／doctor tests |
| D10 | demo 不建立 provider、Lyria、YouTube objects，且不可誤上傳 | Plan 2／fake object tests |
| D11 | 每個 asset 顯示 x/y、scene/variant、polling、elapsed、Ctrl+C/resume 與下一步 | Plan 2、3／fake-clock tests |
| D12 | guided review 支援原尺寸、selected highlight、QC disabled、完成數、checklist、下一步與成本 | Plan 2、3／HTML/state tests |
| D13 | 日常 paid stages 要求本次精確 allowance；scheduler 是另行 opt-in 且有 caps | Plan 4／CLI/scheduler tests |
| D14 | 每個 30 分鐘邊界中央執行真實 2 秒 xfade，保留音訊總長 | Plan 4／timeline/render tests |
| D15 | poll/download 暫時錯誤保留同一 operation，可恢復而不重送 start | Plan 1、3／resume tests |
| D16 | review 狀態轉移原子化；reject 清 stale IDs，finish backend 重驗 | Plan 2、3／state tests |
| D17 | config migration 有 preview/backup、保留 custom values，visual 必須顯式 enable | Plan 1／migration tests |
| D18 | render 前查 disk/temp/encoder/filter/write 並做 smoke；使用 `.partial`、ETA、atomic rename | Plan 4／render preflight |
| D19 | 上傳前做 channel-level originality gate：pHash、prompt/thumbnail/metadata 與 creative evidence；不保證 YPP | Plan 2、4／originality tests |
| D20 | `setup.sh` 依序找 Python 3.13/3.12/3.11，文件與 doctor 一致 | Plan 1／setup tests |
| D21 | `regenerate` 與 `resume` 是不同命令；resume 永不補生 | Plan 4／parser tests |
| D22 | 錯誤包含 code/problem/cause/fix/next command/job/log，支援 `--verbose`、`--json` | Plan 1、4／error snapshots |
| D23 | 唯一新手入口為 `docs/00_START_HERE_zh-TW.md`，其他文件連回；CLI 範例由 parser 驗證 | Plan 4／docs tests |
| D24 | metrics/report 僅本機保存，不含 prompt/secrets；提供 `lyria-auto report --job` | Plan 4／report tests |
| D25 | `reserved/starting/polling/normalizing` 皆視為 Veo in-flight，不得提前進審核 | Plan 3、4／gate tests |
| D26 | paid start 採 at-most-once、無透明 retry；不確定時 `start_uncertain`，新產出需新 allowance | Plan 1、3／crash tests |
| D27 | preflight approve 重新 hash/decode/probe；不符改 `failed_tampered` 且不可逆 | Plan 1／tamper tests |
| D28 | provider 鎖 Gemini Developer API-key adapter，移除任意 endpoint，保存 effective mode | Plan 1／adapter/source tests |
| D29 | 全域邊界 window 為 `[B−1,B+1]`、xfade offset `B−1`，45m/2h/5h 最終誤差 ≤ 1 frame | Plan 4／FFmpeg tests |
| D30 | YouTube 在第一個 byte 前保存 resumable session URI、file SHA、metadata hash；以 308 續傳且先驗 `longUploadsStatus=allowed` | Plan 4／uploader tests |

## 跨計畫依賴與工程進入條件

```text
Plan 1 foundation/preflight
  └─ gate: adapter、migration、at-most-once、raw integrity
      └─ Plan 2 planning/image review
          └─ gate: demo isolation、image atomic review、pHash
              └─ Plan 3 Veo/video review
                  └─ gate: operation persistence、in-flight、video atomic review
                      └─ Plan 4 pipeline/render/upload rollout
```

- Plan 2 不得在 Plan 1 completion gate 前實作；Plan 3 不得在 Plan 2 gate 前實作。
- Plan 4 可以先實作 pure timeline／local render tests，但接 pipeline 前必須通過 Plan 1–3 gates。
- 四份 migration 使用同一 registry 與單調 schema version；upgrade/preview/rollback 測試不得各自另造 migration runner。
- 共用狀態常數、structured error、progress reporter、hash/probe 與 authorization model
  必須由先行計畫提供，後續計畫只擴充，不複製第二套。
- 工程審查先從本表、下方 state/CLI/render/upload contracts、Code completion gate
  與 Paid private rollout acceptance gate 開始，再沿 owner plan 查對應 RED tests。

### Paid side-effect ownership contract

所有 paid image／video start 必須遵守：

1. worker 先取得 SQLite job lease，保存 `owner_token`、`heartbeat_at`、`expires_at`。
2. 以 `BEGIN IMMEDIATE` 開始短 transaction。
3. 驗證精確 `PaidStageAuthorization` 尚有對應 start intent。
4. 以 CAS 將 asset 從 `reserved` 改為 `starting`：
   `WHERE id=? AND status='reserved' AND state_version=?`。
5. 在同一 transaction 寫入唯一 authorization consumption。
6. 只有 affected row 為 1 時才 commit；敗方不得建立 provider object。
7. provider call 在 transaction 外執行，避免長時間鎖 DB。
8. operation ID／結果只能由相同 `owner_token` 以 CAS 保存。
9. lease 尚有效時，其他 worker 不可 reconcile。
10. lease 過期且新 process 重新開啟 DB 後，`starting` 且沒有 operation ID
    才轉為 `start_uncertain`。

`start_uncertain` 不可由普通 resume 重送；補生只能使用 `regenerate`
與新的精確 allowance。scheduler 的 `max_instances=1` 只保護單一 scheduler
process，不可取代 SQLite lease、CAS 或 authorization consumption。

### Durable state transition contract

- 所有 status 使用集中 enum，不接受任意字串。
- SQLite schema 對新表 status 加 `CHECK` constraint；既有 `jobs`／`videos`
  使用 Plan 1 的 migration-owned `state_catalog` validation trigger。可變 state row 保存
  `state_version INTEGER NOT NULL DEFAULT 0`。
- 所有 transition 使用 expected status、expected version 與 owner token CAS。
- `transition_job(job_id, to_status)` 是唯一可省略 caller-provided version 的 API：
  它必須在單一 `BEGIN IMMEDIATE` 中讀取 current status/version、驗證 transition
  table，再以同一 version CAS 更新；不得退化成無條件 `WHERE id=?`。
- affected row 不是 1 時回 structured stale-state error，不可覆蓋最新狀態。
- `failed_tampered`、`start_uncertain`、`upload_uncertain` 不可被普通
  approve／resume 翻回 ready。
- review POST 必須攜帶 `state_version`，防止 stale browser tab 覆蓋新狀態。
- 關鍵 transition 寫 audit event，包含 from/to、owner、attempt ID，但不保存
  prompt、secret 或 session URI。

### Immutable artifact identity contract

通過 hash／decode／probe 的檔案必須先固定為 content-addressed immutable
snapshot，再供 approve、render、originality 與 upload 使用：

- snapshot 使用同 filesystem temp file 寫入後 atomic rename。
- identity 至少包含 SHA-256、size 與 media probe result。
- DB、render manifest、originality review、upload attempt 只引用 snapshot identity。
- 不得在驗證後重新依原始可變 path 開檔；upload 使用已驗證 snapshot 的固定 file handle。
- identity 不符時轉為 `failed_tampered`，且不可逆。

### Schema migration contract

所有 Plan 共用唯一 `schema_migrations` registry：

- migration 有固定 monotonic ID、名稱與 checksum。
- 每個 migration 在單一 transaction 執行，成功後才寫入 `applied_at`；失敗全部 rollback。
- `PRAGMA table_info` 只作 defensive assertion，不作 migration registry。
- preview 顯示 pending migration IDs 與變更，但不寫 DB。
- 測試覆蓋 Plan 0→1→2→3→4 每一跳、完整直升、重跑、中途失敗與 custom values 保留。

---

## Scope and file map

| File | Responsibility |
|---|---|
| `src/lyria_auto/media/timeline.py` | 純時間軸規劃與動態長片 FFmpeg render |
| `src/lyria_auto/media/render_preflight.py` | disk/temp/filter/encoder/write smoke 與容量預估 |
| `src/lyria_auto/visual_models.py` | VisualPlan deserialization、TimelineSlice |
| `src/lyria_auto/db.py` | job error 不破壞 stage、job lookup |
| `src/lyria_auto/pipeline.py` | 視覺 state machine、兩個 pause gate、legacy branch |
| `src/lyria_auto/cli.py` | `review`、`resume`、`regenerate`、`report`、狀態輸出 |
| `src/lyria_auto/scheduler.py` | 只推進至人工 gate，不自動越過 |
| `src/lyria_auto/originality.py` | channel-level 相似度 gate 與 creative evidence |
| `src/lyria_auto/providers/youtube.py` | session-first YouTube resumable upload |
| `tests/conftest.py` | legacy 與 visual 兩套 fixtures |
| `tests/test_visual_timeline.py` | 45m、2h、2.5h、4h、5h sequence 與全域邊界 |
| `tests/test_visual_render.py` | xfade、短時 FFmpeg 整合與音視訊長度 |
| `tests/test_render_preflight.py` | disk、codec/filter、smoke、partial/atomic output |
| `tests/test_visual_pipeline.py` | plan→image gate→video gate→audio→render |
| `tests/test_visual_cli.py` | review／resume／regenerate／allowance arguments |
| `tests/test_originality_gate.py` | recent-channel 相似度與人工 creative evidence |
| `tests/test_youtube_resumable.py` | session persistence、308、hash 與 eligibility |
| `tests/test_report.py` | local-only metrics/redaction |
| `tests/test_visual_legacy.py` | 舊 job static path |
| `README.md`、`docs/*.md` | 操作、成本、故障與揭露 |

全計畫限制：

- dry-run 不呼叫 Gemini 圖片、Veo 或 Lyria。
- 沒有有效 preflight 時，正式 image／video stage 必須在任何付費呼叫前停止。
- `awaiting_image_review` 與 `awaiting_video_review` 都是正常 pause，不是 failed。
- 視覺 stage 發生錯誤時保留 stage，只更新 `jobs.error`；legacy 行為維持原本 failed。
- 音樂只在全部視覺核准後生成。
- 最終長度跟隨 `compilation_extended.m4a` 的實際長度，不在曲中硬切。
- 2 小時以上最多只使用四個唯一視覺 asset；5 小時順序為 A B C D A B C D A B。
- 日常付費 image／video stage 也須輸入本次預期 start intent 的精確 allowance；
  `estimated_budget_usd` 是第二層上限，不能把 CLI allowance 永久寫回設定。
- `resume` 僅恢復既有 asset/operation/local stage；補生只能走獨立 `regenerate`。
- 有任何 video in-flight 時保持 `generating_videos`；`start_uncertain` 顯示人工 blocker。
- 舊 `plan.json` 沒有 `visual_plan_version` 時使用 `create_static_video()`。
- render 只寫 `.partial`，通過 probe/length 後原子 rename；不得留下看似完成的壞檔。
- originality gate 與 `containsSyntheticMedia: true` 只能降低風險，不承諾 YPP 或最大收益。

---

### Task 1: Pure scene timeline for arbitrary audio durations

**Files:**
- Modify: `src/lyria_auto/visual_models.py`
- Create: `src/lyria_auto/media/timeline.py`
- Create: `tests/test_visual_timeline.py`

- [ ] **Step 1: Write failing sequence tests**

建立 `tests/test_visual_timeline.py`：

```python
from __future__ import annotations

import pytest

from lyria_auto.media.timeline import build_timeline


SCENES = [
    ("A", "a.mp4"),
    ("B", "b.mp4"),
    ("C", "c.mp4"),
    ("D", "d.mp4"),
]


@pytest.mark.parametrize(
    ("minutes", "labels", "last_window"),
    [
        (45, ["A", "B"], (1799, 2700)),
        (120, ["A", "B", "C", "D"], (5399, 7200)),
        (150, ["A", "B", "C", "D", "A"], (7199, 9000)),
        (240, ["A", "B", "C", "D", "A", "B", "C", "D"], (12599, 14400)),
        (300, ["A", "B", "C", "D", "A", "B", "C", "D", "A", "B"], (16199, 18000)),
    ],
)
def test_scene_sequence_and_global_windows(minutes, labels, last_window):
    timeline = build_timeline(minutes * 60, SCENES, interval_seconds=1800)

    assert [item.label for item in timeline] == labels
    assert (
        timeline[-1].global_start_seconds,
        timeline[-1].global_end_seconds,
    ) == last_window
    assert timeline[0].global_start_seconds == 0
    assert timeline[-1].global_end_seconds == minutes * 60


def test_45_minute_boundary_uses_two_second_overlap():
    timeline = build_timeline(45 * 60, SCENES, interval_seconds=1800)

    assert (timeline[0].global_start_seconds, timeline[0].global_end_seconds) == (0, 1801)
    assert (timeline[1].global_start_seconds, timeline[1].global_end_seconds) == (1799, 2700)
    assert transition_offsets(timeline) == [1799]


def test_timeline_rejects_no_scenes():
    with pytest.raises(ValueError, match="場景"):
        build_timeline(60, [], interval_seconds=30)
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_timeline.py -v
```

Expected: `media.timeline` import fails.

- [ ] **Step 3: Add TimelineSlice and VisualPlan.from_dict**

在 `src/lyria_auto/visual_models.py` 加入：

```python
@dataclass(frozen=True)
class TimelineSlice:
    label: str
    path: Path
    global_start_seconds: float
    global_end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.global_end_seconds - self.global_start_seconds
```

在 `VisualPlan` 加入：

```python
    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "VisualPlan":
        return cls(
            version=int(value["version"]),
            world=WorldBible(**value["world"]),
            world_anchor_prompt=str(value["world_anchor_prompt"]),
            scenes=tuple(ScenePlan(**scene) for scene in value["scenes"]),
            thumbnail_prompt=str(value["thumbnail_prompt"]),
        )
```

- [ ] **Step 4: Implement timeline builder**

建立 `src/lyria_auto/media/timeline.py`：

```python
from __future__ import annotations

from pathlib import Path

from ..visual_models import TimelineSlice


def build_timeline(
    total_seconds: float,
    scenes: list[tuple[str, str | Path]],
    *,
    interval_seconds: float = 1800,
) -> list[TimelineSlice]:
    if total_seconds <= 0:
        raise ValueError("影片長度必須大於 0")
    if not scenes:
        raise ValueError("至少需要一個核准場景")
    if interval_seconds <= 0:
        raise ValueError("換景間隔必須大於 0")
    result = []
    index = 0
    logical_start = 0.0
    while logical_start < total_seconds:
        logical_end = min(logical_start + interval_seconds, total_seconds)
        label, path = scenes[index % len(scenes)]
        global_start = logical_start - 1.0 if logical_start > 0 else 0.0
        global_end = logical_end + 1.0 if logical_end < total_seconds else total_seconds
        result.append(
            TimelineSlice(label, Path(path), global_start, global_end)
        )
        logical_start = logical_end
        index += 1
    return result


def transition_offsets(timeline: list[TimelineSlice]) -> list[float]:
    # 第 n 個輸入的 global start 就是邊界 B−1；可直接餵給 chained xfade。
    return [item.global_start_seconds for item in timeline[1:]]
```

此資料模型只描述全域時間，不試圖把 overlap durations 相加成影片長度。對任一
內部邊界 `B`，前一 slice 結束於 `B+1`、下一 slice 開始於 `B−1`；因此兩者恰有
2 秒重疊，視覺主切換點在 `B`。45 分鐘、2 小時、5 小時都必須驗證此 invariant。

- [ ] **Step 5: Run timeline tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_timeline.py -v
```

Expected: all parametrized cases pass.

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/visual_models.py src/lyria_auto/media/timeline.py tests/test_visual_timeline.py
git commit -m "feat: build repeating thirty-minute scene timelines"
```

---

### Task 2: Render approved loops against actual final audio length

**Files:**
- Modify: `src/lyria_auto/media/timeline.py`
- Create: `tests/test_visual_render.py`

- [ ] **Step 1: Write failing command and short-media tests**

建立 `tests/test_visual_render.py`：

```python
from __future__ import annotations

import subprocess
from pathlib import Path

from lyria_auto.media.timeline import create_timeline_video
from lyria_auto.media.visual_quality import probe_video


def make_loop(path, color):
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c={color}:s=320x180:r=24:d=2",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )


def make_audio(path, duration):
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-ac", "2", "-c:a", "aac", str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_short_timeline_video_matches_audio_and_has_no_loop_audio(tmp_path):
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    audio = tmp_path / "audio.m4a"
    output = tmp_path / "output.mp4"
    make_loop(a, "red")
    make_loop(b, "blue")
    make_audio(audio, 7)

    create_timeline_video(
        [("A", a), ("B", b)],
        audio,
        output,
        width=320,
        height=180,
        fps=24,
        audio_bitrate="128k",
        preset="veryfast",
        interval_seconds=2,
    )

    info = probe_video(output)
    duration = float(info["format"]["duration"])
    streams = info["streams"]
    assert abs(duration - 7.0) <= (1 / 24)
    assert len([s for s in streams if s["codec_type"] == "video"]) == 1
    assert len([s for s in streams if s["codec_type"] == "audio"]) == 1
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_render.py -v
```

Expected: `create_timeline_video` import fails.

- [ ] **Step 3: Implement one-pass FFmpeg xfade rendering**

在 `src/lyria_auto/media/timeline.py` imports 加入：

```python
from .audio import probe_audio
from .render_preflight import require_render_ready
from ..utils import run_command
```

在同檔末尾加入：

```python
def create_timeline_video(
    scenes: list[tuple[str, str | Path]],
    audio_path: str | Path,
    output_path: str | Path,
    *,
    width: int,
    height: int,
    fps: int,
    audio_bitrate: str,
    preset: str,
    interval_seconds: float = 1800,
) -> Path:
    duration = float(
        probe_audio(audio_path).get("format", {}).get("duration") or 0
    )
    timeline = build_timeline(
        duration, scenes, interval_seconds=interval_seconds
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")
    require_render_ready(
        audio_path=Path(audio_path),
        output_path=output,
        duration_seconds=duration,
        width=width,
        height=height,
        fps=fps,
    )
    args = ["ffmpeg", "-y"]
    for item in timeline:
        args += ["-stream_loop", "-1", "-i", str(item.path)]
    audio_index = len(timeline)
    args += ["-i", str(audio_path)]
    filters = []
    for index, item in enumerate(timeline):
        label = f"v{index}"
        filters.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps},trim=duration={item.duration_seconds:.3f},"
            f"setpts=PTS-STARTPTS,format=yuv420p[{label}]"
        )
    current = "v0"
    for index, offset in enumerate(transition_offsets(timeline), start=1):
        next_label = f"vx{index}"
        filters.append(
            f"[{current}][v{index}]"
            f"xfade=transition=fade:duration=2:offset={offset:.3f}"
            f"[{next_label}]"
        )
        current = next_label
    args += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{current}]", "-map", f"{audio_index}:a:0",
        "-c:v", "libx264", "-preset", preset,
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-pix_fmt", "yuv420p", "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(partial),
    ]
    run_command(args, on_progress=RenderProgress(duration))
    verify_render(partial, expected_duration=duration, max_error_frames=1, fps=fps)
    partial.replace(output)
    return output
```

- [ ] **Step 4: Add command-shape and real-boundary tests**

在 `tests/test_visual_render.py` 加入：

```python
def test_renderer_reuses_four_paths_for_ten_slices(monkeypatch, tmp_path):
    captured = {}
    def fake_run(args, *, on_progress):
        captured["args"] = args
        Path(args[-1]).write_bytes(b"fake mp4")

    monkeypatch.setattr(
        "lyria_auto.media.timeline.probe_audio",
        lambda path: {"format": {"duration": str(5 * 60 * 60)}},
    )
    monkeypatch.setattr(
        "lyria_auto.media.timeline.require_render_ready",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "lyria_auto.media.timeline.verify_render",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "lyria_auto.media.timeline.run_command", fake_run,
    )

    create_timeline_video(
        [("A", "a.mp4"), ("B", "b.mp4"), ("C", "c.mp4"), ("D", "d.mp4")],
        "audio.m4a",
        tmp_path / "out.mp4",
        width=1920,
        height=1080,
        fps=24,
        audio_bitrate="256k",
        preset="veryfast",
    )

    args = captured["args"]
    inputs = [args[index + 1] for index, value in enumerate(args) if value == "-i"]
    assert inputs[:-1] == [
        "a.mp4", "b.mp4", "c.mp4", "d.mp4",
        "a.mp4", "b.mp4", "c.mp4", "d.mp4",
        "a.mp4", "b.mp4",
    ]
    graph = args[args.index("-filter_complex") + 1]
    assert "concat=" not in graph
    assert graph.count("xfade=") == 9
    assert "duration=2:offset=1799.000" in graph
    assert "duration=2:offset=16199.000" in graph
    assert args[args.index("-t") + 1] == "18000.000"
    assert str(tmp_path / "out.partial.mp4") == args[-1]
```

另外用 FFmpeg lavfi 產生紅、藍兩段 loop 與 7 秒音訊，`interval_seconds=4`：

- 擷取 2.5s frame 應仍為紅色主導。
- 擷取 4.0s frame 應為紅藍混合（crossfade 中央）。
- 擷取 5.5s frame 應為藍色主導。
- output 只有一個 video／一個 audio stream，總長與音訊誤差不得超過 `1/fps`。
- 同一測試矩陣以 command/probe 驗證 45m、2h、5h 的最後 `-t` 與 xfade offsets；
  長時測試 mock encoder，不真的渲染五小時。

Ctrl+C 或 FFmpeg failure 後只允許保留 `*.partial.mp4` 診斷檔，不得覆蓋既有正式
output；下一次 render 先驗證 input hash，再安全重建 partial。成功 rename 後
partial 必須不存在。

- [ ] **Step 5: Run render tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_render.py tests/test_visual_timeline.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/media/timeline.py tests/test_visual_render.py
git commit -m "feat: render approved loops across long audio"
```

---

### Task 3: Pipeline visual plan and two normal pause gates

**Files:**
- Modify: `src/lyria_auto/db.py`
- Modify: `src/lyria_auto/models.py`
- Modify: `src/lyria_auto/pipeline.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_visual_pipeline.py`

- [ ] **Step 1: Add separate legacy and visual fixtures**

把 `tests/conftest.py::project_config` 在寫回 settings 前改為：

```python
    settings["visual"]["enabled"] = False
```

再加入：

```python
@pytest.fixture
def visual_project_config(project_config):
    settings_path = project_config.root / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    settings["visual"]["enabled"] = True
    settings["visual"]["poll_interval_seconds"] = 0
    settings_path.write_text(
        yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8"
    )
    return load_config(project_config.root)
```

這讓既有 pipeline tests 繼續驗證 legacy，而新測試明確選用視覺路徑。

- [ ] **Step 2: Write failing state-machine integration test**

建立 `tests/test_visual_pipeline.py`：

```python
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from lyria_auto.pipeline import Pipeline
from lyria_auto.utils import sha256_file
from lyria_auto.visual_models import GeneratedImage, VideoPoll, VideoQC


class FakeVisualClient:
    def __init__(self):
        self.image_calls = 0
        self.video_starts = 0

    def generate_image(self, prompt, output_path, *, reference_paths):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (2048, 1152), (80, 60, 40))
        draw = ImageDraw.Draw(image)
        for x in range(0, 2048, 64):
            color = (110, 85, 55) if (x // 64) % 2 else (45, 60, 40)
            draw.rectangle((x, 0, min(x + 63, 2047), 1151), fill=color)
        image.save(output, "PNG")
        self.image_calls += 1
        return GeneratedImage(output, "image/png", sha256_file(output))

    def start_video(self, prompt, frame_path):
        self.video_starts += 1
        return f"operations/{self.video_starts}"

    def poll_video(self, operation_id, output_path):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"raw")
        return VideoPoll(True, output, sha256_file(output))


class AlwaysValidPreflight:
    def require_valid_preflight(self):
        return None


def fake_normalize(raw, normalized, **kwargs):
    output = Path(normalized)
    output.write_bytes(b"loop")
    return output


def fake_inspect(path, **kwargs):
    return VideoQC(True, 1920, 1080, 8.0, 24.0, 0.01, 0.01, "ok")


def approve_all_images(pipeline, job_id):
    repo = pipeline.visual_repo
    for scene in repo.scenes(job_id):
        asset = next(
            row for row in repo.assets(job_id, asset_type="scene_image")
            if row["scene_id"] == scene["id"] and row["status"] == "ready"
        )
        repo.select_image(int(scene["id"]), int(asset["id"]))
    repo.finish_image_review(job_id)


def approve_all_videos_and_thumbnail(pipeline, job_id):
    repo = pipeline.visual_repo
    for scene in repo.scenes(job_id):
        asset = next(
            row for row in repo.assets(job_id, asset_type="scene_video")
            if row["scene_id"] == scene["id"] and row["status"] == "ready"
        )
        repo.select_video(int(scene["id"]), int(asset["id"]))
    thumbnail = repo.assets(job_id, asset_type="thumbnail_background")[0]
    repo.approve_thumbnail(job_id, int(thumbnail["id"]))
    repo.finish_video_review(job_id)


def test_pipeline_pauses_at_images_then_videos_before_audio(
    visual_project_config, fake_lyria, monkeypatch
):
    fake_visual = FakeVisualClient()
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    pipeline = Pipeline(
        visual_project_config,
        visual_client=fake_visual,
        preflight=AlwaysValidPreflight(),
        normalize_video=fake_normalize,
        inspect_video_fn=fake_inspect,
    )
    try:
        first = pipeline.run_one(
            "main",
            upload=False,
            dry_run=False,
            paid_authorizations=StageAllowances(images=13, videos=0),
        )
        assert first["status"] == "awaiting_image_review"
        assert fake_visual.image_calls == 13
        assert fake_visual.video_starts == 0
        assert fake_lyria.calls == []

        approve_all_images(pipeline, first["job_id"])
        second = pipeline.resume_one(
            first["job_id"],
            allow_image_outputs=1,
            allow_video_outputs=8,
        )
        assert second["status"] == "awaiting_video_review"
        assert fake_visual.image_calls == 14
        assert fake_visual.video_starts == 8
        assert fake_lyria.calls == []

        approve_all_videos_and_thumbnail(pipeline, first["job_id"])
        final = pipeline.resume_one(first["job_id"])
        assert final["status"] == "rendered"
        assert Path(final["video"]).exists()
        assert fake_lyria.calls
    finally:
        pipeline.close()


def test_audio_and_upload_stages_resume_without_restarting_visuals_or_render(
    visual_project_config, fake_lyria, monkeypatch
):
    fake_visual = FakeVisualClient()
    monkeypatch.setattr(
        "lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory()
    )
    pipeline = Pipeline(
        visual_project_config,
        visual_client=fake_visual,
        preflight=AlwaysValidPreflight(),
        normalize_video=fake_normalize,
        inspect_video_fn=fake_inspect,
    )
    try:
        first = pipeline.run_one(
            "main",
            upload=False,
            dry_run=False,
            paid_authorizations=StageAllowances(images=13, videos=0),
        )
        approve_all_images(pipeline, first["job_id"])
        pipeline.resume_one(
            first["job_id"],
            allow_image_outputs=1,
            allow_video_outputs=8,
        )
        approve_all_videos_and_thumbnail(pipeline, first["job_id"])

        real_generate = pipeline._generate

        def fail_audio_once(*args, **kwargs):
            pipeline.db.transition_job(
                first["job_id"], kwargs["job_status"]
            )
            raise RuntimeError("temporary audio failure")

        monkeypatch.setattr(pipeline, "_generate", fail_audio_once)
        with pytest.raises(RuntimeError, match="temporary audio failure"):
            pipeline.resume_one(first["job_id"])
        assert pipeline.db.job(first["job_id"])["status"] == "generating_audio"
        assert fake_visual.image_calls == 14
        assert fake_visual.video_starts == 8

        monkeypatch.setattr(pipeline, "_generate", real_generate)
        final = pipeline.resume_one(first["job_id"])
        assert final["status"] == "rendered"

        pipeline.db.transition_job(first["job_id"], "uploading")

        def forbidden(*args, **kwargs):
            raise AssertionError("existing rendered video must be reused")

        monkeypatch.setattr(pipeline, "_generate", forbidden)
        monkeypatch.setattr(pipeline, "_render_visual", forbidden)
        resumed = pipeline.resume_one(first["job_id"], upload=False)

        assert resumed["status"] == "rendered"
        assert resumed["video"] == final["video"]
        assert fake_visual.image_calls == 14
        assert fake_visual.video_starts == 8
    finally:
        pipeline.close()
```

- [ ] **Step 3: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_pipeline.py -v
```

Expected: `Pipeline.__init__` rejects visual dependencies.

- [ ] **Step 4: Add job lookup and stage-preserving errors**

在 `StateDB` 加入：

```python
    def job(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE id=?", (job_id,)
        ).fetchone()

    def video(self, video_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM videos WHERE id=?", (video_id,)
        ).fetchone()

    def set_job_error(
        self,
        job_id: int,
        expected_version: int,
        error: StructuredError | None,
    ) -> None:
        cur = self.conn.execute(
            """
            UPDATE jobs
            SET error=?,state_version=state_version+1,updated_at=?
            WHERE id=? AND state_version=?
            """,
            (
                render_error(error) if error else None,
                utc_now_iso(),
                job_id,
                expected_version,
            ),
        )
        if cur.rowcount != 1:
            self.conn.rollback()
            raise StaleStateError("job error update lost CAS")
        self.conn.commit()
```

- [ ] **Step 5: Add visual dependencies and planning**

在 `pipeline.py` imports 加入：

```python
from .media.timeline import create_timeline_video
from .media.thumbnail import create_visual_thumbnail
from .media.visual_quality import inspect_video as inspect_visual_video
from .media.visual_quality import normalize_loop_video
from .providers.gemini_visual import GeminiVisualClient
from .visual_models import VisualPlan
from .visual_planner import VisualPlanner
from .visual_preflight import VisualPreflightService
from .visual_repository import VisualRepository
from .visual_workflow import ImageGenerationWorkflow, VideoGenerationWorkflow
```

把 `Pipeline.__init__` 改為：

```python
    def __init__(
        self,
        config: AppConfig,
        *,
        visual_client=None,
        preflight=None,
        normalize_video=normalize_loop_video,
        inspect_video_fn=inspect_visual_video,
    ):
        self.config = config
        project = config.section("project")
        self.workspace = ensure_dir(
            config.root / project.get("workspace", "workspace")
        )
        self.db = StateDB(
            config.root / project.get("database", "workspace/state.sqlite3")
        )
        self.visual_repo = VisualRepository(self.db)
        self._visual_client_override = visual_client
        self._preflight_override = preflight
        self._normalize_video = normalize_video
        self._inspect_video = inspect_video_fn

    def _inspect_visual_loop(self, path):
        visual = self.config.section("visual")
        quality = visual["quality"]
        return self._inspect_video(
            path,
            seam_max=float(quality["seam_max_normalized_mae"]),
            motion_min=float(quality["motion_min_normalized_mae"]),
            motion_max=float(quality["motion_max_normalized_mae"]),
            duration_target=float(visual["video_duration_seconds"]),
            duration_tolerance=float(
                quality["duration_tolerance_seconds"]
            ),
        )
```

在 `_plan()` 的 `plan_payload` 寫入前加入：

```python
        if self.config.section("visual").get("enabled", False):
            visual_plan = VisualPlanner(seed=job_id).compose(
                representative, target_minutes
            )
            plan_payload["visual_plan_version"] = 1
            plan_payload["visual_plan"] = visual_plan.to_dict()
            for scene in visual_plan.scenes:
                self.visual_repo.upsert_scene(
                    job_id,
                    scene.position,
                    scene.label,
                    visual_plan.to_dict()["world"],
                    scene.image_prompt,
                    scene.motion_prompt,
                )
```

在 `_plan()` return 前、thumbnail 建立後加入：

```python
        self.db.transition_job(job_id, "planned")
```

- [ ] **Step 6: Let audio generation report the visual stage without changing legacy behavior**

把 `_generate()` signature 改為：

```python
    def _generate(
        self,
        job_id: int,
        job_dir: Path,
        *,
        job_status: str = "generating",
        preserve_stage_on_error: bool = False,
    ) -> list[Path]:
```

把方法開頭的：

```python
        self.db.transition_job(job_id, "generating")
```

改成：

```python
        self.db.transition_job(job_id, job_status)
```

在 `_generate()` 前加入：

```python
    def _record_generation_error(
        self, job_id: int, summary: str, preserve_stage_on_error: bool
    ) -> None:
        if preserve_stage_on_error:
            row = self.db.job(job_id)
            self.db.set_job_error(
                job_id,
                expected_version=int(row["state_version"]),
                error=structured_error_from_summary(summary),
            )
        else:
            self.db.transition_job(job_id, "failed", error=summary)
```

把 `_generate()` 中兩處：

```python
            self.db.transition_job(job_id, "failed", error=summary)
```

都改成：

```python
            self._record_generation_error(
                job_id, summary, preserve_stage_on_error
            )
```

legacy 呼叫不傳新參數，仍使用 `generating`／`failed`；視覺呼叫傳 `generating_audio` 並保留該 stage。

- [ ] **Step 7: Split legacy execution and add the visual state machine**

把既有 `_execute()` 內容搬到 `_execute_legacy()`：

```python
    def _execute_legacy(
        self, job_id: int, job_dir: Path, ctx: JobContext, upload: bool
    ) -> dict[str, Any]:
        tracks = self._generate(job_id, job_dir)
        video_path = self._render(ctx, tracks)
        youtube_id = self._upload(ctx, video_path) if upload else None
        self.db.transition_job(job_id, "complete")
        return {
            "job_id": job_id,
            "status": "complete",
            "directory": str(job_dir),
            "video": str(video_path),
            "youtube_video_id": youtube_id,
            "publish_at": ctx.metadata.publish_at,
        }
```

新增 helpers：

```python
    def _load_plan_payload(self, job_dir: Path) -> dict[str, Any]:
        return json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))

    def _visual_client(self):
        if self._visual_client_override is not None:
            return self._visual_client_override
        visual = self.config.section("visual")
        return GeminiVisualClient(
            api_key=None,
            image_model=str(visual["image_model"]),
            video_model=str(visual["video_model"]),
        )

    def _preflight(self):
        if self._preflight_override is not None:
            return self._preflight_override
        return VisualPreflightService(self.config, self.db)

    def _paused(self, job_id: int, job_dir: Path, status: str) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "status": status,
            "directory": str(job_dir),
            "next": f"lyria-auto review --job {job_id}",
        }

    def _all_scenes_status(self, job_id: int, status: str) -> bool:
        scenes = self.visual_repo.scenes(job_id)
        return bool(scenes) and all(row["status"] == status for row in scenes)

    def _existing_rendered_video(self, ctx: JobContext) -> Path | None:
        row = self.db.conn.execute(
            "SELECT video_path,status FROM videos WHERE id=?",
            (ctx.video_row_id,),
        ).fetchone()
        if row is None or row["status"] not in ("rendered", "uploaded"):
            return None
        path = Path(row["video_path"] or "")
        return path if path.is_file() and path.stat().st_size > 0 else None

    def _require_default_visual_budget(
        self, job_id: int, stage: str, additional_cost: float
    ) -> None:
        visual = self.config.section("visual")
        current = self.visual_repo.estimated_cost(job_id)
        projected = current + additional_cost
        limit = float(visual["estimated_budget_usd"])
        logger.info(
            "%s：目前 US$%.3f，新增 US$%.3f，預估總額 US$%.3f",
            stage, current, additional_cost, projected,
        )
        if projected > limit:
            raise LyriaAutoError(
                f"{stage} 預估總額 US${projected:.2f} 超過 "
                f"US${limit:.2f} 預算，尚未呼叫付費 API"
            )
```

沿用 Plan 1 持久化的 `PaidStageAuthorization`／`paid_start_intents`：

- key 為 `(job_id, stage, kind)`，保存 expected、allowed、consumed start intents、created_at；
  secrets 不可進資料庫。
- `initial_images` 預期 `1 + scene_count * image_candidates_per_scene` 張；
  `initial_videos` 預期 1 張 thumbnail image 與
  `scene_count * video_candidates_per_scene` 段 video。
- 只有 CLI 本次輸入與 expected 完全相等才建立 authorization；多、少、負數均在
  provider object 建立前失敗。
- `_require_or_resume_stage_authorization()` 回傳依 kind 分開的 bundle；workflow
  必須明確接收對應 authorization，不可只在 pipeline 區域變數驗證後丟棄。
- crash/resume 讀取同一 authorization 與既有 asset identities，不要求第二次輸入
  allowance，也不建立新輸出；每個 start intent 在 asset CAS 的同一 transaction
  消耗一次，`start_uncertain` 仍算已消耗。
- authorization 不是永久設定，也不能跨 job／stage／kind 使用。scheduler authorization
  由 Task 5 的獨立 opt-in caps 建立，不冒用人工 CLI authorization。

用以下 `_execute()` 取代原方法：

```python
    def _execute(
        self,
        job_id: int,
        job_dir: Path,
        ctx: JobContext,
        upload: bool,
        paid_authorizations: StageAllowances | None = None,
    ) -> dict[str, Any]:
        payload = self._load_plan_payload(job_dir)
        if payload.get("visual_plan_version") != 1:
            return self._execute_legacy(job_id, job_dir, ctx, upload)
        visual = self.config.section("visual")
        plan = VisualPlan.from_dict(payload["visual_plan"])
        state = str(self.db.job(job_id)["status"])
        try:
              if state in ("planning", "planned", "generating_images"):
                  self._preflight().require_valid_preflight()
                  image_outputs = 1 + (
                    len(plan.scenes)
                    * int(visual["image_candidates_per_scene"])
                )
                authorization = self._require_or_resume_stage_authorization(
                    job_id,
                    stage="initial_images",
                    expected_images=image_outputs,
                    expected_videos=0,
                    supplied=paid_authorizations,
                )
                  self._require_default_visual_budget(
                    job_id,
                    f"圖片階段 {image_outputs} 張",
                      image_outputs * float(visual["image_2k_estimated_usd"]),
                  )
                  lease = self.db.acquire_side_effect_lease(
                      scope_type="job", scope_id=job_id
                  )
                  if not lease.acquired:
                      return self._already_running(job_id, lease)
                  client = self._visual_client()
                  self.db.transition_job(job_id, "generating_images")
                images = ImageGenerationWorkflow(
                    self.visual_repo,
                    client,
                    image_model=str(visual["image_model"]),
                    max_outputs=int(visual["max_image_outputs"]),
                      unit_cost=float(visual["image_2k_estimated_usd"]),
                      pricing_snapshot={"date": visual["pricing_snapshot_date"]},
                      authorization=authorization.image,
                      lease=lease,
                )
                images.generate_scene_images(job_id, job_dir, plan)
                if self.visual_repo.has_inflight_image_assets(job_id):
                    self.db.transition_job(job_id, "generating_images")
                    return {
                        "job_id": job_id,
                        "status": "generating_images",
                        "next": f"lyria-auto resume --job {job_id}",
                    }
                uncertain_images = [
                    row for row in self.visual_repo.assets(
                        job_id, asset_type="scene_image"
                    )
                    if row["status"] == "start_uncertain"
                ]
                if uncertain_images:
                    raise LyriaAutoError(
                        structured_error(
                            code="VISUAL_START_UNCERTAIN",
                            problem="圖片 start 結果無法確認",
                            cause="至少一個 asset 進入 start_uncertain",
                            fix="人工 reconcile；確認補生後使用 regenerate 與新 allowance",
                            next_command=f"lyria-auto report --job {job_id}",
                            job_id=job_id,
                        )
                    )
                self.db.transition_job(job_id, "awaiting_image_review")
                return self._paused(
                    job_id, job_dir, "awaiting_image_review"
                )

            if state == "awaiting_image_review":
                if not self._all_scenes_status(job_id, "image_approved"):
                    return self._paused(
                        job_id, job_dir, "awaiting_image_review"
                    )
                  self._preflight().require_valid_preflight()
                  video_outputs = (
                    len(plan.scenes)
                    * int(visual["video_candidates_per_scene"])
                )
                authorization = self._require_or_resume_stage_authorization(
                    job_id,
                    stage="initial_videos",
                    expected_images=1,
                    expected_videos=video_outputs,
                    supplied=paid_authorizations,
                )
                video_unit_cost = (
                    float(visual["video_1080p_second_estimated_usd"])
                    * int(visual["video_duration_seconds"])
                )
                  self._require_default_visual_budget(
                    job_id,
                    f"縮圖 1 張與影片 {video_outputs} 段",
                    float(visual["image_2k_estimated_usd"])
                      + video_outputs * video_unit_cost,
                  )
                  lease = self.db.acquire_side_effect_lease(
                      scope_type="job", scope_id=job_id
                  )
                  if not lease.acquired:
                      return self._already_running(job_id, lease)
                  client = self._visual_client()
                  images = ImageGenerationWorkflow(
                    self.visual_repo,
                    client,
                    image_model=str(visual["image_model"]),
                      max_outputs=int(visual["max_image_outputs"]),
                      unit_cost=float(visual["image_2k_estimated_usd"]),
                      authorization=authorization.image,
                      lease=lease,
                )
                thumbnail_asset_id = images.generate_thumbnail_background(
                    job_id, job_dir, plan
                )
                thumbnail_asset = self.visual_repo.asset(thumbnail_asset_id)
                if thumbnail_asset["status"] == "ready":
                    background_path = str(thumbnail_asset["path"])
                else:
                    first_scene = self.visual_repo.scenes(job_id)[0]
                    selected_image = self.visual_repo.asset(
                        int(first_scene["selected_image_asset_id"])
                    )
                    fallback_variant = 1 + max(
                        (
                            int(row["variant_index"])
                            for row in self.visual_repo.assets(
                                job_id,
                                asset_type="thumbnail_background",
                            )
                        ),
                        default=-1,
                    )
                    thumbnail_asset_id = self.visual_repo.reserve_asset(
                        job_id,
                        None,
                        "thumbnail_background",
                        fallback_variant,
                        "local",
                        "pillow-approved-scene-fallback",
                        "Use the approved A scene as local thumbnail background",
                        0.0,
                        {},
                    )
                    background_path = str(selected_image["path"])
                thumbnail_path = create_visual_thumbnail(
                    background_path,
                    ctx.thumbnail,
                    title="COZY JAZZ",
                    subtitle="Study · Read · Relax",
                    width=int(self.config.section("video")["thumbnail_width"]),
                    height=int(self.config.section("video")["thumbnail_height"]),
                    quality=int(self.config.section("video")["thumbnail_quality"]),
                )
                  thumbnail_asset = self.visual_repo.asset(thumbnail_asset_id)
                  self.visual_repo.mark_local_thumbnail_ready(
                      thumbnail_asset_id,
                      expected_version=int(thumbnail_asset["state_version"]),
                      raw_path=background_path,
                    path=str(thumbnail_path),
                    mime_type="image/jpeg",
                    sha256=sha256_file(thumbnail_path),
                    width=int(self.config.section("video")["thumbnail_width"]),
                    height=int(self.config.section("video")["thumbnail_height"]),
                  )
                self.db.transition_job(job_id, "generating_videos")
                state = "generating_videos"

              if state == "generating_videos":
                  self._preflight().require_valid_preflight()
                  video_outputs = (
                      len(plan.scenes)
                      * int(visual["video_candidates_per_scene"])
                  )
                  authorization = self._require_or_resume_stage_authorization(
                      job_id,
                      stage="initial_videos",
                      expected_images=1,
                      expected_videos=video_outputs,
                      supplied=paid_authorizations,
                  )
                  lease = self.db.acquire_side_effect_lease(
                      scope_type="job", scope_id=job_id
                  )
                  if not lease.acquired:
                      return self._already_running(job_id, lease)
                  client = self._visual_client()
                videos = VideoGenerationWorkflow(
                    self.visual_repo,
                    client,
                    video_model=str(visual["video_model"]),
                    max_outputs=int(visual["max_video_outputs"]),
                    unit_cost=(
                        float(visual["video_1080p_second_estimated_usd"])
                        * int(visual["video_duration_seconds"])
                    ),
                    poll_interval_seconds=float(
                        visual["poll_interval_seconds"]
                    ),
                      normalize=self._normalize_video,
                      inspect=self._inspect_visual_loop,
                      authorization=authorization.video,
                      lease=lease,
                )
                videos.generate(job_id, job_dir)
                if self.visual_repo.has_inflight_video_assets(job_id):
                    self.db.transition_job(job_id, "generating_videos")
                    return {
                        "job_id": job_id,
                        "status": "generating_videos",
                        "next": f"lyria-auto resume --job {job_id}",
                    }
                uncertain = [
                    row for row in self.visual_repo.assets(
                        job_id, asset_type="scene_video"
                    )
                    if row["status"] == "start_uncertain"
                ]
                if uncertain:
                    raise LyriaAutoError(
                        structured_error(
                            code="VISUAL_START_UNCERTAIN",
                            problem="Veo start 結果無法確認",
                            cause=f"{len(uncertain)} 個 asset 缺少可確認的 operation",
                            fix="人工 reconcile；若確定補生，改用 regenerate 與新 allowance",
                            next_command=f"lyria-auto report --job {job_id}",
                            job_id=job_id,
                        )
                    )
                self.db.transition_job(job_id, "awaiting_video_review")
                return self._paused(
                    job_id, job_dir, "awaiting_video_review"
                )

            if state in (
                "awaiting_video_review",
                "generating_audio",
                "rendering",
                "rendered",
                "uploading",
            ):
                approved = self._all_scenes_status(job_id, "video_approved")
                if state == "awaiting_video_review" and not approved:
                    return self._paused(
                        job_id, job_dir, "awaiting_video_review"
                    )
                if not approved:
                    raise LyriaAutoError(
                        f"job 處於 {state}，但影片核准狀態不完整"
                    )
                video_path = self._existing_rendered_video(ctx)
                if video_path is None:
                    tracks = self._generate(
                        job_id,
                        job_dir,
                        job_status="generating_audio",
                        preserve_stage_on_error=True,
                    )
                    video_path = self._render_visual(ctx, tracks)
                if not upload:
                    self.db.transition_job(job_id, "rendered")
                    return {
                        "job_id": job_id,
                        "status": "rendered",
                        "directory": str(job_dir),
                        "video": str(video_path),
                        "next": (
                            f"lyria-auto originality-review --job {job_id} "
                            "--approve --evidence-file creative-notes.txt"
                        ),
                    }
                publish_identity = self._current_publish_identity(
                    job_id, video_path, ctx
                )
                self.originality.require_approved(
                    job_id,
                    render_manifest_sha256=publish_identity.render_manifest_sha256,
                    output_snapshot_sha256=publish_identity.output_snapshot_sha256,
                    metadata_sha256=publish_identity.metadata_sha256,
                    thumbnail_sha256=publish_identity.thumbnail_sha256,
                )
                self.db.transition_job(job_id, "uploading")
                youtube_id = self._upload_resumable(
                    ctx, publish_identity
                )
                self.db.transition_job(job_id, "complete")
                return {
                    "job_id": job_id,
                    "status": "complete",
                    "directory": str(job_dir),
                    "video": str(video_path),
                    "youtube_video_id": youtube_id,
                    "publish_at": ctx.metadata.publish_at,
                }
            raise LyriaAutoError(
                structured_error(
                    code="VISUAL_STATE_UNKNOWN",
                    problem="工作進入未知視覺狀態",
                    cause=f"state={state}",
                    fix="停止操作並以 report 檢查 migration/state audit",
                    next_command=f"lyria-auto report --job {job_id}",
                    job_id=job_id,
                )
            )
        except Exception as exc:
            row = self.db.job(job_id)
            self.db.set_job_error(
                job_id,
                expected_version=int(row["state_version"]),
                error=sanitize_exception(exc),
            )
            raise
```

`_current_publish_identity()` 必須 probe 完成 output，並以固定 file descriptor
建立 immutable video/thumbnail snapshots；canonicalize metadata 後回傳四個 hashes
與 snapshot paths。同一 object 同時傳給 originality gate 與 uploader，避免兩者
各自重新依可變 path 計算 identity。

新增視覺 renderer：

```python
    def _render_visual(
        self, ctx: JobContext, tracks: list[Path]
    ) -> Path:
        video_cfg = self.config.section("video")
        visual_cfg = self.config.section("visual")
        crossfade = float(video_cfg.get("crossfade_seconds", 2))
        self.db.transition_job(ctx.job_id, "rendering")
        compilation = combine_audio(
            tracks, ctx.job_dir / "compilation.m4a", crossfade
        )
        target_seconds = int(video_cfg["target_duration_minutes"]) * 60
        audio_final = extend_audio(
            compilation,
            ctx.job_dir / "compilation_extended.m4a",
            target_seconds,
            crossfade,
        )
        # 將已核准內容固定成 immutable render snapshot；renderer 不再重開 raw path。
        render_inputs = self.visual_repo.prepare_render_snapshot(
            ctx.job_id,
            audio_path=audio_final,
            snapshot_root=ctx.job_dir / "snapshots",
            expected_job_version=int(self.db.job(ctx.job_id)["state_version"]),
        )
        scenes = [
            (item.label, item.snapshot_path)
            for item in render_inputs.scenes
        ]
        output = ctx.job_dir / (slugify(ctx.metadata.title) + ".mp4")
        video_path = create_timeline_video(
            scenes,
            render_inputs.audio_snapshot_path,
            output,
            width=int(video_cfg["width"]),
            height=int(video_cfg["height"]),
            fps=int(visual_cfg["video_fps"]),
            audio_bitrate=str(video_cfg["audio_bitrate"]),
            preset=str(video_cfg["video_preset"]),
            interval_seconds=(
                int(visual_cfg["scene_interval_minutes"]) * 60
            ),
        )
        finalized = self.visual_repo.finalize_render_output(
            video_path,
            ctx.thumbnail,
            snapshot_root=ctx.job_dir / "snapshots",
            expected_render_manifest_sha256=render_inputs.manifest_sha256,
        )
        video_row = self.db.video(ctx.video_row_id)
        self.db.record_rendered_video(
            ctx.video_row_id,
            expected_status=str(video_row["status"]),
            expected_version=int(video_row["state_version"]),
            video_snapshot_path=str(finalized.video_snapshot_path),
            video_sha256=finalized.video_sha256,
            thumbnail_snapshot_path=str(finalized.thumbnail_snapshot_path),
            thumbnail_sha256=finalized.thumbnail_sha256,
            publish_at=ctx.metadata.publish_at,
            render_manifest_sha256=render_inputs.manifest_sha256,
        )
        json_dump(ctx.job_dir / "metadata.json", ctx.metadata.__dict__)
        return finalized.video_snapshot_path
```

`prepare_render_snapshot()` 使用固定 input file descriptors 完成 hash／decode／
FFprobe，將相同 bytes 寫入 content-addressed temp files 後 atomic rename，再於單一
transaction 重新驗證 job/scene `state_version` 並保存 snapshot identities。任何
mismatch 把對應 asset 改為不可逆 `failed_tampered` 並清除 selection；renderer
只能讀 `render_inputs` 的 immutable paths。整份 manifest（audio snapshot SHA、
selected snapshot IDs/SHA、timeline version、encoder settings）另存 hash，作為
partial resume、originality approval 與既有正式 output reuse identity。
`finalize_render_output()` 同樣以固定 descriptors 驗證輸出與 thumbnail，建立
content-addressed snapshots；`record_rendered_video()` 只接受 snapshot path/hash，
並以 expected status/version CAS 將 video 轉成 `rendered`。

- [ ] **Step 8: Keep visual stage on errors instead of overwriting it as failed**

在 `Pipeline` 加入共用 helper：

```python
    def _record_job_failure(
        self, job_id: int, job_dir: Path, exc: Exception
    ) -> None:
        safe_error = sanitize_exception(exc)
        plan_path = job_dir / "plan.json"
        is_visual = (
            plan_path.exists()
            and self._load_plan_payload(job_dir).get("visual_plan_version") == 1
        )
        if is_visual:
            row = self.db.job(job_id)
            self.db.set_job_error(
                job_id,
                expected_version=int(row["state_version"]),
                error=safe_error,
            )
        else:
            self.db.transition_job_failed(job_id, safe_error)
        self.db.event(
            job_id, "job_failed", safe_error.problem, "ERROR",
            error_code=safe_error.code,
        )
```

在 `run_one()` 的 outer `except` 中，以：

```python
            self._record_job_failure(job_id, job_dir, exc)
```

取代既有 job failure update 與 raw event 兩行。保留 `logger.exception(...)`，
但 logger 參數也只能使用 sanitized error code/problem。

在 `resume_one()` 與 `run_from_job()` 的 outer `except` 中，以：

```python
            self._record_job_failure(ctx.job_id, ctx.job_dir, exc)
```

取代同樣兩行；兩個方法也都保留原本各自的 `logger.exception(...)`。

- [ ] **Step 9: Run pipeline and legacy regressions**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_pipeline.py tests/test_resume.py tests/test_from_job.py tests/test_dry_run.py -v
```

Expected: all pass; visual test在兩個 gate 前 `fake_lyria.calls == []`。

- [ ] **Step 10: Commit**

```bash
git add src/lyria_auto/db.py src/lyria_auto/models.py src/lyria_auto/pipeline.py tests/conftest.py tests/test_visual_pipeline.py
git commit -m "feat: orchestrate visual approval state machine"
```

---

### Task 4: Separate review, resume and exact regeneration CLI

**Files:**
- Modify: `src/lyria_auto/cli.py`
- Modify: `src/lyria_auto/pipeline.py`
- Create: `tests/test_visual_cli.py`

- [ ] **Step 1: Write failing parser and no-paid-review tests**

建立 `tests/test_visual_cli.py`：

```python
from __future__ import annotations

import pytest

from lyria_auto.cli import _parser


def test_review_parser_accepts_job_and_loopback_port():
    args = _parser().parse_args(["review", "--job", "12", "--port", "8089"])

    assert args.command == "review"
    assert args.job == 12
    assert args.port == 8089


def test_resume_rejects_regeneration_flags():
    with pytest.raises(SystemExit) as exc:
        _parser().parse_args(
            [
                "resume", "--job", "12", "--scene", "A",
            ]
        )
    assert exc.value.code == 2


def test_regenerate_requires_exact_one_kind_of_allowance():
    parser = _parser()
    args = parser.parse_args(
        [
            "regenerate", "--job", "12", "--scene", "A",
            "--kind", "video", "--allow-outputs", "2",
        ]
    )
    assert args.command == "regenerate"
    assert args.kind == "video"
    assert args.allow_outputs == 2


def test_normal_paid_stage_allowances_are_separate_kinds():
    args = _parser().parse_args(
        [
            "resume", "--job", "12",
            "--allow-image-outputs", "1",
            "--allow-video-outputs", "8",
        ]
    )
    assert args.allow_image_outputs == 1
    assert args.allow_video_outputs == 8
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_cli.py -v
```

Expected: review command is unknown.

- [ ] **Step 3: Add parser commands and mutual exclusion**

在 `_parser()` 加入：

```python
    review = sub.add_parser("review", help="在 localhost 核准圖片或影片")
    review.add_argument("--job", type=int, required=True)
    review.add_argument("--port", type=int, default=0)

    resume.add_argument("--allow-image-outputs", type=non_negative_int, default=0)
    resume.add_argument("--allow-video-outputs", type=non_negative_int, default=0)

    regenerate = sub.add_parser(
        "regenerate",
        help="建立新的付費視覺候選；不屬於 resume",
    )
    regenerate.add_argument("--job", type=int, required=True)
    regenerate.add_argument("--scene", choices=("A", "B", "C", "D"), required=True)
    regenerate.add_argument("--kind", choices=("image", "video"), required=True)
    regenerate.add_argument("--allow-outputs", type=positive_int, required=True)

    report = sub.add_parser("report", help="顯示本機 job 指標與下一步")
    report.add_argument("--job", type=int, required=True)
    report.add_argument("--json", action="store_true")
```

- [ ] **Step 4: Add review command before constructing Pipeline**

在 `main()` 的 scheduler branch 前加入：

```python
    if args.command == "review":
        from .db import StateDB
        from .review_server import ReviewServer
        from .visual_repository import VisualRepository

        project = config.section("project")
        db = StateDB(config.root / project.get("database", "workspace/state.sqlite3"))
        try:
            if db.job(args.job) is None:
                raise LyriaAutoError(f"找不到 job {args.job}")
            server = ReviewServer(VisualRepository(db), args.job, port=args.port)
            print(f"審核頁：{server.url}")
            print("按 Ctrl+C 關閉；審核頁不會呼叫付費模型。")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.close()
            return
          except LyriaAutoError as exc:
              render_error(
                  exc,
                  json_mode=bool(args.json),
                  verbose=bool(args.verbose),
              )
              sys.exit(1)
        finally:
            db.close()
```

- [ ] **Step 5: Keep resume pure and expose regeneration as a separate service**

`Pipeline.resume_one()` 只接收正常 stage allowance，不接 scene/kind/regeneration：

```python
    def resume_one(
        self,
        job_id: int | None = None,
        upload: bool = False,
        *,
        allow_image_outputs: int = 0,
        allow_video_outputs: int = 0,
    ) -> dict[str, Any]:
        allowances = StageAllowances(
            images=allow_image_outputs,
            videos=allow_video_outputs,
        )
        return self._execute_existing_job(job_id, upload, allowances)
```

新增獨立方法，且 CLI 只有 `regenerate` branch 能呼叫：

```python
    def regenerate_visual(
        self,
        job_id: int,
        *,
        scene_label: str,
        kind: Literal["image", "video"],
        allow_outputs: int,
    ) -> dict[str, Any]:
        ctx = self._load_job_context(job_id)
        state = str(self.db.job(job_id)["status"])
        payload = self._load_plan_payload(ctx.job_dir)
        if payload.get("visual_plan_version") != 1:
            raise LyriaAutoError("legacy job 不支援視覺再生")
        expected_state = (
            "awaiting_image_review" if kind == "image"
            else "awaiting_video_review"
        )
        if state != expected_state:
            raise LyriaAutoError(f"{kind} 只能在 {expected_state} 再生")
        expected = int(
            self.config.section("visual")[
                f"{kind}_candidates_per_scene"
            ]
        )
        authorization = PaidOutputAuthorization(
            kind,
            scene_label,
            requested_outputs=expected,
            allowed_outputs=allow_outputs,
        )
        authorization.require_exact()  # provider object 尚未建立
        self._preflight().require_valid_preflight()
        return self._run_regeneration(ctx, authorization)
```

`_run_regeneration()` 使用 Plan 3 已建立的 image/video workflow；每個新 variant 先持久化
authorization ID 與 asset identity，遵守 at-most-once。回傳值顯示 `requested starts`、
`started`、`uncertain`、`ready`，不得用 `generated outputs` 混淆成功數與授權數。

- [ ] **Step 6: Validate allowances before constructing paid providers**

CLI branch：

```python
    if args.command == "resume":
        result = pipeline.resume_one(
            args.job,
            args.upload,
            allow_image_outputs=args.allow_image_outputs,
            allow_video_outputs=args.allow_video_outputs,
        )

    if args.command == "regenerate":
        expected = int(
            config.section("visual")[
                f"{args.kind}_candidates_per_scene"
            ]
        )
        if args.allow_outputs != expected:
            raise LyriaAutoError(
                f"{args.kind} 再生固定建立 {expected} 個 start intents；"
                f"--allow-outputs 必須等於 {expected}"
            )
        print_cost_confirmation(args.kind, args.scene, expected, config)
        result = pipeline.regenerate_visual(
            args.job,
            scene_label=args.scene,
            kind=args.kind,
            allow_outputs=args.allow_outputs,
        )
```

同樣把 `--allow-image-outputs`／`--allow-video-outputs` 加到會開始初始 paid stage
的 `run --from-job` 路徑。錯誤數量、錯誤 stage、額度過大或過小，都必須在
`GeminiVisualClient`／Pipeline paid provider 建立前結束。`--json` 回傳 D22 的
structured error schema；一般輸出顯示問題、原因、修正方式、下一條命令、job 與 log。

測試須證明：

- `resume` parser 不認得 `--scene`、`--kind`、`--allow-outputs`。
- `regenerate` 必須同時具備 job/scene/kind/精確 allowance。
- planned image stage 缺 `--allow-image-outputs N` 時 paid fake calls 為零。
- image-approved video stage 必須恰有 `--allow-image-outputs 1` 與
  `--allow-video-outputs N`；任一不符時 paid fake calls 為零。
- 既有 `polling`／`normalizing` resume 不要求新 allowance，也不建立新 start。
- CLI allowance 不修改 YAML、SQLite global config 或下個 job。

- [ ] **Step 7: Run CLI tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_cli.py tests/test_cli_resume.py tests/test_paid_regeneration.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/lyria_auto/cli.py src/lyria_auto/pipeline.py tests/test_visual_cli.py
git commit -m "feat: add review and explicit visual regeneration commands"
```

---

### Task 5: Legacy compatibility, scheduler boundary and end-to-end fake tests

**Files:**
- Modify: `src/lyria_auto/scheduler.py`
- Create: `tests/test_visual_legacy.py`
- Create: `tests/test_visual_scheduler.py`

- [ ] **Step 1: Write legacy test from a plan without visual version**

建立 `tests/test_visual_legacy.py`：

```python
from __future__ import annotations

import json

from lyria_auto.pipeline import Pipeline


def test_old_plan_without_visual_version_uses_static_renderer(
    project_config, fake_lyria, monkeypatch
):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    static_calls = []

    def fake_static(*args, **kwargs):
        output = args[2]
        output.write_bytes(b"static-video")
        static_calls.append(output)
        return output

    monkeypatch.setattr("lyria_auto.pipeline.create_static_video", fake_static)
    pipeline = Pipeline(project_config)
    try:
        dry = pipeline.run_one("main", upload=False, dry_run=True)
        plan_path = pipeline.workspace / f"job_{dry['job_id']:06d}" / "plan.json"
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        payload.pop("visual_plan_version", None)
        payload.pop("visual_plan", None)
        plan_path.write_text(json.dumps(payload), encoding="utf-8")

        result = pipeline.run_from_job(dry["job_id"])

        assert result["status"] == "complete"
        assert len(static_calls) == 1
    finally:
        pipeline.close()
```

- [ ] **Step 2: Write scheduler pause test**

建立 `tests/test_visual_scheduler.py`：

```python
from __future__ import annotations

import inspect

import lyria_auto.scheduler as scheduler_module
from lyria_auto.errors import VisualPreflightError
from lyria_auto.scheduler import run_scheduled_batch


class FakePipeline:
    def __init__(self, config):
        self.db = object()
        self.run_calls = 0
        self.closed = False

    def run_batch(self, **kwargs):
        self.run_calls += 1
        return []

    def close(self):
        self.closed = True


class InvalidPreflight:
    def __init__(self, config, db):
        pass

    def require_valid_preflight(self):
        raise VisualPreflightError("expired")


def test_scheduler_never_auto_approves_visuals():
    source = (
        inspect.getsource(scheduler_module.start_scheduler)
        + inspect.getsource(scheduler_module.run_scheduled_batch)
    )

    assert "finish_image_review" not in source
    assert "finish_video_review" not in source
    assert "review_preflight" not in source


def test_scheduler_does_not_run_batch_when_preflight_is_invalid(
    visual_project_config
):
    pipeline = FakePipeline(visual_project_config)

    results = run_scheduled_batch(
        visual_project_config,
        pipeline_factory=lambda config: pipeline,
        preflight_factory=InvalidPreflight,
    )

    assert results == []
    assert pipeline.run_calls == 0
    assert pipeline.closed is True


def test_scheduler_requires_separate_paid_opt_in_and_caps(
    visual_project_config
):
    visual_project_config.data["scheduler"]["visual_paid_opt_in"] = False
    pipeline = FakePipeline(visual_project_config)

    assert run_scheduled_batch(
        visual_project_config,
        pipeline_factory=lambda config: pipeline,
    ) == []
    assert pipeline.run_calls == 0
```

- [ ] **Step 3: Add a testable scheduler batch helper and log expected pauses**

在 `scheduler.py` imports 加入：

```python
from .errors import VisualPreflightError
from .visual_preflight import VisualPreflightService
```

在 `start_scheduler()` 前新增：

```python
def run_scheduled_batch(
    config: AppConfig,
    *,
    pipeline_factory=Pipeline,
    preflight_factory=VisualPreflightService,
) -> list[dict]:
    sched_cfg = config.section("scheduler")
    pipeline = pipeline_factory(config)
    try:
        if config.section("visual").get("enabled", False):
            if not bool(sched_cfg.get("visual_paid_opt_in", False)):
                logger.info(
                    "scheduler visual paid opt-in 關閉；不建立付費視覺 job"
                )
                return []
            image_cap = int(sched_cfg.get("max_image_starts_per_run", 0))
            video_cap = int(sched_cfg.get("max_video_starts_per_run", 0))
            if image_cap <= 0 or video_cap <= 0:
                logger.error("scheduler paid caps 必須明確設定且大於 0")
                return []
            if str(config.section("youtube").get("privacy_status")) != "private":
                logger.error("scheduler 視覺 rollout 只允許 private upload")
                return []
            try:
                preflight_factory(
                    config, pipeline.db
                ).require_valid_preflight()
            except VisualPreflightError as exc:
                logger.error(
                    "視覺 preflight 無效，scheduler 不建立新 job：%s",
                    exc,
                )
                return []
        results = pipeline.run_batch(
            videos=int(sched_cfg.get("videos_per_run", 1)),
            channel_name=str(sched_cfg.get("channel", "main")),
            upload=bool(sched_cfg.get("upload", True)),
            dry_run=False,
            scheduler_allowances=SchedulerAllowances(
                max_image_starts=image_cap,
                max_video_starts=video_cap,
            ),
        )
        for result in results:
            if result.get("status") in (
                "awaiting_image_review",
                "awaiting_video_review",
            ):
                logger.info(
                    "job %s 已停在 %s，等待人工 review",
                    result["job_id"],
                    result["status"],
                )
        return results
    finally:
        pipeline.close()
```

`SchedulerAllowances` 是 scheduler 專用且每次 run 歸零；不能寫入人工
`PaidStageAuthorization`，也不能用來 `regenerate`、重送 `start_uncertain` 或越過 review。
實際 expected 若大於 cap，整個 job 在 paid provider 建立前停止，不可部分產生。
測試矩陣另覆蓋 opt-in false、cap 缺失、expected 超 cap、privacy 非 private、
兩次排程各自重置 cap，以及 scheduler source 不含任何 review approval/regeneration 呼叫。

把 `start_scheduler()` 的 nested `run_job()` 改成：

```python
    def run_job():
        run_scheduled_batch(config)
```

- [ ] **Step 4: Assert upload remains private and synthetic disclosure remains true**

在 `tests/test_visual_pipeline.py` 加入：

```python
def test_visual_metadata_keeps_synthetic_disclosure(
    visual_project_config
):
    pipeline = Pipeline(visual_project_config)
    try:
        result = pipeline.run_one("main", upload=False, dry_run=True)
        payload = pipeline._load_plan_payload(
            pipeline.workspace / f"job_{result['job_id']:06d}"
        )
        assert payload["metadata"]["contains_synthetic_media"] is True
        assert payload["metadata"]["privacy_status"] == "private"
    finally:
        pipeline.close()
```

- [ ] **Step 5: Run compatibility suite**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_legacy.py tests/test_visual_scheduler.py tests/test_visual_pipeline.py tests/test_upload.py -v
```

Expected: all pass; scheduler source沒有核准 method。

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/scheduler.py tests/test_visual_legacy.py tests/test_visual_scheduler.py tests/test_visual_pipeline.py
git commit -m "test: preserve legacy and manual scheduler boundaries"
```

---

### Task 6: Render preflight, progress and atomic output

**Files:**
- Create: `src/lyria_auto/media/render_preflight.py`
- Modify: `src/lyria_auto/media/timeline.py`
- Create: `tests/test_render_preflight.py`
- Modify: `tests/test_visual_render.py`

- [ ] **Step 1: Write RED preflight tests**

以 fake subprocess、fake disk usage 與 temp directory 覆蓋：

1. `ffmpeg -filters` 缺 `xfade` 時回 `RENDER_FILTER_MISSING`。
2. 缺 `libx264`（或設定選定 encoder）／AAC encoder 時，在 render 前失敗。
3. output parent 不可寫、temp 不可寫、input 不可讀，各自有 structured fix。
4. 保守 estimate `video_bitrate * duration + audio + 20% overhead` 大於可用空間時停止；
   顯示 required/available，不能開始正式 FFmpeg。
5. 3 秒、兩色、含 2 秒 xfade 的 lavfi smoke 寫入同 filesystem temp，再 probe
   video/audio/duration；不呼叫任何 paid provider。
6. preflight 成功結果可在單次 process 內依 FFmpeg binary fingerprint cache；
   跨執行仍須重查 disk 與 write smoke。

- [ ] **Step 2: Implement a structured render capability report**

```python
@dataclass(frozen=True)
class RenderPreflightReport:
    ffmpeg_path: Path
    ffmpeg_version: str
    video_encoder: str
    audio_encoder: str
    has_xfade: bool
    required_bytes: int
    available_bytes: int
    temp_directory: Path
    output_directory: Path


def require_render_ready(...) -> RenderPreflightReport:
    ...
```

任何檢查失敗都使用 D22 error envelope，包含下一條 `lyria-auto doctor --media`
或可安全重跑的 render/resume 命令；`--json` 不得包含完整 environment 或 API key。

- [ ] **Step 3: Stream progress and preserve atomicity**

`create_timeline_video()`：

- 正式輸出固定先寫 `<stem>.partial.mp4`，成功 probe 且音訊差 ≤ 1 frame 後才
  `Path.replace()` 原子替換正式檔。
- 解析 `ffmpeg -progress pipe:1` 的 `out_time_ms`，節流顯示 percent、elapsed、ETA、
  output size 與 render speed；fake clock 測試 0%、中段、100% 及不倒退。
- Ctrl+C 先終止子程序並 flush job stage=`rendering`；顯示 partial 路徑及下一條
  `resume`。普通 resume 可重做本機 render，不呼叫 Gemini/Veo/Lyria。
- 若已有正式 output，先比對 input manifest hash、probe 與 duration；有效則重用，
  不因存在 partial 而覆蓋。

- [ ] **Step 4: Run render safety suite**

除 capability smoke 外，新增真實縮短 timeline integration：

```text
resolution: 320×180
fps: 10
scene interval: 2 seconds
xfade: 0.2 seconds
sequence: A B C D A
```

以五種固定純色／frame marker 輸入執行真實 FFmpeg，逐一在四個全域 transition
window 前、中、後取 frame 驗證；必須涵蓋 D→A、PTS 不倒退、video/audio stream
存在，且總長誤差 ≤1 frame。45m／2h／2.5h／4h／5h 的 command tests 負責完整
offsets，但不得取代此多邊界真實 render。

```bash
python -m pytest \
  tests/test_render_preflight.py \
  tests/test_visual_timeline.py \
  tests/test_visual_render.py -v
python scripts/self_test_media.py
```

Expected: local-only；所有長度 command tests 的全域 xfade offsets 正確，
縮短 A→B→C→D→A 真實影片的四個邊界、顏色、PTS、audio 與長度全部通過。

---

### Task 7: Channel-level originality gate and creative evidence

**Files:**
- Create: `src/lyria_auto/originality.py`
- Modify: `src/lyria_auto/db.py`
- Modify: `src/lyria_auto/pipeline.py`
- Create: `tests/test_originality_gate.py`

- [ ] **Step 1: Add versioned originality schema**

沿用 Plan 1 migration registry，以固定 migration ID
`0004_originality_reviews` 新增：

```sql
ALTER TABLE videos ADD COLUMN video_sha256 TEXT;
ALTER TABLE videos ADD COLUMN thumbnail_sha256 TEXT;
ALTER TABLE videos ADD COLUMN render_manifest_sha256 TEXT;
CREATE TABLE originality_reviews (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id),
    render_manifest_sha256 TEXT NOT NULL,
    output_snapshot_sha256 TEXT NOT NULL,
    metadata_sha256 TEXT NOT NULL,
    thumbnail_sha256 TEXT NOT NULL,
  compared_job_ids_json TEXT NOT NULL,
  scene_phash_distances_json TEXT NOT NULL,
  thumbnail_phash_distance INTEGER,
  prompt_signature_distance REAL,
  metadata_signature_distance REAL,
  creative_evidence TEXT,
    reviewer_decision TEXT NOT NULL
      CHECK(reviewer_decision IN ('approved','rejected')),
    rules_version TEXT NOT NULL,
    state_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(
      job_id,render_manifest_sha256,output_snapshot_sha256,
      metadata_sha256,thumbnail_sha256,rules_version
    )
);
```

`creative_evidence` 是操作者簡述本片在場景、敘事、攝影、音樂／視覺搭配上的
實質差異；不可預填空泛文案。migration preview、rollback 與舊 DB 保留測試必做。
`require_approved()` 必須接收目前 render manifest、immutable output、canonical
metadata 與 thumbnail identities，逐項完全相等才可回傳 approved；只以
`job_id` 查到舊核准不算有效。

- [ ] **Step 2: Compare against recent published channel jobs**

在 upload 前讀本機最近 `N` 支已上傳 jobs（預設 20，可設上限 100）：

- 逐景比較 Plan 2 保存的 dHash/pHash，計算最佳配對與重複 scene 比例。
- 比較 thumbnail pHash、normalized prompt signature、title/description/tag metadata
  signature；prompt 原文不寫入 metrics/report。
- 規則與 threshold 寫成 versioned config，輸出每項命中原因；不能只給單一黑箱分數。
- 缺歷史資料時仍要求 creative evidence；資料不完整不得宣稱「已證明原創」。

- [ ] **Step 3: Gate upload, not local render**

高度相似或缺 creative evidence 時，job 保持 `rendered`，阻擋 YouTube session creation，
輸出 `lyria-auto originality-review --job JOB_ID`。人工只能：

- `approve` 並填具體 evidence，保存 reviewer decision/rules version；或
- `reject`，回到獨立 `regenerate`／metadata edit 流程。

此 gate 不自動重生、不自動改 prompt，也不宣稱通過後必獲 YPP／最大收益。
`containsSyntheticMedia: true` 始終保留。

- [ ] **Step 4: Test channel comparisons**

測試相同 pHash、近似 thumbnail、重複 metadata、完全不同 job、缺 history、
threshold 邊界、人工 approve/reject，以及 approval 後分別修改 render manifest、
output snapshot、metadata、thumbnail 的 identity；任一變動都使 approval 失效，
且 YouTube session/byte fake calls 為零。

---

### Task 8: YouTube session-first resumable upload and long-video eligibility

**Files:**
- Modify: `src/lyria_auto/db.py`
- Modify: `src/lyria_auto/providers/youtube.py`
- Modify: `src/lyria_auto/pipeline.py`
- Create: `tests/test_youtube_resumable.py`
- Modify: `tests/test_upload.py`

現有 `YouTubeClient.upload()` 中以 `MediaFileUpload(..., resumable=True)` 加
`request.next_chunk()` 的迴圈必須拆除。影片 resource upload 改由新的
`YouTubeResumableUploader` 負責；`YouTubeClient` 保留 channel identity、
eligibility、thumbnail、playlist 與 metadata methods。video ID 落盤後，
thumbnail／playlist 各自使用獨立冪等 stage，任何後處理失敗都不得重建影片。

- [ ] **Step 1: Add a resumable-attempt migration**

使用固定 migration ID `0005_youtube_resumable`：

```sql
CREATE TABLE youtube_upload_attempts (
  id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  video_snapshot_path TEXT NOT NULL,
  video_snapshot_sha256 TEXT NOT NULL,
  file_size_bytes INTEGER NOT NULL,
  metadata_sha256 TEXT NOT NULL,
  thumbnail_sha256 TEXT NOT NULL,
  session_uri TEXT,
  status TEXT NOT NULL CHECK(status IN (
    'creating_session','uploading','finalizing','upload_uncertain',
    'complete','expired_safe','superseded','failed'
  )),
  confirmed_bytes INTEGER NOT NULL DEFAULT 0,
  final_byte_attempted INTEGER NOT NULL DEFAULT 0,
  youtube_video_id TEXT,
  owner_token TEXT NOT NULL,
  state_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE youtube_chunk_intents (
  id INTEGER PRIMARY KEY,
  attempt_id INTEGER NOT NULL REFERENCES youtube_upload_attempts(id),
  start_byte INTEGER NOT NULL,
  end_byte INTEGER NOT NULL,
  is_final INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('intent','confirmed','uncertain')),
  created_at TEXT NOT NULL,
  confirmed_at TEXT,
  UNIQUE(attempt_id,start_byte,end_byte)
);
CREATE UNIQUE INDEX uq_youtube_active_job
ON youtube_upload_attempts(job_id)
WHERE status IN (
  'creating_session','uploading','finalizing','upload_uncertain'
);
CREATE INDEX idx_youtube_attempt_identity
ON youtube_upload_attempts(
  job_id,video_snapshot_sha256,metadata_sha256,thumbnail_sha256
);
```

session URI 具有 bearer-like 敏感性：SQLite/workspace 必須保持 user-only 權限，
URI 永不進 stdout/log/report/error JSON；診斷只顯示 attempt ID 與 URI hash prefix。
`YouTubeResumableUploader` 使用既有 OAuth credentials 的 authenticated HTTP
transport，明確分離 session POST、URI commit、status query 與 chunk PUT。
不得以 `googleapiclient` 的 `request.next_chunk()` 實作此 flow，因為它沒有提供
「已取得 Location、尚未送第一個 byte」的 application-level durable commit boundary。
googleapiclient 仍可用於 channel eligibility、thumbnail、playlist 與 metadata calls。
chunk size 可設定，預設 64 MiB，且除 final chunk 外必須為 256 KiB 倍數；
每個 server-confirmed chunk 才 commit。測試比較 8/64 MiB 的 request count，
並驗證 crash 時最多重送一個未確認 chunk。

- [ ] **Step 2: Check long-upload eligibility before session creation**

呼叫 `channels.list(part="status", mine=True)`，要求
`status.longUploadsStatus == "allowed"`。`eligible`／未驗證／disallowed 時：

- 不建立 upload session、不送檔案 bytes。
- structured error 說明長影片驗證問題、官方驗證入口與下一條 doctor/report 命令。
- 45 分鐘、2 小時、5 小時都走相同檢查；不可因測試帳號跳過。

- [ ] **Step 3: Persist identity and session before bytes**

順序固定：

1. 從 immutable render snapshot／thumbnail snapshot 取得 SHA-256、size，
   並計算 canonical metadata SHA-256。
2. transaction 建立 `creating_session` attempt；相同 job/hash 重用，hash 不同則明確
   標記舊 attempt `superseded`，不可把舊 session 套到新檔。
3. POST 建立 resumable session，取得 `Location`。
4. 在上傳第一個 byte 前 commit `session_uri` 與 status=`uploading`。
5. 每次 PUT 前先 transaction 寫入唯一 `youtube_chunk_intents`；若包含最後一個
   byte，同時把 attempt 的 `final_byte_attempted=1`。
6. PUT 完成後只依 server response 把 intent 標為 confirmed，並保存
   server-confirmed `confirmed_bytes`。

若 crash 發生於遠端建立 session、URI 尚未 commit，因尚未送 bytes，可建立新 session；
這不會建立 YouTube video resource。第一個 byte 後則永遠先 query 舊 session。

- [ ] **Step 4: Resume with 308 and finalize one resource**

- resume 先以 `Content-Range: bytes */TOTAL` 查狀態；HTTP 308 解析 `Range`，從
  server-confirmed next byte 繼續，不信任本機計數。
- 200/201 保存同一 `youtube_video_id` 並把 attempt `complete`；job 重跑直接回傳該 ID。
- final byte 曾被嘗試，但無法取得 200/201 或 video ID 時，標記
  `upload_uncertain`；普通 resume、scheduler 與 regenerate 都不得建立新 session。
- 404/410 且 `final_byte_attempted=1` 時保持 `upload_uncertain`，要求人工 reconcile。
- 404/410 只有在 final byte 從未被嘗試、所有 snapshot/metadata hashes 仍相同時，
  才標記 `expired_safe` 並建立新 session；identity 不同回 structured mismatch。
- 5xx/timeout 保留 session 與 uploading 狀態，普通 resume 不呼叫 videos.insert 第二次。
- thumbnail upload 與 metadata update 在 video ID 保存後各自具冪等 stage，失敗不重建影片。

- [ ] **Step 5: Test crash windows and byte ranges**

fake HTTP 測試：

1. session POST 後、URI commit 前 hard crash；重新執行可建新 session且 byte calls 為零。
2. URI commit 後、第一個 body byte 前 hard crash；resume 沿用同一 URI。
3. 每個 non-final chunk intent／PUT／confirm 前後 hard crash，以 308 server Range 恢復。
4. final PUT 已被 server 接受但 201 response 遺失，之後 session 404/410；
   attempt 必須為 `upload_uncertain` 且 session POST 次數不增加。
5. expired same-identity session 只有 final byte 從未被嘗試時可安全重建；
   不同 identity 或 final attempted 都不重建。
6. 兩個獨立 process 同時 resume 同一 attempt，lease/CAS 保證只有一個 PUT owner。
7. completed job 重跑只有一個 video ID。
8. `longUploadsStatus != allowed`、originality 未核准、privacy 非 private rollout
   均在 session/byte calls 前停止。
9. logs、DB error、`--json`、`report` 都找不到完整 session URI。

上述 crash tests 必須由 child `os._exit()`、parent 新建 DB connection／uploader
完成；不得只在同一 process 重用 mock request object。

---

### Task 9: Local-only operational metrics and report UX

**Files:**
- Create: `src/lyria_auto/metrics.py`
- Modify: `src/lyria_auto/cli.py`
- Modify: `src/lyria_auto/pipeline.py`
- Create: `tests/test_report.py`

- [ ] **Step 1: Define a redacted event schema**

保存 job/stage/event/status、monotonic duration、asset kind/count、poll count、
regeneration count、review wait、render speed、upload confirmed bytes、error code；
不保存 prompt、API key、session URI、完整 provider payload 或 media bytes。
`visual-demo` 額外保存 TTHW，驗收 D5 的 <2 分鐘目標。

Plan 1 定義唯一 typed error envelope：
`code/problem/cause/fix/next_command/job_id/attempt_id/log_reference`。所有外部
exception 必須先轉成 typed fields，再經單一 `sanitize_error()` 才可進
SQLite、log、report、human output 或 `--json`。禁止直接持久化／輸出
`str(exc)`；舊 snippets 若仍使用 `error=str(exc)`、`print(str(exc))` 或
`raise LyriaAutoError(str(exc))`，以本契約為準並在實作前改寫。

property-based redaction tests 產生 API key、Bearer/OAuth token、YouTube session
URI、query token 與 nested provider payload，逐一驗證 DB error、events、log、
report、`--json` 都只保留 error code 與安全欄位。

- [ ] **Step 2: Implement `lyria-auto report --job`**

人類輸出先顯示目前 stage、阻塞原因、已用／剩餘 allowance、各 asset x/y、elapsed、
最近 error、log path 與一條下一步命令；`--json` 使用穩定 versioned schema。
另提供 recent aggregate（成功率、各 stage p50/p95、review wait、regeneration rate）
但只讀本機 SQLite，不傳 telemetry。

- [ ] **Step 3: Test redaction and operator paths**

用含假 API key、prompt、session URI 的事件輸入，驗證 SQLite view、文字報告、
JSON 與 logs 全部不含秘密；測試 `start_uncertain`、polling、awaiting review、
rendering、uploading、complete 各自提供正確下一步。

---

### Task 10: Documentation, full verification and paid rollout checkpoints

**Files:**
- Modify: `README.md`
- Modify: `docs/00_START_HERE_zh-TW.md`
- Modify: `docs/01_SETUP_zh-TW.md`
- Modify: `docs/02_USAGE_zh-TW.md`
- Modify: `docs/03_AUTOMATION_zh-TW.md`
- Modify: `docs/05_TROUBLESHOOTING_zh-TW.md`
- Modify: `docs/06_SECURITY_zh-TW.md`
- Modify: `docs/07_OPERATING_PROCESS_zh-TW.md`
- Modify: `docs/08_OFFICIAL_REFERENCES.md`
- Modify: `scripts/self_test_media.py`

- [ ] **Step 1: Make one canonical Mac-first daily workflow**

`docs/00_START_HERE_zh-TW.md` 是唯一 canonical 新手入口；README 與其他 `docs/*.md`
只保留各自專題和回到 START_HERE 的連結，不再複製一份可能過期的 daily workflow。
所有範例預設 macOS bash，多行用 `\`，另提供不含 shell substitution 的安全單行版：

```bash
lyria-auto doctor
lyria-auto run --dry-run --videos 1
lyria-auto run --from-job JOB_ID \
  --allow-image-outputs 13
lyria-auto review --job JOB_ID
lyria-auto resume --job JOB_ID \
  --allow-image-outputs 1 \
  --allow-video-outputs 8
lyria-auto resume --job JOB_ID
lyria-auto review --job JOB_ID
lyria-auto resume --job JOB_ID
lyria-auto originality-review --job JOB_ID \
  --approve \
  --evidence-file creative-notes.txt
lyria-auto resume --job JOB_ID --upload
lyria-auto report --job JOB_ID
```

說明每次命令的結果：

- `run --from-job`：精確授權 13 個 image start intents，完成後停在
  `awaiting_image_review`；失敗／uncertain 不代表成功產出 13 張。
- 第一次 review：每景選一張；不產生費用。
- 帶雙 allowance 的 resume：授權 1 張縮圖與 8 個 Veo start intents。
- Veo poll 未完成時保持 `generating_videos`；後續純 resume 沿用 operation，無新 allowance、
  無新 start。全部 ready/terminal 且無 blocker 才停在 `awaiting_video_review`。
- 第二次 review：每景選一段、核准縮圖；不產生費用。
- 第二次純 resume：才生成 Lyria、做 render preflight/xfade/atomic render。
- originality-review 必須由操作者寫具體 creative evidence；核准只允許進上傳，
  不承諾 YPP。
- `--upload` 前先過 originality gate、longUploadsStatus 與 resumable session identity。

新增 `tests/test_docs_cli_examples.py`：抽取 START_HERE fenced `bash` 中所有
`lyria-auto` 命令，移除 continuation 後交給 argparse parser；命令名稱與 flags 必須全數
解析。其他文件不得出現完整 daily sequence，避免雙重真相。

- [ ] **Step 2: Document long-duration behavior and cost**

加入表格：

```markdown
| 目標長度 | 30 分鐘場景順序 | 2 秒 xfade 邊界 | 唯一視覺數 | 預設視覺生成成本 |
|---|---|---|---:|---:|
| 45 分鐘 | A → B（15m） | 30m | 2 | 依實際唯一場景減少 |
| 2 小時 | A → B → C → D | 30/60/90m | 4 | 約 US$9.09 |
| 2.5 小時 | A → B → C → D → A | 30/60/90/120m | 4 | 約 US$9.09 |
| 4 小時 | A → B → C → D × 2 | 每 30m，共 7 次 | 4 | 約 US$9.09 |
| 5 小時 | A → B → C → D → A → B → C → D → A → B | 每 30m，共 9 次 | 4 | 約 US$9.09 |
```

另列一次性 watermark smoke test 的「執行當日官方價格快照」與 estimate；不可把
本計畫撰寫時的數字當成固定價格。所有長度的視覺成本來自最多四個唯一 scene，
延長到 5 小時不增加 Veo asset，只有本機 render／儲存／YouTube upload 成本增加。

- [ ] **Step 3: Document official sources and disclosure**

在 `docs/08_OFFICIAL_REFERENCES.md` 加入：

```markdown
- Nano Banana image generation: https://ai.google.dev/gemini-api/docs/image-generation
- Veo 3.1 generation and first/last frames: https://ai.google.dev/gemini-api/docs/veo
- Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing
- SynthID verification: https://support.google.com/gemini/answer/16722517?hl=en
- Flow visible watermark behavior: https://support.google.com/flow/answer/16353333?hl=en&rd=1
- YouTube altered/synthetic disclosure: https://support.google.com/youtube/answer/14328491?hl=en
- YouTube monetization policies: https://support.google.com/youtube/answer/1311392?hl=en-GB
- YouTube resumable uploads: https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol
- YouTube channel status / longUploadsStatus: https://developers.google.com/youtube/v3/docs/channels
- YouTube long-upload verification and limits: https://support.google.com/youtube/answer/71673
```

文件必須說明：無可見產品標誌不等於隱瞞 AI；SynthID 保留，`containsSyntheticMedia` 維持 `true`。

- [ ] **Step 4: Extend media self-test without using paid APIs**

在 `scripts/self_test_media.py` imports 加入：

```python
from lyria_auto.media.timeline import create_timeline_video
from lyria_auto.media.visual_quality import probe_video
```

在 `main()` 的既有 static video assertion 後加入完整的本機 timeline self-test：

```python
        loop_a = td / "loop_a.mp4"
        loop_b = td / "loop_b.mp4"
        timeline_audio = td / "timeline_audio.m4a"
        timeline_output = td / "timeline_self_test.mp4"
        for path, color in ((loop_a, "red"), (loop_b, "blue")):
            run_command(
                [
                    "ffmpeg", "-y", "-f", "lavfi",
                    "-i", f"color=c={color}:s=320x180:r=24:d=2",
                    "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(path),
                ]
            )
        run_command(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "sine=frequency=440:sample_rate=48000:duration=7",
                "-ac", "2", "-c:a", "aac", str(timeline_audio),
            ]
        )
        create_timeline_video(
            [("A", loop_a), ("B", loop_b)],
            timeline_audio,
            timeline_output,
            width=320,
            height=180,
            fps=24,
            audio_bitrate="128k",
            preset="ultrafast",
            interval_seconds=2,
        )
        info = probe_video(timeline_output)
        streams = info["streams"]
        video_streams = [
            stream for stream in streams if stream["codec_type"] == "video"
        ]
        audio_streams = [
            stream for stream in streams if stream["codec_type"] == "audio"
        ]
        assert abs(float(info["format"]["duration"]) - 7.0) <= (1 / 24)
        assert len(video_streams) == 1
        assert len(audio_streams) == 1
        print(
            "PASS: timeline "
            f"{timeline_output} ({timeline_output.stat().st_size} bytes)"
        )
```

測試素材全部由 FFmpeg lavfi 建立，不能 import Gemini provider。

- [ ] **Step 5: Run full automated verification**

Run:

```bash
python -m pytest -q
python -m ruff check src tests
python scripts/self_test_media.py
```

Expected:

- pytest 全部通過。
- Ruff 顯示 `All checks passed!`。
- self-test 顯示 timeline video、video stream、audio stream 與 duration 都通過。

- [ ] **Step 6: Commit code-complete documentation**

```bash
git add README.md docs scripts/self_test_media.py
git commit -m "docs: document looping visual production workflow"
```

- [ ] **Step 7: Stop for explicit paid smoke-test authorization**

不要自行執行。向使用者顯示執行當日官方價格快照與依模型／輸出長度計算的 estimate，
取得明確同意後才執行：

```bash
lyria-auto visual-preflight --watermark-smoke-test \
  --allow-image-outputs 1 --allow-video-outputs 1
```

Expected: command輸出 run ID、原始圖片與原始影片路徑，狀態為 `awaiting_review`。

- [ ] **Step 8: Stop for manual watermark decision**

使用者直接查看兩個原始檔後執行：

```bash
lyria-auto visual-preflight --approve RUN_ID
lyria-auto doctor
```

Expected: 只有兩次都輸入 `yes` 時顯示 `passed_no_visible_mark`；doctor 顯示有效期限。任何可見標誌都停止後續 paid rollout。

- [ ] **Step 9: Stop for 2-hour private acceptance authorization**

在使用者明確同意視覺與 Lyria 費用後，依 daily workflow 產生 2 小時 private 影片。人工檢查：

```text
A、B、C、D 各自 8 秒循環接點
30、60、90 分鐘各自於 [B−1s,B+1s] 完成 2 秒平滑 xfade
四景同一咖啡館、同一時段與天氣
縮圖文字及第一場景世界一致
originality evidence 已保存且相似度報告可解釋
longUploadsStatus=allowed
YouTube 完成 1080p 處理
containsSyntheticMedia 揭露存在
```

不把影片切換為 public。

- [ ] **Step 10: Stop for 5-hour private acceptance authorization**

2 小時驗收通過且使用者再次明確同意成本後，產生 5 小時 private 影片。人工檢查：

```text
時間軸 A B C D A B C D A B
2 小時 D→A 無時間倒退
每 30 分鐘換景，共 10 個 block、9 個 2 秒 xfade
仍只有四個唯一影片 asset、沒有新增 Veo 費用
最終音訊沒有被裁在曲中
模擬中斷後以同一 resumable session/308 完成
YouTube 沒有重複建立 video ID，report/log 沒有 session URI
```

- [ ] **Step 11: Enable scheduler only after both private acceptances**

兩支 private 影片都通過後，才允許同時將 `scheduler.enabled` 與
`scheduler.visual_paid_opt_in` 改為 `true`，並設定非零且足夠的
`max_image_starts_per_run`／`max_video_starts_per_run`。scheduler 仍只會停在人工 gate，
不會自動核准、regenerate、處理 `start_uncertain` 或自動公開；若 channel publish
設定不是 private，保持 scheduler disabled。

- [ ] **Step 12: Final verification commit**

若人工驗收只產生 ignored workspace 檔案與外部 YouTube private video，不提交這些檔案。只在操作文件需要記錄已驗收日期時提交：

```bash
git add docs/07_OPERATING_PROCESS_zh-TW.md
git commit -m "docs: record private visual acceptance"
```

---

## Code completion gate

此 gate 必須可在零 paid call、無人工授權下由 CI／本機重複執行；通過後代表
production code complete，但不代表已核准 paid rollout：

1. 所有新增及既有 pytest 通過，Ruff 無錯。
2. dry-run 保持零 Gemini 圖片、Veo、Lyria 付費呼叫。
3. Plan 1–3 completion gates 依序通過；migration registry 能從舊 DB 一路升級且保留 custom values。
4. preflight 精確授權、來源失效、30 天期限、rehash/decode/probe 與
   `failed_tampered` 不可逆都有測試。
5. subprocess hard crash、DB reopen 與兩個 concurrent workers 下，SQLite
   lease＋CAS＋同 transaction allowance consumption 仍保證 paid starts at-most-once；
   `start_uncertain`、poll timeout、Ctrl+C 與 `normalizing` resume 不透明重送。
6. 初始 paid stages 與獨立 `regenerate` 都要求精確 allowance；純 `resume` 無補生 flags，
   provider 建立前失敗的 fake paid calls 為零。
7. `reserved/starting/polling/normalizing` 任一存在時保持 `generating_videos`；
   uncertain 顯示人工 blocker。
8. 圖片／影片 review 使用 `state_version` CAS；reject 原子清 stale selection，
   finish 在同 transaction 重驗歸屬、ready 與 immutable snapshot identity。
9. guided review 顯示 selected、disabled QC、x/y、checklist、原尺寸、下一步與付費影響。
10. 45m、2h、2.5h、4h、5h 只引用最多四景；每個全域邊界採
    `[B−1,B+1]` 2 秒 xfade；縮短 A→B→C→D→A 真實 FFmpeg integration
    驗證每個邊界、PTS、audio 與最終誤差 ≤ 1 frame。
11. render preflight 驗證 disk/temp/write/xfade/encoders/smoke；正式 render 有
    progress/ETA、`.partial`、probe 與 atomic rename。
12. originality gate 比較 recent channel pHash/prompt/thumbnail/metadata，保存具體
    creative evidence，並綁定 render manifest/output/metadata/thumbnail hashes；
    identity 改變即失效，文件明示這不保證 YPP。
13. YouTube 先驗 `longUploadsStatus=allowed`；session URI、immutable file SHA、
    metadata/thumbnail hashes 在第一個 byte 前持久化；chunk intent 先於 PUT，
    308 resume 只產生一個 video ID。
14. final byte 曾被嘗試但 completion response／video ID 不明時進
    `upload_uncertain`；404/410 或 ordinary resume 都不得建立新 session。
15. session URI、API key、prompt 不出現在 DB error/log/report/JSON；
    所有輸出經單一 sanitizer，`report --job` 只讀本機。
16. 舊 job 沒有 visual version 時仍能 static render／resume／upload。
17. scheduler 只有 separate paid opt-in、private 與 per-run caps 全部成立才可啟動，
    且不自動核准/regenerate/處理 uncertain。
18. canonical START_HERE 的 Mac bash 命令全由 parser test 驗證；doctor/demo TTHW
    都是零 paid call。

## Paid private rollout acceptance gate

此 gate 需要執行當下的明確成本授權，不阻擋 code-complete 或 merge。只有通過後
才允許 scheduler paid opt-in：

1. 使用當日 pricing snapshot 完成 watermark smoke test 與人工可見標誌判定。
2. 2 小時 private 影片通過 xfade、originality、長片 eligibility、disclosure、
   YouTube 1080p processing 與人工視覺驗收。
3. 5 小時 private 影片通過 A B C D A B C D A B、D→A、九個 xfade、
   音訊完整性與最多四個唯一 Veo assets。
4. 以可安全控制的 non-final chunk 中斷驗證同一 session／308 resume；不得用
   production video 人為製造 final-byte uncertainty。
5. 保存 acceptance artifact：`accepted_at`、operator、pricing snapshot、job ID、
   YouTube video ID、duration、checklist results；不保存 session URI 或 secrets。
6. 任一項失敗只保持 scheduler disabled，不撤銷已通過的 code completion。
