# 續跑機制與無縫循環 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 Lyria-Auto-Publisher 在長批次生成（25~51 軌）中途失敗時可以續跑而不重複付費，並讓循環補長從硬切改為交叉淡化的無縫接縫。

**Architecture:** 把 `Pipeline.run_one` 拆成 `_plan` / `_generate` / `_render` / `_upload` 四個階段，`_generate` 內建「單軌失敗不中斷、跑完整批再重試一輪」的容錯邏輯，並改為只依賴 SQLite 裡的 `tracks` 表（不依賴記憶體中的 `PromptPlan` 物件），使新增的 `resume_one` 可以在完全不同的行程重新呼叫 `_generate`/`_render`/`_upload` 而不必重新規劃。無縫循環透過重用既有的 `combine_audio`（acrossfade）把同一份素材當成多個輸入串接後裁切到目標長度，取代 `-stream_loop -1` 的硬切。

**Tech Stack:** Python 3.13、ffmpeg/ffprobe（CLI 子行程）、SQLite（`sqlite3` 標準庫）、pytest、google-genai（生產環境用，測試中一律 mock）。

## Global Constraints

以下規則對本計畫所有任務都成立，每個任務不再重複列出：

- 這個專案**不是 git repository**（已確認 `git rev-parse --is-inside-work-tree` 失敗）。每個任務原本該有的「commit」步驟改為「確認變更」步驟（重跑相關測試 + 手動檢查 diff），不執行任何 git 指令。
- **一律使用 `./.venv/bin/python`、`./.venv/bin/pytest`、`./.venv/bin/lyria-auto`**。系統預設 `python3` 是 3.9.6，跑不動本專案（需要 3.11+）。
- **測試中絕對不能呼叫真實 Lyria API**。任何練習到 `_generate` 的測試都必須先 `monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", ...)` 換成假的 client。
- **驗收指令一律用 `--dry-run` 或假 client**，絕不執行 `lyria-auto run`（不含 `--dry-run`）——那會真的呼叫 API 花錢。
- `ffmpeg` 的 `-t` 是**輸出選項**，必須放在輸出檔名之前；放到輸入端語意完全不同。
- `src/lyria_auto/media/video.py` 裡 `create_static_video` 已經修過 `-shortest` 對不齊音視訊長度的 bug（明確加上 `-t`）。**這個檔案本次計畫不需要修改，也絕對不要把 `-t` 改回單純的 `-shortest`。**
- `src/lyria_auto/utils.py` 的 `slugify` 刻意保留中文字元（`一-鿿`），這是設計決定，不是 bug，不要「修正」成純 ASCII。
- 不要修改資料庫 schema（`db.py` 的 `SCHEMA` 常數）、`config/prompts.yaml`、`config/settings.yaml` 的內容、YouTube 上傳相關程式碼（`providers/youtube.py`）、`config/channels.example.yaml`。
- 不要新增任何 CLAUDE.md 未提到的設定項目、不要為了「順手」重構本計畫未列出的檔案。

---

## Task 1: 無縫循環 — `media/audio.py` 的 `loop_audio` 與 `combine_audio` target_seconds

**Files:**
- Modify: `src/lyria_auto/media/audio.py`
- Create: `tests/conftest.py`
- Create: `tests/test_loop.py`

**Interfaces:**
- Produces: `combine_audio(tracks, output_path, crossfade_seconds=2.0, target_seconds: float | None = None) -> Path`（新增第 4 個選用參數）
- Produces: `loop_audio(input_path, output_path, target_seconds: float, crossfade_seconds: float = 2.0) -> Path`
- Produces（`tests/conftest.py`）: `write_sine_audio(path: Path, duration: float = 25.0) -> None`

### 現況（`src/lyria_auto/media/audio.py` 目前完整內容）

```python
from __future__ import annotations

import json
from pathlib import Path

from ..errors import MediaError
from ..utils import run_command


def probe_audio(path: str | Path) -> dict:
    result = run_command([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ])
    return json.loads(result.stdout)


def validate_audio(path: str | Path, minimum_duration: float, minimum_sample_rate: int, require_stereo: bool) -> dict:
    data = probe_audio(path)
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise MediaError(f"找不到音訊串流：{path}")
    stream = audio_streams[0]
    duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
    sample_rate = int(stream.get("sample_rate") or 0)
    channels = int(stream.get("channels") or 0)
    if duration < minimum_duration:
        raise MediaError(f"音訊過短：{duration:.1f}s < {minimum_duration}s")
    if sample_rate < minimum_sample_rate:
        raise MediaError(f"取樣率過低：{sample_rate} < {minimum_sample_rate}")
    if require_stereo and channels < 2:
        raise MediaError(f"需要立體聲，但 channels={channels}")
    return {"duration": duration, "sample_rate": sample_rate, "channels": channels}


def normalize_audio(input_path: str | Path, output_path: str | Path, lufs: float, true_peak: float) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        "ffmpeg", "-y", "-i", str(input_path),
        "-af", f"loudnorm=I={lufs}:TP={true_peak}:LRA=11",
        "-ar", "48000", "-ac", "2", "-b:a", "256k", str(out)
    ])
    return out


def combine_audio(tracks: list[str | Path], output_path: str | Path, crossfade_seconds: float = 2.0) -> Path:
    if not tracks:
        raise MediaError("沒有可合併的音訊")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if len(tracks) == 1:
        run_command(["ffmpeg", "-y", "-i", str(tracks[0]), "-c:a", "aac", "-b:a", "256k", str(out)])
        return out

    args = ["ffmpeg", "-y"]
    for track in tracks:
        args += ["-i", str(track)]
    filters = []
    for i in range(len(tracks)):
        filters.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[a{i}]")
    current = "a0"
    for i in range(1, len(tracks)):
        output_label = f"xf{i}"
        filters.append(f"[{current}][a{i}]acrossfade=d={crossfade_seconds}:c1=tri:c2=tri[{output_label}]")
        current = output_label
    args += ["-filter_complex", ";".join(filters), "-map", f"[{current}]", "-c:a", "aac", "-b:a", "256k", str(out)]
    run_command(args)
    return out


def extend_audio(input_path: str | Path, output_path: str | Path, target_seconds: int) -> Path:
    info = probe_audio(input_path)
    duration = float(info.get("format", {}).get("duration") or 0)
    if abs(duration - target_seconds) <= 1:
        return Path(input_path)
    out = Path(output_path)
    if duration > target_seconds:
        run_command([
            "ffmpeg", "-y", "-i", str(input_path), "-t", str(target_seconds),
            "-c:a", "aac", "-b:a", "256k", str(out)
        ])
    else:
        run_command([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(input_path),
            "-t", str(target_seconds), "-c:a", "aac", "-b:a", "256k", str(out)
        ])
    return out
```

- [ ] **Step 1: 寫失敗測試 — `combine_audio` 支援 `target_seconds` 裁切**

建立 `tests/conftest.py`：

```python
from __future__ import annotations

import subprocess
from pathlib import Path


def write_sine_audio(path: Path, duration: float = 25.0) -> None:
    """產生真實可播放的測試音訊（440Hz 正弦波，44.1kHz 立體聲）。
    刻意用 sine 而非 anullsrc：純靜音會讓 loudnorm 行為異常。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-ac", "2", "-c:a", "libmp3lame", str(path),
        ],
        check=True, capture_output=True,
    )
```

建立 `tests/test_loop.py`：

