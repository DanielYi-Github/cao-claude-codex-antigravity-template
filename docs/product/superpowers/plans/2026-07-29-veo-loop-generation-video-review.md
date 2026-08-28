# Veo 循環生成與影片審核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 針對每張核准場景圖生成兩段 Veo 3.1 Fast 候選，可靠保存 operation、原始影片與 normalized 無聲循環，執行接縫／動態 QC，並在 localhost 完成影片與縮圖人工核准。

**Architecture:** `VideoGenerationWorkflow` 只負責 at-most-once 付費 start、持久化 operation 與安全 poll／normalize；`media.visual_quality` 負責 FFprobe、音軌移除、8 秒正規化與像素分數；`VisualRepository` 執行 scene／asset 歸屬與原子核准 transaction；既有 `ReviewServer` 依資料狀態顯示影片模式。任何 in-flight 或 uncertain 資產都不會被普通 resume 重送，也不能進入完成審核。

**Tech Stack:** Python 3.11+、google-genai Veo long-running operations、FFmpeg／FFprobe、Pillow、SQLite、stdlib HTTP、pytest。

---

## DX review 核准契約與前置依賴

開始本計畫前，Plan 1 與 Plan 2 completion gate 必須全數通過；Plan 3 直接沿用：

- `PaidStartUncertainError` 與 `start_uncertain`，不得自行設計第二套 retry。
- `ProgressReporter` 的 x/y、scene、variant、elapsed、safe Ctrl+C 與 next command。
- `VisualRepository` 的 transaction／selected asset invariant。
- versioned migration runner；不得要求刪除 SQLite 或退回舊計畫手改 schema。

本計畫新增的硬規則：

- reserve asset 與 `status='starting'` 必須在呼叫 Veo 前 commit；若 hard crash 後沒有 operation ID，resume 將資產改成 `start_uncertain`，不會第二次 start。
- operation ID 一旦取得立即 commit 為 `polling`；poll／download 暫時失敗只更新 error，保留 `polling`。
- 只要 job 仍有 `reserved`、`starting`、`polling` 或 `normalizing` video asset，Plan 4 必須維持 `generating_videos`，不得切換 `awaiting_video_review`。
- reject video scene 在同一 transaction 清除 `selected_video_asset_id`、`approved_at` 並設 `needs_video_regeneration`。
- finish video review 必須重新 join selected ready video、檢查 normalized 檔案存在、scene 狀態為 `video_selected`，並驗證恰好一張 approved thumbnail。
- 額外生成 service 可以在本計畫建立，但 CLI 只能由 Plan 4 的獨立 `regenerate` 命令公開；`resume` 不接受 regeneration flags。

---

## Scope and file map

| File | Responsibility |
|---|---|
| `src/lyria_auto/visual_models.py` | VideoQC 與 explicit paid authorization types |
| `src/lyria_auto/db.py` | visual asset 的 raw path 欄位 |
| `src/lyria_auto/media/visual_quality.py` | probe、抽幀、MAE、音軌移除、loop normalize |
| `src/lyria_auto/visual_workflow.py` | Veo reserve／start／persist／poll／QC |
| `src/lyria_auto/visual_repository.py` | video selection、thumbnail approval、finish gate |
| `src/lyria_auto/review_server.py` | muted loop comparison、接點模式、thumbnail preview |
| `tests/test_visual_video_quality.py` | 媒體 QC 與 audio removal |
| `tests/test_visual_video_workflow.py` | 八段硬上限、operation resume、無自動補生 |
| `tests/test_video_review.py` | 跨 scene 防護、完成條件、HTTP 頁面 |
| `tests/test_paid_regeneration.py` | 額外輸出精確授權 |
| `tests/test_video_review_state.py` | select→reject→finish、stale video 與 thumbnail invariant |
| `tests/test_video_inflight_gate.py` | starting／polling／normalizing 不進入審核 |

全計畫限制：

- 預設每個有效 scene 兩段影片，四景共八段。
- first frame 與 last frame 都使用同一張核准圖片。
- operation ID 必須在第一次 poll 前 commit。
- raw Veo 檔保留；審核與渲染只使用移除音軌後的 normalized loop。
- QC 不合格時標記 `qc_failed`；不自動重生、不自動切 Omni 或 Standard。
- 只有 Plan 4 的 `regenerate` 命令搭配精確 `--allow-outputs` 才可超出預設候選。
- Review server 不呼叫模型、不把 qc_failed 資產設為可核准。
- `start_uncertain` 不可核准、不可由普通 resume 重送，也不計為可完成的 terminal candidate。

---

### Task 1: Video probe, frame metrics and normalized silent loops

**Files:**
- Modify: `src/lyria_auto/visual_models.py`
- Modify: `src/lyria_auto/media/visual_quality.py`
- Create: `tests/test_visual_video_quality.py`

- [ ] **Step 1: Write failing metric and FFmpeg tests**

建立 `tests/test_visual_video_quality.py`：

```python
from __future__ import annotations

import subprocess

from PIL import Image

from lyria_auto.media.visual_quality import (
    inspect_video,
    normalized_mae,
    normalize_loop_video,
    probe_video,
)


def image(path, color):
    Image.new("RGB", (64, 36), color).save(path, "PNG")


def test_normalized_mae_distinguishes_same_and_different_frames(tmp_path):
    a = tmp_path / "a.png"
    b = tmp_path / "b.png"
    c = tmp_path / "c.png"
    image(a, (20, 20, 20))
    image(b, (20, 20, 20))
    image(c, (220, 220, 220))

    assert normalized_mae(a, b) == 0
    assert normalized_mae(a, c) > 0.7


def test_normalize_loop_removes_audio_and_sets_1080p_24fps(tmp_path):
    raw = tmp_path / "raw.mp4"
    normalized = tmp_path / "normalized.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(raw),
        ],
        check=True,
        capture_output=True,
    )

    normalize_loop_video(raw, normalized, width=1920, height=1080, fps=24, duration=2)
    info = probe_video(normalized)
    streams = info["streams"]

    assert len([s for s in streams if s["codec_type"] == "video"]) == 1
    assert len([s for s in streams if s["codec_type"] == "audio"]) == 0
    video = next(s for s in streams if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1920, 1080)
    assert video["avg_frame_rate"] == "24/1"


def test_inspect_video_rejects_static_motion(monkeypatch, tmp_path):
    frames = []
    for index in range(5):
        path = tmp_path / f"{index}.png"
        image(path, (50, 50, 50))
        frames.append(path)
    monkeypatch.setattr(
        "lyria_auto.media.visual_quality.extract_sample_frames",
        lambda path, duration, output_dir: frames,
    )
    monkeypatch.setattr(
        "lyria_auto.media.visual_quality.probe_video",
        lambda path: {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24/1",
                }
            ],
            "format": {"duration": "8.0"},
        },
    )

    result = inspect_video(tmp_path / "video.mp4")

    assert result.passed is False
    assert result.motion_score == 0
    assert "靜止" in result.reason


def test_inspect_video_rejects_black_sample_frames(monkeypatch, tmp_path):
    frames = []
    for index in range(5):
        path = tmp_path / f"black_{index}.png"
        image(path, (0, 0, 0))
        frames.append(path)
    monkeypatch.setattr(
        "lyria_auto.media.visual_quality.extract_sample_frames",
        lambda path, duration, output_dir: frames,
    )
    monkeypatch.setattr(
        "lyria_auto.media.visual_quality.probe_video",
        lambda path: {
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24/1",
                }
            ],
            "format": {"duration": "8.0"},
        },
    )

    result = inspect_video(tmp_path / "video.mp4")

    assert result.passed is False
    assert "黑畫面" in result.reason
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_video_quality.py -v
```

Expected: imports for video functions fail.

