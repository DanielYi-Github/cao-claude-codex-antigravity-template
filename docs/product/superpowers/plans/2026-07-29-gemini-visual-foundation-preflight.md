# Gemini 視覺基礎與浮水印前置驗證 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立視覺設定、SQLite 資料模型、Gemini 圖片／Veo provider，以及一次性付費浮水印 smoke test，讓任何正式視覺生成都必須先通過人工核准的原始 API 輸出驗證。

**Architecture:** `GeminiVisualClient` 是唯一接觸 `google-genai` 的 Gemini Developer API adapter；所有非冪等付費 start 都採 at-most-once，無法判定遠端是否接受時落入 `start_uncertain`，絕不透明重試。`VisualPreflightService` 負責明確付費授權、operation 續跑、原始檔完整性與人工判定；`StateDB` 保存實際生效的來源指紋及 30 天效期。正式 pipeline 只依賴 `require_valid_preflight()`，不自行推測來源是否可信，也不提供裁切、遮蓋或修補浮水印的路徑。

**Tech Stack:** Python 3.11+、google-genai Interactions／generateVideos API、SQLite、Pillow、ffprobe、pytest、argparse。

---

## DX review 核准契約（2026-07-29）

本節是四份視覺計畫的共同前提；後續實作不得以舊 snippet 覆蓋這些規則。

- 產品定位：私人使用的 Mac-first CLI／自動化 pipeline；操作者懂 CLI 與 YAML，但日常不應讀程式、SQLite 或 traceback。
- 免費 magical moment：安裝完成後兩分鐘內可執行 `lyria-auto visual-demo`，只使用隔離的 sample DB／workspace 與標示 `SAMPLE — NOT FOR UPLOAD` 的本機素材。
- 本計畫先完成設定遷移、SDK capability、at-most-once 與 preflight；Plan 2 才能產生正式圖片，Plan 3 才能 start Veo，Plan 4 才能接入 Lyria、renderer、YouTube 或 scheduler。
- `resume` 只接續已存在的工作或 operation，永不建立新的付費輸出；額外生成一律使用 Plan 4 的 `regenerate` 命令與新的一次性額度。
- CLI 錯誤都使用 `code / problem / cause / fix / next_command / job_or_run_id / log_path`；預設簡潔，`--verbose` 顯示底層細節，`--json` 供 scheduler 使用。
- 所有 terminal 長任務顯示目前 stage、資產 `x/y`、scene／variant、elapsed、下一步與可安全中斷說明。
- 無可見產品標誌不等於隱瞞 AI；SynthID 保留，最終 YouTube 仍揭露合成內容。若 raw API preflight 出現可見標誌，流程停止，不提供移除路徑。

### 跨計畫 schema／狀態契約

Plan 1 必須一次建立後續三份計畫會使用的欄位及 migration runner；Plan 2、3、4 只能透過版本化 migration 增加欄位，不得要求使用者刪除 DB 重建。

| 狀態 | 可自動續跑 | 可重新 start | 說明 |
|---|---:|---:|---|
| `reserved` | yes | 僅尚未送出時 | DB intent 已 commit |
| `starting` | 僅可 reconcile | no | paid start intent 已 commit；無回應時轉 uncertain |
| `polling`／`normalizing` | yes | no | 必須沿用 operation ID |
| `start_uncertain` | no | no | 人工 reconcile；補生需要新額度 |
| `ready`／`qc_failed`／`failed_generation` | terminal | no | 普通 resume 不增加費用 |
| `failed_visible_mark`／`failed_tampered` | no | no | preflight 不可逆失敗 |

---

## Scope and file map

本計畫只交付「基礎與 preflight」，不生成正式場景、不建立 review 網頁、不改 FFmpeg 長片渲染。

| File | Responsibility |
|---|---|
| `src/lyria_auto/visual_models.py` | 視覺來源、資產與 preflight 的不可變型別 |
| `src/lyria_auto/providers/gemini_visual.py` | Nano Banana 2／Veo 3.1 Fast SDK adapter |
| `src/lyria_auto/visual_preflight.py` | 付費 smoke test、resume、人工核准與有效性 gate |
| `src/lyria_auto/db.py` | 三張視覺表及 preflight repository methods |
| `src/lyria_auto/cli.py` | `visual-preflight` 指令 |
| `src/lyria_auto/doctor.py` | 模型、價格日期及 preflight 狀態診斷 |
| `src/lyria_auto/config_migration.py` | 設定 schema preview、備份及非破壞遷移 |
| `config/settings.yaml` | 視覺模型、成本與輸出硬上限 |
| `scripts/setup.sh` | Mac Python 3.11～3.13 discovery 與可重現安裝 |
| `pyproject.toml` | 已驗證的 `google-genai` 相容範圍 |
| `tests/fakes_visual.py` | 不連網、不付費的 deterministic fake client |
| `tests/test_visual_config.py` | 設定與來源指紋測試 |
| `tests/test_config_migration.py` | preview、backup、保留自訂值及明確啟用測試 |
| `tests/test_setup_script.py` | Mac Python discovery 的靜態／子程序測試 |
| `tests/test_visual_db.py` | schema、狀態不可逆與有效性測試 |
| `tests/test_gemini_visual.py` | SDK response parsing、operation resume 測試 |
| `tests/test_visual_preflight.py` | service／CLI gate 端到端測試 |

全計畫限制：

- 所有 pytest 都必須使用 fake client；不得讀取或呼叫真實 `GEMINI_API_KEY`。
- smoke test 預設不執行，只有同時給 `--watermark-smoke-test --allow-image-outputs 1 --allow-video-outputs 1` 才可建立付費請求。
- 圖片寫入與影片下載先落到 `.partial`，通過基本解碼後才用 `Path.replace()` 原子移動。
- API key 不寫入 SQLite、log、JSON 或例外；只保存以安裝期 secret 計算的 HMAC。
- operation ID 必須在第一次 polling 前 commit。
- 使用者判定 `failed_visible_mark` 後不可改回通過。
- production start 一律只送一次；429、5xx、timeout 或斷線不得在 adapter 內透明重試。
- `google-genai` 使用 `>=2.13.0,<3`；`doctor` 以 symbol／signature capability 檢查目前 SDK，檢查本身零 API 呼叫。
- 設定 migration 預設只 preview；`--apply` 先建立時間戳備份，新增 `visual.enabled: false`，需另外 `--enable-visual` 才啟用。

---

### Task 0: Mac setup、SDK capability 與安全設定遷移

**Files:**
- Modify: `scripts/setup.sh`
- Modify: `pyproject.toml`
- Modify: `src/lyria_auto/cli.py`
- Modify: `src/lyria_auto/doctor.py`
- Create: `src/lyria_auto/config_migration.py`
- Create: `tests/test_config_migration.py`
- Create: `tests/test_setup_script.py`

**Entry gate：先清除既有 lint baseline。**

在任何視覺 production code 改動前，先使 `ruff check src tests` 通過並保存
baseline commit。2026-07-30 工程審查基準為 `pytest -q` 42 passed、Ruff 23 errors；
Plan 1 completion gate 不得在既有 Ruff errors 尚未清除時標記完成。

- [ ] **Step 1: Write failing migration and setup tests**

測試必須證明：

```python
def test_migration_preview_does_not_write(custom_settings):
    before = custom_settings.read_bytes()
    result = preview_settings_migration(custom_settings)
    assert result.missing_sections == ("visual",)
    assert custom_settings.read_bytes() == before


def test_apply_creates_backup_preserves_values_and_keeps_visual_disabled(custom_settings):
    apply_settings_migration(custom_settings)
    migrated = yaml.safe_load(custom_settings.read_text(encoding="utf-8"))
    assert migrated["video"]["target_duration_minutes"] == 300
    assert migrated["visual"]["enabled"] is False
    assert list(custom_settings.parent.glob("settings.yaml.*.bak"))


def test_setup_prefers_supported_python_in_descending_order(fake_path):
    assert choose_python(fake_path("python3.13", "python3.12")) == "python3.13"
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_config_migration.py tests/test_setup_script.py -v
```

Expected: imports or new assertions fail; no real settings file is changed.

- [ ] **Step 3: Implement the exact CLI and dependency contract**

`pyproject.toml` must use:

```toml
"google-genai>=2.13.0,<3",
```

CLI surface:

```bash
lyria-auto config-migrate --preview
lyria-auto config-migrate --apply
lyria-auto config-migrate --enable-visual
```

`scripts/setup.sh` searches `python3.13`, `python3.12`, then `python3.11`; if none exists it exits with one actionable Homebrew command. It must never silently use an unsupported system `python3`.

- [ ] **Step 4: Add zero-call SDK capability checks**

`doctor` verifies package version and the existence/signatures of:

```python
client.interactions.create
client.models.generate_videos
client.operations.get
```

Expected output includes the effective API mode `gemini-developer-api`, SDK version and capability result, without invoking any generation endpoint.

- [ ] **Step 5: Run tests and commit**

```bash
./.venv/bin/python -m pytest tests/test_config_migration.py tests/test_setup_script.py tests/test_doctor_visual.py -v
./.venv/bin/python -m ruff check src tests
git add scripts/setup.sh pyproject.toml src/lyria_auto/cli.py src/lyria_auto/doctor.py src/lyria_auto/config_migration.py tests/test_config_migration.py tests/test_setup_script.py
git commit -m "feat: add safe visual setup and config migration"
```

---

### Task 1: 視覺設定、錯誤與 domain types

**Files:**
- Modify: `config/settings.yaml`
- Modify: `src/lyria_auto/errors.py`
- Create: `src/lyria_auto/visual_models.py`
- Create: `tests/test_visual_config.py`