```python
from __future__ import annotations

import pytest

from conftest import write_sine_audio
from lyria_auto.errors import MediaError
from lyria_auto.media.audio import combine_audio, loop_audio, probe_audio


def test_combine_audio_respects_target_seconds(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    write_sine_audio(a, duration=10.0)
    write_sine_audio(b, duration=10.0)
    out = tmp_path / "combined.m4a"

    combine_audio([a, b], out, crossfade_seconds=2.0, target_seconds=15.0)

    duration = float(probe_audio(out)["format"]["duration"])
    assert abs(duration - 15.0) <= 1.0


def test_loop_audio_produces_target_duration(tmp_path):
    src = tmp_path / "src.mp3"
    write_sine_audio(src, duration=60.0)
    out = tmp_path / "looped.m4a"

    loop_audio(src, out, target_seconds=200.0, crossfade_seconds=2.0)

    duration = float(probe_audio(out)["format"]["duration"])
    assert abs(duration - 200.0) <= 1.0


def test_loop_audio_rejects_crossfade_longer_than_source(tmp_path):
    src = tmp_path / "src.mp3"
    write_sine_audio(src, duration=3.0)

    with pytest.raises(MediaError):
        loop_audio(src, tmp_path / "out.m4a", target_seconds=30.0, crossfade_seconds=5.0)
```

- [ ] **Step 2: 執行測試確認失敗**

執行：`./.venv/bin/python -m pytest tests/test_loop.py -v`
預期：`ImportError: cannot import name 'loop_audio'`（`loop_audio` 尚未存在），或 `TypeError: combine_audio() got an unexpected keyword argument 'target_seconds'`

- [ ] **Step 3: 實作 `loop_audio` 與 `combine_audio` 的 `target_seconds`**

修改 `src/lyria_auto/media/audio.py`，把開頭的 import 改成：

old_string:
```python
from __future__ import annotations

import json
from pathlib import Path

from ..errors import MediaError
from ..utils import run_command
```

new_string:
```python
from __future__ import annotations

import json
import math
from pathlib import Path

from ..errors import MediaError
from ..utils import run_command
```

把 `combine_audio` 整個函式：

old_string:
```python
def combine_audio(tracks: list[str | Path], output_path: str | Path, crossfade_seconds: float = 2.0) -> Path:
    if not tracks:
        raise MediaError("沒有可合併的音訊")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if len(tracks) == 1:
        run_command(["ffmpeg", "-y", "-i", str(tracks[0]), "-c:a", "aac", "-b:a", "256k", str(out)])
        return out

    args = ["ffmpeg", "-y"]
    for track in tracks:
        args += ["-i", str(track)]
    filters = []
    for i in range(len(tracks)):
        filters.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[a{i}]")
    current = "a0"
    for i in range(1, len(tracks)):
        output_label = f"xf{i}"
        filters.append(f"[{current}][a{i}]acrossfade=d={crossfade_seconds}:c1=tri:c2=tri[{output_label}]")
        current = output_label
    args += ["-filter_complex", ";".join(filters), "-map", f"[{current}]", "-c:a", "aac", "-b:a", "256k", str(out)]
    run_command(args)
    return out
```

new_string:
```python
def combine_audio(
    tracks: list[str | Path],
    output_path: str | Path,
    crossfade_seconds: float = 2.0,
    target_seconds: float | None = None,
) -> Path:
    if not tracks:
        raise MediaError("沒有可合併的音訊")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    trailing = ["-t", f"{target_seconds:.3f}"] if target_seconds is not None else []

    if len(tracks) == 1:
        run_command(["ffmpeg", "-y", "-i", str(tracks[0]), "-c:a", "aac", "-b:a", "256k", *trailing, str(out)])
        return out

    args = ["ffmpeg", "-y"]
    for track in tracks:
        args += ["-i", str(track)]
    filters = []
    for i in range(len(tracks)):
        filters.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[a{i}]")
    current = "a0"
    for i in range(1, len(tracks)):
        output_label = f"xf{i}"
        filters.append(f"[{current}][a{i}]acrossfade=d={crossfade_seconds}:c1=tri:c2=tri[{output_label}]")
        current = output_label
    args += ["-filter_complex", ";".join(filters), "-map", f"[{current}]", "-c:a", "aac", "-b:a", "256k", *trailing, str(out)]
    run_command(args)
    return out


def loop_audio(
    input_path: str | Path,
    output_path: str | Path,
    target_seconds: float,
    crossfade_seconds: float = 2.0,
) -> Path:
    info = probe_audio(input_path)
    duration = float(info.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise MediaError(f"無法讀取音訊長度：{input_path}")
    if crossfade_seconds >= duration:
        raise MediaError(f"交叉淡化秒數（{crossfade_seconds}）必須小於素材長度（{duration:.1f}s）")
    step = duration - crossfade_seconds
    copies = max(2, math.ceil(target_seconds / step))
    return combine_audio([input_path] * copies, output_path, crossfade_seconds, target_seconds)
```

- [ ] **Step 4: 改 `extend_audio` 呼叫 `loop_audio`，並讓 crossfade 可配置**

old_string:
```python
def extend_audio(input_path: str | Path, output_path: str | Path, target_seconds: int) -> Path:
    info = probe_audio(input_path)
    duration = float(info.get("format", {}).get("duration") or 0)
    if abs(duration - target_seconds) <= 1:
        return Path(input_path)
    out = Path(output_path)
    if duration > target_seconds:
        run_command([
            "ffmpeg", "-y", "-i", str(input_path), "-t", str(target_seconds),
            "-c:a", "aac", "-b:a", "256k", str(out)
        ])
    else:
        run_command([
            "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(input_path),
            "-t", str(target_seconds), "-c:a", "aac", "-b:a", "256k", str(out)
        ])
    return out
```

new_string:
```python
def extend_audio(
    input_path: str | Path,
    output_path: str | Path,
    target_seconds: int,
    crossfade_seconds: float = 2.0,
) -> Path:
    info = probe_audio(input_path)
    duration = float(info.get("format", {}).get("duration") or 0)
    if abs(duration - target_seconds) <= 1:
        return Path(input_path)
    out = Path(output_path)
    if duration > target_seconds:
        run_command([
            "ffmpeg", "-y", "-i", str(input_path), "-t", str(target_seconds),
            "-c:a", "aac", "-b:a", "256k", str(out)
        ])
    else:
        loop_audio(input_path, out, float(target_seconds), crossfade_seconds)
    return out
```

**注意（刻意偏離 spec 字面敘述，附理由）：** spec 第 7 節原文只要求改「不夠長」那個分支，沒提到給 `extend_audio` 加第 4 個參數。這裡加了 `crossfade_seconds: float = 2.0` 選用參數，理由：`combine_audio` 已經從 `video_cfg.crossfade_seconds` 讀取交叉淡化秒數，如果 `loop_audio` 內部寫死 2.0，使用者以後調整 `config/settings.yaml` 的 `crossfade_seconds` 時，曲目間的接縫會變、但循環接縫不會變，兩種接縫從此不一致。加這個參數是把*既有*設定值正確接到*新*用途，不是新增設定項目，且有預設值 `2.0` 不會破壞任何呼叫端。Task 3 會把 `pipeline.py` 呼叫端接上 `video_cfg.get("crossfade_seconds", 2)`。

- [ ] **Step 5: 執行測試確認通過**

執行：`./.venv/bin/python -m pytest tests/test_loop.py -v`
預期：3 個測試全部 PASS

- [ ] **Step 6: 確認既有測試不受影響**

執行：`./.venv/bin/python -m pytest -q`
預期：`5 passed`（既有 5 個測試）+ 新增的 3 個 = 8 passed

- [ ] **Step 7: 確認變更（無 git，僅記錄）**

無 git commit。確認 `src/lyria_auto/media/audio.py`、`tests/conftest.py`、`tests/test_loop.py` 三個檔案內容與上方一致即可進入下一個任務。