- [ ] **Step 3: Add VideoQC type**

在 `src/lyria_auto/visual_models.py` 加入：

```python
@dataclass(frozen=True)
class VideoQC:
    passed: bool
    width: int
    height: int
    duration_seconds: float
    fps: float
    seam_score: float
    motion_score: float
    reason: str
    reason_code: str = "VISUAL_VIDEO_QC_FAILED"


@dataclass(frozen=True)
class PaidOutputAuthorization:
    kind: str
    scene_label: str
    requested_outputs: int
    allowed_outputs: int

    def require_exact(self) -> None:
        if self.requested_outputs <= 0:
            raise ValueError("requested_outputs 必須大於 0")
        if self.requested_outputs != self.allowed_outputs:
            raise ValueError(
                f"{self.kind} 額外輸出必須精確授權 "
                f"{self.requested_outputs}，目前授權 {self.allowed_outputs}"
            )
```

- [ ] **Step 4: Implement video quality functions**

在 `src/lyria_auto/media/visual_quality.py` imports 加入：

```python
import json
import statistics
import tempfile

from PIL import ImageChops, ImageStat

from ..utils import run_command
from ..visual_models import VideoQC
```

在檔案末尾加入：

```python
def probe_video(path: str | Path) -> dict:
    result = run_command(
        [
            "ffprobe", "-v", "error", "-show_streams", "-show_format",
            "-of", "json", str(path),
        ]
    )
    return json.loads(result.stdout)


def normalized_mae(first: str | Path, second: str | Path) -> float:
    with Image.open(first).convert("RGB") as left:
        with Image.open(second).convert("RGB") as right:
            if left.size != right.size:
                right = right.resize(left.size, Image.Resampling.BILINEAR)
            difference = ImageChops.difference(left, right)
            means = ImageStat.Stat(difference).mean
    return sum(means) / (len(means) * 255.0)


def extract_sample_frames(
    path: str | Path, duration: float, output_dir: str | Path
) -> list[Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamps = [float(second) for second in range(max(1, int(duration)))]
    timestamps.append(max(0.0, duration - 0.05))
    outputs = []
    for index, timestamp in enumerate(timestamps):
        output = directory / f"frame_{index}.png"
        run_command(
            [
                "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(path),
                "-frames:v", "1", "-vf", "scale=480:-2", str(output),
            ]
        )
        outputs.append(output)
    return outputs


def _fps(value: str) -> float:
    numerator, denominator = value.split("/", 1)
    return float(numerator) / max(1.0, float(denominator))


def inspect_video(
    path: str | Path,
    *,
    seam_max: float = 0.06,
    motion_min: float = 0.002,
    motion_max: float = 0.10,
    duration_target: float = 8.0,
    duration_tolerance: float = 0.25,
) -> VideoQC:
    try:
        info = probe_video(path)
        streams = [s for s in info["streams"] if s["codec_type"] == "video"]
        if len(streams) != 1:
            raise ValueError("需要且只能有一個 video stream")
        stream = streams[0]
        width = int(stream["width"])
        height = int(stream["height"])
        duration = float(info["format"]["duration"])
        fps = _fps(stream["avg_frame_rate"])
        with tempfile.TemporaryDirectory(prefix="lyria-visual-qc-") as temp:
            frames = extract_sample_frames(path, duration, temp)
            seam = normalized_mae(frames[0], frames[-1])
            steps = [
                normalized_mae(frames[index], frames[index + 1])
                for index in range(len(frames) - 1)
            ]
            motion = statistics.median(steps)
            brightness = []
            for frame in frames:
                with Image.open(frame).convert("RGB") as image:
                    brightness.append(sum(ImageStat.Stat(image).mean) / 3)
    except Exception as exc:
        return VideoQC(False, 0, 0, 0, 0, 1, 0, f"影片無法解碼：{exc}")
    reasons = []
    if (width, height) != (1920, 1080):
        reasons.append(f"解析度錯誤 {width}x{height}")
    if abs(fps - 24.0) > 0.1:
        reasons.append(f"fps 錯誤 {fps:.3f}")
    if abs(duration - duration_target) > duration_tolerance:
        reasons.append(f"時長錯誤 {duration:.3f}s")
    if seam > seam_max:
        reasons.append(f"首尾接縫過大 {seam:.4f}")
    if motion < motion_min:
        reasons.append(f"畫面近乎靜止 {motion:.4f}")
    if motion > motion_max:
        reasons.append(f"動態過劇烈 {motion:.4f}")
    if min(brightness) < 2:
        reasons.append("取樣影格包含長時間黑畫面")
    return VideoQC(
        not reasons, width, height, duration, fps, seam, motion,
        "；".join(reasons) if reasons else "ok",
    )


def normalize_loop_video(
    input_path: str | Path,
    output_path: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 24,
    duration: float = 8.0,
) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.stem + ".partial" + output.suffix)
    run_command(
        [
            "ffmpeg", "-y", "-i", str(input_path), "-an",
            "-vf",
            (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},"
                "format=yuv420p"
            ),
            "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "medium",
            "-movflags", "+faststart", str(partial),
        ]
    )
    partial.replace(output)
    return output
```

- [ ] **Step 5: Run video quality tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_video_quality.py -v
```

Expected: `4 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/visual_models.py src/lyria_auto/media/visual_quality.py tests/test_visual_video_quality.py
git commit -m "feat: score and normalize silent visual loops"
```

---

### Task 2: Use the persisted raw video and fps fields for approval state

**Files:**
- Modify: `src/lyria_auto/config_migration.py`
- Modify: `src/lyria_auto/visual_repository.py`
- Create: `tests/test_video_repository.py`
- Create: `tests/test_visual_schema_migration.py`

- [ ] **Step 1: Write failing repository tests**

建立 `tests/test_video_repository.py`：

```python
from __future__ import annotations

import pytest

from lyria_auto.db import StateDB
from lyria_auto.errors import VisualApprovalError
from lyria_auto.visual_repository import VisualRepository