- [ ] **Step 1: Write the failing config and source identity tests**

建立 `tests/test_visual_config.py`：

```python
from __future__ import annotations

from lyria_auto.visual_models import VisualSource, credential_fingerprint


def test_default_visual_models_and_hard_limits(project_config):
    visual = project_config.section("visual")

    assert visual["enabled"] is False
    assert visual["image_model"] == "gemini-3.1-flash-image"
    assert visual["video_model"] == "veo-3.1-fast-generate-preview"
    assert visual["max_image_outputs"] == 14
    assert visual["max_video_outputs"] == 8
    assert visual["quality"]["seam_max_normalized_mae"] == 0.06
    assert visual["quality"]["motion_min_normalized_mae"] == 0.002
    assert visual["quality"]["motion_max_normalized_mae"] == 0.10
    assert visual["quality"]["duration_tolerance_seconds"] == 0.25
    assert visual["preflight"]["max_image_outputs"] == 1
    assert visual["preflight"]["max_video_outputs"] == 1
    assert visual["preflight"]["valid_days"] == 30


def test_credential_fingerprint_is_stable_and_does_not_contain_key(tmp_path):
    secret_path = tmp_path / "fingerprint.key"

    first = credential_fingerprint("private-api-key", secret_path)
    second = credential_fingerprint("private-api-key", secret_path)

    assert first == second
    assert len(first) == 64
    assert "private-api-key" not in first
    assert secret_path.read_bytes() != b"private-api-key"


def test_visual_source_key_changes_with_model():
    base = VisualSource(
        provider="gemini-developer-api",
        image_model="gemini-3.1-flash-image",
        video_model="veo-3.1-fast-generate-preview",
        credential_fingerprint="abc",
        sdk_version="2.13.0",
    )
    changed = VisualSource(
        provider=base.provider,
        image_model="gemini-3-pro-image",
        video_model=base.video_model,
        credential_fingerprint=base.credential_fingerprint,
        sdk_version=base.sdk_version,
    )

    assert base.identity_key() != changed.identity_key()
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_config.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'lyria_auto.visual_models'`.

- [ ] **Step 3: Add visual settings and domain types**

在 `config/settings.yaml` 的 `video:` 區段之前加入：

```yaml
visual:
  enabled: false
  provider: "gemini-developer-api"
  image_model: "gemini-3.1-flash-image"
  video_model: "veo-3.1-fast-generate-preview"
  image_size: "2K"
  aspect_ratio: "16:9"
  video_resolution: "1080p"
  video_duration_seconds: 8
  video_fps: 24
  max_unique_scenes: 4
  scene_interval_minutes: 30
  image_candidates_per_scene: 3
  video_candidates_per_scene: 2
  max_world_anchor_images: 1
  max_thumbnail_backgrounds: 1
  max_image_outputs: 14
  max_video_outputs: 8
  estimated_budget_usd: 10.00
  image_2k_estimated_usd: 0.101
  video_1080p_second_estimated_usd: 0.12
  pricing_snapshot_date: "2026-07-29"
  poll_interval_seconds: 10
  quality:
    seam_max_normalized_mae: 0.06
    motion_min_normalized_mae: 0.002
    motion_max_normalized_mae: 0.10
    duration_tolerance_seconds: 0.25
  preflight:
    max_image_outputs: 1
    max_video_outputs: 1
    valid_days: 30
```

在 `src/lyria_auto/errors.py` 末尾加入：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class StructuredError:
    code: str
    problem: str
    cause: str
    fix: str
    next_command: str
    job_id: int | None = None
    attempt_id: int | None = None
    log_reference: str | None = None


class VisualGenerationError(LyriaAutoError):
    code = "VISUAL_GENERATION_FAILED"


class PaidStartUncertainError(VisualGenerationError):
    """遠端可能已接受非冪等付費 start；禁止透明重試。"""
    code = "VISUAL_START_UNCERTAIN"


class VisualPollTransientError(VisualGenerationError):
    """operation ID 已保存，poll／download 可安全續跑。"""
    code = "VISUAL_POLL_TRANSIENT"


class VisualPreflightError(LyriaAutoError):
    code = "VISUAL_PREFLIGHT_FAILED"


class VisualApprovalError(LyriaAutoError):
    code = "VISUAL_APPROVAL_FAILED"


class MigrationInvariantError(LyriaAutoError):
    code = "MIGRATION_INVARIANT_FAILED"