---

## Task 2: 續跑支援 — `db.py` 的 `tracks_for_job` / `_track_is_reusable` / `resumable_job`

**Files:**
- Modify: `src/lyria_auto/db.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Consumes: `tests/conftest.py` 的 `write_sine_audio`（Task 1 建立）
- Produces: `StateDB.tracks_for_job(job_id: int) -> list[sqlite3.Row]`
- Produces: `StateDB._track_is_reusable(row: sqlite3.Row) -> bool`（刻意保留底線前綴，spec 明確要求「方便測試」的獨立函式，測試會直接呼叫這個「私有」方法，這是預期用法不是繞過封裝）
- Produces: `StateDB.resumable_job(job_id: int | None = None) -> dict[str, Any] | None`

### 現況（`src/lyria_auto/db.py` 目前完整內容已在對話中確認，重點：`StateDB` class 從第 60 行開始，`recent_jobs` 是最後一個方法，結尾在第 141 行）

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_db.py`：

```python
from __future__ import annotations

from pathlib import Path

from conftest import write_sine_audio
from lyria_auto.db import StateDB


def test_resumable_job_finds_latest_incomplete(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        j1 = db.create_job("main", False, {})
        db.update_job(j1, "complete")
        j2 = db.create_job("main", False, {})
        db.update_job(j2, "failed", "boom")

        found = db.resumable_job()

        assert found is not None
        assert found["id"] == j2
    finally:
        db.close()


def test_resumable_job_excludes_dry_run(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        j1 = db.create_job("main", True, {})
        db.update_job(j1, "dry_run_complete")

        assert db.resumable_job() is None
    finally:
        db.close()


def test_resumable_job_returns_none_when_nothing_pending(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        assert db.resumable_job() is None
    finally:
        db.close()


def test_resumable_job_by_explicit_id(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        j1 = db.create_job("main", False, {})

        found = db.resumable_job(j1)

        assert found is not None
        assert found["id"] == j1
    finally:
        db.close()


def test_tracks_for_job_orders_by_id(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        db.add_track(job_id, "prompt 1", "sig1")
        db.add_track(job_id, "prompt 2", "sig2")

        rows = db.tracks_for_job(job_id)

        assert [r["prompt"] for r in rows] == ["prompt 1", "prompt 2"]
    finally:
        db.close()


def test_track_is_reusable_false_when_not_ready(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        db.add_track(job_id, "prompt", "sig")
        row = db.tracks_for_job(job_id)[0]

        assert db._track_is_reusable(row) is False
    finally:
        db.close()


def test_track_is_reusable_false_when_file_missing(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        track_id = db.add_track(job_id, "prompt", "sig")
        db.update_track(track_id, status="ready", audio_path=str(tmp_path / "missing.m4a"))
        row = db.tracks_for_job(job_id)[0]

        assert db._track_is_reusable(row) is False
    finally:
        db.close()


def test_track_is_reusable_false_when_file_corrupted(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        track_id = db.add_track(job_id, "prompt", "sig")
        broken = tmp_path / "broken.m4a"
        broken.write_bytes(b"")
        db.update_track(track_id, status="ready", audio_path=str(broken))
        row = db.tracks_for_job(job_id)[0]

        assert db._track_is_reusable(row) is False
    finally:
        db.close()


def test_track_is_reusable_true_for_valid_audio(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        track_id = db.add_track(job_id, "prompt", "sig")
        audio = tmp_path / "ok.mp3"
        write_sine_audio(audio, duration=1.0)
        db.update_track(track_id, status="ready", audio_path=str(audio))
        row = db.tracks_for_job(job_id)[0]

        assert db._track_is_reusable(row) is True
    finally:
        db.close()
```

- [ ] **Step 2: 執行測試確認失敗**

執行：`./.venv/bin/python -m pytest tests/test_db.py -v`
預期：`AttributeError: 'StateDB' object has no attribute 'resumable_job'`（或 `tracks_for_job`/`_track_is_reusable`）

- [ ] **Step 3: 實作三個新方法**

修改 `src/lyria_auto/db.py` 開頭 import：

old_string:
```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .utils import utc_now_iso
```

new_string:
```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .media.audio import probe_audio
from .utils import utc_now_iso
```

在 `recent_jobs` 方法（檔案最後一個方法）之後加入三個新方法：

old_string:
```python
    def recent_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,created_at,updated_at,status,channel,dry_run,error FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
```

new_string:
```python
    def recent_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,created_at,updated_at,status,channel,dry_run,error FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def tracks_for_job(self, job_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM tracks WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()

    def _track_is_reusable(self, row: sqlite3.Row) -> bool:
        """三個條件全部成立才能跳過重新生成：狀態為 ready、檔案存在、檔案可被 ffprobe 正確解析。
        第三條是關鍵：程式被中斷時可能正好寫到一半，只信資料庫狀態會拿到半殘檔。"""
        if row["status"] != "ready":
            return False
        audio_path = row["audio_path"]
        if not audio_path:
            return False
        path = Path(audio_path)
        if not path.exists():
            return False
        try:
            info = probe_audio(path)
        except Exception:
            return False
        duration = info.get("format", {}).get("duration")
        return duration is not None and float(duration) > 0

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

- [ ] **Step 4: 執行測試確認通過**

執行：`./.venv/bin/python -m pytest tests/test_db.py -v`
預期：9 個測試全部 PASS

- [ ] **Step 5: 確認既有測試不受影響**

執行：`./.venv/bin/python -m pytest -q`
預期：全部通過（8 + 9 = 17 passed）

- [ ] **Step 6: 確認變更**

無 git commit。確認 `src/lyria_auto/db.py` 新增的三個方法縮排正確、位於 `StateDB` class 內。

---

## Task 3: 拆分 `Pipeline.run_one` 為 `_plan` / `_generate` / `_render` / `_upload`（純重構，行為不變）

**Files:**
- Modify: `src/lyria_auto/models.py`
- Modify: `src/lyria_auto/pipeline.py`

**Interfaces:**
- Consumes: Task 2 的 `StateDB.tracks_for_job`
- Produces: `models.JobContext` frozen dataclass
- Produces: `Pipeline._plan(job_id, job_dir, channel_name, publish_offset_index, tracks_count) -> JobContext`
- Produces: `Pipeline._generate(job_id, job_dir) -> list[Path]`（尚未加容錯，Task 4 才加）
- Produces: `Pipeline._render(ctx: JobContext, tracks: list[Path]) -> Path`
- Produces: `Pipeline._upload(ctx: JobContext, video_path: Path) -> str`
- `Pipeline.run_one` 對外行為（回傳值形狀、拋出的例外、DB 副作用順序）與重構前完全一致

**這是純重構任務：不新增測試，靠既有 5 個測試 + dry-run 手動檢查驗證行為不變。** `_generate`/`_render`/`_upload` 在這個任務還沒有自動化測試覆蓋（因為需要假 LyriaClient，那是 Task 4 才建立的），Task 4 完成後這三個方法會被回溯驗證到。

### Step 1: 修改 `models.py`，加入 `JobContext`

現況（`src/lyria_auto/models.py` 完整內容）：

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PromptPlan:
    prompt: str
    signature: str
    scene: str
    mood: str
    instrumentation: str
    scene_label: str = ""
    mood_label: str = ""


@dataclass(frozen=True)
class Metadata:
    title: str
    description: str
    tags: list[str]
    category_id: str
    privacy_status: str
    publish_at: str | None
    made_for_kids: bool
    contains_synthetic_media: bool
    notify_subscribers: bool
    default_language: str
```