def test_video_selection_requires_ready_asset_from_same_scene(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        repo = VisualRepository(db)
        a = repo.upsert_scene(job_id, 0, "A", {}, "i", "m")
        b = repo.upsert_scene(job_id, 1, "B", {}, "i", "m")
        video = repo.reserve_asset(job_id, b, "scene_video", 0, "gemini", "veo", "p", 0.96, {})
        mark_video_ready_in_fixture(
            repo, video, path="/tmp/b.mp4", raw_path="/tmp/b_raw.mp4"
        )

          with pytest.raises(VisualApprovalError, match="不屬於"):
              repo.select_video(
                  a,
                  video,
                  expected_version=int(repo.scene(a)["state_version"]),
              )
    finally:
        db.close()


def test_finish_video_review_requires_every_scene_and_thumbnail(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        repo = VisualRepository(db)
        scene = repo.upsert_scene(job_id, 0, "A", {}, "i", "m")
        video = repo.reserve_asset(job_id, scene, "scene_video", 0, "gemini", "veo", "p", 0.96, {})
        mark_video_ready_in_fixture(
            repo, video, path="/tmp/a.mp4", raw_path="/tmp/a_raw.mp4"
        )
          repo.select_video(
              scene,
              video,
              expected_version=int(repo.scene(scene)["state_version"]),
          )

        with pytest.raises(VisualApprovalError, match="縮圖"):
            repo.finish_video_review(job_id)
    finally:
        db.close()
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_video_repository.py -v
```

Expected: `update_asset` ignores `raw_path`／`fps` and `select_video` is missing.

- [ ] **Step 3: Add Plan 3 migration `0003_visual_video_fields`**

Plan 1 的全新資料庫已包含下列欄位；但既有 Plan 2 資料庫仍須由版本化 migration 安全升級：

```sql
  raw_path TEXT,
  path TEXT,
  duration_seconds REAL,
  fps REAL,
```

在 Plan 1 共用 `schema_migrations` registry 增加固定 ID／checksum：

- registry 尚未套用此 ID 時才執行；`PRAGMA table_info(visual_assets)` 只作
  precondition／postcondition assertion，不可代替 registry。
- migration 在單一 transaction 內更新 schema version；失敗時 rollback。
- `--preview` 僅顯示將新增的欄位，不寫入資料庫。
- `tests/test_visual_schema_migration.py` 建立一份 Plan 2 舊 DB，放入 scene、image selection 與既有 asset，升級後逐筆驗證資料與 custom config 不變。

- [ ] **Step 4: Extend explicit repository transition methods**

不得恢復 Plan 2 已禁止的任意 `update_asset(**values)`。擴充
`record_operation()`、`mark_normalizing()`、`mark_video_ready()`、
`mark_qc_failed()` 等明確方法，使其可保存 `raw_path`／`fps`，且每個方法都要求
expected status、expected `state_version` 與 lease `owner_token`。

在 `src/lyria_auto/visual_repository.py` 末尾加入：

```python
    def select_video(
        self, scene_id: int, asset_id: int, *, expected_version: int
    ) -> None:
        asset = self.asset(asset_id)
        scene = self.scene(scene_id)
        if (
            asset is None
            or scene is None
            or int(asset["scene_id"] or -1) != scene_id
            or int(asset["job_id"]) != int(scene["job_id"])
        ):
            raise VisualApprovalError("選擇的影片資產不屬於這個 scene")
        if asset["asset_type"] != "scene_video" or asset["status"] != "ready":
            raise VisualApprovalError("只能選擇通過 QC 的 ready scene_video")
        with self.db.conn:
            changed = self.db.conn.execute(
                """
                UPDATE visual_scenes
                SET selected_video_asset_id=?,status='video_selected',
                    state_version=state_version+1,approved_at=NULL,updated_at=?
                WHERE id=? AND state_version=?
                  AND status IN ('awaiting_video_review','video_selected')
                """,
                (asset_id, utc_now_iso(), scene_id, expected_version),
            ).rowcount
            if changed != 1:
                raise VisualApprovalError("scene 狀態已更新；重新載入 review page")

    def reject_video_scene(
        self, scene_id: int, *, expected_version: int
    ) -> None:
        if self.scene(scene_id) is None:
            raise VisualApprovalError("找不到要退回的 scene")
        with self.db.conn:
            changed = self.db.conn.execute(
                """
                UPDATE visual_scenes
                SET selected_video_asset_id=NULL,
                      status='needs_video_regeneration',
                      state_version=state_version+1,
                      approved_at=NULL,
                      updated_at=?
                WHERE id=? AND state_version=?
                  AND status IN ('awaiting_video_review','video_selected')
                """,
                (utc_now_iso(), scene_id, expected_version),
            ).rowcount
            if changed != 1:
                raise VisualApprovalError("scene 狀態已更新；重新載入 review page")

    def approve_thumbnail(
        self, job_id: int, asset_id: int, *, expected_version: int
    ) -> None:
        asset = self.asset(asset_id)
        if (
            asset is None
            or int(asset["job_id"]) != job_id
            or asset["asset_type"] != "thumbnail_background"
            or asset["status"] != "ready"
        ):
            raise VisualApprovalError("只能核准目前 job 的 ready thumbnail background")
        if not self._cas_transition_asset(
            asset_id=asset_id,
            expected_status="ready",
            expected_version=expected_version,
            new_status="approved",
            values={},
        ):
            raise VisualApprovalError("縮圖狀態已更新；重新載入 review page")

    def finish_video_review(self, job_id: int) -> None:
        scenes = self.scenes(job_id)
        if not scenes:
            raise VisualApprovalError("job 沒有可審核的 scene")
        for scene in scenes:
            selected_id = scene["selected_video_asset_id"]
            asset = self.asset(int(selected_id)) if selected_id is not None else None
            if (
                scene["status"] != "video_selected"
                or asset is None
                or int(asset["job_id"]) != job_id
                or int(asset["scene_id"]) != int(scene["id"])
                or asset["asset_type"] != "scene_video"
                or asset["status"] != "ready"
                or not asset["path"]
                or not Path(asset["path"]).is_file()
            ):
                raise VisualApprovalError(
                    f"scene {scene['label']} 的選擇已失效，請重新審核"
                )
        thumbnails = [
            row for row in self.assets(job_id, asset_type="thumbnail_background")
            if row["status"] == "approved"
            and row["path"]
            and Path(row["path"]).is_file()
        ]
        if len(thumbnails) != 1:
            raise VisualApprovalError("有效縮圖必須且只能核准一張")
        now = utc_now_iso()
        with self.db.conn:
            self.db.conn.execute(
                """
                  UPDATE visual_scenes
                  SET status='video_approved',state_version=state_version+1,
                      approved_at=?,updated_at=?
                  WHERE job_id=? AND status='video_selected'
                """,
                (now, now, job_id),
            )
```

`finish_video_review()` 的 scene/asset/thumbnail 查詢、immutable snapshot identity
重驗與最後 UPDATE 必須全部位於同一 `BEGIN IMMEDIATE` transaction，並驗證
updated rowcount 等於 scene count。所有 review POST 都攜帶 hidden
`state_version`；stale request 回 `VISUAL_REVIEW_STALE`。

- [ ] **Step 5: Run repository tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_video_repository.py tests/test_visual_repository.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/visual_repository.py tests/test_video_repository.py
git commit -m "feat: persist raw Veo assets and video approvals"
```

---

### Task 3: Bounded resumable Veo generation workflow

**Files:**
- Modify: `src/lyria_auto/visual_workflow.py`
- Create: `tests/test_visual_video_workflow.py`

`VideoGenerationWorkflow` 必須由 caller 注入 Plan 1 的 persisted
`PaidStageAuthorization` 與 active `SideEffectLease`。每個 scene/variant 的
`identity_key` 在同一 `BEGIN IMMEDIATE` transaction 中消耗 allowance 並以
`reserved → starting` CAS claim；claim 失敗者不得呼叫 `start_video()`。
`starting` asset 只有在原 owner lease 已過期且新 process 重開 DB 後，才可
reconcile 為 `start_uncertain`。

- [ ] **Step 1: Write failing operation lifecycle tests**

建立 `tests/test_visual_video_workflow.py`：

```python
from __future__ import annotations

from pathlib import Path

from lyria_auto.db import StateDB
from lyria_auto.utils import sha256_file
from lyria_auto.visual_models import VideoPoll, VideoQC
from lyria_auto.visual_repository import VisualRepository
from lyria_auto.visual_workflow import VideoGenerationWorkflow


class FakeVeo:
    def __init__(self):
        self.starts = 0
        self.polls: list[str] = []
        self.operation_lookup = lambda operation_id: None

    def start_video(self, prompt, frame_path):
        self.starts += 1
        return f"operations/{self.starts}"

    def poll_video(self, operation_id, output_path):
        self.polls.append(operation_id)
        persisted = self.operation_lookup(operation_id)
        assert persisted is not None
        assert persisted["operation_id"] == operation_id
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"raw-video")
        return VideoPoll(True, output, sha256_file(output))


class PollFailsOnce(FakeVeo):
    def __init__(self):
        super().__init__()
        self.failed_once = False

    def poll_video(self, operation_id, output_path):
        if not self.failed_once:
            self.failed_once = True
            self.polls.append(operation_id)
            raise RuntimeError("temporary poll failure")
        return super().poll_video(operation_id, output_path)


def fake_normalize(raw, normalized, **kwargs):
    output = Path(normalized)
    output.write_bytes(b"normalized-video")
    return output


def fake_inspect(path, **kwargs):
    return VideoQC(True, 1920, 1080, 8.0, 24.0, 0.01, 0.01, "ok")


def prepared(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    job_id = db.create_job("main", False, {})
    repo = VisualRepository(db)
    for position, label in enumerate(("A", "B", "C", "D")):
        scene = repo.upsert_scene(job_id, position, label, {}, "image", "motion")
          image = repo.reserve_asset(job_id, scene, "scene_image", 0, "gemini", "image", "p", 0.101, {})
          image_path = tmp_path / f"{label}.png"
          image_path.write_bytes(b"image")
          mark_image_ready_in_fixture(repo, image, path=str(image_path))
          repo.select_image(
              scene,
              image,
              expected_version=int(repo.scene(scene)["state_version"]),
          )
    repo.finish_image_review(job_id)
    return db, job_id, repo


def workflow(repo, fake, job_id):
    fake.operation_lookup = lambda operation_id: repo.db.conn.execute(
        "SELECT * FROM visual_assets WHERE operation_id=?",
        (operation_id,),
    ).fetchone()
    authorization, lease = authorized_test_context(repo, job_id, videos=8)
    return VideoGenerationWorkflow(
        repo,
        fake,
        video_model="veo-3.1-fast-generate-preview",
        sleep=lambda _: None,
        normalize=fake_normalize,
        inspect=fake_inspect,
        authorization=authorization,
        lease=lease,
    )


def test_four_scenes_create_exactly_eight_operations(tmp_path):
    db, job_id, repo = prepared(tmp_path)
    fake = FakeVeo()
    try:
        workflow(repo, fake, job_id).generate(job_id, tmp_path / "job")

        assert fake.starts == 8
        assert len(repo.assets(job_id, asset_type="scene_video")) == 8
        assert all(row["status"] == "ready" for row in repo.assets(job_id, asset_type="scene_video"))
    finally:
        db.close()


def test_resume_polls_persisted_operation_without_second_start(tmp_path):
    db, job_id, repo = prepared(tmp_path)
    fake = FakeVeo()
    try:
        scene = repo.scenes(job_id)[0]
        asset = repo.reserve_asset(job_id, int(scene["id"]), "scene_video", 0, "gemini", "veo", "p", 0.96, {})
        seed_asset_state(
            repo,
            asset,
            status="polling",
            operation_id="operations/existing",
        )

        workflow(repo, fake, job_id).generate(job_id, tmp_path / "job")

        assert "operations/existing" in fake.polls
        assert fake.starts == 7
    finally:
        db.close()


def test_poll_exception_keeps_operation_resumable_without_second_start(tmp_path):
    db, job_id, repo = prepared(tmp_path)
    fake = PollFailsOnce()
    try:
        runner = workflow(repo, fake, job_id)
        runner.generate(job_id, tmp_path / "job")
        first = repo.assets(job_id, asset_type="scene_video")[0]
        assert first["status"] == "polling"
        assert first["operation_id"] == "operations/1"
        assert fake.starts == 8

        runner.generate(job_id, tmp_path / "job")

        assert fake.starts == 8
        assert repo.asset(int(first["id"]))["status"] == "ready"
    finally:
        db.close()
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_video_workflow.py -v
```

Expected: `VideoGenerationWorkflow` import fails.

- [ ] **Step 3: Implement Veo workflow**

在 `src/lyria_auto/visual_workflow.py` imports 加入：

```python
import time
from collections.abc import Callable

from .media.visual_quality import inspect_video, normalize_loop_video
from .visual_models import VideoQC
```

在同檔末尾加入：

```python
class VideoGenerationWorkflow:
    def __init__(
        self,
        repo: VisualRepository,
        client: Any,
        *,
        video_model: str,
        provider: str = "gemini-developer-api",
        max_outputs: int = 8,
        unit_cost: float = 0.96,
        budget_usd: float = 10.0,
        pricing_snapshot: dict[str, Any] | None = None,
        poll_interval_seconds: float = 10,
          sleep: Callable[[float], None] = time.sleep,
          normalize: Callable[..., Path] = normalize_loop_video,
          inspect: Callable[..., VideoQC] = inspect_video,
          authorization: PaidStageAuthorization,
          lease: SideEffectLease,
    ):
        self.repo = repo
        self.client = client
        self.video_model = video_model
        self.provider = provider
        self.max_outputs = max_outputs
        self.unit_cost = unit_cost
        self.budget_usd = budget_usd
        self.pricing_snapshot = pricing_snapshot or {}
        self.poll_interval_seconds = poll_interval_seconds
        self.sleep = sleep
          self.normalize = normalize
          self.inspect = inspect
          self.authorization = authorization
          self.lease = lease

    def _existing_identity(
        self, job_id: int, scene_id: int, variant: int
    ) -> list[Any]:
        return [
            asset for asset in self.repo.assets(job_id, asset_type="scene_video")
            if int(asset["scene_id"]) == scene_id
            and int(asset["variant_index"]) == variant
        ]

    def _generate_one(
        self,
        job_id: int,
        scene: Any,
        variant: int,
        job_dir: Path,
        authorized_extra: bool = False,
    ) -> None:
        scene_id = int(scene["id"])
        existing = self._existing_identity(job_id, scene_id, variant)
        if (
            not existing
            and not authorized_extra
            and len(self.repo.assets(job_id, asset_type="scene_video"))
            >= self.max_outputs
        ):
            raise VisualGenerationError("預設影片硬上限為八段，額外輸出需要顯式授權")
        if (
            not existing
            and not authorized_extra
            and self.repo.estimated_cost(job_id) + self.unit_cost
            > self.budget_usd
        ):
            projected = self.repo.estimated_cost(job_id) + self.unit_cost
            raise VisualGenerationError(
                f"預估視覺成本 US${projected:.2f} 超過預設預算 "
                f"US${self.budget_usd:.2f}"
            )
        asset_id = self.repo.reserve_asset(
            job_id, scene_id, "scene_video", variant, self.provider,
            self.video_model, scene["motion_prompt"], self.unit_cost,
            self.pricing_snapshot,
        )
        asset = self.repo.asset(asset_id)
        if asset["status"] == "ready" and asset["path"] and Path(asset["path"]).exists():
            return
        if asset["status"] in (
            "failed_generation", "failed_tampered", "qc_failed", "start_uncertain"
        ):
            return
        image = self.repo.asset(int(scene["selected_image_asset_id"]))
        root = job_dir / "visual"
        raw = root / f"scene_{scene['label']}_{variant + 1}_raw.mp4"
        normalized = root / f"scene_{scene['label']}_{variant + 1}_loop.mp4"
        operation_id = asset["operation_id"]

          # start 已送出但 operation ID 未 commit 的 crash window 不可透明重送。
          if asset["status"] == "starting" and not operation_id:
              if self.repo.lease_is_active_for_owner(
                  job_id, str(asset["owner_token"])
              ):
                  return
              self.repo.mark_start_uncertain(
                  asset_id,
                  expected_version=int(asset["state_version"]),
                  reason_code="VISUAL_VIDEO_START_UNCERTAIN",
              )
              return

          try:
              if asset["status"] == "reserved":
                  claimed = self.repo.claim_paid_start(
                      asset_id=asset_id,
                      expected_status="reserved",
                      expected_version=int(asset["state_version"]),
                      authorization_id=self.authorization.id,
                      identity_key=f"asset:{asset_id}",
                      owner_token=self.lease.owner_token,
                  )
                  if not claimed:
                      return
                  operation_id = self.client.start_video(
                      scene["motion_prompt"], image["path"]
                  )
                  current = self.repo.asset(asset_id)
                  self.repo.record_operation(
                      asset_id,
                      operation_id=operation_id,
                      expected_status="starting",
                      expected_version=int(current["state_version"]),
                      owner_token=self.lease.owner_token,
                  )

            current = self.repo.asset(asset_id)
            if current["status"] == "polling":
                while True:
                      try:
                          poll = self.client.poll_video(operation_id, raw)
                      except VisualPollTransientError as exc:
                          latest = self.repo.asset(asset_id)
                          self.repo.record_poll_transient(
                              asset_id,
                              expected_version=int(latest["state_version"]),
                              error=sanitize_exception(exc),
                          )
                          return
                    if not poll.done:
                        self.reporter.polling(
                            asset_id=asset_id,
                            operation_id=operation_id,
                            elapsed_seconds=self.clock.elapsed(asset_id),
                        )
                        self.sleep(self.poll_interval_seconds)
                        continue
                      if poll.error or poll.path is None:
                          current = self.repo.asset(asset_id)
                          self.repo.mark_generation_failed(
                              asset_id,
                              expected_status="polling",
                              expected_version=int(current["state_version"]),
                              error_code=poll.error_code or "VEO_RESULT_MISSING",
                          )
                          return
                      current = self.repo.asset(asset_id)
                      self.repo.mark_normalizing(
                          asset_id,
                          expected_status="polling",
                          expected_version=int(current["state_version"]),
                          owner_token=self.lease.owner_token,
                          raw_path=str(raw),
                      )
                    break

            # normalizing 是可重入的本機階段；resume 使用已下載 raw。
            current = self.repo.asset(asset_id)
              raw = Path(current["raw_path"]) if current["raw_path"] else raw
              if current["status"] != "normalizing" or not raw.is_file():
                  self.repo.mark_generation_failed(
                      asset_id,
                      expected_status=str(current["status"]),
                      expected_version=int(current["state_version"]),
                      error_code="VEO_RAW_VIDEO_MISSING",
                  )
                return
            self.normalize(raw, normalized, width=1920, height=1080, fps=24, duration=8.0)
              qc = self.inspect(normalized)
              if not qc.passed:
                  current = self.repo.asset(asset_id)
                  self.repo.mark_qc_failed(
                      asset_id,
                      expected_status="normalizing",
                      expected_version=int(current["state_version"]),
                      owner_token=self.lease.owner_token,
                      raw_path=str(raw), path=str(normalized),
                      duration_seconds=qc.duration_seconds,
                      fps=qc.fps,
                      seam_score=qc.seam_score, motion_score=qc.motion_score,
                      reason_code=qc.reason_code,
                  )
                  raise VisualGenerationError(qc.reason)
              current = self.repo.asset(asset_id)
              self.repo.mark_video_ready(
                  asset_id,
                  expected_status="normalizing",
                  expected_version=int(current["state_version"]),
                  owner_token=self.lease.owner_token,
                  raw_path=str(raw), path=str(normalized),
                  mime_type="video/mp4", sha256=sha256_file(normalized),
                  width=qc.width, height=qc.height,
                  duration_seconds=qc.duration_seconds,
                  fps=qc.fps,
                  seam_score=qc.seam_score, motion_score=qc.motion_score,
              )
          except PaidStartUncertainError as exc:
              current = self.repo.asset(asset_id)
              self.repo.mark_start_uncertain(
                  asset_id,
                  expected_version=int(current["state_version"]),
                  reason_code=exc.code,
              )
              return
          except Exception as exc:
              current = self.repo.asset(asset_id)
              if current["status"] in ("starting", "polling", "normalizing"):
                  self.repo.record_stage_error(
                      asset_id,
                      expected_version=int(current["state_version"]),
                      error=sanitize_exception(exc),
                  )
              elif current["status"] != "qc_failed":
                  self.repo.mark_generation_failed(
                      asset_id,
                      expected_status=str(current["status"]),
                      expected_version=int(current["state_version"]),
                      error=sanitize_exception(exc),
                  )
            return

    def generate(self, job_id: int, job_dir: str | Path) -> None:
        scenes = self.repo.scenes(job_id)
        if not scenes or any(row["status"] != "image_approved" for row in scenes):
            raise VisualGenerationError("所有場景圖片必須先完成核准")
        for scene in scenes:
            for variant in range(2):
                self._generate_one(job_id, scene, variant, Path(job_dir))
```

在 imports 補上：

```python
from .errors import PaidStartUncertainError, VisualPollTransientError
from .utils import sha256_file
```

`reporter`、`clock` 使用 Plan 2 的共用 progress abstraction；畫面至少顯示
`scene/variant`、`x/y`、polling、elapsed、Ctrl+C 可安全中止，以及下一條 `resume`
命令。progress UI 的例外不得改變資產狀態。

- [ ] **Step 4: Run workflow tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_video_workflow.py tests/test_visual_video_quality.py -v
```

Expected: all pass；預存 operation case 只有 7 個新 starts，poll exception case 第二次執行仍維持總數 8。

- [ ] **Step 5: Commit**

```bash
git add src/lyria_auto/visual_workflow.py tests/test_visual_video_workflow.py
git commit -m "feat: generate resumable Veo loop candidates"
```

---

### Task 4: Video and thumbnail review mode

**Files:**
- Modify: `src/lyria_auto/review_server.py`
- Create: `tests/test_video_review.py`

- [ ] **Step 1: Write failing review service tests**

建立 `tests/test_video_review.py`：

```python
from __future__ import annotations

import pytest

from lyria_auto.db import StateDB
from lyria_auto.errors import VisualApprovalError
from lyria_auto.review_server import ReviewService, render_review
from lyria_auto.visual_repository import VisualRepository


def prepared(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    job_id = db.create_job("main", False, {})
    repo = VisualRepository(db)
    scene = repo.upsert_scene(job_id, 0, "A", {}, "image", "motion")
    video = repo.reserve_asset(job_id, scene, "scene_video", 0, "gemini", "veo", "p", 0.96, {})
    path = tmp_path / "loop.mp4"
    path.write_bytes(b"video")
    mark_video_ready_in_fixture(
        repo,
        video,
        path=str(path),
        raw_path=str(tmp_path / "raw.mp4"),
        duration_seconds=8.0,
        seam_score=0.01,
        motion_score=0.01,
    )
    thumb = repo.reserve_asset(job_id, None, "thumbnail_background", 0, "gemini", "image", "p", 0.101, {})
    thumb_path = tmp_path / "thumbnail.jpg"
    thumb_path.write_bytes(b"image")
    mark_image_ready_in_fixture(repo, thumb, path=str(thumb_path))
    return db, job_id, scene, video, thumb, repo


def test_finish_requires_video_and_thumbnail_selection(tmp_path):
    db, job_id, scene, video, thumb, repo = prepared(tmp_path)
    try:
        service = ReviewService(repo, job_id)
        service.approve_video(
            scene,
            video,
            expected_version=int(repo.scene(scene)["state_version"]),
        )
        with pytest.raises(VisualApprovalError, match="縮圖"):
            service.finish_videos()
        service.approve_thumbnail(
            thumb,
            expected_version=int(repo.asset(thumb)["state_version"]),
        )
        service.finish_videos()
        assert repo.scenes(job_id)[0]["status"] == "video_approved"
    finally:
        db.close()


def test_video_review_html_is_muted_looping_and_shows_scores(tmp_path):
    db, job_id, scene, video, thumb, repo = prepared(tmp_path)
    try:
        page = render_review(repo, job_id, "token")
        assert "<video" in page
        assert "muted" in page
        assert "loop" in page
        assert "seam 0.0100" in page
        assert "motion 0.0100" in page
    finally:
        db.close()
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_video_review.py -v
```

Expected: `ReviewService.approve_video` and `render_review` are missing.

- [ ] **Step 3: Extend review service**

在 `ReviewService` 加入：

```python
    def approve_video(
        self, scene_id: int, asset_id: int, *, expected_version: int
    ) -> None:
        scenes = {int(row["id"]) for row in self.repo.scenes(self.job_id)}
        if scene_id not in scenes:
            raise VisualApprovalError("scene 不屬於目前 job")
        self.repo.select_video(
            scene_id, asset_id, expected_version=expected_version
        )

    def reject_video(self, scene_id: int, *, expected_version: int) -> None:
        scenes = {int(row["id"]) for row in self.repo.scenes(self.job_id)}
        if scene_id not in scenes:
            raise VisualApprovalError("scene 不屬於目前 job")
        self.repo.reject_video_scene(
            scene_id, expected_version=expected_version
        )

    def approve_thumbnail(
        self, asset_id: int, *, expected_version: int
    ) -> None:
        self.repo.approve_thumbnail(
            self.job_id, asset_id, expected_version=expected_version
        )

    def finish_videos(self) -> None:
        self.repo.finish_video_review(self.job_id)
```

- [ ] **Step 4: Add state-driven video HTML and POST routes**

把 `render_image_review()` 重新命名為 `_render_image_review()`，新增：

```python
def _render_video_review(repo: VisualRepository, job_id: int, token: str) -> str:
    sections = []
    for scene in repo.scenes(job_id):
        cards = []
        assets = [
            row for row in repo.assets(job_id, asset_type="scene_video")
            if row["scene_id"] == scene["id"]
        ]
        for asset in assets:
            selectable = asset["status"] == "ready"
            button = (
                f"<form method='post' action='/approve-video'>"
                f"<input type='hidden' name='token' value='{token}'>"
                f"<input type='hidden' name='scene_id' value='{int(scene['id'])}'>"
                f"<input type='hidden' name='asset_id' value='{int(asset['id'])}'>"
                "<button>核准這段</button></form>"
                if selectable else "<strong>QC failed，不可核准</strong>"
            )
            cards.append(
                f"<article><video muted loop autoplay controls "
                f"src='/asset?id={int(asset['id'])}&token={token}'></video>"
                "<button type='button' onclick='showSeam(this)'>顯示循環接點</button>"
                f"<p>{html.escape(str(asset['model']))} · "
                f"{float(asset['duration_seconds'] or 0):.3f}s · "
                f"{float(asset['fps'] or 0):.3f} fps · "
                f"seam {float(asset['seam_score'] or 0):.4f} · "
                f"motion {float(asset['motion_score'] or 0):.4f} · "
                f"US${float(asset['estimated_cost_usd']):.2f}</p>"
                f"<p>{html.escape(str(asset['error'] or 'QC ok'))}</p>"
                f"{button}</article>"
            )
        sections.append(
            f"<section><h2>場景 {html.escape(scene['label'])}</h2>"
            + "".join(cards) + "</section>"
        )
    thumbnails = [
        row for row in repo.assets(job_id, asset_type="thumbnail_background")
        if row["status"] in ("ready", "approved")
    ]
    thumbnail = ""
    if thumbnails:
        asset = thumbnails[-1]
        thumbnail = (
            f"<section><h2>縮圖</h2><img src='/asset?id={int(asset['id'])}&token={token}'>"
            f"<form method='post' action='/approve-thumbnail'>"
            f"<input type='hidden' name='token' value='{token}'>"
            f"<input type='hidden' name='asset_id' value='{int(asset['id'])}'>"
            "<button>核准縮圖</button></form></section>"
        )
    return (
        "<!doctype html><meta charset='utf-8'><title>Video Review</title>"
        "<style>body{font:16px system-ui;background:#17130f;color:#f5eadc;"
        "max-width:1400px;margin:auto;padding:24px}article{display:inline-block;"
        "width:47%;margin:1%;vertical-align:top}video,img{width:100%}</style>"
        "<script>function showSeam(button){const video=button.parentElement."
        "querySelector('video');video.currentTime=Math.max(0,video.duration-0.75);"
        "video.play();}</script>"
        + "".join(sections) + thumbnail
        + f"<form method='post' action='/finish-videos'>"
        f"<input type='hidden' name='token' value='{token}'>"
        "<button>確認完成影片核准</button></form>"
    )


def render_review(repo: VisualRepository, job_id: int, token: str) -> str:
    scenes = repo.scenes(job_id)
    if scenes and all(
        row["status"] in ("image_approved", "video_selected", "video_approved")
        for row in scenes
    ) and repo.assets(job_id, asset_type="scene_video"):
        return _render_video_review(repo, job_id, token)
    return _render_image_review(repo, job_id, token)
```

把 GET `/` 改為呼叫 `render_review()`。在 POST handler 加入：

```python
                      elif self.path == "/approve-video":
                          owner.service.approve_video(
                              int(form["scene_id"][0]),
                              int(form["asset_id"][0]),
                              expected_version=int(form["state_version"][0]),
                          )
                      elif self.path == "/reject-video":
                          owner.service.reject_video(
                              int(form["scene_id"][0]),
                              expected_version=int(form["state_version"][0]),
                          )
                      elif self.path == "/approve-thumbnail":
                          owner.service.approve_thumbnail(
                              int(form["asset_id"][0]),
                              expected_version=int(form["state_version"][0]),
                          )
                    elif self.path == "/finish-videos":
                        owner.service.finish_videos()
```

`/asset` 的 Content-Type 要在 DB 值為空時依 asset type fallback：

```python
                    mime = asset["mime_type"] or (
                        "video/mp4" if asset["asset_type"] == "scene_video"
                        else "image/jpeg"
                    )
                    self.send_header("Content-Type", mime)
```

同一任務內完成下列 guided review 契約：

- 每景只有一個「退回此場景」POST；成功後 repository 原子清除 stale selection，
  不能只切換前端樣式。
- selected 影片標示 `aria-current="true"` 且有明顯外框；`qc_failed`、
  `start_uncertain`、`failed_tampered` 顯示原因，選擇按鈕使用原生 `disabled`。
- 頁首固定顯示「已完成 scene x/y、縮圖是否核准」。條件未滿足時完成按鈕 disabled；
  backend 完成動作仍依 Task 2 重新 JOIN 驗證，不信任前端。
- checklist 包含接縫、過度運動、人物／物件變形、閃爍、文字／浮水印；
  原尺寸對話框、鍵盤 Esc 與 focus return 沿用圖片審核元件。
- 完成或退回後顯示下一條可複製命令與付費影響；不得暗示 `resume` 會補生。
- `tests/test_video_review_state.py` 覆蓋 select→reject→finish 被擋、
  selected asset 後來變 `qc_failed` 被擋、跨 job asset 被擋、缺檔被擋。
- `tests/test_video_review.py` 解析 HTML 驗證 selected、disabled、完成數與每景恰好一個 reject form。

- [ ] **Step 5: Run both review modes**

Run:

```bash
./.venv/bin/python -m pytest tests/test_review_server.py tests/test_video_review.py -v
```

Expected: all pass; image mode regression仍通過。

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/review_server.py tests/test_video_review.py
git commit -m "feat: review Veo loops and branded thumbnail locally"
```

---

### Task 5: Exact authorization for paid regeneration

`requested_outputs == allowed_outputs` 授權的是「最多可發出的新 paid start
intent 數」，不是保證成功產出的檔案數。每一個 intent 仍須遵守 Task 3 的
at-most-once 狀態機：先建立唯一 `authorization_id` 與 asset reservation，再把
asset 寫成 `starting`，最後才呼叫 provider。`start_uncertain` 不會消耗第二次
透明重試；若操作者決定重新生成，必須先人工 reconcile，再建立新的
authorization 與新的 variant identity。所有 normal `resume` 路徑都不得接受或
隱含 regeneration 授權。

**Files:**
- Modify: `src/lyria_auto/visual_workflow.py`
- Create: `tests/test_paid_regeneration.py`

- [ ] **Step 1: Write failing exact-authorization tests**

建立 `tests/test_paid_regeneration.py`：

```python
from __future__ import annotations

import pytest

from lyria_auto.visual_models import PaidOutputAuthorization


@pytest.mark.parametrize(
    ("requested", "allowed"),
    [(3, 0), (3, 2), (3, 4)],
)
def test_extra_image_authorization_must_match_exactly(requested, allowed):
    authorization = PaidOutputAuthorization("image", "A", requested, allowed)

    with pytest.raises(ValueError, match="精確授權"):
        authorization.require_exact()


def test_exact_video_authorization_passes():
    authorization = PaidOutputAuthorization("video", "A", 2, 2)

    authorization.require_exact()
```

- [ ] **Step 2: Run and verify RED or GREEN**

Run:

```bash
./.venv/bin/python -m pytest tests/test_paid_regeneration.py -v
```

Expected: tests pass if Task 1 already added `PaidOutputAuthorization`; this is the unit contract before workflows consume it.

- [ ] **Step 3: Add explicit image regeneration method**

在 `ImageGenerationWorkflow` 加入：

```python
    def regenerate_scene_images(
        self,
        job_id: int,
        scene_label: str,
        job_dir: str | Path,
        plan: VisualPlan,
        authorization: PaidOutputAuthorization,
    ) -> list[int]:
        authorization.require_exact()
        if authorization.kind != "image" or authorization.scene_label != scene_label:
            raise VisualGenerationError("圖片再生授權的 kind 或 scene 不符")
        scene = next(
            (row for row in self.repo.scenes(job_id) if row["label"] == scene_label),
            None,
        )
        scene_plan = next(
            (row for row in plan.scenes if row.label == scene_label), None
        )
        if scene is None or scene_plan is None:
            raise VisualGenerationError(f"找不到 scene {scene_label}")
        anchor = self.repo.assets(job_id, asset_type="world_anchor")[0]
        existing = [
            row for row in self.repo.assets(job_id, asset_type="scene_image")
            if row["scene_id"] == scene["id"]
        ]
        start = max((int(row["variant_index"]) for row in existing), default=-1) + 1
        generated = []
        for offset in range(authorization.requested_outputs):
            variant = start + offset
            generated.append(
                self._generate(
                    job_id=job_id,
                    scene_id=int(scene["id"]),
                    asset_type="scene_image",
                    variant=variant,
                    prompt=scene_plan.image_prompt,
                    output=Path(job_dir) / "visual" / f"scene_{scene_label}_{variant + 1}.png",
                    references=[anchor["path"]],
                    authorized_extra=True,
                )
            )
        return generated
```

在 imports 加入 `PaidOutputAuthorization`。

- [ ] **Step 4: Add explicit video regeneration method**

在 `VideoGenerationWorkflow` 加入：

```python
    def regenerate_scene_videos(
        self,
        job_id: int,
        scene_label: str,
        job_dir: str | Path,
        authorization: PaidOutputAuthorization,
    ) -> None:
        authorization.require_exact()
        if authorization.kind != "video" or authorization.scene_label != scene_label:
            raise VisualGenerationError("影片再生授權的 kind 或 scene 不符")
        scene = next(
            (row for row in self.repo.scenes(job_id) if row["label"] == scene_label),
            None,
        )
        if scene is None:
            raise VisualGenerationError(f"找不到 scene {scene_label}")
        existing = [
            row for row in self.repo.assets(job_id, asset_type="scene_video")
            if row["scene_id"] == scene["id"]
        ]
        start = max((int(row["variant_index"]) for row in existing), default=-1) + 1
        for offset in range(authorization.requested_outputs):
            self._generate_one(
                job_id,
                scene,
                start + offset,
                Path(job_dir),
                authorized_extra=True,
            )
```

- [ ] **Step 5: Add workflow-level tests for wrong scene/kind and exact counts**

在 `tests/test_paid_regeneration.py` 追加以下完整測試支援與測試：

```python
from pathlib import Path

from PIL import Image, ImageDraw

from lyria_auto.db import StateDB
from lyria_auto.errors import VisualGenerationError
from lyria_auto.models import PromptPlan
from lyria_auto.utils import sha256_file
from lyria_auto.visual_models import (
    GeneratedImage,
    VideoPoll,
    VideoQC,
)
from lyria_auto.visual_planner import VisualPlanner
from lyria_auto.visual_repository import VisualRepository
from lyria_auto.visual_workflow import (
    ImageGenerationWorkflow,
    VideoGenerationWorkflow,
)


class RecordingImages:
    def __init__(self):
        self.calls = 0

    def generate_image(self, prompt, output_path, *, reference_paths):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (2048, 1152), (70, 50, 35))
        draw = ImageDraw.Draw(image)
        for x in range(0, 2048, 64):
            color = (100, 80, 50) if (x // 64) % 2 else (40, 60, 45)
            draw.rectangle((x, 0, min(x + 63, 2047), 1151), fill=color)
        image.save(output, "PNG")
        self.calls += 1
        return GeneratedImage(output, "image/png", sha256_file(output))


class RecordingVeo:
    def __init__(self):
        self.starts = 0

    def start_video(self, prompt, frame_path):
        self.starts += 1
        return f"operations/{self.starts}"

    def poll_video(self, operation_id, output_path):
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"raw")
        return VideoPoll(True, output, sha256_file(output))


def four_scene_setup(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    job_id = db.create_job("main", False, {})
    repo = VisualRepository(db)
    album = PromptPlan(
        "p", "s", "rainy cafe", "warm", "piano",
        "雨天咖啡館", "溫暖", "jazz", "tape", "natural", "rain",
    )
    plan = VisualPlanner(9).compose(album, target_minutes=120)
    for scene in plan.scenes:
        repo.upsert_scene(
            job_id, scene.position, scene.label,
            plan.to_dict()["world"], scene.image_prompt, scene.motion_prompt,
        )
    return db, job_id, repo, plan


@pytest.mark.parametrize(
    "authorization",
    [
        PaidOutputAuthorization("video", "A", 3, 3),
        PaidOutputAuthorization("image", "B", 3, 3),
    ],
)
def test_wrong_image_regeneration_scope_makes_no_paid_call(
    tmp_path, authorization
):
    db, job_id, repo, plan = four_scene_setup(tmp_path)
    client = RecordingImages()
    workflow = ImageGenerationWorkflow(repo, client, image_model="image")
    try:
        with pytest.raises(VisualGenerationError):
            workflow.regenerate_scene_images(
                job_id, "A", tmp_path / "job", plan, authorization
            )
        assert client.calls == 0
    finally:
        db.close()


def test_exact_image_regeneration_adds_three_only_to_scene_a(tmp_path):
    db, job_id, repo, plan = four_scene_setup(tmp_path)
    client = RecordingImages()
    workflow = ImageGenerationWorkflow(repo, client, image_model="image")
    try:
        workflow.generate_scene_images(job_id, tmp_path / "job", plan)
        a, b, *_ = repo.scenes(job_id)
        before_a = {
            int(row["id"]) for row in repo.assets(job_id, asset_type="scene_image")
            if row["scene_id"] == a["id"]
        }
        before_b = {
            int(row["id"]) for row in repo.assets(job_id, asset_type="scene_image")
            if row["scene_id"] == b["id"]
        }
        calls_before = client.calls
        authorization = PaidOutputAuthorization("image", "A", 3, 3)

        new_ids = workflow.regenerate_scene_images(
            job_id, "A", tmp_path / "job", plan, authorization
        )

        after_b = {
            int(row["id"]) for row in repo.assets(job_id, asset_type="scene_image")
            if row["scene_id"] == b["id"]
        }
        assert len(new_ids) == 3
        assert client.calls - calls_before == 3
        assert not before_a.intersection(new_ids)
        assert after_b == before_b
    finally:
        db.close()


def test_exact_video_regeneration_adds_two_only_to_scene_a(tmp_path):
    db, job_id, repo, plan = four_scene_setup(tmp_path)
    images = RecordingImages()
    image_authorization, image_lease = authorized_test_context(
        repo, job_id, images=13
    )
    images_workflow = ImageGenerationWorkflow(
        repo,
        images,
        image_model="image",
        authorization=image_authorization,
        lease=image_lease,
    )
    veo = RecordingVeo()

    def normalize(raw, normalized, **kwargs):
        output = Path(normalized)
        output.write_bytes(b"normalized")
        return output

    def inspect(path, **kwargs):
        return VideoQC(True, 1920, 1080, 8.0, 24.0, 0.01, 0.01, "ok")

    try:
        images_workflow.generate_scene_images(job_id, tmp_path / "job", plan)
        for scene in repo.scenes(job_id):
            image_asset = next(
                row for row in repo.assets(job_id, asset_type="scene_image")
                if row["scene_id"] == scene["id"] and row["status"] == "ready"
              )
              repo.select_image(
                  int(scene["id"]),
                  int(image_asset["id"]),
                  expected_version=int(scene["state_version"]),
              )
          repo.finish_image_review(job_id)
          video_authorization, video_lease = authorized_test_context(
              repo, job_id, videos=8
          )
          workflow = VideoGenerationWorkflow(
              repo, veo, video_model="veo", sleep=lambda _: None,
              normalize=normalize, inspect=inspect,
              authorization=video_authorization,
              lease=video_lease,
          )
        workflow.generate(job_id, tmp_path / "job")
        a, b, *_ = repo.scenes(job_id)
        before_b = {
            int(row["id"]) for row in repo.assets(job_id, asset_type="scene_video")
            if row["scene_id"] == b["id"]
        }
        starts_before = veo.starts
        authorization = PaidOutputAuthorization("video", "A", 2, 2)

        workflow.regenerate_scene_videos(
            job_id, "A", tmp_path / "job", authorization
        )

        after_b = {
            int(row["id"]) for row in repo.assets(job_id, asset_type="scene_video")
            if row["scene_id"] == b["id"]
        }
        assert veo.starts - starts_before == 2
        assert after_b == before_b
    finally:
        db.close()
```

這兩個測試用明確的 before／after asset IDs 證明未改動非目標 scene。

- [ ] **Step 6: Run paid-regeneration tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_paid_regeneration.py tests/test_visual_image_workflow.py tests/test_visual_video_workflow.py -v
```

Expected: all pass;錯誤授權產生零 fake calls。

- [ ] **Step 7: Commit**

```bash
git add src/lyria_auto/visual_workflow.py tests/test_paid_regeneration.py
git commit -m "feat: require exact authorization for visual regeneration"
```

---

### Task 6: Lock in-flight gating, at-most-once and atomic-review regressions

**Files:**
- Create: `tests/test_video_inflight_gate.py`
- Create: `tests/test_video_review_state.py`
- Modify: `tests/test_visual_video_workflow.py`

- [ ] **Step 1: Add workflow crash-window tests**

先使用 fake Veo 與 fake clock 覆蓋狀態分支，再以 subprocess fault harness
證明 durability。只有同 process 重跑 workflow 不算 crash/restart 測試。

1. `reserved` 先 commit 成 `starting`，fake 才看得到第一次 `start_video()`。
2. `starting` 且無 operation ID 的 resume 變 `start_uncertain`，`starts == 0`。
3. `PaidStartUncertainError` 變 `start_uncertain`，同一 asset 重跑仍 `starts == 1`。
4. operation ID 已 commit 後 poll timeout，asset 保持 `polling`；resume poll 同一
   operation，`starts` 不增加。
5. `normalizing` 且 raw 存在時直接重做本機 normalize，start/poll 都是零。
6. Ctrl+C 發生於 poll 時保留 operation 與 `polling`，輸出下一條 `resume` 命令。

subprocess matrix：

1. child 以全新 DB connection／workflow/client 執行，於 lease、allowance consumption、
   start CAS、provider response、operation commit、raw download、`normalizing` commit、
   normalized rename 前後分別 `os._exit(91)`。
2. parent 等待 child 後，以全新 connection／repository／workflow/client 重開 DB，
   執行 `PRAGMA integrity_check` 再 resume。
3. provider fake 使用跨 process 可見的 append-only call ledger，不使用記憶體
   `starts` counter 作唯一證據。
4. 兩個 child 以 barrier 同時 claim 同一 asset，只有一方可取得 CAS；
   ledger 恰好一個 `start_video`。

- [ ] **Step 2: Add the Plan 4 hand-off predicate**

在 repository 實作一個唯一的 `has_inflight_video_assets(job_id)` 查詢，狀態集合固定為：

```python
VIDEO_INFLIGHT = {"reserved", "starting", "polling", "normalizing"}
```

`start_uncertain` 不算可自動續跑的 in-flight，但也不是可進審核的 ready 終態；
Plan 4 必須把它呈現為人工介入 blocker。測試矩陣逐一放入上述四種 in-flight，
證明 hand-off predicate 為 true；全部 ready／terminal 後才為 false。

- [ ] **Step 3: Add atomic review regression tests**

執行本計畫前述 select→reject、stale selected asset、跨 job 與缺檔測試；每個失敗
案例都要驗證 job 未被部分更新為 `video_approved`。

- [ ] **Step 4: Run the Plan 3 contract suite**

Run on macOS:

```bash
python -m pytest \
  tests/test_visual_schema_migration.py \
  tests/test_visual_video_quality.py \
  tests/test_visual_video_workflow.py \
  tests/test_video_inflight_gate.py \
  tests/test_video_repository.py \
  tests/test_video_review.py \
  tests/test_video_review_state.py \
  tests/test_paid_regeneration.py -v
```

Expected: all pass; tests use fakes only and issue zero Gemini／Veo paid calls.

---

## Plan 3 completion gate

進入主 pipeline 與長片渲染前必須同時成立：

1. 四景預設建立八個唯一 asset identities；lease＋CAS＋同 transaction allowance
   consumption 保證兩個獨立 process 下每個 identity 最多一次 paid start。
2. subprocess hard crash 後由全新 DB connection 恢復；expired owner 的
   `starting` crash window 落入 `start_uncertain`，普通 resume 不增加第九個 start。
3. 每個 operation ID 在第一次 poll 前已 commit 且由新 process 可見；
   暫時 poll 失敗續用同一 ID。
4. `reserved`／`starting`／`polling`／`normalizing` 任一存在時不得進影片審核。
5. raw file 與 normalized silent loop 都保留，審核只引用 normalized path。
6. normalized loop 是 1920×1080、24 fps、無 audio stream。
7. 靜止、劇烈運動、接縫過大、時長或解析度錯誤都標記 `qc_failed`。
8. `qc_failed`／`start_uncertain`／`failed_tampered` 不能核准，且不自動重生。
9. reject 使用 `state_version` CAS 原子清 selection；stale POST 不能覆寫；
   finish 在同一 `BEGIN IMMEDIATE` transaction 重驗 asset 歸屬、ready 與 snapshot identity。
10. 額外圖片／影片的精確授權不匹配時，付費 fake call 為零；`resume` 不補生。
11. Review server 以 muted loop 播放，顯示 seam／motion、完成數、checklist 與下一步。
12. `0003_visual_video_fields` 由共用 migration registry 套用，可 rollback／重跑，
    並保留既有 Plan 2 資料與自訂設定。
