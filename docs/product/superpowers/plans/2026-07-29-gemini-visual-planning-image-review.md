# Gemini 視覺規劃與圖片審核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 從既有 Lyria 專輯計畫建立同一間咖啡館的 world bible 與最多四個固定鏡位，受控生成一張 world anchor、每景三張圖片及專用縮圖背景，並在 localhost 完成不可跳過的圖片人工審核。

**Architecture:** `VisualPlanner` 是純函式式、seeded 且零 API 成本；`VisualRepository` 封裝所有視覺 SQLite 存取與原子核准 invariant；`ImageGenerationWorkflow` 以 Plan 1 的 at-most-once adapter 處理資產 intent、provider 呼叫與 QC；`ReviewService`／`ReviewServer` 只讀寫既有 DB，不 import 或建構付費 provider。正式 pipeline 整合留給 Plan 4。

**Tech Stack:** Python 3.11+、dataclasses、SQLite、Pillow、stdlib `http.server`、pytest。

---

## DX review 核准契約與前置依賴

開始本計畫前，Plan 1 completion gate 必須全數通過；尤其是 versioned migration、`start_uncertain`、SDK capability 與有效 preflight。Plan 2 不得重新定義付費重試策略。

- `lyria-auto visual-demo` 使用 `workspace/demo/` 與 `workspace/demo/state.sqlite3`，不得建立 Gemini／Lyria／YouTube client，資產永久帶有 `SAMPLE — NOT FOR UPLOAD` 視覺標記。
- 圖片生成前先 commit asset intent 與 `starting`；若 process 留在 `starting` 且沒有
  本機可驗證輸出，普通 resume 將其轉為 `start_uncertain`，不得再送一次。
- repository 提供 `has_inflight_image_assets(job_id)`，狀態集合固定為
  `{"reserved", "starting"}`；Plan 4 看到任一 in-flight 時保持 `generating_images`。
- `reject_image_scene()` 必須在同一 transaction 清除 `selected_image_asset_id`、`approved_at`，並把 scene 設為 `needs_regeneration`。
- `finish_image_review()` 不只檢查非空 ID；它必須 join selected asset，驗證同 job／scene、`scene_image`、`ready`、檔案存在、scene 狀態為 `image_selected`。
- review 頁必須支援全尺寸／縮放、selected 高亮、禁用 `qc_failed`、品質 checklist、完成數 `x/y`、下一條命令及下一階段成本；瀏覽器只是 UI，所有 invariant 仍由 repository 強制。
- 每張 ready 圖片保存 perceptual hash；Plan 4 會與近期已發布 job 比較，作為頻道層級原創性 gate 的輸入。
- terminal 逐資產顯示 `圖片 x/y · scene · variant · elapsed`，Ctrl+C 後印出同一 job 的 resume 命令。

---

## Scope and file map

| File | Responsibility |
|---|---|
| `src/lyria_auto/models.py` | 補齊 PromptPlan 的 album-level 欄位 |
| `src/lyria_auto/visual_models.py` | WorldBible、ScenePlan、VisualPlan、QC 型別 |
| `src/lyria_auto/visual_planner.py` | deterministic world／camera／prompt 規劃 |
| `src/lyria_auto/visual_repository.py` | scene／asset repository 與核准 transaction |
| `src/lyria_auto/media/visual_quality.py` | 圖片解碼、尺寸及比例 QC |
| `src/lyria_auto/visual_workflow.py` | world anchor、scene candidates、thumbnail background |
| `src/lyria_auto/review_server.py` | localhost HTML、token、圖片選擇與完成關卡 |
| `src/lyria_auto/media/thumbnail.py` | 以核准世界背景產生本機品牌文字縮圖 |
| `tests/test_visual_planner.py` | seed、同世界、鏡位與提示詞測試 |
| `tests/test_visual_repository.py` | idempotency、資產歸屬、核准完整性 |
| `tests/test_visual_quality.py` | 圖片 QC |
| `tests/test_visual_image_workflow.py` | 十四張上限、resume、不自動補生 |
| `tests/test_review_server.py` | HTTP token、選擇驗證、零 provider 呼叫 |
| `tests/test_visual_demo.py` | 隔離 DB／workspace、sample watermark、零 provider object |
| `tests/test_image_review_state.py` | select→reject→finish、stale ID 及跨 scene transaction |
| `tests/test_visual_progress.py` | x/y、elapsed、Ctrl+C 與 next command |

全計畫限制：

- 不在 review request handler 內呼叫任何生成模型。
- world anchor 和 thumbnail background 都算入十四張圖片硬上限。
- 每個場景候選固定三張；普通 resume 不可增加第四張。
- scene candidate 必須以同一張 world anchor 作 reference。
- 所有提示詞都包含 `no people`、`no text`、`no logo`、物理合理光線與照片語彙。
- 被拒絕的 scene 會原子清除舊選取並標記 `needs_regeneration`，不自動花錢。
- Review server 只綁 `127.0.0.1`，所有 POST 都驗證隨機 token。
- review handler 不得把 UI 狀態當作授權；所有完成條件在 repository transaction 內重新驗證。
- Plan 2 不提供額外付費生成 CLI；明確 `regenerate` 命令留給 Plan 4。

---

### Task 0: Plan 2 migration、隔離 visual demo 與 perceptual hash

**Files:**
- Modify: `src/lyria_auto/db.py`
- Modify: `src/lyria_auto/cli.py`
- Modify: `src/lyria_auto/media/visual_quality.py`
- Create: `src/lyria_auto/visual_demo.py`
- Create: `tests/test_visual_demo.py`
- Modify: `tests/test_visual_db.py`

- [ ] **Step 1: Write failing migration and demo-isolation tests**

```python
def test_plan2_migration_adds_perceptual_hash_without_rebuilding_existing_db(old_db):
    migrate(old_db)
    assert "perceptual_hash" in table_columns(old_db, "visual_assets")
    assert old_db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_visual_demo_uses_isolated_state_and_never_constructs_provider(tmp_path, monkeypatch):
    forbid = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("production object forbidden")
    )
    monkeypatch.setattr("lyria_auto.providers.gemini_visual.GeminiVisualClient", forbid)
    monkeypatch.setattr("lyria_auto.providers.lyria.LyriaClient", forbid)
    monkeypatch.setattr("lyria_auto.providers.youtube.YouTubeClient", forbid)
    monkeypatch.setattr("lyria_auto.pipeline.Pipeline", forbid)
    monkeypatch.setattr("lyria_auto.config.load_config", forbid)
    result = run_visual_demo(tmp_path)
    assert result.db_path == tmp_path / "workspace/demo/state.sqlite3"
    assert result.review_url.startswith("http://127.0.0.1:")
    assert all(has_demo_stamp(path) for path in result.assets if path.suffix == ".png")
```

測試 fixture 另以 Pillow 讀圖確認右下角實際有 `SAMPLE — NOT FOR UPLOAD` raster 標記；不得只靠檔名或 HTML 文字。

- [ ] **Step 2: Implement versioned migration and local demo**

Plan 2 migration 的固定 ID 為 `0002_visual_image_phash`：

```sql
ALTER TABLE visual_assets ADD COLUMN perceptual_hash TEXT;
```

runner 先由共用 `schema_migrations` 驗證 ID／checksum，再在單一 transaction
執行；`PRAGMA table_info` 只確認 precondition／postcondition，不可取代 registry。
`visual-demo` 只用 Pillow／FFmpeg 建立四張 sample 圖及四段短 sample loop，啟動與正式 review 相同的 localhost UI，但不讀正式 DB、設定、prompt、API key 或 YouTube OAuth。
測試另以 filesystem spy 證明正式 DB、settings、prompt 與 OAuth paths 都未被讀取。

CLI：

```bash
lyria-auto visual-demo
lyria-auto visual-demo --open
```