- [ ] 用 Write 工具把 `src/lyria_auto/models.py` 完整內容換成：

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromptPlan:
    prompt: str
    signature: str
    scene: str
    mood: str
    instrumentation: str
    scene_label: str = ""
    mood_label: str = ""


@dataclass(frozen=True)
class Metadata:
    title: str
    description: str
    tags: list[str]
    category_id: str
    privacy_status: str
    publish_at: str | None
    made_for_kids: bool
    contains_synthetic_media: bool
    notify_subscribers: bool
    default_language: str


@dataclass(frozen=True)
class JobContext:
    job_id: int
    job_dir: Path
    metadata: Metadata
    video_row_id: int
    thumbnail: Path
    channel_cfg: dict
```

### Step 2: 重寫 `pipeline.py`

**注意（刻意偏離 spec 字面 pseudocode，附理由）：** spec 第 6 節給的 `_plan` 簽名是 `_plan(self, channel_name, upload, dry_run, publish_offset_index)`。這裡改成 `_plan(self, job_id, job_dir, channel_name, publish_offset_index, tracks_count)` ——把 `create_job`/`ensure_dir(job_dir)` 留在 `run_one` 裡、在 `try` 區塊**之前**執行。原因：原本程式碼裡 `job_id`（[pipeline.py:57](../../../src/lyria_auto/pipeline.py) 舊版）是在 `try` 區塊外建立的，所以任何規劃階段的例外都還是能在 `except` 裡正確寫回這個 job 的失敗狀態。如果照 spec 字面把 `create_job` 搬進 `_plan` 內部、又讓 `_plan()` 整個包在 `run_one` 的 `try` 裡，那麼當 `_plan` 內部（例如 `compose_many` 因為找不到 profile）拋例外時，`run_one` 拿不到 `job_id`，就沒辦法把這次失敗正確記錄到資料庫——這是對原本行為的一個退化。搬到 `run_one` 開頭可以保留這個保證。

- [ ] 用 Write 工具把 `src/lyria_auto/pipeline.py` 完整內容換成：

```python
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .config import AppConfig
from .db import StateDB
from .errors import GenerationError, LyriaAutoError, SafetyBlockedError
from .media.audio import combine_audio, extend_audio, normalize_audio, validate_audio
from .media.thumbnail import create_thumbnail
from .media.video import create_static_video
from .metadata import build_metadata
from .models import JobContext, Metadata
from .prompt_engine import PromptEngine
from .providers.lyria import LyriaClient
from .providers.youtube import YouTubeClient
from .safety import PromptSafety
from .utils import ensure_dir, json_dump, sha256_file, slugify

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: AppConfig):
        self.config = config
        project = config.section("project")
        self.workspace = ensure_dir(config.root / project.get("workspace", "workspace"))
        self.db = StateDB(config.root / project.get("database", "workspace/state.sqlite3"))

    def close(self):
        self.db.close()

    def _engine(self, offset: int = 0) -> PromptEngine:
        project = self.config.section("project")
        generation = self.config.section("generation")
        return PromptEngine(
            self.config.prompts,
            generation.get("prompt_profile", "coffeehouse_lofi_jazz"),
            int(project.get("random_seed", 1)) + offset,
        )

    def run_batch(self, videos: int, channel_name: str, upload: bool, dry_run: bool) -> list[dict[str, Any]]:
        results = []
        for index in range(videos):
            results.append(self.run_one(channel_name, upload, dry_run, publish_offset_index=index))
        return results

    def run_one(self, channel_name: str, upload: bool, dry_run: bool, publish_offset_index: int = 0) -> dict[str, Any]:
        generation = self.config.section("generation")
        tracks_count = int(generation.get("tracks_per_video", 4))
        job_id = self.db.create_job(channel_name, dry_run, {"tracks": tracks_count, "upload": upload})
        job_dir = ensure_dir(self.workspace / f"job_{job_id:06d}")
        try:
            ctx = self._plan(job_id, job_dir, channel_name, publish_offset_index, tracks_count)

            if dry_run:
                self.db.update_video(ctx.video_row_id, status="dry_run")
                self.db.update_job(job_id, "dry_run_complete")
                return {"job_id": job_id, "dry_run": True, "directory": str(job_dir), "plan": str(job_dir / "plan.json")}

            tracks = self._generate(job_id, job_dir)
            video_path = self._render(ctx, tracks)

            youtube_id = None
            if upload:
                youtube_id = self._upload(ctx, video_path)

            self.db.update_job(job_id, "complete")
            return {
                "job_id": job_id,
                "directory": str(job_dir),
                "video": str(video_path),
                "youtube_video_id": youtube_id,
                "publish_at": ctx.metadata.publish_at,
            }
        except Exception as exc:
            logger.exception("Job %s 失敗", job_id)
            self.db.update_job(job_id, "failed", str(exc))
            self.db.event(job_id, "job_failed", str(exc), "ERROR")
            raise

    def resume_one(self, job_id: int | None = None, upload: bool = False, channel_name: str = "main") -> dict[str, Any]:
        job_row = self.db.resumable_job(job_id)
        if job_row is None:
            raise LyriaAutoError("找不到可續跑的工作。用 lyria-auto status 查看歷史。")
        if job_row["dry_run"]:
            raise LyriaAutoError(f"job {job_row['id']} 是 dry-run，不需要續跑。")

        resolved_job_id = int(job_row["id"])
        job_dir = self.workspace / f"job_{resolved_job_id:06d}"
        plan_path = job_dir / "plan.json"
        if not plan_path.exists():
            raise LyriaAutoError(f"job {resolved_job_id} 找不到 plan.json，規劃階段就已失敗，請重新執行 run。")
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
        metadata = Metadata(**plan_payload["metadata"])

        video_row = self.db.conn.execute(
            "SELECT id FROM videos WHERE job_id=? ORDER BY id DESC LIMIT 1", (resolved_job_id,)
        ).fetchone()
        if video_row is None:
            raise LyriaAutoError(f"job {resolved_job_id} 找不到對應的 video 紀錄，無法續跑。")

        thumbnail = job_dir / "thumbnail.jpg"
        if not thumbnail.exists():
            raise LyriaAutoError(f"job {resolved_job_id} 找不到縮圖，無法續跑。")

        channel_cfg = self.config.channel(channel_name) if self.config.channels else {}
        ctx = JobContext(
            job_id=resolved_job_id,
            job_dir=job_dir,
            metadata=metadata,
            video_row_id=int(video_row["id"]),
            thumbnail=thumbnail,
            channel_cfg=channel_cfg,
        )

        try:
            tracks = self._generate(resolved_job_id, job_dir)
            video_path = self._render(ctx, tracks)

            youtube_id = None
            if upload:
                youtube_id = self._upload(ctx, video_path)

            self.db.update_job(resolved_job_id, "complete")
            return {
                "job_id": resolved_job_id,
                "directory": str(job_dir),
                "video": str(video_path),
                "youtube_video_id": youtube_id,
                "publish_at": metadata.publish_at,
            }
        except Exception as exc:
            logger.exception("Job %s 續跑失敗", resolved_job_id)
            self.db.update_job(resolved_job_id, "failed", str(exc))
            self.db.event(resolved_job_id, "job_failed", str(exc), "ERROR")
            raise

    def _plan(
        self,
        job_id: int,
        job_dir: Path,
        channel_name: str,
        publish_offset_index: int,
        tracks_count: int,
    ) -> JobContext:
        settings = self.config.settings
        generation = settings["generation"]
        video_cfg = settings["video"]
        quality = settings["quality"]
        channel_cfg = self.config.channel(channel_name) if self.config.channels else {}
        recent = self.db.recent_signatures(int(quality.get("max_recent_prompt_signatures", 100)))

        self.db.update_job(job_id, "planning")
        plans = self._engine(job_id).compose_many(
            tracks_count,
            int(generation.get("target_track_seconds", 150)),
            recent,
        )
        for plan in plans:
            self.db.add_track(job_id, plan.prompt, plan.signature)
        representative = plans[0]
        target_minutes = int(video_cfg.get("target_duration_minutes", 10))
        metadata = build_metadata(settings, channel_cfg, representative, target_minutes, publish_offset_index, episode=job_id)
        video_row_id = self.db.add_video(job_id, metadata.title)
        plan_payload = {
            "job_id": job_id,
            "prompts": [p.__dict__ for p in plans],
            "metadata": metadata.__dict__,
        }
        json_dump(job_dir / "plan.json", plan_payload)

        thumb = create_thumbnail(
            job_dir / "thumbnail.jpg",
            title="COZY JAZZ",
            subtitle=representative.scene_label or representative.scene,
            width=int(video_cfg.get("thumbnail_width", 1280)),
            height=int(video_cfg.get("thumbnail_height", 720)),
            quality=int(video_cfg.get("thumbnail_quality", 88)),
            background_dir=self.config.root / video_cfg.get("background_directory", "assets/backgrounds"),
            seed=job_id,
        )
        self.db.update_video(video_row_id, thumbnail_path=str(thumb), status="planned")

        return JobContext(
            job_id=job_id,
            job_dir=job_dir,
            metadata=metadata,
            video_row_id=video_row_id,
            thumbnail=thumb,
            channel_cfg=channel_cfg,
        )

    def _generate(self, job_id: int, job_dir: Path) -> list[Path]:
        settings = self.config.settings
        generation = settings["generation"]
        quality = settings["quality"]
        self.db.update_job(job_id, "generating")
        lyria = LyriaClient(
            model=generation.get("model", "lyria-3-pro-preview"),
            max_attempts=int(generation.get("max_generation_attempts", 3)),
            request_delay_seconds=float(generation.get("request_delay_seconds", 3)),
        )
        safety = PromptSafety(self.config.prompts.get("blocked_reference_terms", []))
        track_rows = self.db.tracks_for_job(job_id)

        for idx, row in enumerate(track_rows, start=1):
            track_id = int(row["id"])
            raw = job_dir / f"track_{idx:02d}_raw.mp3"
            prompt = row["prompt"]
            try:
                lyria.generate(prompt, raw)
            except SafetyBlockedError:
                if not generation.get("safe_rewrite_on_block", True):
                    raise
                prompt = safety.neutral_rewrite(prompt)
                safety.require_safe(prompt)
                lyria.generate(prompt, raw)
            info = validate_audio(
                raw,
                float(quality.get("minimum_duration_seconds", 20)),
                int(quality.get("minimum_sample_rate", 44100)),
                bool(quality.get("require_stereo", True)),
            )
            final_track = raw
            if quality.get("normalize_audio", True):
                final_track = normalize_audio(
                    raw,
                    job_dir / f"track_{idx:02d}_normalized.m4a",
                    float(quality.get("loudness_target_lufs", -16)),
                    float(quality.get("true_peak_db", -1.5)),
                )
            self.db.update_track(
                track_id,
                audio_path=str(final_track),
                duration_seconds=info["duration"],
                sha256=sha256_file(final_track),
                status="ready",
            )
            time.sleep(float(generation.get("request_delay_seconds", 3)))

        final_rows = self.db.tracks_for_job(job_id)
        return [Path(r["audio_path"]) for r in final_rows]

    def _render(self, ctx: JobContext, tracks: list[Path]) -> Path:
        video_cfg = self.config.settings["video"]
        crossfade = float(video_cfg.get("crossfade_seconds", 2))
        self.db.update_job(ctx.job_id, "rendering")
        compilation = combine_audio(tracks, ctx.job_dir / "compilation.m4a", crossfade)
        target_minutes = int(video_cfg.get("target_duration_minutes", 10))
        target_seconds = target_minutes * 60
        audio_final = extend_audio(compilation, ctx.job_dir / "compilation_extended.m4a", target_seconds, crossfade)
        output_name = slugify(ctx.metadata.title) + ".mp4"
        video_path = create_static_video(
            ctx.thumbnail,
            audio_final,
            ctx.job_dir / output_name,
            int(video_cfg.get("width", 1920)),
            int(video_cfg.get("height", 1080)),
            int(video_cfg.get("fps", 1)),
            str(video_cfg.get("audio_bitrate", "256k")),
            str(video_cfg.get("video_preset", "veryfast")),
        )
        self.db.update_video(
            ctx.video_row_id,
            video_path=str(video_path),
            thumbnail_path=str(ctx.thumbnail),
            publish_at=ctx.metadata.publish_at,
            status="rendered",
        )
        json_dump(ctx.job_dir / "metadata.json", ctx.metadata.__dict__)
        return video_path

    def _upload(self, ctx: JobContext, video_path: Path) -> str:
        self.db.update_job(ctx.job_id, "uploading")
        yt = YouTubeClient(ctx.channel_cfg, self.config.root)
        identity = yt.channel_identity()
        logger.info("上傳至頻道：%s (%s)", identity["title"], identity["id"])
        youtube_id = yt.upload(video_path, ctx.thumbnail, ctx.metadata)
        self.db.update_video(ctx.video_row_id, youtube_video_id=youtube_id, status="uploaded")
        return youtube_id