```

同檔提供唯一 `sanitize_exception()` 與 `render_error()`。sanitizer 只把已知、
typed fields 放入 `StructuredError`，並移除 API key、Bearer/OAuth token、session
URI、query secret 與 provider payload；任何 plan snippet 都不得直接持久化或輸出
raw `str(exc)`。

建立 `src/lyria_auto/visual_models.py`：

```python
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VisualSource:
    provider: str
    image_model: str
    video_model: str
    credential_fingerprint: str
    sdk_version: str
    billing_project_id: str | None = None

    def identity_key(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GeneratedImage:
    path: Path
    mime_type: str
    sha256: str


@dataclass(frozen=True)
class VideoPoll:
    done: bool
    path: Path | None = None
    sha256: str | None = None
    error: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class SideEffectLease:
    scope_type: str
    scope_id: int
    owner_token: str
    expires_at: str
    acquired: bool


@dataclass(frozen=True)
class PaidStageAuthorization:
    id: int
    scope_type: str
    scope_id: int
    stage: str
    kind: str
    expected_count: int
    allowed_count: int


@dataclass(frozen=True)
class MediaSnapshot:
    path: Path
    sha256: str
    size_bytes: int
    probe: dict[str, Any]


def credential_fingerprint(api_key: str, secret_path: str | Path) -> str:
    path = Path(secret_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(os.urandom(32))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    secret = path.read_bytes()
    return hmac.new(secret, api_key.encode("utf-8"), hashlib.sha256).hexdigest()
```

- [ ] **Step 4: Make the shared fixture preserve legacy tests while exposing visual defaults**

在 `tests/conftest.py` 的 `project_config` fixture 寫回 YAML 前加入：

```python
    settings["visual"]["enabled"] = True
```

現階段不要把它改成 `False`；後續 pipeline 整合計畫才會新增專用 legacy fixture。

- [ ] **Step 5: Run tests and verify GREEN**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_config.py -v
```

Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add config/settings.yaml src/lyria_auto/errors.py src/lyria_auto/visual_models.py tests/conftest.py tests/test_visual_config.py
git commit -m "feat: add visual configuration and source identity"
```

---

### Task 2: SQLite visual schema and immutable preflight state

**Files:**
- Modify: `src/lyria_auto/db.py`
- Create: `tests/test_visual_db.py`

- [ ] **Step 1: Write failing schema and transition tests**

建立 `tests/test_visual_db.py`：

```python
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from lyria_auto.db import StateDB
from lyria_auto.errors import MigrationInvariantError, VisualApprovalError
from lyria_auto.visual_models import VisualSource


def source() -> VisualSource:
    return VisualSource(
        provider="gemini-developer-api",
        image_model="gemini-3.1-flash-image",
        video_model="veo-3.1-fast-generate-preview",
        credential_fingerprint="fingerprint-a",
        sdk_version="2.13.0",
    )


def test_visual_schema_is_idempotent(tmp_path):
    path = tmp_path / "state.sqlite3"
    first = StateDB(path)
    first.close()
    second = StateDB(path)
    try:
        names = {
            row["name"]
            for row in second.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"visual_preflight_runs", "visual_scenes", "visual_assets"} <= names
    finally:
        second.close()


def test_legacy_job_and_video_statuses_are_db_constrained(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        video_id = db.add_video(job_id, "title")

        with pytest.raises(sqlite3.IntegrityError, match="invalid jobs.status"):
            db.conn.execute(
                "UPDATE jobs SET status='typo' WHERE id=?",
                (job_id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="invalid videos.status"):
            db.conn.execute(
                "UPDATE videos SET status='typo' WHERE id=?",
                (video_id,),
            )
    finally:
        db.close()


def test_migration_rejects_unknown_preexisting_status_without_rewriting_it(
    legacy_db_with_unknown_job_status,
):
    with pytest.raises(MigrationInvariantError, match=r"jobs row .* unknown status"):
        StateDB(legacy_db_with_unknown_job_status)

    raw = sqlite3.connect(legacy_db_with_unknown_job_status)
    try:
        assert raw.execute("SELECT status FROM jobs").fetchone()[0] == "legacy_custom"
    finally:
        raw.close()


def test_asset_identity_is_unique_even_when_scene_is_null(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        values = (
            job_id,
            None,
            "world_anchor",
            0,
            "gemini-developer-api",
            "gemini-3.1-flash-image",
            "prompt",
            0.101,
            "{}",
            "planned",
            "2026-07-29T00:00:00+00:00",
            "2026-07-29T00:00:00+00:00",
        )
        sql = """
            INSERT INTO visual_assets(
              job_id,scene_id,asset_type,variant_index,provider,model,
              prompt,estimated_cost_usd,pricing_snapshot_json,status,
              created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """
        db.conn.execute(sql, values)
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute(sql, values)
    finally:
        db.close()


def test_failed_visible_mark_cannot_be_approved(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        run_id = db.create_preflight_run(source(), "/tmp/raw.png", "/tmp/raw.mp4", 1.061, {})
        finish_preflight_in_fixture(
            db, run_id, image_sha256="image-sha", video_sha256="video-sha"
        )
        review_preflight_in_fixture(
            db, run_id, image_ok=False, video_ok=True
        )

        with pytest.raises(VisualApprovalError, match="不可改寫"):
            review_preflight_in_fixture(
                db, run_id, image_ok=True, video_ok=True
            )
    finally:
        db.close()


def test_valid_preflight_requires_exact_source_and_unexpired_time(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        now = datetime.now(timezone.utc)
        run_id = db.create_preflight_run(source(), "/tmp/raw.png", "/tmp/raw.mp4", 1.061, {})
        finish_preflight_in_fixture(
            db, run_id, image_sha256="image-sha", video_sha256="video-sha"
        )
        review_preflight_in_fixture(
            db, run_id, image_ok=True, video_ok=True, now=now
        )

        assert db.valid_preflight(source(), now=now + timedelta(days=29)) is not None
        assert db.valid_preflight(source(), now=now + timedelta(days=31)) is None

        changed = VisualSource(
            provider=source().provider,
            image_model="gemini-3-pro-image",
            video_model=source().video_model,
            credential_fingerprint=source().credential_fingerprint,
            sdk_version=source().sdk_version,
        )
        assert db.valid_preflight(changed, now=now) is None
    finally:
        db.close()
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_db.py -v
```

Expected: fails because `StateDB.create_preflight_run` does not exist.

- [ ] **Step 3: Add the three tables**

在 `src/lyria_auto/db.py` 的 `SCHEMA` 中、結尾三引號之前加入：
此 schema migration 的固定 ID 為 `0001_visual_foundation`；checksum 與
`applied_at` 必須寫入共用 `schema_migrations`，不可只靠
`CREATE TABLE IF NOT EXISTS` 判定已套用。
同一 migration 為既有 `jobs`、`videos` 加入
`state_version INTEGER NOT NULL DEFAULT 0`；後續 job/video status 更新也使用
expected version CAS，不再呼叫無條件 `update_job(status)`。
SQLite 無法用 `ALTER TABLE ... ADD CHECK` 安全補強既有 status 欄位，因此
`0001_visual_foundation` 同時建立 `state_catalog` 與 validation triggers。
migration 必須先確認所有既有 row 都落在下列目錄；若發現未知值就 fail closed，
輸出 row id 與經 sanitizer 處理的 status，不可默默把值改成 `failed`。

```sql
CREATE TABLE IF NOT EXISTS visual_preflight_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT NOT NULL,
  image_model TEXT NOT NULL,
  video_model TEXT NOT NULL,
  billing_project_id TEXT,
  credential_fingerprint TEXT NOT NULL,
  source_identity_key TEXT NOT NULL,
  sdk_version TEXT NOT NULL,
    image_start_state TEXT NOT NULL DEFAULT 'reserved'
      CHECK(image_start_state IN ('reserved','starting','completed')),
    video_start_state TEXT NOT NULL DEFAULT 'reserved'
      CHECK(video_start_state IN ('reserved','starting','completed')),
    image_operation_id TEXT,
    video_operation_id TEXT,
    owner_token TEXT,
    raw_image_path TEXT NOT NULL,
    raw_video_path TEXT NOT NULL,
    approved_image_snapshot_path TEXT,
    approved_video_snapshot_path TEXT,
    image_sha256 TEXT,
    video_sha256 TEXT,
    image_size_bytes INTEGER,
    video_size_bytes INTEGER,
    video_probe_json TEXT,
  image_result TEXT,
  video_result TEXT,
  estimated_cost_usd REAL NOT NULL,
  pricing_snapshot_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
      'running','polling','awaiting_review','passed_no_visible_mark',
      'failed_visible_mark','failed_tampered','failed_generation','start_uncertain'
    )),
    state_version INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  approved_at TEXT,
  expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visual_scenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  label TEXT NOT NULL,
  world_json TEXT NOT NULL,
  image_prompt TEXT NOT NULL,
  motion_prompt TEXT NOT NULL,
  selected_image_asset_id INTEGER,
  selected_video_asset_id INTEGER,
    status TEXT NOT NULL CHECK(status IN (
      'planned','generating_images','awaiting_image_review','image_selected',
      'image_approved','needs_regeneration','generating_videos',
      'awaiting_video_review','video_selected','video_approved',
      'needs_video_regeneration'
    )),
    state_version INTEGER NOT NULL DEFAULT 0,
  approved_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(job_id, position),
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS visual_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  scene_id INTEGER,
  asset_type TEXT NOT NULL,
  variant_index INTEGER NOT NULL,
  provider TEXT NOT NULL,
    model TEXT NOT NULL,
    operation_id TEXT,
    owner_token TEXT,
  prompt TEXT NOT NULL,
  raw_path TEXT,
  path TEXT,
  mime_type TEXT,
  width INTEGER,
  height INTEGER,
  duration_seconds REAL,
  fps REAL,
  seam_score REAL,
  motion_score REAL,
  sha256 TEXT,
  estimated_cost_usd REAL NOT NULL,
  pricing_snapshot_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN (
      'reserved','starting','polling','normalizing','ready','qc_failed',
      'approved','failed_generation','failed_tampered','start_uncertain',
      'rejected','superseded'
    )),
    state_version INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES jobs(id),
    FOREIGN KEY(scene_id) REFERENCES visual_scenes(id)
);
CREATE TABLE IF NOT EXISTS side_effect_leases (
  scope_type TEXT NOT NULL,
  scope_id INTEGER NOT NULL,
  owner_token TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  state_version INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(scope_type, scope_id)
);
CREATE TABLE IF NOT EXISTS paid_stage_authorizations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scope_type TEXT NOT NULL,
  scope_id INTEGER NOT NULL,
  stage TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('image','video')),
  expected_count INTEGER NOT NULL CHECK(expected_count >= 0),
  allowed_count INTEGER NOT NULL CHECK(allowed_count >= 0),
  created_at TEXT NOT NULL,
  UNIQUE(scope_type, scope_id, stage, kind)
);
CREATE TABLE IF NOT EXISTS paid_start_intents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  authorization_id INTEGER NOT NULL REFERENCES paid_stage_authorizations(id),
  identity_key TEXT NOT NULL,
  owner_token TEXT NOT NULL,
  consumed_at TEXT NOT NULL,
  UNIQUE(authorization_id, identity_key)
);
CREATE TABLE IF NOT EXISTS state_catalog (
  entity TEXT NOT NULL CHECK(entity IN ('job','video')),
  status TEXT NOT NULL,
  PRIMARY KEY(entity, status)
);
INSERT INTO state_catalog(entity,status) VALUES
  ('job','created'),
  ('job','planning'),
  ('job','planned'),
  ('job','generating'),
  ('job','generating_images'),
  ('job','awaiting_image_review'),
  ('job','generating_videos'),
  ('job','awaiting_video_review'),
  ('job','generating_audio'),
  ('job','rendering'),
  ('job','rendered'),
  ('job','uploading'),
  ('job','complete'),
  ('job','dry_run_complete'),
  ('job','failed'),
  ('video','planned'),
  ('video','dry_run'),
  ('video','rendering'),
  ('video','rendered'),
  ('video','uploading'),
  ('video','uploaded')
ON CONFLICT(entity,status) DO NOTHING;
CREATE TRIGGER IF NOT EXISTS trg_jobs_status_insert
BEFORE INSERT ON jobs
WHEN NOT EXISTS (
  SELECT 1 FROM state_catalog
  WHERE entity='job' AND status=NEW.status
)
BEGIN
  SELECT RAISE(ABORT, 'invalid jobs.status');
END;
CREATE TRIGGER IF NOT EXISTS trg_jobs_status_update
BEFORE UPDATE OF status ON jobs
WHEN NOT EXISTS (
  SELECT 1 FROM state_catalog
  WHERE entity='job' AND status=NEW.status
)
BEGIN
  SELECT RAISE(ABORT, 'invalid jobs.status');
END;
CREATE TRIGGER IF NOT EXISTS trg_videos_status_insert
BEFORE INSERT ON videos
WHEN NOT EXISTS (
  SELECT 1 FROM state_catalog
  WHERE entity='video' AND status=NEW.status
)
BEGIN
  SELECT RAISE(ABORT, 'invalid videos.status');
END;
CREATE TRIGGER IF NOT EXISTS trg_videos_status_update
BEFORE UPDATE OF status ON videos
WHEN NOT EXISTS (
  SELECT 1 FROM state_catalog
  WHERE entity='video' AND status=NEW.status
)
BEGIN
  SELECT RAISE(ABORT, 'invalid videos.status');