預期在一般 Mac 上 120 秒內印出 review URL；測試環境目標小於 5 秒。
`tests/test_visual_demo.py` 使用 monotonic fake clock 驗證 URL-ready event 與 progress，
另由 Mac smoke test 以真實 FFmpeg 驗收 TTHW < 120 秒；event 不含 prompt、正式
workspace 路徑或任何 secret。

- [ ] **Step 3: Add a dependency-free image hash**

在 `media.visual_quality` 加入固定 9×8 grayscale dHash，輸出 64-bit hex；同一圖片的重新編碼應維持低 Hamming distance，明顯不同 fixture 應超過測試門檻。

- [ ] **Step 4: Run and commit**

```bash
./.venv/bin/python -m pytest tests/test_visual_demo.py tests/test_visual_db.py tests/test_visual_quality.py -v
git add src/lyria_auto/db.py src/lyria_auto/cli.py src/lyria_auto/media/visual_quality.py src/lyria_auto/visual_demo.py tests/test_visual_demo.py tests/test_visual_db.py tests/test_visual_quality.py
git commit -m "feat: add isolated visual demo and image fingerprints"
```

---

### Task 1: Preserve album-level prompt facts and build deterministic visual plans

**Files:**
- Modify: `src/lyria_auto/models.py`
- Modify: `src/lyria_auto/prompt_engine.py`
- Modify: `src/lyria_auto/visual_models.py`
- Create: `src/lyria_auto/visual_planner.py`
- Create: `tests/test_visual_planner.py`

- [ ] **Step 1: Write failing planner tests**

建立 `tests/test_visual_planner.py`：

```python
from __future__ import annotations

from lyria_auto.models import PromptPlan
from lyria_auto.visual_planner import VisualPlanner


def representative() -> PromptPlan:
    return PromptPlan(
        prompt="album prompt",
        signature="sig",
        scene="a rainy bookstore café beside a large window",
        mood="warm, peaceful, focused",
        instrumentation="upright piano, bass, brushes",
        scene_label="雨天窗邊書店",
        mood_label="溫暖・平靜・專注",
        genre="coffeehouse lofi jazz",
        texture="soft tape hiss with wooden-room resonance",
        production="warm analog mix with natural acoustic space",
        atmosphere="soft rain tapping on the windows",
    )


def test_visual_plan_is_seeded_and_four_views_share_world():
    first = VisualPlanner(seed=42).compose(representative(), target_minutes=300)
    second = VisualPlanner(seed=42).compose(representative(), target_minutes=300)

    assert first == second
    assert [scene.label for scene in first.scenes] == ["A", "B", "C", "D"]
    assert len({scene.world_id for scene in first.scenes}) == 1
    assert len({scene.camera_role for scene in first.scenes}) == 4


def test_short_video_only_plans_needed_unique_scenes():
    plan = VisualPlanner(seed=42).compose(representative(), target_minutes=45)

    assert [scene.label for scene in plan.scenes] == ["A", "B"]


def test_prompts_lock_time_weather_camera_and_exclude_people_and_text():
    plan = VisualPlanner(seed=42).compose(representative(), target_minutes=120)

    for scene in plan.scenes:
        prompt = (scene.image_prompt + " " + scene.motion_prompt).lower()
        assert plan.world.time_of_day.lower() in prompt
        assert plan.world.weather.lower() in prompt
        assert "locked-off camera" in prompt
        assert "no people" in prompt
        assert "no text" in prompt
        assert "no logo" in prompt
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_planner.py -v
```

Expected: `VisualPlanner` import fails.

- [ ] **Step 3: Extend PromptPlan without breaking old plan.json**

在 `src/lyria_auto/models.py::PromptPlan` 末尾加入有預設值的欄位：

```python
    genre: str = ""
    texture: str = ""
    production: str = ""
    atmosphere: str = ""
```

在 `src/lyria_auto/prompt_engine.py::compose_album()` 建立 `PromptPlan` 時改成 keyword arguments：

```python
                plans.append(
                    PromptPlan(
                        prompt=prompt,
                        signature=signature,
                        scene=scene,
                        mood=mood,
                        instrumentation=instrumentation,
                        scene_label=scene_label,
                        mood_label=mood_label,
                        genre=genre,
                        texture=texture,
                        production=production,
                        atmosphere=atmosphere,
                    )
                )
```

舊 `plan.json` 沒有新欄位時仍可由 dataclass defaults 讀取。

- [ ] **Step 4: Add visual planning dataclasses**

在 `src/lyria_auto/visual_models.py` 加入：

```python
from typing import Any


@dataclass(frozen=True)
class WorldBible:
    world_id: str
    scene: str
    scene_label: str
    genre: str
    mood: str
    texture: str
    production: str
    atmosphere: str
    time_of_day: str
    weather: str
    palette: str
    materials: str


@dataclass(frozen=True)
class ScenePlan:
    position: int
    label: str
    world_id: str
    camera_role: str
    image_prompt: str
    motion_prompt: str


@dataclass(frozen=True)
class VisualPlan:
    version: int
    world: WorldBible
    world_anchor_prompt: str
    scenes: tuple[ScenePlan, ...]
    thumbnail_prompt: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "world": asdict(self.world),
            "world_anchor_prompt": self.world_anchor_prompt,
            "scenes": [asdict(scene) for scene in self.scenes],
            "thumbnail_prompt": self.thumbnail_prompt,
        }


@dataclass(frozen=True)
class ImageQC:
    passed: bool
    width: int
    height: int
    reason: str
    reason_code: str = "VISUAL_IMAGE_QC_FAILED"
```

- [ ] **Step 5: Implement the pure VisualPlanner**

建立 `src/lyria_auto/visual_planner.py`：

```python
from __future__ import annotations

import hashlib
import math
import random

from .models import PromptPlan
from .visual_models import ScenePlan, VisualPlan, WorldBible


CAMERAS = (
    ("A", "window reading nook", "eye-level 35mm view toward the rain-streaked main window"),
    ("B", "coffee bar", "eye-level 40mm three-quarter view across the wooden coffee counter"),
    ("C", "bookshelf corner", "eye-level 50mm view past a foreground table toward the bookshelves"),
    ("D", "fireplace lounge", "eye-level 35mm wide view from the quiet rear lounge"),
)
TIMES = ("late afternoon", "early evening")
PALETTES = (
    "warm amber practical lights, walnut brown, muted forest green",
    "soft honey light, dark oak, restrained cream and moss green",
)
MATERIALS = (
    "aged walnut, lime plaster, linen, brushed brass and clear glass",
    "dark oak, matte plaster, wool upholstery, antique brass and glass",
)


class VisualPlanner:
    def __init__(self, seed: int):
        self.seed = seed

    def compose(self, representative: PromptPlan, target_minutes: int) -> VisualPlan:
        rng = random.Random(self.seed)
        world_id = hashlib.sha256(
            f"{self.seed}|{representative.scene}|{representative.genre}".encode("utf-8")
        ).hexdigest()[:16]
        source_text = (
            representative.scene + " " + representative.atmosphere
        ).lower()
        if "snow" in source_text or "winter" in source_text:
            weather = "steady gentle snow outside"
        elif "mist" in source_text or "mountain" in source_text:
            weather = "soft stationary mountain mist outside"
        elif "rain" in source_text:
            weather = "steady gentle rain outside"
        else:
            weather = "calm overcast weather outside"
        if "late-night" in source_text or "night" in source_text:
            time_of_day = "late evening"
        elif "sunrise" in source_text or "morning" in source_text:
            time_of_day = "early morning"
        else:
            time_of_day = rng.choice(TIMES)
        world = WorldBible(
            world_id=world_id,
            scene=representative.scene,
            scene_label=representative.scene_label or representative.scene,
            genre=representative.genre,
            mood=representative.mood,
            texture=representative.texture,
            production=representative.production,
            atmosphere=representative.atmosphere,
            time_of_day=time_of_day,
            weather=weather,
            palette=rng.choice(PALETTES),
            materials=rng.choice(MATERIALS),
        )
        fixed = (
            f"Photorealistic architectural interior photograph of {world.scene}. "
            "Maintain the exact same café architecture and furnishings; "
            f"{world.time_of_day}; "
            f"{world.weather}; palette: {world.palette}; materials: {world.materials}. "
            "Physically plausible global illumination, realistic lens behavior, natural "
            "surface imperfections, documentary photography, no people, no human figures, "
            "no text, no letters, no in-scene signage, no logo, no border."
        )
        count = min(4, max(1, math.ceil(target_minutes / 30)))
        scenes = []
        for position, (label, role, camera) in enumerate(CAMERAS[:count]):
            scenes.append(
                ScenePlan(
                    position=position,
                    label=label,
                    world_id=world.world_id,
                    camera_role=role,
                    image_prompt=f"{fixed} Camera: {camera}. Locked-off camera.",
                    motion_prompt=(
                        f"{fixed} Camera: {camera}. Locked-off camera, single continuous "
                        "shot, no pan, no tilt, no zoom, no dolly, no cuts. Only subtle "
                        "steam, rain trails, curtain drift, plant movement and natural "
                        "light fluctuation. Return to the exact starting composition."
                    ),
                )
            )
        return VisualPlan(
            version=1,
            world=world,
            world_anchor_prompt=(
                f"{fixed} Establishing wide photograph showing the full coherent floor "
                "plan and all recurring architectural landmarks. Locked-off camera."
            ),
            scenes=tuple(scenes),
            thumbnail_prompt=(
                f"{fixed} Dedicated YouTube thumbnail composition matching camera A, "
                "clean negative space on the left for locally added title text."
            ),
        )
```