```

**注意：** 這一步已經把 `resume_one` 的完整實作也寫進去了（雖然它依賴的 `_generate` 容錯邏輯要到 Task 4 才會加上、CLI 要到 Task 6 才會接上）。這是因為 `run_one`/`resume_one` 這兩個方法互相參照同一組私有方法，分成兩次寫入同一個檔案容易出錯，一次寫完更不容易破壞既有結構。Task 4 只會替換 `_generate` 這一個方法的內容，不會動這裡的其他部分。

- [ ] **Step 3: 執行既有測試確認行為未變**

執行：`./.venv/bin/python -m pytest -q`
預期：17 passed（Task 1 的 8 + Task 2 的 9）—— 這一步不應該讓任何既有測試變紅。

- [ ] **Step 4: 手動確認 dry-run 行為與重構前一致**

執行：
```bash
./.venv/bin/lyria-auto run --dry-run --videos 1
```
預期：印出包含 `"dry_run": true` 的 JSON，且 `directory`／`plan` 兩個路徑實際存在。用這個指令確認：
```bash
./.venv/bin/python -c "
import json, sys
d = json.loads(open(sys.argv[1]).read())
print('job_id' in d, 'directory' in d, 'plan' in d)
" <(./.venv/bin/lyria-auto run --dry-run --videos 1 2>/dev/null | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)[0]))")
```
如果上面的管線指令太複雜跑不動，直接目視檢查 `lyria-auto run --dry-run --videos 1` 的 JSON 輸出裡有 `job_id`、`dry_run: true`、`directory`、`plan` 四個欄位即可，這就是重構前的原始行為。

- [ ] **Step 5: 逐行核對 `_generate`/`_render`/`_upload` 與原始邏輯等價**

這是本任務唯一沒有自動化測試覆蓋的部分（假 LyriaClient 要到 Task 4 才建立）。手動核對：`_generate` 的邏輯必須跟本文件 Task 3 Step 2 之前貼出的「舊版 `run_one` 第 96~139 行」在語意上完全一致，差別只在於 prompt 來源從 `zip(plans, track_rows)` 改成直接讀 `row["prompt"]`。`_render`/`_upload` 對照舊版第 141~176 行，確認呼叫順序、參數、DB 寫入欄位都相同。Task 4 完成後，`test_resume.py` 的測試會實際跑過 `_generate`→`_render` 這條路徑，屆時能自動驗證這裡的邏輯是否正確；如果 Task 4 的測試失敗，要回頭檢查這裡的邏輯轉譯有沒有出錯。

- [ ] **Step 6: 確認變更**

無 git commit。

---

## Task 4: `_generate` 加上單軌容錯與批次重試

**Files:**
- Modify: `src/lyria_auto/pipeline.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_resume.py`

**Interfaces:**
- Consumes: Task 3 的 `Pipeline._generate`、`Pipeline._render`
- Produces: `Pipeline._generate_track(...)  -> bool`（新私有方法）
- Produces（`tests/conftest.py` 新增）: `FakeLyria` class + `client_factory()`、`project_config` fixture
- `_generate` 對外簽名不變（`(self, job_id, job_dir) -> list[Path]`），但內部行為改變：單軌失敗不中斷，全部跑完後對失敗的軌重試一輪，仍失敗才拋 `GenerationError`

### Step 1: 擴充 `tests/conftest.py`

現況（Task 1 建立的內容）：

```python
from __future__ import annotations