END;
CREATE UNIQUE INDEX IF NOT EXISTS idx_visual_assets_identity
ON visual_assets(
  job_id,
  asset_type,
  COALESCE(scene_id, -1),
    variant_index
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_visual_assets_operation
ON visual_assets(operation_id)
WHERE operation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_visual_assets_job_kind_status
ON visual_assets(job_id,asset_type,status);
CREATE INDEX IF NOT EXISTS idx_visual_scenes_job_status
ON visual_scenes(job_id,status);
CREATE INDEX IF NOT EXISTS idx_side_effect_leases_expiry
ON side_effect_leases(expires_at);
```

status 清單是 schema 契約；後續 Plan 若新增狀態，必須以新的 migration 擴充，
不可只修改 Python 常數。`state_catalog` 不提供 runtime mutation API；只能由
checksum 固定的新 migration 新增狀態。所有可變 row transition 都必須攜帶 expected
`state_version`，成功時將 version 加一。

`side_effect_leases`、`paid_stage_authorizations` 與 `paid_start_intents` 是
Plan 1 建立的共用 ownership primitive。Plan 2–4 只能擴充 repository API，
不得另造第二套 allowance／lock 表。

- [ ] **Step 4: Add preflight repository methods**

把 `StateDB.__init__()` 的連線建立改為：

```python
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
```

後續 localhost review server 使用單執行緒 `HTTPServer`，但測試會把 server 放在背景執行緒；這個設定允許該單一 server thread 使用同一連線，不代表允許多執行緒同時寫入。

在 `src/lyria_auto/db.py` imports 加入：

```python
from datetime import datetime, timedelta, timezone

from .errors import VisualApprovalError
from .visual_models import VisualSource
```

在 `StateDB` 類別末尾加入：

```python
    def create_preflight_run(
        self,
        source: VisualSource,
        raw_image_path: str,
        raw_video_path: str,
        estimated_cost_usd: float,
        pricing_snapshot: dict[str, Any],
    ) -> int:
        now = utc_now_iso()
        cur = self.conn.execute(
            """
            INSERT INTO visual_preflight_runs(
              provider,image_model,video_model,billing_project_id,
              credential_fingerprint,source_identity_key,sdk_version,
              raw_image_path,raw_video_path,estimated_cost_usd,
              pricing_snapshot_json,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                source.provider,
                source.image_model,
                source.video_model,
                source.billing_project_id,
                source.credential_fingerprint,
                source.identity_key(),
                source.sdk_version,
                raw_image_path,
                raw_video_path,
                estimated_cost_usd,
                json.dumps(pricing_snapshot, ensure_ascii=False),
                "running",
                now,
                now,
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def preflight_run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM visual_preflight_runs WHERE id=?", (run_id,)
        ).fetchone()

    # 不提供任意 update_preflight(**values)。實作下列明確 CAS methods：
    # claim_paid_start(), record_preflight_image_result(),
    # record_preflight_video_operation(), record_preflight_error(),
    # mark_preflight_start_uncertain(), finish_preflight_generation(),
    # transition_preflight(), review_preflight()。
    # 每個 method 都要求 expected status/version；paid methods 另要求 owner_token。

    def finish_preflight_generation(
        self,
        run_id: int,
        image_sha256: str,
        video_sha256: str,
        *,
        expected_version: int,
        owner_token: str,
    ) -> None:
        changed = self.conn.execute(
            """
            UPDATE visual_preflight_runs
            SET image_sha256=?,video_sha256=?,status='awaiting_review',
                state_version=state_version+1,error=NULL,updated_at=?
            WHERE id=? AND status IN ('running','polling')
              AND state_version=? AND owner_token=?
            """,
            (
                image_sha256, video_sha256, utc_now_iso(), run_id,
                expected_version, owner_token,
            ),
        ).rowcount
        if changed != 1:
            self.conn.rollback()
            raise VisualApprovalError("preflight 狀態已更新，不能完成 generation")
        self.conn.commit()

    def review_preflight(
        self,
        run_id: int,
        *,
        image_ok: bool,
        video_ok: bool,
        expected_status: str,
        expected_version: int,
        image_snapshot: MediaSnapshot,
        video_snapshot: MediaSnapshot,
        now: datetime | None = None,
        valid_days: int = 30,
    ) -> None:
        row = self.preflight_run(run_id)
        if row is None:
            raise VisualApprovalError(f"找不到 preflight run {run_id}")
        if row["status"] in (
            "failed_visible_mark",
            "failed_tampered",
            "failed_generation",
            "start_uncertain",
        ):
            raise VisualApprovalError("失敗的 preflight 不可改寫成通過，請建立新的 run")
        if row["status"] != "awaiting_review":
            raise VisualApprovalError(f"preflight 尚不可審核（status={row['status']}）")
        current = now or datetime.now(timezone.utc)
        passed = image_ok and video_ok
        status = "passed_no_visible_mark" if passed else "failed_visible_mark"
        expires = current + timedelta(days=valid_days) if passed else None
        changed = self.conn.execute(
            """
            UPDATE visual_preflight_runs
            SET image_result=?,video_result=?,status=?,approved_at=?,
                expires_at=?,approved_image_snapshot_path=?,
                approved_video_snapshot_path=?,image_size_bytes=?,
                video_size_bytes=?,video_probe_json=?,
                state_version=state_version+1,updated_at=?
            WHERE id=? AND status=? AND state_version=?
            """,
            (
                "passed_no_visible_mark" if image_ok else "failed_visible_mark",
                "passed_no_visible_mark" if video_ok else "failed_visible_mark",
                status,
                current.isoformat(),
                expires.isoformat() if expires else None,
                str(image_snapshot.path),
                str(video_snapshot.path),
                image_snapshot.size_bytes,
                video_snapshot.size_bytes,
                json.dumps(video_snapshot.probe, ensure_ascii=False),
                current.isoformat(),
                run_id,
                expected_status,
                expected_version,
            ),
        ).rowcount
        if changed != 1:
            self.conn.rollback()
            raise VisualApprovalError("preflight 狀態已更新；重新載入後再審核")
        self.conn.commit()

    def valid_preflight(
        self, source: VisualSource, *, now: datetime | None = None
    ) -> sqlite3.Row | None:
        current = (now or datetime.now(timezone.utc)).isoformat()
        return self.conn.execute(
            """
            SELECT * FROM visual_preflight_runs
              WHERE source_identity_key=?
                AND status='passed_no_visible_mark'
                AND expires_at>?
                AND approved_image_snapshot_path IS NOT NULL
                AND approved_video_snapshot_path IS NOT NULL
            ORDER BY id DESC LIMIT 1
            """,
            (source.identity_key(), current),
        ).fetchone()
```

- [ ] **Step 5: Run DB tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_db.py tests/test_visual_db.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/db.py tests/test_visual_db.py
git commit -m "feat: persist visual preflight and asset state"
```

---

### Task 3: Gemini Developer API visual adapter

**Files:**
- Create: `src/lyria_auto/providers/gemini_visual.py`
- Create: `tests/fakes_visual.py`
- Create: `tests/test_gemini_visual.py`

- [ ] **Step 1: Write SDK adapter tests with object-shaped fakes**

建立 `tests/fakes_visual.py`：

```python
from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 90), (80, 60, 40)).save(buffer, "PNG")
    return buffer.getvalue()


class FakeVisualSDK:
    def __init__(self):
        self.image_calls = 0
        self.video_starts = 0
        self.video_polls = 0
        self.operations_seen: list[str] = []
        self.interactions = SimpleNamespace(create=self._create_image)
        self.models = SimpleNamespace(generate_videos=self._start_video)
        self.operations = SimpleNamespace(get=self._poll_video)
        self.files = SimpleNamespace(download=self._download)

    def _create_image(self, **kwargs):
        self.image_calls += 1
        return SimpleNamespace(
            output_image=SimpleNamespace(
                data=base64.b64encode(png_bytes()).decode("ascii"),
                mime_type="image/png",
            )
        )

    def _start_video(self, **kwargs):
        self.video_starts += 1
        return SimpleNamespace(name="operations/video-1", done=False)

    def _poll_video(self, operation):
        self.video_polls += 1
        self.operations_seen.append(operation.name)
        video = SimpleNamespace(save=lambda path: Path(path).write_bytes(b"fake-mp4"))
        return SimpleNamespace(
            name=operation.name,
            done=True,
            error=None,
            response=SimpleNamespace(
                generated_videos=[SimpleNamespace(video=video)]
            ),
        )

    def _download(self, *, file):
        return None
```

建立 `tests/test_gemini_visual.py`：

```python
from __future__ import annotations

from pathlib import Path

import pytest

from fakes_visual import FakeVisualSDK, png_bytes
from lyria_auto.errors import PaidStartUncertainError, VisualGenerationError
from lyria_auto.providers.gemini_visual import GeminiVisualClient


def client(fake):
    return GeminiVisualClient(
        api_key="unused",
        image_model="gemini-3.1-flash-image",
        video_model="veo-3.1-fast-generate-preview",
        sdk_client=fake,
        video_validator=lambda path: None,
    )


def test_generate_image_writes_decodable_raw_bytes(tmp_path):
    fake = FakeVisualSDK()

    result = client(fake).generate_image(
        "photorealistic empty cafe",
        tmp_path / "raw.png",
        reference_paths=[],
    )

    assert result.path.exists()
    assert result.mime_type == "image/png"
    assert len(result.sha256) == 64
    assert fake.image_calls == 1


def test_video_operation_can_resume_from_persisted_name(tmp_path):
    fake = FakeVisualSDK()
    adapter = client(fake)
    frame = tmp_path / "frame.png"
    frame.write_bytes(png_bytes())

    operation_id = adapter.start_video("locked camera", frame)
    poll = adapter.poll_video(operation_id, tmp_path / "raw.mp4")

    assert operation_id == "operations/video-1"
    assert poll.done is True
    assert poll.path == tmp_path / "raw.mp4"
    assert fake.operations_seen == ["operations/video-1"]
    assert fake.video_starts == 1


@pytest.mark.parametrize("status_code", [429, 503])
def test_paid_start_is_at_most_once_when_result_is_uncertain(tmp_path, status_code):
    fake = FakeVisualSDK()
    attempts = 0

    class TransientError(RuntimeError):
        pass

    def uncertain(**kwargs):
        nonlocal attempts
        attempts += 1
        error = TransientError("response lost")
        error.status_code = status_code
        raise error

    fake.interactions.create = uncertain
    adapter = GeminiVisualClient(
        api_key="unused",
        image_model="gemini-3.1-flash-image",
        video_model="veo-3.1-fast-generate-preview",
        sdk_client=fake,
        video_validator=lambda path: None,
    )

    with pytest.raises(PaidStartUncertainError, match="不得自動重試"):
        adapter.generate_image(
            "photorealistic empty cafe",
            tmp_path / "raw.png",
            reference_paths=[],
        )

    assert attempts == 1


def test_non_transient_failure_is_not_retried(tmp_path):
    fake = FakeVisualSDK()
    attempts = 0

    def invalid(**kwargs):
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid request")

    fake.interactions.create = invalid
    adapter = client(fake)

    with pytest.raises(VisualGenerationError, match="invalid request"):
        adapter.generate_image(
            "photorealistic empty cafe",
            tmp_path / "raw.png",
            reference_paths=[],
        )

    assert attempts == 1
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_gemini_visual.py -v
```

Expected: import fails because `providers.gemini_visual` does not exist.

- [ ] **Step 3: Implement the adapter**

建立 `src/lyria_auto/providers/gemini_visual.py`：

```python
from __future__ import annotations

import base64
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from ..errors import (
    PaidStartUncertainError,
    VisualGenerationError,
    VisualPollTransientError,
)
from ..utils import run_command, sha256_file
from ..visual_models import GeneratedImage, VideoPoll


class GeminiVisualClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        image_model: str,
        video_model: str,
        sdk_client: Any | None = None,
        video_validator: Callable[[Path], None] | None = None,
    ):
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key and sdk_client is None:
            raise VisualGenerationError("尚未設定 GEMINI_API_KEY")
        if sdk_client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise VisualGenerationError("尚未安裝 google-genai") from exc
            sdk_client = genai.Client(api_key=key)
        self.client = sdk_client
        self.image_model = image_model
        self.video_model = video_model
        self.video_validator = video_validator or self._validate_video

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(exc, "code", None)
        for candidate in (status, getattr(status, "value", None)):
            try:
                code = int(candidate)
            except (TypeError, ValueError):
                continue
            return code == 429 or 500 <= code <= 599
        return str(getattr(status, "name", status)).upper() in {
            "RESOURCE_EXHAUSTED",
            "UNAVAILABLE",
            "INTERNAL",
        }

    def _paid_start(self, call: Callable[[], Any]) -> Any:
        try:
            return call()
        except Exception as exc:
            if self._is_transient(exc):
                raise PaidStartUncertainError(
                    "付費 start 的回應不確定；本次不得自動重試，"
                    "請保留 run／asset ID 並人工 reconcile"
                ) from exc
            raise

    @staticmethod
    def _validate_video(path: Path) -> None:
        result = run_command(
            [
                "ffprobe", "-v", "error", "-show_streams", "-show_format",
                "-of", "json", str(path),
            ]
        )
        payload = json.loads(result.stdout)
        streams = [
            stream for stream in payload.get("streams", [])
            if stream.get("codec_type") == "video"
        ]
        duration = float(payload.get("format", {}).get("duration") or 0)
        if len(streams) != 1 or duration <= 0:
            raise VisualGenerationError("Veo 原始影片無法通過 FFprobe")

    @staticmethod
    def _atomic_bytes(output_path: Path, data: bytes) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial = output_path.with_name(
            output_path.stem + ".partial" + output_path.suffix
        )
        partial.write_bytes(data)
        partial.replace(output_path)
        return output_path

    def generate_image(
        self,
        prompt: str,
        output_path: str | Path,
        *,
        reference_paths: list[str | Path],
    ) -> GeneratedImage:
        inputs: list[dict[str, str]] = [{"type": "text", "text": prompt}]
        for path in reference_paths:
            ref = Path(path)
            mime = "image/png" if ref.suffix.lower() == ".png" else "image/jpeg"
            inputs.append(
                {
                    "type": "image",
                    "data": base64.b64encode(ref.read_bytes()).decode("ascii"),
                    "mime_type": mime,
                }
            )
        try:
            interaction = self._paid_start(
                lambda: self.client.interactions.create(
                    model=self.image_model,
                    input=inputs,
                    response_format={
                        "type": "image",
                        "mime_type": "image/png",
                        "aspect_ratio": "16:9",
                        "image_size": "2K",
                    },
                )
            )
            output = getattr(interaction, "output_image", None)
            if output is None or getattr(output, "data", None) is None:
                raise VisualGenerationError("Gemini 圖片回應沒有 output_image")
            data = base64.b64decode(output.data)
            path = self._atomic_bytes(Path(output_path), data)
            with Image.open(path) as image:
                image.verify()
            return GeneratedImage(
                path=path,
                mime_type=getattr(output, "mime_type", None) or "image/png",
                sha256=sha256_file(path),
            )
        except VisualGenerationError:
            raise
        except Exception as exc:
            raise VisualGenerationError(f"Gemini 圖片生成失敗：{exc}") from exc

    def start_video(self, prompt: str, frame_path: str | Path) -> str:
        try:
            from google.genai import types

            frame = types.Image.from_file(location=str(frame_path))
            operation = self._paid_start(
                lambda: self.client.models.generate_videos(
                    model=self.video_model,
                    prompt=prompt,
                    image=frame,
                    config=types.GenerateVideosConfig(
                        last_frame=frame,
                        number_of_videos=1,
                        duration_seconds=8,
                        resolution="1080p",
                        aspect_ratio="16:9",
                    ),
                )
            )
            if not getattr(operation, "name", None):
                raise VisualGenerationError("Veo 沒有回傳 operation name")
            return str(operation.name)
        except VisualGenerationError:
            raise
        except Exception as exc:
            raise VisualGenerationError(f"Veo 建立 operation 失敗：{exc}") from exc

    def poll_video(self, operation_id: str, output_path: str | Path) -> VideoPoll:
        try:
            from google.genai import types

            operation = types.GenerateVideosOperation(name=operation_id)
            operation = self.client.operations.get(operation)
            if not operation.done:
                return VideoPoll(done=False)
            if getattr(operation, "error", None):
                return VideoPoll(done=True, error=str(operation.error))
            generated = operation.response.generated_videos
            if not generated:
                return VideoPoll(done=True, error="Veo 完成但沒有 generated_videos")
            video = generated[0].video
            self.client.files.download(file=video)
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            partial = output.with_name(
                output.stem + ".partial" + output.suffix
            )
            video.save(str(partial))
            self.video_validator(partial)
            partial.replace(output)
            return VideoPoll(done=True, path=output, sha256=sha256_file(output))
        except Exception as exc:
            if self._is_transient(exc):
                raise VisualPollTransientError(
                    f"Veo polling／下載暫時失敗，可沿用 operation {operation_id} 續跑：{exc}"
                ) from exc
            raise VisualGenerationError(f"Veo polling／下載失敗：{exc}") from exc
```

- [ ] **Step 4: Keep the fake independent from installed SDK types**

在 `tests/test_gemini_visual.py` 的兩個測試中，以 monkeypatch 替換 `google.genai.types.Image.from_file` 和 `GenerateVideosOperation`；加入：

```python
def install_fake_types(monkeypatch):
    from google.genai import types

    monkeypatch.setattr(
        types.Image,
        "from_file",
        classmethod(lambda cls, location: object()),
    )
    monkeypatch.setattr(
        types,
        "GenerateVideosOperation",
        lambda name: type("OperationRef", (), {"name": name})(),
    )
```

把 `test_video_operation_can_resume_from_persisted_name` signature 改成：

```python
def test_video_operation_can_resume_from_persisted_name(tmp_path, monkeypatch):
    install_fake_types(monkeypatch)
```

- [ ] **Step 5: Run adapter tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_gemini_visual.py -v
```

Expected: all pass；429／503／timeout 的 paid start 各只有一次 call 並回
`PaidStartUncertainError`；poll transient 可重試同一 operation，但不會再次 start。

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/providers/gemini_visual.py tests/fakes_visual.py tests/test_gemini_visual.py
git commit -m "feat: add Gemini image and Veo video adapter"
```

---

### Task 4: Resumable paid watermark smoke test service

**Files:**
- Create: `src/lyria_auto/visual_preflight.py`
- Create: `tests/test_visual_preflight.py`

**Paid-start rule：** `VisualPreflightService` 進入任何 paid adapter 前，必須先取得
`scope_type='visual_preflight'` 的 lease，並呼叫 Plan 1 共用
`claim_paid_start()`。該方法在單一 `BEGIN IMMEDIATE` transaction 中：

1. 驗證 lease owner／expiry。
2. 驗證 persisted exact authorization。
3. 以 expected `state_version` CAS 將對應 start state 從 `reserved` 改為 `starting`。
4. 插入唯一 `paid_start_intents(identity_key)`。
5. 只有 affected row 為 1 才 commit 並回傳 acquired。

provider call 在 transaction 外執行。claim 失敗的 worker 不得建立
`GeminiVisualClient`。lease 仍有效時其他 process 只能顯示已有 owner；
lease 過期且 DB reopen 後，`starting` 且沒有可驗證結果才轉
`start_uncertain`。

- [ ] **Step 1: Write failing service tests**

建立 `tests/test_visual_preflight.py`：

```python
from __future__ import annotations

from pathlib import Path

import pytest

from lyria_auto.db import StateDB
from lyria_auto.errors import VisualPreflightError
from lyria_auto.visual_preflight import VisualPreflightService
from fakes_visual import FakeVisualSDK, png_bytes


def service(project_config, fake):
    db = StateDB(project_config.root / "workspace" / "preflight.sqlite3")
    return db, VisualPreflightService(
        project_config,
        db,
        sdk_client=fake,
        sleep=lambda _: None,
        video_validator=lambda _: None,
    )


def test_exact_paid_allowance_is_required(project_config):
    fake = FakeVisualSDK()
    db, preflight = service(project_config, fake)
    try:
        with pytest.raises(VisualPreflightError, match="精確授權"):
            preflight.start(allow_image_outputs=0, allow_video_outputs=1)
        assert fake.image_calls == 0
        assert fake.video_starts == 0
    finally:
        db.close()


def test_preflight_never_retries_transient_paid_call(project_config):
    fake = FakeVisualSDK()
    attempts = 0

    class TooManyRequests(RuntimeError):
        status_code = 429

    def transient(**kwargs):
        nonlocal attempts
        attempts += 1
        raise TooManyRequests("temporary")

    fake.interactions.create = transient
    db, preflight = service(project_config, fake)
    try:
        with pytest.raises(VisualPreflightError, match="temporary"):
            preflight.start(allow_image_outputs=1, allow_video_outputs=1)
        assert attempts == 1
        assert fake.video_starts == 0
    finally:
        db.close()


def test_smoke_test_saves_raw_outputs_and_waits_for_review(project_config):
    fake = FakeVisualSDK()
    db, preflight = service(project_config, fake)
    try:
        run_id = preflight.start(allow_image_outputs=1, allow_video_outputs=1)
        row = db.preflight_run(run_id)

        assert row["status"] == "awaiting_review"
        assert Path(row["raw_image_path"]).exists()
        assert Path(row["raw_video_path"]).read_bytes() == b"fake-mp4"
        assert row["video_operation_id"] == "operations/video-1"
        assert fake.image_calls == 1
        assert fake.video_starts == 1
    finally:
        db.close()


def test_resume_polls_existing_operation_without_second_start(project_config):
    fake = FakeVisualSDK()
    db, preflight = service(project_config, fake)
    try:
        run_id = preflight._create_run()
        row = db.preflight_run(run_id)
        Path(row["raw_image_path"]).write_bytes(png_bytes())
        seed_preflight_state(
            db,
            run_id,
            image_sha256="existing-image",
            video_operation_id="operations/video-1",
        )

        preflight.resume(run_id)

        assert fake.video_starts == 0
        assert fake.video_polls == 1
    finally:
        db.close()
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_preflight.py -v
```

Expected: import fails because `visual_preflight.py` does not exist.

- [ ] **Step 3: Implement source identity, cost guard and resume**

建立 `src/lyria_auto/visual_preflight.py`：

```python
from __future__ import annotations

import importlib.metadata
import os
import time
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .config import AppConfig
from .db import StateDB
from .errors import (
    PaidStartUncertainError,
    VisualPollTransientError,
    VisualPreflightError,
)
from .providers.gemini_visual import GeminiVisualClient
from .utils import ensure_dir, sha256_file, utc_now_iso
from .visual_models import VisualSource, credential_fingerprint


PREFLIGHT_IMAGE_PROMPT = (
    "Photorealistic documentary photograph of an empty neighborhood coffee shop, "
    "natural materials, physically plausible light, 16:9, no people, no text, "
    "no logo, no border, no product mark."
)
PREFLIGHT_VIDEO_PROMPT = (
    "Single continuous photorealistic shot of this empty coffee shop. Locked-off "
    "camera. Only subtle steam and curtain movement. No people, no text, no cuts, "
    "no zoom, no pan, no logo."
)


class VisualPreflightService:
    def __init__(
        self,
        config: AppConfig,
        db: StateDB,
        *,
        sdk_client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        video_validator: Callable[[Path], None] | None = None,
    ):
        self.config = config
        self.db = db
        self.visual = config.section("visual")
        self.sleep = sleep
        self.workspace = ensure_dir(
            config.root
            / config.section("project").get("workspace", "workspace")
            / "visual_preflight"
        )
        api_key = os.getenv("GEMINI_API_KEY") or (
            "fake-key" if sdk_client is not None else ""
        )
        try:
            sdk_version = importlib.metadata.version("google-genai")
        except importlib.metadata.PackageNotFoundError:
            sdk_version = "unknown"
        self.source = VisualSource(
            provider=str(self.visual["provider"]),
            image_model=str(self.visual["image_model"]),
            video_model=str(self.visual["video_model"]),
            credential_fingerprint=credential_fingerprint(
                api_key, self.workspace / ".fingerprint.key"
            ),
            sdk_version=sdk_version,
            billing_project_id=os.getenv("GOOGLE_CLOUD_PROJECT"),
        )
        self.client = GeminiVisualClient(
            api_key=api_key,
            image_model=self.source.image_model,
            video_model=self.source.video_model,
            sdk_client=sdk_client,
            video_validator=video_validator,
        )

    def _estimated_cost(self) -> float:
        return float(self.visual["image_2k_estimated_usd"]) + (
            int(self.visual["video_duration_seconds"])
            * float(self.visual["video_1080p_second_estimated_usd"])
        )

    def _create_run(self) -> int:
        run_dir = ensure_dir(self.workspace / "pending")
        run_id = self.db.create_preflight_run(
            self.source,
            str(run_dir / "raw_image.png"),
            str(run_dir / "raw_video.mp4"),
            self._estimated_cost(),
            {
                "date": self.visual["pricing_snapshot_date"],
                "image_2k_usd": self.visual["image_2k_estimated_usd"],
                "video_1080p_second_usd": self.visual[
                    "video_1080p_second_estimated_usd"
                ],
            },
        )
        final_dir = ensure_dir(self.workspace / f"run_{run_id:06d}")
        self.db.conn.execute(
            """
            UPDATE visual_preflight_runs
            SET raw_image_path=?,raw_video_path=?,updated_at=?
            WHERE id=?
            """,
            (
                str(final_dir / "raw_image.png"),
                str(final_dir / "raw_video.mp4"),
                utc_now_iso(),
                run_id,
            ),
        )
        self.db.conn.commit()
        return run_id

    def start(self, *, allow_image_outputs: int, allow_video_outputs: int) -> int:
        preflight = self.visual["preflight"]
        expected_image = int(preflight["max_image_outputs"])
        expected_video = int(preflight["max_video_outputs"])
        if (allow_image_outputs, allow_video_outputs) != (
            expected_image,
            expected_video,
        ):
            raise VisualPreflightError(
                "付費 smoke test 必須精確授權一張圖片及一段影片"
            )
          run_id = self._create_run()
          self.db.create_exact_paid_authorizations(
              scope_type="visual_preflight",
              scope_id=run_id,
              stages={
                  ("watermark_smoke", "image"): expected_image,
                  ("watermark_smoke", "video"): expected_video,
              },
          )
          return self.resume(run_id)

      def resume(self, run_id: int) -> int:
          lease = self.db.acquire_side_effect_lease(
              scope_type="visual_preflight",
              scope_id=run_id,
          )
          if not lease.acquired:
              raise VisualPreflightError(
                  structured_error(
                      code="VISUAL_PREFLIGHT_ALREADY_RUNNING",
                      problem="另一個 process 正在處理此 preflight",
                      cause="目前 lease 尚未過期",
                      fix="等待 owner 完成或 lease 過期後再執行 resume",
                      next_command=f"lyria-auto visual-preflight --status {run_id}",
                      job_id=run_id,
                  )
              )
          row = self.db.preflight_run(run_id)
          if row is None:
              raise VisualPreflightError(f"找不到 preflight run {run_id}")
          if row["status"] not in ("running", "polling"):
              return run_id
          image_path = Path(row["raw_image_path"])
          video_path = Path(row["raw_video_path"])
          try:
              image_sha = row["image_sha256"]
              if row["image_start_state"] == "starting":
                  if self.db.lease_is_active_for_owner(
                      "visual_preflight", run_id, str(row["owner_token"])
                  ):
                      return run_id
                  if not image_path.exists():
                      self.db.mark_preflight_start_uncertain(
                          run_id,
                          expected_version=int(row["state_version"]),
                          error_code="VISUAL_IMAGE_START_UNCERTAIN",
                      )
                      raise PaidStartUncertainError(
                          f"preflight run {run_id} 圖片 start 結果不確定；不得自動重試"
                      )
                  with Image.open(image_path) as image:
                      image.verify()
                  image_sha = sha256_file(image_path)
                  self.db.record_preflight_image_result(
                      run_id,
                      expected_version=int(row["state_version"]),
                      owner_token=lease.owner_token,
                      image_sha256=image_sha,
                  )
              elif not image_sha:
                  claimed = self.db.claim_paid_start(
                      scope_type="visual_preflight",
                      scope_id=run_id,
                      stage="watermark_smoke",
                      kind="image",
                      identity_key=f"preflight:{run_id}:image",
                      expected_state="reserved",
                      expected_version=int(row["state_version"]),
                      owner_token=lease.owner_token,
                  )
                  if not claimed:
                      return run_id
                  generated = self.client.generate_image(
                      PREFLIGHT_IMAGE_PROMPT,
                      image_path,
                      reference_paths=[],
                  )
                  current = self.db.preflight_run(run_id)
                  image_sha = generated.sha256
                  self.db.record_preflight_image_result(
                      run_id,
                      expected_version=int(current["state_version"]),
                      owner_token=lease.owner_token,
                      image_sha256=image_sha,
                  )

              current = self.db.preflight_run(run_id)
              operation_id = current["video_operation_id"]
              if not operation_id:
                  if current["video_start_state"] == "starting":
                      if self.db.lease_is_active_for_owner(
                          "visual_preflight",
                          run_id,
                          str(current["owner_token"]),
                      ):
                          return run_id
                      self.db.mark_preflight_start_uncertain(
                          run_id,
                          expected_version=int(current["state_version"]),
                          error_code="VISUAL_VIDEO_START_UNCERTAIN",
                      )
                      raise PaidStartUncertainError(
                          f"preflight run {run_id} Veo start 結果不確定；不得自動重試"
                      )
                  claimed = self.db.claim_paid_start(
                      scope_type="visual_preflight",
                      scope_id=run_id,
                      stage="watermark_smoke",
                      kind="video",
                      identity_key=f"preflight:{run_id}:video",
                      expected_state="reserved",
                      expected_version=int(current["state_version"]),
                      owner_token=lease.owner_token,
                  )
                  if not claimed:
                      return run_id
                  operation_id = self.client.start_video(
                      PREFLIGHT_VIDEO_PROMPT, image_path
                  )
                  claimed_row = self.db.preflight_run(run_id)
                  self.db.record_preflight_video_operation(
                      run_id,
                      expected_version=int(claimed_row["state_version"]),
                      owner_token=lease.owner_token,
                      operation_id=operation_id,
                  )

              while True:
                  poll = self.client.poll_video(operation_id, video_path)
                  if not poll.done:
                      self.sleep(float(self.visual["poll_interval_seconds"]))
                      continue
                  if poll.error or poll.path is None or poll.sha256 is None:
                      raise VisualPreflightError(
                          poll.error or "Veo 沒有產生可下載影片"
                      )
                  latest = self.db.preflight_run(run_id)
                  self.db.finish_preflight_generation(
                      run_id,
                      str(image_sha),
                      poll.sha256,
                      expected_version=int(latest["state_version"]),
                      owner_token=lease.owner_token,
                  )
                  return run_id
          except VisualPollTransientError as exc:
              safe_error = sanitize_exception(
                  exc,
                  code="VISUAL_POLL_TRANSIENT",
                  next_command=f"lyria-auto visual-preflight --resume {run_id}",
              )
              self.db.record_preflight_error(run_id, safe_error)
              raise VisualPreflightError(safe_error) from exc
          except PaidStartUncertainError as exc:
              safe_error = sanitize_exception(exc)
              self.db.mark_preflight_start_uncertain(
                  run_id,
                  expected_version=int(
                      self.db.preflight_run(run_id)["state_version"]
                  ),
                  error=safe_error,
              )
              raise VisualPreflightError(safe_error) from exc
          except Exception as exc:
              if isinstance(exc, VisualPreflightError):
                  raise
              safe_error = sanitize_exception(exc)
              self.db.record_preflight_error(run_id, safe_error)
              raise VisualPreflightError(safe_error) from exc

      def approve(self, run_id: int, *, image_ok: bool, video_ok: bool) -> None:
          row = self.db.preflight_run(run_id)
          if row is None:
              raise VisualPreflightError(f"找不到 preflight run {run_id}")
          image_path = Path(row["raw_image_path"])
          video_path = Path(row["raw_video_path"])
          try:
              image_snapshot = snapshot_verified_media(
                  image_path,
                  expected_sha256=row["image_sha256"],
                  kind="image",
                  snapshot_root=self.workspace / "snapshots",
              )
              video_snapshot = snapshot_verified_media(
                  video_path,
                  expected_sha256=row["video_sha256"],
                  kind="video",
                  snapshot_root=self.workspace / "snapshots",
              )
          except Exception:
              changed = self.db.transition_preflight(
                  run_id,
                  expected_status="awaiting_review",
                  expected_version=int(row["state_version"]),
                  status="failed_tampered",
                  error_code="VISUAL_PREFLIGHT_TAMPERED",
              )
              if not changed:
                  raise VisualPreflightError("preflight 狀態已被其他 reviewer 更新")
              raise VisualPreflightError(
                  "raw preflight 檔案已改變或無法解碼；此 run 不可核准，請建立新 run"
              )
          valid_days = int(self.visual["preflight"]["valid_days"])
          self.db.review_preflight(
            run_id,
              image_ok=image_ok,
              video_ok=video_ok,
              valid_days=valid_days,
              expected_status="awaiting_review",
              expected_version=int(row["state_version"]),
              image_snapshot=image_snapshot,
              video_snapshot=video_snapshot,
          )

    def require_valid_preflight(self) -> None:
        if self.db.valid_preflight(self.source) is None:
            raise VisualPreflightError(
                "目前 API 模式／模型／SDK／憑證沒有有效的浮水印 preflight；"
                "先執行 lyria-auto visual-preflight --watermark-smoke-test "
                "--allow-image-outputs 1 --allow-video-outputs 1"
            )
```

`snapshot_verified_media()` 必須以固定 input file descriptor 讀取、完成
hash／decode／probe，再把相同 bytes 寫入 content-addressed temp file 並 atomic
rename；回傳 snapshot path、SHA-256、size 與 probe。`review_preflight()` 在同一
transaction 重新驗證 status/version 並保存 snapshot identity。後續不得再使用
`raw_image_path`／`raw_video_path` 作為已核准 consumer input。

- [ ] **Step 4: Remove the test-only `_create_run` dependency**

把 `test_resume_polls_existing_operation_without_second_start` 的 setup 改為呼叫公開的 DB method，並用與 service 相同來源：

```python
        run_dir = project_config.root / "workspace" / "manual_preflight"
        run_dir.mkdir(parents=True)
        run_id = db.create_preflight_run(
            preflight.source,
            str(run_dir / "raw.png"),
            str(run_dir / "raw.mp4"),
            1.061,
            {},
        )
```

這能讓 `_create_run()` 保持 private，而測試仍只驗證公開 resume contract。

- [ ] **Step 5: Run service tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_preflight.py -v
```

Expected: `3 passed`; no network access and no real API key required.

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/visual_preflight.py tests/test_visual_preflight.py
git commit -m "feat: add resumable visual watermark preflight"
```

---

### Task 5: CLI approval flow and doctor gate

**Files:**
- Modify: `src/lyria_auto/cli.py`
- Modify: `src/lyria_auto/doctor.py`
- Modify: `tests/test_visual_preflight.py`
- Create: `tests/test_doctor_visual.py`

- [ ] **Step 1: Write failing CLI tests**

在 `tests/test_visual_preflight.py` 加入：

```python
from lyria_auto.cli import main


def test_cli_rejects_missing_paid_allowance(project_config, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--root",
                str(project_config.root),
                "visual-preflight",
                "--watermark-smoke-test",
            ]
        )

    assert exc.value.code == 1
    assert "精確授權" in capsys.readouterr().out


def test_cli_approval_asks_about_image_and_video(
    project_config, monkeypatch, capsys
):
    db = StateDB(project_config.root / "workspace" / "state.sqlite3")
    fake = FakeVisualSDK()
    preflight = VisualPreflightService(
        project_config,
        db,
        sdk_client=fake,
        sleep=lambda _: None,
        video_validator=lambda _: None,
    )
    run_id = preflight.start(allow_image_outputs=1, allow_video_outputs=1)
    db.close()
    answers = iter(["yes", "yes"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    main(
        [
            "--root",
            str(project_config.root),
            "visual-preflight",
            "--approve",
            str(run_id),
        ]
    )

    assert "passed_no_visible_mark" in capsys.readouterr().out
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_preflight.py -v
```

Expected: argparse rejects unknown command `visual-preflight`.

- [ ] **Step 3: Add parser arguments and command handler**

在 `src/lyria_auto/cli.py::_parser()` 的 `resume` parser 後加入：

```python
    visual_preflight = sub.add_parser(
        "visual-preflight", help="驗證付費 Gemini API 原始輸出沒有可見產品標誌"
    )
    mode = visual_preflight.add_mutually_exclusive_group(required=True)
    mode.add_argument("--watermark-smoke-test", action="store_true")
    mode.add_argument("--resume", type=int, metavar="RUN_ID")
    mode.add_argument("--approve", type=int, metavar="RUN_ID")
    visual_preflight.add_argument("--allow-image-outputs", type=int, default=0)
    visual_preflight.add_argument("--allow-video-outputs", type=int, default=0)
```

在 `main()` 的 `scheduler` 分支之前加入：

```python
    if args.command == "visual-preflight":
        from .db import StateDB
        from .visual_preflight import VisualPreflightService

        project = config.section("project")
        db = StateDB(config.root / project.get("database", "workspace/state.sqlite3"))
        try:
            service = VisualPreflightService(config, db)
            if args.watermark_smoke_test:
                run_id = service.start(
                    allow_image_outputs=args.allow_image_outputs,
                    allow_video_outputs=args.allow_video_outputs,
                )
                row = db.preflight_run(run_id)
                print(f"preflight run {run_id} 等待人工檢查")
                print(f"圖片原始檔：{row['raw_image_path']}")
                print(f"影片原始檔：{row['raw_video_path']}")
                return
            if args.resume is not None:
                run_id = service.resume(args.resume)
                row = db.preflight_run(run_id)
                print(f"preflight run {run_id}: {row['status']}")
                return
            row = db.preflight_run(args.approve)
            if row is None:
                raise LyriaAutoError(f"找不到 preflight run {args.approve}")
            print(f"請直接查看未修改原始圖片：{row['raw_image_path']}")
            image_ok = input("圖片沒有肉眼可見產品標誌？輸入 yes 才通過：").strip().lower() == "yes"
            print(f"請直接查看未修改原始影片：{row['raw_video_path']}")
            video_ok = input("影片沒有肉眼可見產品標誌？輸入 yes 才通過：").strip().lower() == "yes"
            service.approve(args.approve, image_ok=image_ok, video_ok=video_ok)
            reviewed = db.preflight_run(args.approve)
            print(f"preflight run {args.approve}: {reviewed['status']}")
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

- [ ] **Step 4: Add a doctor row without making a paid call**

建立 `tests/test_doctor_visual.py`：

```python
from __future__ import annotations

from datetime import date

from lyria_auto.doctor import pricing_snapshot_status, run_doctor


def test_doctor_reports_preflight_without_calling_api(project_config, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "fake")

    rows = run_doctor(project_config)
    row = next(item for item in rows if item[0] == "visual preflight")

    assert row[1] is False
    assert "尚未通過" in row[2]


def test_pricing_snapshot_warns_after_thirty_days():
    ok, detail = pricing_snapshot_status(
        "2026-07-29", today=date(2026, 8, 29)
    )

    assert ok is False
    assert "31 天" in detail
```

在 `src/lyria_auto/doctor.py` imports 加入：

```python
from datetime import date
```

在 `run_doctor()` 前加入：

```python
def pricing_snapshot_status(
    value: str, *, today: date | None = None
) -> tuple[bool, str]:
    snapshot = date.fromisoformat(value)
    age = ((today or date.today()) - snapshot).days
    return (
        age <= 30,
        f"價格快照 {value}，距今 {age} 天"
        + ("；請先核對官方價格" if age > 30 else ""),
    )
```

在 `src/lyria_auto/doctor.py` 的 `return rows` 前加入：

```python
    visual = config.section("visual")
    if visual.get("enabled", False):
        from .db import StateDB
        from .visual_preflight import VisualPreflightService

        db = StateDB(config.root / project.get("database", "workspace/state.sqlite3"))
        try:
            try:
                service = VisualPreflightService(config, db)
                valid = db.valid_preflight(service.source)
                detail = (
                    f"run {valid['id']}，有效至 {valid['expires_at']}"
                    if valid
                    else "尚未通過或已過期"
                )
                rows.append(("visual preflight", valid is not None, detail))
              except Exception as exc:
                  safe_error = sanitize_exception(exc)
                  rows.append(
                      ("visual preflight", False, f"{safe_error.code}: {safe_error.problem}")
                  )
        finally:
            db.close()
        pricing_ok, pricing_detail = pricing_snapshot_status(
            str(visual["pricing_snapshot_date"])
        )
        rows.append(("visual pricing snapshot", pricing_ok, pricing_detail))
```

此檢查只建立 SDK client 和讀 DB，不呼叫圖片或影片生成 API。

- [ ] **Step 5: Run CLI and doctor tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_preflight.py tests/test_doctor_visual.py tests/test_cli_resume.py -v
```

Expected: all tests pass; pytest output不包含任何真實 HTTP request。

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/cli.py src/lyria_auto/doctor.py tests/test_visual_preflight.py tests/test_doctor_visual.py
git commit -m "feat: expose visual preflight approval CLI"
```

---

### Task 6: Foundation regression check and operator documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/00_START_HERE_zh-TW.md`
- Modify: `docs/02_USAGE_zh-TW.md`
- Modify: `docs/05_TROUBLESHOOTING_zh-TW.md`
- Modify: `docs/06_SECURITY_zh-TW.md`

- [ ] **Step 1: Add the exact operator flow**

只在 `docs/00_START_HERE_zh-TW.md` 保存完整 canonical 操作順序；README 與其他文件連回該段落。操作指令以 Mac bash 為主，另提供不含反斜線續行風險的單行版本：

```bash
lyria-auto visual-preflight --watermark-smoke-test \
  --allow-image-outputs 1 \
  --allow-video-outputs 1
lyria-auto visual-preflight --approve RUN_ID
lyria-auto doctor
```

文字必須明確說明：

- 預估成本約 US$1.061，與單支影片預算分開。
- 只查看命令顯示的原始檔，不查看截圖或轉碼檔。
- 兩次問題都必須輸入完整 `yes`。
- 出現可見標誌時回答非 `yes`；不可裁切、遮蓋、修補或破壞 SynthID。
- 失敗 run 不能翻轉；修正 API 來源後建立新 run。
- API 模式、模型、SDK、credential fingerprint 或計費專案改變會使核准失效。
- 30 天後需要重做，避免 preview model alias 靜默更新。

- [ ] **Step 2: Add troubleshooting decision table**

在 `docs/05_TROUBLESHOOTING_zh-TW.md` 加入：

```markdown
| 現象 | 處理 |
|---|---|
| 原始 API 圖片右下角有 Gemini／Google 標誌 | 將 preflight 判為失敗；確認不是 Gemini App、Flow、Google Photos 或 AI Studio 預覽下載 |
| 原始 Veo 影片有 made with Veo | 將 preflight 判為失敗；保存 run ID、model ID、API 模式、SDK 與原始檔，向 Google 查證 |
| operation 中斷 | 執行 `lyria-auto visual-preflight --resume RUN_ID`，不得重新建立 run |
| doctor 顯示過期 | 重新執行一張圖片＋一段影片的付費 smoke test |
| 只想移除 SynthID | 不支援；SynthID 是來源揭露機制，最終 YouTube 仍維持 `containsSyntheticMedia: true` |
```

- [ ] **Step 3: Run all automated tests**

Run:

```bash
./.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run lint**

Run:

```bash
./.venv/bin/python -m ruff check src tests
```

Expected: `All checks passed!`

- [ ] **Step 5: Verify no secret or generated preflight asset is tracked**

Run:

```bash
git status --short
git check-ignore workspace/visual_preflight/.fingerprint.key
git grep -n "private-api-key"
```

Expected:

- `workspace/visual_preflight/.fingerprint.key` is ignored.
- `git grep` only finds the intentional test literal in `tests/test_visual_config.py`.
- No `.png`, `.mp4`, API key or fingerprint secret is staged.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/00_START_HERE_zh-TW.md docs/02_USAGE_zh-TW.md docs/05_TROUBLESHOOTING_zh-TW.md docs/06_SECURITY_zh-TW.md
git commit -m "docs: document visual watermark preflight"
```

---

### Task 7: At-most-once、可恢復 poll 與 raw integrity regression

**Files:**
- Modify: `tests/test_gemini_visual.py`
- Modify: `tests/test_visual_preflight.py`
- Modify: `tests/test_visual_db.py`

- [ ] **Step 1: Add crash-window and integrity tests**

至少加入以下案例：

```python
def test_transient_poll_error_keeps_same_operation_and_resume_does_not_start_again():
    run_id = started_run_with_operation("operations/video-7")
    client.poll_side_effects = [VisualPollTransientError("timeout"), completed_poll()]
    with pytest.raises(VisualPreflightError):
        service.resume(run_id)
    service.resume(run_id)
    assert client.video_starts == 0
    assert client.operations_seen == ["operations/video-7", "operations/video-7"]


def test_crash_after_start_intent_without_operation_becomes_uncertain():
    run_id = run_with(video_start_state="starting", video_operation_id=None)
    with pytest.raises(VisualPreflightError, match="不得自動重試"):
        service.resume(run_id)
    assert db.preflight_run(run_id)["status"] == "start_uncertain"
    assert client.video_starts == 0


def test_approve_rejects_replaced_raw_asset():
    run_id = completed_preflight()
    Path(db.preflight_run(run_id)["raw_image_path"]).write_bytes(b"replacement")
    with pytest.raises(VisualPreflightError, match="不可核准"):
        service.approve(run_id, image_ok=True, video_ok=True)
    assert db.preflight_run(run_id)["status"] == "failed_tampered"
```

上列同 process tests 只驗證狀態分支，不能作為 crash durability 證據。
另建立 subprocess fault harness：

```text
parent 建立 DB／run
  └─ child 重新建立 StateDB、service、client
       └─ 在指定 failpoint 執行 os._exit(91)
parent 等待 child
  └─ 以全新 connection／repository／service 重開 DB
       ├─ PRAGMA integrity_check
       ├─ 驗證 durable lease、authorization、state、operation ID
       └─ 執行 resume 並核對 paid call ledger
```

至少覆蓋 lease commit、authorization consumption、start CAS、provider call
前、provider response 後、operation ID commit 前後。另以兩個獨立 process
加 barrier 同時 claim 同一 identity，只有一方可取得 rowcount=1，provider
call ledger 必須恰好一筆。

- [ ] **Step 2: Run the complete foundation gate**

```bash
./.venv/bin/python -m pytest tests/test_visual_config.py tests/test_config_migration.py tests/test_visual_db.py tests/test_gemini_visual.py tests/test_visual_preflight.py tests/test_doctor_visual.py -q
./.venv/bin/python -m ruff check src tests
```

Expected: all tests pass；subprocess ledger 證明 hard crash／DB reopen 與兩個
concurrent workers 下，每個 identity 仍最多一次 paid start。只在同一 process
重用 fake client 的 call counter 不足以通過此 gate。

- [ ] **Step 3: Commit**

```bash
git add tests/test_gemini_visual.py tests/test_visual_preflight.py tests/test_visual_db.py
git commit -m "test: enforce visual paid-start and preflight invariants"
```

---

## Plan 1 completion gate

在進入正式場景生成計畫前，必須同時成立：

1. fake-client 測試證明沒有付費授權時零 API 呼叫。
2. operation ID 在 poll 前存在 SQLite，並由新 process／新 connection 可見。
3. production start 使用 SQLite lease、asset/run CAS 與同 transaction allowance
   consumption；兩個獨立 process 競爭時仍是 at-most-once。
4. hard crash 後以全新 process／DB reopen 恢復；`start_uncertain` 不會被普通 resume 重送。
5. 暫時 poll／download 錯誤保留 operation ID，resume 不會建立第二段 Veo。
6. approve 前重新驗證 raw SHA、圖片解碼及影片 probe，建立 immutable snapshot；
   `failed_tampered` 不可翻轉，後續 consumer 不再讀 raw path。
7. exact effective source identity 和 30 天效期會擋下無效核准。
8. 單一 numbered migration registry 可由舊 DB 完整升級、rollback、重跑並保留
   custom values；`jobs`／`videos` 的合法舊狀態保留，未知狀態 fail closed 且不改寫，
   非法 INSERT／UPDATE 由 SQLite trigger 拒絕。
9. `doctor` 在零 API 呼叫下驗證 `google-genai>=2.13.0,<3` 所需 capability。
10. `pytest -q` 與 `ruff check src tests` 都成功，且既有 Ruff baseline 已清零。
11. 真實付費 smoke test 仍未自動執行；只能由使用者在執行階段明確授權。