- [ ] **Step 6: Run planner and existing prompt tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_planner.py tests/test_prompt_engine.py tests/test_from_job.py -v
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/lyria_auto/models.py src/lyria_auto/prompt_engine.py src/lyria_auto/visual_models.py src/lyria_auto/visual_planner.py tests/test_visual_planner.py
git commit -m "feat: plan consistent photorealistic visual worlds"
```

---

### Task 2: Visual repository, idempotent assets and approval transactions

**Files:**
- Create: `src/lyria_auto/visual_repository.py`
- Create: `tests/test_visual_repository.py`

- [ ] **Step 1: Write failing repository tests**

建立 `tests/test_visual_repository.py`：

```python
from __future__ import annotations

import pytest

from lyria_auto.db import StateDB
from lyria_auto.errors import VisualApprovalError
from lyria_auto.visual_repository import VisualRepository


def setup_repo(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    job_id = db.create_job("main", False, {})
    return db, job_id, VisualRepository(db)


def test_scene_upsert_is_idempotent(tmp_path):
    db, job_id, repo = setup_repo(tmp_path)
    try:
        first = repo.upsert_scene(job_id, 0, "A", {"world_id": "w"}, "image", "motion")
        second = repo.upsert_scene(job_id, 0, "A", {"world_id": "w"}, "image", "motion")

        assert first == second
        assert len(repo.scenes(job_id)) == 1
    finally:
        db.close()


def test_asset_identity_is_idempotent(tmp_path):
    db, job_id, repo = setup_repo(tmp_path)
    try:
        scene_id = repo.upsert_scene(job_id, 0, "A", {}, "image", "motion")
        first = repo.reserve_asset(job_id, scene_id, "scene_image", 0, "gemini", "model", "p", 0.101, {})
        second = repo.reserve_asset(job_id, scene_id, "scene_image", 0, "gemini", "model", "p", 0.101, {})

        assert first == second
    finally:
        db.close()


def test_cannot_select_asset_from_another_scene(tmp_path):
    db, job_id, repo = setup_repo(tmp_path)
    try:
        a = repo.upsert_scene(job_id, 0, "A", {}, "a", "a")
        b = repo.upsert_scene(job_id, 1, "B", {}, "b", "b")
        asset = repo.reserve_asset(job_id, b, "scene_image", 0, "gemini", "model", "p", 0.101, {})
        mark_ready_in_fixture(repo, asset, "/tmp/b.png")

        with pytest.raises(VisualApprovalError, match="不屬於"):
            repo.select_image(
                a,
                asset,
                expected_version=int(repo.scene(a)["state_version"]),
            )
    finally:
        db.close()
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_repository.py -v
```

Expected: `VisualRepository` import fails.

- [ ] **Step 3: Implement repository methods**

建立 `src/lyria_auto/visual_repository.py`：

```python
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import StateDB
from .errors import VisualApprovalError
from .utils import utc_now_iso


class VisualRepository:
    def __init__(self, db: StateDB):
        self.db = db

    def upsert_scene(
        self,
        job_id: int,
        position: int,
        label: str,
        world: dict[str, Any],
        image_prompt: str,
        motion_prompt: str,
    ) -> int:
        now = utc_now_iso()
        self.db.conn.execute(
            """
            INSERT INTO visual_scenes(
              job_id,position,label,world_json,image_prompt,motion_prompt,
              status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(job_id,position) DO NOTHING
            """,
            (
                job_id, position, label, json.dumps(world, ensure_ascii=False),
                image_prompt, motion_prompt, "planned", now, now,
            ),
        )
        self.db.conn.commit()
        row = self.db.conn.execute(
            "SELECT * FROM visual_scenes WHERE job_id=? AND position=?",
            (job_id, position),
        ).fetchone()
        expected = {
            "label": label,
            "world_json": json.dumps(world, ensure_ascii=False),
            "image_prompt": image_prompt,
            "motion_prompt": motion_prompt,
        }
        if row is None or any(row[key] != value for key, value in expected.items()):
            raise VisualApprovalError(
                "相同 scene identity 已存在，但 label/world/prompts 不同"
            )
        return int(row["id"])

    def scenes(self, job_id: int) -> list[sqlite3.Row]:
        return self.db.conn.execute(
            "SELECT * FROM visual_scenes WHERE job_id=? ORDER BY position",
            (job_id,),
        ).fetchall()

    def scene(self, scene_id: int) -> sqlite3.Row | None:
        return self.db.conn.execute(
            "SELECT * FROM visual_scenes WHERE id=?", (scene_id,)
        ).fetchone()

    def reserve_asset(
        self,
        job_id: int,
        scene_id: int | None,
        asset_type: str,
        variant_index: int,
        provider: str,
        model: str,
        prompt: str,
        estimated_cost_usd: float,
        pricing_snapshot: dict[str, Any],
    ) -> int:
        now = utc_now_iso()
        self.db.conn.execute(
            """
            INSERT INTO visual_assets(
              job_id,scene_id,asset_type,variant_index,provider,model,prompt,
              estimated_cost_usd,pricing_snapshot_json,status,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT DO NOTHING
            """,
            (
                job_id, scene_id, asset_type, variant_index, provider, model,
                prompt, estimated_cost_usd,
                json.dumps(pricing_snapshot, ensure_ascii=False),
                "reserved", now, now,
            ),
        )
        self.db.conn.commit()
        row = self.db.conn.execute(
            """
            SELECT * FROM visual_assets
            WHERE job_id=? AND asset_type=? AND scene_id IS ? AND variant_index=?
            """,
            (job_id, asset_type, scene_id, variant_index),
        ).fetchone()
        if row is None:
            raise VisualGenerationError("asset reservation 沒有產生或找到 identity")
        expected_identity = {
            "provider": provider,
            "model": model,
            "prompt": prompt,
            "estimated_cost_usd": estimated_cost_usd,
            "pricing_snapshot_json": json.dumps(
                pricing_snapshot, ensure_ascii=False
            ),
        }
        if any(row[key] != value for key, value in expected_identity.items()):
            raise VisualGenerationError(
                "相同 asset identity 已存在，但 provider/model/prompt/pricing 不同"
            )
        return int(row["id"])

    def asset(self, asset_id: int) -> sqlite3.Row | None:
        return self.db.conn.execute(
            "SELECT * FROM visual_assets WHERE id=?", (asset_id,)
        ).fetchone()

    def assets(
        self, job_id: int, *, asset_type: str | None = None
    ) -> list[sqlite3.Row]:
        if asset_type is None:
            return self.db.conn.execute(
                "SELECT * FROM visual_assets WHERE job_id=? ORDER BY id",
                (job_id,),
            ).fetchall()
        return self.db.conn.execute(
            """
            SELECT * FROM visual_assets
            WHERE job_id=? AND asset_type=?
            ORDER BY scene_id,variant_index
            """,
            (job_id, asset_type),
        ).fetchall()

    def estimated_cost(self, job_id: int) -> float:
        row = self.db.conn.execute(
            """
            SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total
            FROM visual_assets WHERE job_id=?
            """,
            (job_id,),
        ).fetchone()
        return float(row["total"])

    # 不提供可任意改 status 的 update_asset(**values)。所有 state mutation
    # 必須使用下列具 expected status/version 的明確方法：
    # claim_paid_start(), record_operation(), mark_asset_ready(),
    # mark_start_uncertain(), mark_tampered(), transition_review_state().

      def mark_asset_ready(
          self,
          asset_id: int,
          *,
          expected_status: str,
          expected_version: int,
          owner_token: str,
          path: str,
          mime_type: str,
          sha256: str,
        width: int,
          height: int,
          perceptual_hash: str | None = None,
      ) -> bool:
          return self._cas_transition_asset(
              asset_id=asset_id,
              expected_status=expected_status,
              expected_version=expected_version,
              owner_token=owner_token,
              new_status="ready",
              values={
                  "path": path,
                  "mime_type": mime_type,
                  "sha256": sha256,
                  "width": width,
                  "height": height,
                  "perceptual_hash": perceptual_hash,
                  "error_code": None,
              },
          )

    def select_image(
        self, scene_id: int, asset_id: int, *, expected_version: int
    ) -> None:
        asset = self.asset(asset_id)
        if asset is None or int(asset["scene_id"] or -1) != scene_id:
            raise VisualApprovalError("選擇的圖片資產不屬於這個 scene")
        if asset["asset_type"] != "scene_image" or asset["status"] != "ready":
            raise VisualApprovalError("只能選擇通過 QC 的 ready scene_image")
        changed = self.db.conn.execute(
            """
            UPDATE visual_scenes
            SET selected_image_asset_id=?,status='image_selected',
                state_version=state_version+1,updated_at=?
            WHERE id=? AND state_version=?
              AND status IN ('awaiting_image_review','image_selected')
            """,
            (asset_id, utc_now_iso(), scene_id, expected_version),
        ).rowcount
        if changed != 1:
            self.db.conn.rollback()
            raise VisualApprovalError("scene 狀態已更新；重新載入 review page")
        self.db.conn.commit()

    def reject_image_scene(self, scene_id: int, *, expected_version: int) -> None:
        now = utc_now_iso()
        with self.db.conn:
            changed = self.db.conn.execute(
                """
                UPDATE visual_scenes
                  SET selected_image_asset_id=NULL,
                      status='needs_regeneration',
                      state_version=state_version+1,
                      approved_at=NULL,
                      updated_at=?
                  WHERE id=? AND state_version=?
                    AND status IN ('awaiting_image_review','image_selected')
                  """,
                  (now, scene_id, expected_version),
              ).rowcount
              if changed != 1:
                  raise VisualApprovalError(
                      f"scene {scene_id} 狀態已更新；重新載入 review page"
                  )

    def finish_image_review(self, job_id: int) -> None:
        scenes = self.scenes(job_id)
        selected = self.db.conn.execute(
            """
            SELECT s.id AS scene_id,s.status AS scene_status,
                   a.id AS asset_id,a.job_id AS asset_job_id,
                   a.scene_id AS asset_scene_id,a.asset_type,a.status AS asset_status,
                   a.path
            FROM visual_scenes s
            LEFT JOIN visual_assets a ON a.id=s.selected_image_asset_id
            WHERE s.job_id=?
            ORDER BY s.position
            """,
            (job_id,),
        ).fetchall()
        valid = (
            scenes
            and len(selected) == len(scenes)
            and all(
                row["scene_status"] == "image_selected"
                and row["asset_id"] is not None
                and int(row["asset_job_id"]) == job_id
                and int(row["asset_scene_id"]) == int(row["scene_id"])
                and row["asset_type"] == "scene_image"
                and row["asset_status"] == "ready"
                and row["path"]
                and Path(row["path"]).exists()
                for row in selected
            )
        )
        if not valid:
            raise VisualApprovalError(
                "圖片審核未完成：每個 scene 都必須維持 image_selected，"
                "且所選 ready scene_image 仍存在並屬於目前 job／scene"
            )
        now = utc_now_iso()
        with self.db.conn:
            self.db.conn.execute(
                """
                  UPDATE visual_scenes
                  SET status='image_approved',state_version=state_version+1,
                      approved_at=?,updated_at=?
                  WHERE job_id=? AND status='image_selected'
                  """,
                  (now, now, job_id),
              )
```

`finish_image_review()` 上述 selected rows 查詢、檔案 snapshot identity 重驗與
`image_selected → image_approved` 更新必須全部位於同一
`BEGIN IMMEDIATE` transaction；最後 UPDATE rowcount 必須等於 scene count，
否則 rollback。HTML form 的 hidden `state_version` 必須傳到 select/reject，
stale POST 回 structured `VISUAL_REVIEW_STALE`，不可覆寫新狀態。

- [ ] **Step 4: Run repository tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_repository.py tests/test_visual_db.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/lyria_auto/visual_repository.py tests/test_visual_repository.py
git commit -m "feat: add idempotent visual asset repository"
```

---

### Task 3: Image QC

**Files:**
- Create: `src/lyria_auto/media/visual_quality.py`
- Create: `tests/test_visual_quality.py`

- [ ] **Step 1: Write failing image QC tests**

建立 `tests/test_visual_quality.py`：

```python
from __future__ import annotations

from PIL import Image, ImageDraw

from lyria_auto.media.visual_quality import inspect_image


def save(path, size):
    image = Image.new("RGB", size, (70, 50, 35))
    draw = ImageDraw.Draw(image)
    for x in range(0, size[0], 64):
        color = (110, 85, 55) if (x // 64) % 2 else (45, 60, 40)
        draw.rectangle(
            (x, 0, min(x + 63, size[0] - 1), size[1] - 1),
            fill=color,
        )
    image.save(path, "PNG")


def test_image_qc_accepts_16_by_9_2k(tmp_path):
    path = tmp_path / "ok.png"
    save(path, (2048, 1152))

    result = inspect_image(path)

    assert result.passed is True
    assert (result.width, result.height) == (2048, 1152)


def test_image_qc_rejects_wrong_aspect_ratio(tmp_path):
    path = tmp_path / "square.png"
    save(path, (2048, 2048))

    result = inspect_image(path)

    assert result.passed is False
    assert "16:9" in result.reason


def test_image_qc_rejects_corrupt_file(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not-an-image")

    result = inspect_image(path)

    assert result.passed is False
    assert "解碼" in result.reason


def test_image_qc_rejects_nearly_monochrome_output(tmp_path):
    path = tmp_path / "flat.png"
    Image.new("RGB", (2048, 1152), (5, 5, 5)).save(path, "PNG")

    result = inspect_image(path)

    assert result.passed is False
    assert "單色" in result.reason or "全黑" in result.reason
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_quality.py -v
```

Expected: module import fails.

- [ ] **Step 3: Implement image inspection**

建立 `src/lyria_auto/media/visual_quality.py`：

```python
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageStat

from ..visual_models import ImageQC


def inspect_image(
    path: str | Path,
    *,
    expected_ratio: float = 16 / 9,
    ratio_tolerance: float = 0.01,
    minimum_width: int = 1920,
) -> ImageQC:
    try:
        if Path(path).stat().st_size <= 0:
            raise ValueError("檔案大小為 0")
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            statistics = ImageStat.Stat(image.convert("RGB"))
    except Exception as exc:
        return ImageQC(False, 0, 0, f"圖片無法解碼：{exc}")
    if width < minimum_width:
        return ImageQC(False, width, height, f"圖片寬度不足：{width} < {minimum_width}")
    ratio = width / max(1, height)
    if abs(ratio - expected_ratio) > ratio_tolerance:
        return ImageQC(False, width, height, f"圖片比例不是 16:9：{width}x{height}")
    brightness = sum(statistics.mean) / 3
    variation = sum(statistics.stddev) / 3
    if brightness < 2:
        return ImageQC(False, width, height, "圖片近乎全黑或單色")
    if brightness > 253:
        return ImageQC(False, width, height, "圖片近乎全白或單色")
    if variation < 1:
        return ImageQC(False, width, height, "圖片近乎單色")
    return ImageQC(True, width, height, "ok")
```

- [ ] **Step 4: Run QC tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_quality.py -v
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/lyria_auto/media/visual_quality.py tests/test_visual_quality.py
git commit -m "feat: validate generated scene images"
```

---

### Task 4: Bounded and resumable image generation workflow

**Files:**
- Create: `src/lyria_auto/visual_workflow.py`
- Create: `tests/test_visual_image_workflow.py`

- [ ] **Step 1: Write failing cost and resume tests**

建立 `tests/test_visual_image_workflow.py`：

```python
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from lyria_auto.db import StateDB
from lyria_auto.errors import VisualGenerationError
from lyria_auto.models import PromptPlan
from lyria_auto.visual_planner import VisualPlanner
from lyria_auto.visual_repository import VisualRepository
from lyria_auto.visual_workflow import ImageGenerationWorkflow


class FakeImages:
    def __init__(self):
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def generate_image(self, prompt, output_path, *, reference_paths):
        from lyria_auto.utils import sha256_file
        from lyria_auto.visual_models import GeneratedImage

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (2048, 1152), (80, 60, 40))
        draw = ImageDraw.Draw(image)
        for x in range(0, 2048, 64):
            color = (110, 85, 55) if (x // 64) % 2 else (45, 60, 40)
            draw.rectangle((x, 0, min(x + 63, 2047), 1151), fill=color)
        image.save(output, "PNG")
        self.calls.append((prompt, tuple(str(path) for path in reference_paths)))
        return GeneratedImage(output, "image/png", sha256_file(output))


def album():
    return PromptPlan(
        "p", "s", "rainy cafe", "warm", "piano",
        "雨天咖啡館", "溫暖", "jazz", "tape", "natural mix", "rain",
    )


def setup(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    job_id = db.create_job("main", False, {})
    repo = VisualRepository(db)
    plan = VisualPlanner(seed=7).compose(album(), 120)
    for scene in plan.scenes:
        repo.upsert_scene(
            job_id, scene.position, scene.label,
            plan.to_dict()["world"], scene.image_prompt, scene.motion_prompt,
        )
    return db, job_id, repo, plan


def test_default_four_scene_run_creates_thirteen_images_before_thumbnail(tmp_path):
    db, job_id, repo, plan = setup(tmp_path)
    fake = FakeImages()
    try:
        authorization, lease = authorized_test_context(repo, job_id, images=13)
        workflow = ImageGenerationWorkflow(
            repo, fake, image_model="model",
            authorization=authorization, lease=lease,
        )
        workflow.generate_scene_images(job_id, tmp_path / "job", plan)

        assert len(fake.calls) == 13
        assert len(repo.assets(job_id, asset_type="world_anchor")) == 1
        assert len(repo.assets(job_id, asset_type="scene_image")) == 12
        anchor_path = repo.assets(job_id, asset_type="world_anchor")[0]["path"]
        assert all(call[1] == (anchor_path,) for call in fake.calls[1:])
    finally:
        db.close()


def test_resume_does_not_regenerate_ready_images(tmp_path):
    db, job_id, repo, plan = setup(tmp_path)
    fake = FakeImages()
    try:
        authorization, lease = authorized_test_context(repo, job_id, images=13)
        workflow = ImageGenerationWorkflow(
            repo, fake, image_model="model",
            authorization=authorization, lease=lease,
        )
        workflow.generate_scene_images(job_id, tmp_path / "job", plan)
        workflow.generate_scene_images(job_id, tmp_path / "job", plan)

        assert len(fake.calls) == 13
    finally:
        db.close()


def test_thumbnail_is_fourteenth_and_fifteenth_is_blocked(tmp_path):
    db, job_id, repo, plan = setup(tmp_path)
    fake = FakeImages()
    try:
        authorization, lease = authorized_test_context(repo, job_id, images=14)
        workflow = ImageGenerationWorkflow(
            repo, fake, image_model="model",
            authorization=authorization, lease=lease,
        )
        workflow.generate_scene_images(job_id, tmp_path / "job", plan)
        first_scene = repo.scenes(job_id)[0]
        first_asset = next(
            asset for asset in repo.assets(job_id, asset_type="scene_image")
            if asset["scene_id"] == first_scene["id"]
        )
        repo.select_image(
            int(first_scene["id"]),
            int(first_asset["id"]),
            expected_version=int(first_scene["state_version"]),
        )
        workflow.generate_thumbnail_background(job_id, tmp_path / "job", plan)

        with pytest.raises(VisualGenerationError, match="十四張"):
            workflow.generate_extra_image(job_id, int(first_scene["id"]), tmp_path / "extra.png")
    finally:
        db.close()
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_image_workflow.py -v
```

Expected: `visual_workflow` import fails.

- [ ] **Step 3: Implement the image workflow**

建立 `src/lyria_auto/visual_workflow.py`：

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from .errors import PaidStartUncertainError, VisualGenerationError
from .media.visual_quality import inspect_image, perceptual_hash
from .utils import sha256_file
from .visual_models import VisualPlan
from .visual_repository import VisualRepository


class ImageGenerationWorkflow:
    def __init__(
        self,
        repo: VisualRepository,
        client: Any,
        *,
        image_model: str,
        provider: str = "gemini-developer-api",
        max_outputs: int = 14,
          unit_cost: float = 0.101,
          budget_usd: float = 10.0,
          pricing_snapshot: dict[str, Any] | None = None,
          authorization: PaidStageAuthorization,
          lease: SideEffectLease,
    ):
        self.repo = repo
        self.client = client
        self.image_model = image_model
        self.provider = provider
        self.max_outputs = max_outputs
        self.unit_cost = unit_cost
          self.budget_usd = budget_usd
          self.pricing_snapshot = pricing_snapshot or {}
          self.authorization = authorization
          self.lease = lease

    def _assert_room(self, job_id: int) -> None:
        count = len(
            [
                asset for asset in self.repo.assets(job_id)
                if asset["asset_type"] in {
                    "world_anchor", "scene_image", "thumbnail_background"
                }
            ]
        )
        if count >= self.max_outputs:
            raise VisualGenerationError("預設圖片硬上限為十四張，額外輸出需要顯式授權")

    def _assert_budget(self, job_id: int) -> None:
        projected = self.repo.estimated_cost(job_id) + self.unit_cost
        if projected > self.budget_usd:
            raise VisualGenerationError(
                f"預估視覺成本 US${projected:.2f} 超過預設預算 "
                f"US${self.budget_usd:.2f}"
            )

    def _generate(
        self,
        *,
        job_id: int,
        scene_id: int | None,
        asset_type: str,
        variant: int,
        prompt: str,
        output: Path,
        references: list[str | Path],
        authorized_extra: bool = False,
    ) -> int:
        asset_id = self.repo.reserve_asset(
            job_id, scene_id, asset_type, variant, self.provider,
            self.image_model, prompt, self.unit_cost, self.pricing_snapshot,
        )
        row = self.repo.asset(asset_id)
        if row["status"] == "ready" and row["path"] and Path(row["path"]).exists():
            return asset_id
        if row["status"] in (
            "failed_generation", "failed_tampered", "qc_failed", "start_uncertain"
        ):
            return asset_id
          if row["status"] == "starting":
              if self.repo.lease_is_active_for_owner(
                  job_id, str(row["owner_token"])
              ):
                  return asset_id
              if output.exists():
                  qc = inspect_image(output)
                  if qc.passed:
                      current = self.repo.asset(asset_id)
                      self.repo.mark_asset_ready(
                          asset_id,
                          expected_status="starting",
                          expected_version=int(current["state_version"]),
                          owner_token=self.lease.owner_token,
                          path=str(output),
                          mime_type="image/png",
                          sha256=sha256_file(output),
                          width=qc.width,
                          height=qc.height,
                          perceptual_hash=perceptual_hash(output),
                      )
                      return asset_id
              self.repo.mark_start_uncertain(
                  asset_id,
                  expected_version=int(row["state_version"]),
                  reason_code="VISUAL_IMAGE_START_UNCERTAIN",
              )
              return asset_id
          self._assert_room(job_id)
          claimed = self.repo.claim_paid_start(
              asset_id=asset_id,
              expected_status="reserved",
              expected_version=int(row["state_version"]),
              authorization_id=self.authorization.id,
              identity_key=f"asset:{asset_id}",
              owner_token=self.lease.owner_token,
          )
          if not claimed:
              return asset_id
          try:
              generated = self.client.generate_image(
                prompt, output, reference_paths=references
            )
              qc = inspect_image(generated.path)
              if not qc.passed:
                  current = self.repo.asset(asset_id)
                  self.repo.mark_qc_failed(
                      asset_id,
                      expected_status="starting",
                      expected_version=int(current["state_version"]),
                      owner_token=self.lease.owner_token,
                      reason_code=qc.reason_code,
                      path=str(generated.path),
                      sha256=generated.sha256,
                      width=qc.width,
                      height=qc.height,
                  )
                  raise VisualGenerationError(qc.reason)
              current = self.repo.asset(asset_id)
              self.repo.mark_asset_ready(
                  asset_id,
                  expected_status="starting",
                  expected_version=int(current["state_version"]),
                  owner_token=self.lease.owner_token,
                  path=str(generated.path),
                  mime_type=generated.mime_type,
                  sha256=generated.sha256,
                  width=qc.width,
                  height=qc.height,
                  perceptual_hash=perceptual_hash(generated.path),
              )
              return asset_id
          except PaidStartUncertainError as exc:
              current = self.repo.asset(asset_id)
              self.repo.mark_start_uncertain(
                  asset_id,
                  expected_version=int(current["state_version"]),
                  reason_code=exc.code,
              )
              return asset_id
          except Exception as exc:
              current = self.repo.asset(asset_id)
              if current["status"] == "starting":
                  self.repo.mark_start_uncertain(
                      asset_id,
                      expected_version=int(current["state_version"]),
                      reason_code=classify_paid_start_exception(exc).code,
                  )
              elif current["status"] != "qc_failed":
                  self.repo.mark_generation_failed(
                      asset_id,
                      expected_version=int(current["state_version"]),
                      error=sanitize_exception(exc),
                  )
            return asset_id

    def generate_scene_images(
        self, job_id: int, job_dir: str | Path, plan: VisualPlan
    ) -> None:
        root = Path(job_dir) / "visual"
        anchor_id = self._generate(
            job_id=job_id,
            scene_id=None,
            asset_type="world_anchor",
            variant=0,
            prompt=plan.world_anchor_prompt,
            output=root / "world_anchor.png",
            references=[],
        )
        anchor = self.repo.asset(anchor_id)
        for scene_row, scene_plan in zip(
            self.repo.scenes(job_id), plan.scenes, strict=True
        ):
            for variant in range(3):
                self._generate(
                    job_id=job_id,
                    scene_id=int(scene_row["id"]),
                    asset_type="scene_image",
                    variant=variant,
                    prompt=scene_plan.image_prompt,
                    output=root / f"scene_{scene_plan.label}_{variant + 1}.png",
                    references=[anchor["path"]],
                )

    def generate_thumbnail_background(
        self, job_id: int, job_dir: str | Path, plan: VisualPlan
    ) -> int:
        first = self.repo.scenes(job_id)[0]
        selected = first["selected_image_asset_id"]
        if selected is None:
            raise VisualGenerationError("A 場景尚未核准，不能產生縮圖背景")
        reference = self.repo.asset(int(selected))
        return self._generate(
            job_id=job_id,
            scene_id=None,
            asset_type="thumbnail_background",
            variant=0,
            prompt=plan.thumbnail_prompt,
            output=Path(job_dir) / "visual" / "thumbnail_background.png",
            references=[reference["path"]],
        )

    def generate_extra_image(
        self, job_id: int, scene_id: int, output: str | Path
    ) -> int:
        self._assert_room(job_id)
        raise VisualGenerationError(
            "額外圖片只能由獨立 regenerate --kind image "
            "--allow-outputs N 流程建立；resume 不補生"
        )
```

- [ ] **Step 4: Fix the reservation count order**

`_assert_room()` 必須在 reserve 新資產之前執行，否則第十四張 reserve 後會被誤算成已滿。把 `_generate()` 中的順序改成：

```python
        existing = [
            asset for asset in self.repo.assets(job_id)
            if asset["asset_type"] == asset_type
            and asset["scene_id"] == scene_id
            and int(asset["variant_index"]) == variant
        ]
        if not existing:
            if not authorized_extra:
                self._assert_room(job_id)
                self._assert_budget(job_id)
        asset_id = self.repo.reserve_asset(
            job_id, scene_id, asset_type, variant, self.provider,
            self.image_model, prompt, self.unit_cost, self.pricing_snapshot,
        )
```

並刪除後面的第二次 `self._assert_room(job_id)`。

- [ ] **Step 5: Run workflow tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_image_workflow.py tests/test_visual_quality.py -v
```

Expected: all pass; fake call count stays 13 after resume。

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/visual_workflow.py tests/test_visual_image_workflow.py
git commit -m "feat: generate bounded resumable scene images"
```

---

### Task 5: Image review service and localhost server

**Files:**
- Create: `src/lyria_auto/review_server.py`
- Create: `tests/test_review_server.py`

- [ ] **Step 1: Write failing service and HTTP security tests**

建立 `tests/test_review_server.py`：

```python
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

import pytest

from lyria_auto.db import StateDB
from lyria_auto.errors import VisualApprovalError
from lyria_auto.review_server import ReviewServer, ReviewService
from lyria_auto.visual_repository import VisualRepository


def prepared(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    job_id = db.create_job("main", False, {})
    repo = VisualRepository(db)
    scene = repo.upsert_scene(job_id, 0, "A", {}, "prompt", "motion")
    asset = repo.reserve_asset(job_id, scene, "scene_image", 0, "gemini", "model", "p", 0.101, {})
    repo.mark_asset_ready(asset, str(tmp_path / "a.png"), "image/png", "sha", 1920, 1080)
    return db, job_id, scene, asset, repo


def test_review_service_rejects_cross_job_asset(tmp_path):
    db, job_id, scene, asset, repo = prepared(tmp_path)
    try:
        service = ReviewService(repo, job_id)
        with pytest.raises(VisualApprovalError):
            service.approve_image(scene + 999, asset)
    finally:
        db.close()


def test_post_requires_token_and_get_has_no_paid_provider(tmp_path):
    db, job_id, scene, asset, repo = prepared(tmp_path)
    server = ReviewServer(repo, job_id, port=0)
    server.start_in_thread()
    try:
        page = urllib.request.urlopen(server.url, timeout=2).read().decode("utf-8")
        assert "場景 A" in page
        request = urllib.request.Request(
            server.url + "approve-image",
            data=urllib.parse.urlencode(
                {"scene_id": scene, "asset_id": asset, "token": "wrong"}
            ).encode(),
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request, timeout=2)
        assert exc.value.code == 403
    finally:
        server.close()
        db.close()
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_review_server.py -v
```

Expected: `review_server` import fails.

- [ ] **Step 3: Implement service, HTML escaping and token-protected POST**

建立 `src/lyria_auto/review_server.py`：

```python
from __future__ import annotations

import html
import secrets
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .errors import VisualApprovalError
from .utils import utc_now_iso
from .visual_repository import VisualRepository


class ReviewService:
    def __init__(self, repo: VisualRepository, job_id: int):
        self.repo = repo
        self.job_id = job_id

    def approve_image(
        self, scene_id: int, asset_id: int, *, expected_version: int
    ) -> None:
        scenes = {int(row["id"]) for row in self.repo.scenes(self.job_id)}
        if scene_id not in scenes:
            raise VisualApprovalError("scene 不屬於目前 job")
        self.repo.select_image(
            scene_id, asset_id, expected_version=expected_version
        )

    def reject_scene(self, scene_id: int, *, expected_version: int) -> None:
        scenes = {int(row["id"]) for row in self.repo.scenes(self.job_id)}
        if scene_id not in scenes:
            raise VisualApprovalError("scene 不屬於目前 job")
        self.repo.reject_image_scene(
            scene_id, expected_version=expected_version
        )

    def finish_images(self) -> None:
        self.repo.finish_image_review(self.job_id)


def render_image_review(repo: VisualRepository, job_id: int, token: str) -> str:
    sections = []
    for scene in repo.scenes(job_id):
        cards = []
        assets = [
            row for row in repo.assets(job_id, asset_type="scene_image")
            if row["scene_id"] == scene["id"]
        ]
        for asset in assets:
            preview = (
                f"<img src='/asset?id={int(asset['id'])}&token={token}'>"
                if asset["path"]
                else "<div class='missing'>沒有可檢查的輸出檔</div>"
            )
            details = (
                f"{html.escape(str(asset['model']))} · variant "
                f"{int(asset['variant_index']) + 1} · "
                f"{int(asset['width'] or 0)}×{int(asset['height'] or 0)} · "
                f"US${float(asset['estimated_cost_usd']):.3f} · "
                f"{html.escape(str(asset['status']))}"
            )
            cards.append(
                f"<article>{preview}"
                f"<p>{details}</p>"
                f"<p>{html.escape(str(asset['error'] or 'QC ok'))}</p>"
                f"<form method='post' action='/approve-image'>"
                f"<input type='hidden' name='token' value='{token}'>"
                f"<input type='hidden' name='scene_id' value='{int(scene['id'])}'>"
                f"<input type='hidden' name='asset_id' value='{int(asset['id'])}'>"
                "<button>核准這張</button></form>"
                f"<form method='post' action='/reject-scene'>"
                f"<input type='hidden' name='token' value='{token}'>"
                f"<input type='hidden' name='scene_id' value='{int(scene['id'])}'>"
                "<button>拒絕這組</button></form></article>"
            )
        sections.append(
            f"<section><h2>場景 {html.escape(scene['label'])}</h2>"
            + "".join(cards)
            + "</section>"
        )
    return (
        "<!doctype html><meta charset='utf-8'><title>Visual Review</title>"
        "<style>body{font:16px system-ui;background:#17130f;color:#f5eadc;"
        "max-width:1400px;margin:auto;padding:24px}section{margin:32px 0}"
        "article{display:inline-block;width:31%;vertical-align:top;margin:1%;"
        "background:#292119;padding:12px;box-sizing:border-box}"
        "img,.missing{width:100%;aspect-ratio:16/9;object-fit:cover}"
        ".missing{display:grid;place-items:center;background:#100d0a}"
        "button{padding:10px}</style>"
        + "".join(sections)
        + f"<form method='post' action='/finish-images'>"
        f"<input type='hidden' name='token' value='{token}'>"
        "<button>確認完成圖片核准</button></form>"
    )


class ReviewServer:
    def __init__(self, repo: VisualRepository, job_id: int, port: int = 0):
        self.repo = repo
        self.job_id = job_id
        self.token = secrets.token_urlsafe(24)
        self.service = ReviewService(repo, job_id)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/":
                    self._send_html(render_image_review(repo, job_id, owner.token))
                    return
                if parsed.path == "/asset":
                    query = urllib.parse.parse_qs(parsed.query)
                    if query.get("token", [""])[0] != owner.token:
                        self.send_error(HTTPStatus.FORBIDDEN)
                        return
                    asset = repo.asset(int(query["id"][0]))
                    if asset is None or int(asset["job_id"]) != job_id:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    if not asset["path"]:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    path = Path(asset["path"])
                    if not path.exists():
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    data = path.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", asset["mime_type"])
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(
                    self.rfile.read(length).decode("utf-8")
                )
                if form.get("token", [""])[0] != owner.token:
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                try:
                      if self.path == "/approve-image":
                          owner.service.approve_image(
                              int(form["scene_id"][0]),
                              int(form["asset_id"][0]),
                              expected_version=int(form["state_version"][0]),
                          )
                      elif self.path == "/reject-scene":
                          owner.service.reject_scene(
                              int(form["scene_id"][0]),
                              expected_version=int(form["state_version"][0]),
                          )
                    elif self.path == "/finish-images":
                        owner.service.finish_images()
                    else:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                  except VisualApprovalError as exc:
                      safe_error = sanitize_exception(exc)
                      self.send_structured_error(
                          HTTPStatus.BAD_REQUEST, safe_error
                      )
                    return
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", "/")
                self.end_headers()

            def _send_html(self, body: str):
                data = body.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                return

        self.httpd = HTTPServer(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/"
        self.thread: threading.Thread | None = None

    def start_in_thread(self) -> None:
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, daemon=True
        )
        self.thread.start()

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def close(self) -> None:
        if self.thread and self.thread.is_alive():
            self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=2)
```

- [ ] **Step 4: Run review tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_review_server.py tests/test_visual_repository.py -v
```

Expected: all pass; server address starts with `http://127.0.0.1:`。

- [ ] **Step 5: Commit**

```bash
git add src/lyria_auto/review_server.py tests/test_review_server.py
git commit -m "feat: add local image approval server"
```

---

### Task 6: Dedicated thumbnail composition from the approved world

**Files:**
- Modify: `src/lyria_auto/media/thumbnail.py`
- Create: `tests/test_visual_thumbnail.py`

- [ ] **Step 1: Write failing thumbnail test**

建立 `tests/test_visual_thumbnail.py`：

```python
from __future__ import annotations

from PIL import Image

from lyria_auto.media.thumbnail import create_visual_thumbnail


def test_visual_thumbnail_uses_exact_background_and_local_text(tmp_path):
    background = tmp_path / "background.png"
    Image.new("RGB", (2048, 1152), (120, 90, 60)).save(background)

    output = create_visual_thumbnail(
        background,
        tmp_path / "thumbnail.jpg",
        title="COZY JAZZ",
        subtitle="Study · Read · Relax",
        width=1280,
        height=720,
        quality=88,
    )

    with Image.open(output) as image:
        assert image.size == (1280, 720)
    assert output.stat().st_size < 2 * 1024 * 1024
```

- [ ] **Step 2: Run and verify RED**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_thumbnail.py -v
```

Expected: import fails because `create_visual_thumbnail` does not exist.

- [ ] **Step 3: Add the dedicated composition without changing legacy thumbnail**

在 `src/lyria_auto/media/thumbnail.py` 末尾加入：

```python
def create_visual_thumbnail(
    background_path: str | Path,
    output_path: str | Path,
    *,
    title: str,
    subtitle: str,
    width: int,
    height: int,
    quality: int,
) -> Path:
    with Image.open(background_path) as source:
        image = _fit_cover(source.convert("RGB"), width, height)
    image = ImageEnhance.Brightness(image).enhance(0.78)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    left_width = int(width * 0.54)
    draw.rectangle((0, 0, left_width, height), fill=(7, 8, 7, 118))
    title_font = _font(max(42, width // 14))
    subtitle_font = _font(max(22, width // 36))
    draw.text(
        (int(width * 0.055), int(height * 0.34)),
        title,
        font=title_font,
        fill=(248, 239, 220, 255),
    )
    draw.text(
        (int(width * 0.058), int(height * 0.57)),
        subtitle,
        font=subtitle_font,
        fill=(222, 198, 158, 255),
    )
    composed = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    composed.save(output, "JPEG", quality=quality, optimize=True, progressive=True)
    if output.stat().st_size > 2 * 1024 * 1024:
        composed.save(output, "JPEG", quality=max(65, quality - 20), optimize=True)
    return output
```

- [ ] **Step 4: Run thumbnail regression tests**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_thumbnail.py tests/test_dry_run.py -v
```

Expected: all pass; existing `create_thumbnail()` remains unchanged.

- [ ] **Step 5: Run Plan 2 test set**

Run:

```bash
./.venv/bin/python -m pytest tests/test_visual_planner.py tests/test_visual_repository.py tests/test_visual_quality.py tests/test_visual_image_workflow.py tests/test_review_server.py tests/test_visual_thumbnail.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/lyria_auto/media/thumbnail.py tests/test_visual_thumbnail.py
git commit -m "feat: compose branded thumbnail from approved visual world"
```

---

### Task 7: Atomic image review、guided UI 與 terminal progress

**Files:**
- Modify: `src/lyria_auto/review_server.py`
- Modify: `src/lyria_auto/visual_repository.py`
- Modify: `src/lyria_auto/visual_workflow.py`
- Create: `src/lyria_auto/progress.py`
- Create: `tests/test_image_review_state.py`
- Create: `tests/test_visual_progress.py`
- Modify: `tests/test_review_server.py`

- [ ] **Step 1: Write state-invariant tests**

```python
def test_select_then_reject_clears_selection_and_finish_is_blocked(prepared):
    repo, job_id, scene_id, asset_id = prepared
    version = int(repo.scene(scene_id)["state_version"])
    repo.select_image(scene_id, asset_id, expected_version=version)
    selected_version = int(repo.scene(scene_id)["state_version"])
    repo.reject_image_scene(scene_id, expected_version=selected_version)
    scene = repo.scenes(job_id)[0]
    assert scene["selected_image_asset_id"] is None
    assert scene["status"] == "needs_regeneration"
    assert scene["approved_at"] is None
    with pytest.raises(VisualApprovalError, match="審核未完成"):
        repo.finish_image_review(job_id)


def test_finish_revalidates_selected_asset_instead_of_trusting_stale_id(prepared):
    repo, job_id, scene_id, asset_id = prepared
    repo.select_image(
        scene_id,
        asset_id,
        expected_version=int(repo.scene(scene_id)["state_version"]),
    )
    # 模擬舊 selection 指向已失效 row；production code 不提供任意 status setter。
    repo.db.conn.execute(
        "UPDATE visual_assets SET status='qc_failed',state_version=state_version+1 "
        "WHERE id=?",
        (asset_id,),
    )
    repo.db.conn.commit()
    with pytest.raises(VisualApprovalError, match="ready"):
        repo.finish_image_review(job_id)


def test_stale_review_post_cannot_overwrite_newer_reject(prepared):
    repo, job_id, scene_id, asset_id = prepared
    version = int(repo.scene(scene_id)["state_version"])
    repo.reject_image_scene(scene_id, expected_version=version)
    with pytest.raises(VisualApprovalError, match="狀態已更新"):
        repo.select_image(
            scene_id,
            asset_id,
            expected_version=version,
        )
```

- [ ] **Step 2: Write guided-page tests**

HTML 測試必須找到：

```python
assert 'data-review-complete="3/4"' in page
assert 'data-selected="true"' in page
assert 'data-qc-status="qc_failed"' in page
assert 'disabled aria-disabled="true"' in page
assert "<dialog id=\"full-size-preview\">" in page
assert "構圖與真實感" in page
assert "世界一致性" in page
assert "無人物／文字／Logo" in page
assert "下一階段預估成本" in page
assert "lyria-auto resume --job" in page
```

頁面規則：

- 點縮圖開啟可縮放的原尺寸 `<dialog>`。
- selected card 有文字、邊框及 `aria-pressed`，不能只靠顏色。
- `qc_failed`、missing、`start_uncertain` 按鈕由 server render 為 disabled。
- 每個 scene 只顯示一個 reject action，不在每張 card 重複。
- 完成按鈕只有在後端 snapshot 顯示全部 selected 時可用；即使 UI 顯示錯誤，POST 仍由 repository invariant 阻擋。

- [ ] **Step 3: Add reusable progress reporter**

`ProgressReporter` 接收 stage、current、total、scene、variant、started_at，terminal 範例：

```text
[images 7/13] scene C · variant 1/3 · elapsed 01:42
Safe to Ctrl+C. Resume: lyria-auto resume --job 42
```

workflow 每個 reserve、start、QC、terminal state 都更新同一行；中斷時另印一行穩定的 next command。測試使用 fake clock，不使用 sleep。

- [ ] **Step 4: Run and commit**

```bash
./.venv/bin/python -m pytest tests/test_image_review_state.py tests/test_review_server.py tests/test_visual_progress.py tests/test_visual_image_workflow.py -q
./.venv/bin/python -m ruff check src tests
git add src/lyria_auto/review_server.py src/lyria_auto/visual_repository.py src/lyria_auto/visual_workflow.py src/lyria_auto/progress.py tests/test_image_review_state.py tests/test_review_server.py tests/test_visual_progress.py
git commit -m "feat: make image review guided and state-safe"
```

---

## Plan 2 completion gate

進入 Veo 與 pipeline 整合前必須同時成立：

1. 相同 seed 產生完全相同 world bible 與 scene prompts。
2. 45 分鐘只規劃 A、B；120 分鐘以上最多 A～D。
3. 四景共用 world ID、時段、天氣、材料與色盤，鏡位角色不重複。
4. 第一次圖片階段恰好 13 張，縮圖背景是第 14 張。
5. 圖片 paid start 使用 Plan 1 lease＋CAS＋同 transaction authorization
   consumption；兩個獨立 process 同時 claim 時仍只有一次 provider call。
6. 普通 resume 對 ready／failed_generation／failed_tampered／qc_failed／
   start_uncertain 資產都不會靜默付費重生。
7. Review server 只綁 loopback、POST 驗證 token、不能 import 付費 provider。
8. select／reject 使用 `state_version` CAS；stale POST 不可覆寫新狀態；
   finish 在同一 `BEGIN IMMEDIATE` transaction 重驗並更新。
9. guided review 支援全尺寸、選取高亮、QC disabled、完成數、品質 checklist、下一步與下一階段成本。
10. `visual-demo` 使用隔離 DB／workspace，測試封鎖 Gemini、Lyria、YouTube、
    Pipeline、正式 config/DB/OAuth 路徑，並將所有 sample 資產標成不可上傳。
11. ready 圖片保存 perceptual hash，供 Plan 4 channel-originality gate 使用。
12. terminal progress 顯示 x/y、scene、variant、elapsed、Ctrl+C 與 resume command。
13. `reserved`／`starting` image asset 會阻止 pipeline 提前進入圖片審核。
14. `0002_visual_image_phash` 由共用 migration registry 套用，可 rollback／重跑並保留 Plan 1 資料。
15. 所有相關 pytest 和既有 prompt／dry-run tests 通過。