import subprocess
from pathlib import Path


def write_sine_audio(path: Path, duration: float = 25.0) -> None:
    """產生真實可播放的測試音訊（440Hz 正弦波，44.1kHz 立體聲）。
    刻意用 sine 而非 anullsrc：純靜音會讓 loudnorm 行為異常。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-ac", "2", "-c:a", "libmp3lame", str(path),
        ],
        check=True, capture_output=True,
    )
```

- [ ] 用 Write 工具把 `tests/conftest.py` 完整內容換成：

```python
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from lyria_auto.config import load_config
from lyria_auto.errors import GenerationError


def write_sine_audio(path: Path, duration: float = 25.0) -> None:
    """產生真實可播放的測試音訊（440Hz 正弦波，44.1kHz 立體聲）。
    刻意用 sine 而非 anullsrc：純靜音會讓 loudnorm 行為異常。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-ac", "2", "-c:a", "libmp3lame", str(path),
        ],
        check=True, capture_output=True,
    )


class FakeLyria:
    """假的 Lyria client 生成器。用輸出檔名（例如 track_02_raw.mp3）而非 prompt
    內容來控制哪一軌失敗，因為 prompt 是 PromptEngine 隨機組出來的，用檔名穩定得多。"""

    def __init__(self):
        self.calls: list[str] = []
        self.fail_targets: dict[str, int] = {}

    def client_factory(self):
        recorder = self

        class _FakeLyriaClient:
            def __init__(self, *, model, max_attempts, request_delay_seconds):
                pass

            def generate(self, prompt, output_path):
                name = Path(output_path).name
                recorder.calls.append(name)
                remaining = recorder.fail_targets.get(name, 0)
                if remaining > 0:
                    recorder.fail_targets[name] = remaining - 1
                    raise GenerationError(f"模擬失敗：{name}")
                write_sine_audio(Path(output_path))
                return Path(output_path)

        return _FakeLyriaClient


@pytest.fixture
def fake_lyria():
    return FakeLyria()


@pytest.fixture
def project_config(tmp_path):
    """複製一份完整的 config/ 到暫存目錄，並把測試需要的三個參數改小，
    避免每次測試都花時間編碼 10 分鐘音訊或真的 sleep 3 秒。"""
    root = tmp_path / "project"
    shutil.copytree("config", root / "config")
    (root / "config" / "channels.yaml").write_text(
        (root / "config" / "channels.example.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    settings_path = root / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    settings["generation"]["tracks_per_video"] = 3
    settings["generation"]["request_delay_seconds"] = 0
    settings["video"]["target_duration_minutes"] = 2
    settings_path.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    return load_config(root)
```

### Step 2: 寫失敗測試

建立 `tests/test_resume.py`：

```python
from __future__ import annotations

from pathlib import Path

from lyria_auto.errors import GenerationError
from lyria_auto.pipeline import Pipeline


def test_single_track_failure_is_retried_and_succeeds(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 1

    pipeline = Pipeline(project_config)
    try:
        result = pipeline.run_one("main", upload=False, dry_run=False)

        assert Path(result["video"]).exists()
        rows = pipeline.db.tracks_for_job(result["job_id"])
        assert all(r["status"] == "ready" for r in rows)
        assert fake_lyria.calls.count("track_02_raw.mp3") == 2
    finally:
        pipeline.close()


def test_permanent_track_failure_aborts_job_without_producing_video(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 99

    pipeline = Pipeline(project_config)
    try:
        try:
            pipeline.run_one("main", upload=False, dry_run=False)
            assert False, "應該要拋出 GenerationError"
        except GenerationError:
            pass

        jobs = pipeline.db.recent_jobs(1)
        assert jobs[0]["status"] == "failed"
        job_id = jobs[0]["id"]
        job_dir = pipeline.workspace / f"job_{job_id:06d}"
        assert list(job_dir.glob("*.mp4")) == []
    finally:
        pipeline.close()
```

- [ ] **Step 3: 執行測試確認失敗**

執行：`./.venv/bin/python -m pytest tests/test_resume.py -v`
預期：第一個測試因為 `_generate` 目前沒有容錯邏輯而直接拋出例外失敗；第二個測試也會因為錯誤訊息不含 `resume` 相關字樣或狀態不對而失敗（依現有 `_generate` 實作，單軌失敗會讓整個 job 立刻中止，這部分行為剛好符合第二個測試的「不產出 MP4」，但 job 不會有機會做第二輪重試，所以第一個測試一定會失敗）。

- [ ] **Step 4: 實作容錯與重試**

修改 `src/lyria_auto/pipeline.py`，先確認頂部 import 已經有 `GenerationError`（Task 3 已加入，此步驟不需再改 import）。

把整個 `_generate` 方法：

old_string:
```python
    def _generate(self, job_id: int, job_dir: Path) -> list[Path]:
        settings = self.config.settings
        generation = settings["generation"]
        quality = settings["quality"]
        self.db.update_job(job_id, "generating")
        lyria = LyriaClient(
            model=generation.get("model", "lyria-3-pro-preview"),
            max_attempts=int(generation.get("max_generation_attempts", 3)),
            request_delay_seconds=float(generation.get("request_delay_seconds", 3)),
        )
        safety = PromptSafety(self.config.prompts.get("blocked_reference_terms", []))
        track_rows = self.db.tracks_for_job(job_id)

        for idx, row in enumerate(track_rows, start=1):
            track_id = int(row["id"])
            raw = job_dir / f"track_{idx:02d}_raw.mp3"
            prompt = row["prompt"]
            try:
                lyria.generate(prompt, raw)
            except SafetyBlockedError:
                if not generation.get("safe_rewrite_on_block", True):
                    raise
                prompt = safety.neutral_rewrite(prompt)
                safety.require_safe(prompt)
                lyria.generate(prompt, raw)
            info = validate_audio(
                raw,
                float(quality.get("minimum_duration_seconds", 20)),
                int(quality.get("minimum_sample_rate", 44100)),
                bool(quality.get("require_stereo", True)),
            )
            final_track = raw
            if quality.get("normalize_audio", True):
                final_track = normalize_audio(
                    raw,
                    job_dir / f"track_{idx:02d}_normalized.m4a",
                    float(quality.get("loudness_target_lufs", -16)),
                    float(quality.get("true_peak_db", -1.5)),
                )
            self.db.update_track(
                track_id,
                audio_path=str(final_track),
                duration_seconds=info["duration"],
                sha256=sha256_file(final_track),
                status="ready",
            )
            time.sleep(float(generation.get("request_delay_seconds", 3)))

        final_rows = self.db.tracks_for_job(job_id)
        return [Path(r["audio_path"]) for r in final_rows]
```

new_string:
```python
    def _generate_track(
        self,
        lyria: LyriaClient,
        safety: PromptSafety,
        generation: dict,
        quality: dict,
        job_dir: Path,
        job_id: int,
        track_id: int,
        idx: int,
        prompt: str,
    ) -> bool:
        raw = job_dir / f"track_{idx:02d}_raw.mp3"
        try:
            try:
                lyria.generate(prompt, raw)
            except SafetyBlockedError:
                if not generation.get("safe_rewrite_on_block", True):
                    raise
                prompt = safety.neutral_rewrite(prompt)
                safety.require_safe(prompt)
                lyria.generate(prompt, raw)
            info = validate_audio(
                raw,
                float(quality.get("minimum_duration_seconds", 20)),
                int(quality.get("minimum_sample_rate", 44100)),
                bool(quality.get("require_stereo", True)),
            )
            final_track = raw
            if quality.get("normalize_audio", True):
                final_track = normalize_audio(
                    raw,
                    job_dir / f"track_{idx:02d}_normalized.m4a",
                    float(quality.get("loudness_target_lufs", -16)),
                    float(quality.get("true_peak_db", -1.5)),
                )
            self.db.update_track(
                track_id,
                audio_path=str(final_track),
                duration_seconds=info["duration"],
                sha256=sha256_file(final_track),
                status="ready",
                error=None,
            )
            time.sleep(float(generation.get("request_delay_seconds", 3)))
            return True
        except Exception as exc:
            self.db.update_track(track_id, status="failed", error=str(exc))
            self.db.event(job_id, "track_failed", f"track {idx}: {exc}", "ERROR")
            return False

    def _generate(self, job_id: int, job_dir: Path) -> list[Path]:
        settings = self.config.settings
        generation = settings["generation"]
        quality = settings["quality"]
        self.db.update_job(job_id, "generating")
        lyria = LyriaClient(
            model=generation.get("model", "lyria-3-pro-preview"),
            max_attempts=int(generation.get("max_generation_attempts", 3)),
            request_delay_seconds=float(generation.get("request_delay_seconds", 3)),
        )
        safety = PromptSafety(self.config.prompts.get("blocked_reference_terms", []))
        track_rows = self.db.tracks_for_job(job_id)
        idx_by_id = {int(row["id"]): idx for idx, row in enumerate(track_rows, start=1)}

        for idx, row in enumerate(track_rows, start=1):
            if self.db._track_is_reusable(row):
                continue
            self._generate_track(
                lyria, safety, generation, quality, job_dir, job_id,
                int(row["id"]), idx, row["prompt"],
            )

        failed_rows = [r for r in self.db.tracks_for_job(job_id) if r["status"] == "failed"]
        for row in failed_rows:
            track_id = int(row["id"])
            self._generate_track(
                lyria, safety, generation, quality, job_dir, job_id,
                track_id, idx_by_id[track_id], row["prompt"],
            )

        still_failed = [r for r in self.db.tracks_for_job(job_id) if r["status"] == "failed"]
        if still_failed:
            summary = f"{len(still_failed)} 首曲目生成失敗"
            self.db.update_job(job_id, "failed", summary)
            raise GenerationError(f"{summary}，可用 lyria-auto resume 續跑")

        final_rows = self.db.tracks_for_job(job_id)
        return [Path(r["audio_path"]) for r in final_rows]
```

- [ ] **Step 5: 執行測試確認通過**

執行：`./.venv/bin/python -m pytest tests/test_resume.py -v`
預期：2 個測試全部 PASS

- [ ] **Step 6: 確認既有測試不受影響**

執行：`./.venv/bin/python -m pytest -q`
預期：全部通過（17 + 2 = 19 passed）。這一步同時回溯驗證了 Task 3 的 `_render` 邏輯正確（因為 `test_single_track_failure_is_retried_and_succeeds` 會真的跑到 `_render` 產出 MP4）。

- [ ] **Step 7: 確認變更**

無 git commit。

---

## Task 5: `resume_one` 的剩餘驗收測試

**Files:**
- Modify: `tests/test_resume.py`

**Interfaces:**
- Consumes: Task 3 的 `Pipeline.resume_one`（已在 Task 3 寫好完整實作）、Task 4 的容錯 `_generate`

`resume_one` 的實作已經在 Task 3 完成（因為要避免同一個檔案分兩次改動同一段程式碼），這個任務只補齊驗收測試。

- [ ] **Step 1: 在 `tests/test_resume.py` 追加測試**

在檔案開頭加入 `import json`：

old_string:
```python
from __future__ import annotations

from pathlib import Path

from lyria_auto.errors import GenerationError
from lyria_auto.pipeline import Pipeline
```

new_string:
```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lyria_auto.errors import GenerationError, LyriaAutoError
from lyria_auto.pipeline import Pipeline
```

在檔案結尾追加四個測試：

```python


def test_resume_only_regenerates_remaining_tracks(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 99

    pipeline = Pipeline(project_config)
    try:
        with pytest.raises(GenerationError):
            pipeline.run_one("main", upload=False, dry_run=False)

        first_run_calls = list(fake_lyria.calls)
        assert first_run_calls.count("track_01_raw.mp3") == 1
        assert first_run_calls.count("track_03_raw.mp3") == 1
        assert first_run_calls.count("track_02_raw.mp3") == 2  # 主流程失敗一次 + 批次重試失敗一次

        fake_lyria.calls.clear()
        fake_lyria.fail_targets["track_02_raw.mp3"] = 0  # 這次讓它成功

        result = pipeline.resume_one()

        # 核心驗收：只呼叫剩餘那一軌一次，不是總軌數
        assert fake_lyria.calls == ["track_02_raw.mp3"]
        assert Path(result["video"]).exists()
    finally:
        pipeline.close()


def test_resume_regenerates_truncated_file(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 99

    pipeline = Pipeline(project_config)
    try:
        with pytest.raises(GenerationError):
            pipeline.run_one("main", upload=False, dry_run=False)

        jobs = pipeline.db.recent_jobs(1)
        job_id = jobs[0]["id"]
        rows = pipeline.db.tracks_for_job(job_id)
        id_to_idx = {int(r["id"]): i for i, r in enumerate(rows, start=1)}
        ready_row = next(r for r in rows if r["status"] == "ready")
        Path(ready_row["audio_path"]).write_bytes(b"")  # 模擬寫到一半被中斷

        fake_lyria.calls.clear()
        fake_lyria.fail_targets["track_02_raw.mp3"] = 0

        pipeline.resume_one(job_id)

        expected_raw_name = f"track_{id_to_idx[int(ready_row['id'])]:02d}_raw.mp3"
        assert expected_raw_name in fake_lyria.calls
    finally:
        pipeline.close()


def test_resume_preserves_original_metadata(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 99

    pipeline = Pipeline(project_config)
    try:
        with pytest.raises(GenerationError):
            pipeline.run_one("main", upload=False, dry_run=False)

        jobs = pipeline.db.recent_jobs(1)
        job_id = jobs[0]["id"]
        job_dir = pipeline.workspace / f"job_{job_id:06d}"
        plan_before = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))

        fake_lyria.calls.clear()
        fake_lyria.fail_targets["track_02_raw.mp3"] = 0

        pipeline.resume_one(job_id)

        plan_after = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
        assert plan_before["metadata"] == plan_after["metadata"]
        assert plan_before["prompts"] == plan_after["prompts"]
    finally:
        pipeline.close()


def test_resume_one_raises_when_nothing_to_resume(project_config):
    pipeline = Pipeline(project_config)
    try:
        with pytest.raises(LyriaAutoError):
            pipeline.resume_one()
    finally:
        pipeline.close()
```

- [ ] **Step 2: 執行測試確認通過**

執行：`./.venv/bin/python -m pytest tests/test_resume.py -v`
預期：6 個測試全部 PASS（Task 4 的 2 個 + 這裡新增的 4 個）

- [ ] **Step 3: 確認既有測試不受影響**

執行：`./.venv/bin/python -m pytest -q`
預期：全部通過（19 + 4 = 23 passed）

- [ ] **Step 4: 確認變更**

無 git commit。

---

## Task 6: CLI `lyria-auto resume` 子指令

**Files:**
- Modify: `src/lyria_auto/cli.py`
- Create: `tests/test_cli_resume.py`

**Interfaces:**
- Consumes: `Pipeline.resume_one`（Task 3）
- Produces：CLI 子指令 `resume`，支援 `--job` / `--upload` / `--channel`

### 現況（`src/lyria_auto/cli.py` 目前完整內容已在對話中確認，86 行）

- [ ] **Step 1: 寫失敗測試**

建立 `tests/test_cli_resume.py`：

```python
from __future__ import annotations

import pytest

from lyria_auto.cli import main


def test_resume_cli_reports_clear_message_when_nothing_to_resume(project_config, capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["--root", str(project_config.root), "resume"])

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "找不到可續跑的工作" in captured.out
```

- [ ] **Step 2: 執行測試確認失敗**

執行：`./.venv/bin/python -m pytest tests/test_cli_resume.py -v`
預期：`argparse` 報 `invalid choice: 'resume'`（子指令還不存在）

- [ ] **Step 3: 加入 CLI 子指令**

修改 `src/lyria_auto/cli.py` 開頭 import：

old_string:
```python
from .config import load_config
from .doctor import run_doctor
from .logging_utils import configure_logging
from .pipeline import Pipeline
from .prompt_engine import PromptEngine
from .providers.youtube import YouTubeClient
```

new_string:
```python
from .config import load_config
from .doctor import run_doctor
from .errors import LyriaAutoError
from .logging_utils import configure_logging
from .pipeline import Pipeline
from .prompt_engine import PromptEngine
from .providers.youtube import YouTubeClient
```

加入 `resume` 子指令定義：

old_string:
```python
    sub.add_parser("scheduler", help="啟動常駐排程器")

    status = sub.add_parser("status", help="查看近期工作")
    status.add_argument("--limit", type=int, default=20)
    return parser
```

new_string:
```python
    sub.add_parser("scheduler", help="啟動常駐排程器")

    status = sub.add_parser("status", help="查看近期工作")
    status.add_argument("--limit", type=int, default=20)

    resume = sub.add_parser("resume", help="續跑未完成的工作")
    resume.add_argument("--job", type=int, default=None, help="指定 job id，省略則自動找最新一個")
    resume.add_argument("--upload", action="store_true")
    resume.add_argument("--channel", default="main")
    return parser
```

在 `main()` 裡加入 `resume` 分支：

old_string:
```python
    pipeline = Pipeline(config)
    try:
        if args.command == "run":
            results = pipeline.run_batch(args.videos, args.channel, args.upload, args.dry_run)
            print(json.dumps(results, ensure_ascii=False, indent=2))
        elif args.command == "status":
            print(json.dumps(pipeline.db.recent_jobs(args.limit), ensure_ascii=False, indent=2))
    finally:
        pipeline.close()
```

new_string:
```python
    pipeline = Pipeline(config)
    try:
        if args.command == "run":
            results = pipeline.run_batch(args.videos, args.channel, args.upload, args.dry_run)
            print(json.dumps(results, ensure_ascii=False, indent=2))
        elif args.command == "status":
            print(json.dumps(pipeline.db.recent_jobs(args.limit), ensure_ascii=False, indent=2))
        elif args.command == "resume":
            try:
                result = pipeline.resume_one(args.job, args.upload, args.channel)
            except LyriaAutoError as exc:
                print(str(exc))
                sys.exit(1)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        pipeline.close()
```

- [ ] **Step 4: 執行測試確認通過**

執行：`./.venv/bin/python -m pytest tests/test_cli_resume.py -v`
預期：1 個測試 PASS

- [ ] **Step 5: 確認既有測試不受影響**

執行：`./.venv/bin/python -m pytest -q`
預期：全部通過（23 + 1 = 24 passed）

- [ ] **Step 6: 確認變更**

無 git commit。

---

## Task 7: 最終驗收（對照 spec 第 9 節）

**Files:** 無新檔案，純驗證。

- [ ] **Step 1: 全部測試通過（含逐項列出）**

```bash
cd ~/Downloads/Lyria-Auto-Publisher
./.venv/bin/python -m pytest -v
```
預期：24 passed，逐一列出每個測試名稱，全部 PASS。**把完整輸出貼出來作為證據，不要只寫「測試通過」。**

- [ ] **Step 2: 環境檢查沒退步**

```bash
./.venv/bin/lyria-auto doctor
```
預期：與本次修改前一樣的結果（`ffmpeg`/`ffprobe`/套件 `[OK]`；`GEMINI_API_KEY`／YouTube 憑證視使用者環境而定，不因這次改動而改變）。

- [ ] **Step 3: dry-run 仍可用，文案未改變**

```bash
./.venv/bin/lyria-auto run --dry-run --videos 1
```
預期：印出 `dry_run: true` 的 JSON，`directory`/`plan` 檔案實際存在。

- [ ] **Step 4: resume 在沒有可續跑工作時給出明確訊息（不是 traceback）**

先確認目前 `workspace/state.sqlite3` 裡是否有非 complete/dry_run_complete 的 job（如果 Task 4/5 的測試用的是各自獨立的 tmp_path 資料庫，不會污染這個真實資料庫，這裡量的是使用者的真實工作區）：

```bash
./.venv/bin/lyria-auto resume
```
預期：印出「找不到可續跑的工作。用 lyria-auto status 查看歷史。」且離開碼為 1（不是 Python traceback）。如果使用者的真實 `workspace/state.sqlite3` 剛好有一個真的失敗中的 job，這裡會印出實際續跑結果而不是這則訊息——這種情況下改用 `./.venv/bin/lyria-auto resume --job 999999`（不存在的 job id）來驗證「找不到」訊息，或先用 `./.venv/bin/lyria-auto status --limit 5` 確認沒有非 complete 狀態的 job 再執行。

- [ ] **Step 5: 無縫循環實跑（用既有素材，不花錢）**

```bash
./.venv/bin/python -c "
import sys; sys.path.insert(0,'src')
from lyria_auto.media.audio import loop_audio
p = loop_audio('workspace/job_000004/compilation.m4a', '/tmp/loop_test.m4a', 1800, 2.0)
print(p)
"
ffprobe -v error -show_entries format=duration -of csv=p=0 /tmp/loop_test.m4a
```
預期：印出的長度接近 `1800.0`（±1 秒內）。**把實際數字貼出來。**

- [ ] **Step 6: 彙整結果**

把 Step 1~5 的實際輸出整理成一份簡短報告（不是重新宣稱「全部完成」，而是逐條列出每個驗收指令的實際輸出或關鍵數字），回報給使用者。若任何一步失敗或輸出不符預期，明確寫出哪一步、失敗訊息是什麼，不要略過或改寫成「大致正常」。

**不在本次計畫範圍內、不要順手做：** 更新 `docs/00_START_HERE_zh-TW.md` 加入 `resume` 指令說明（這是合理的下一步，但 spec 沒有要求，留給使用者決定要不要做）；分批 checkpoint；平行生成多軌；YouTube OAuth 驗證；標題文案模板調整。
