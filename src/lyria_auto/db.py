from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .errors import MigrationInvariantError, VisualApprovalError
from .media.audio import probe_audio
from .utils import utc_now_iso
from .visual_models import MediaSnapshot, SideEffectLease, VisualSource

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL,
  channel TEXT,
  dry_run INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  payload_json TEXT,
  state_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tracks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  prompt TEXT NOT NULL,
  signature TEXT NOT NULL,
  audio_path TEXT,
  duration_seconds REAL,
  sha256 TEXT,
  status TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS videos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  video_path TEXT,
  thumbnail_path TEXT,
  youtube_video_id TEXT,
  publish_at TEXT,
  status TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL,
  state_version INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER,
  level TEXT NOT NULL,
  event_type TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""

VISUAL_SCHEMA_MIGRATION_ID = "0001_visual_foundation"

VISUAL_SCHEMA_SQL = """
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
  error_code TEXT,
  perceptual_hash TEXT,
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
"""

STUDIO_SCHEMA_MIGRATION_ID = "0002_studio_foundation"

STUDIO_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('draft','in_progress','complete','archived')),
  music_job_id INTEGER,
  state_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(music_job_id) REFERENCES jobs(id)
);
CREATE TABLE IF NOT EXISTS episode_assets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN (
    'keyframe','motion_test','clip','clip_1080p','loop','final'
  )),
  role TEXT NOT NULL CHECK(role IN ('shared','sleep','lookup')),
  variant_index INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK(status IN (
    'queued','running','ready','awaiting_review','approved','rejected','superseded','failed'
  )),
  path TEXT,
  sha256 TEXT,
  width INTEGER,
  height INTEGER,
  duration_seconds REAL,
  fps REAL,
  source_prompt TEXT,
  source_seed INTEGER,
  comfyui_prompt_id TEXT,
  parent_asset_id INTEGER,
  error TEXT,
  state_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(episode_id) REFERENCES episodes(id),
  FOREIGN KEY(parent_asset_id) REFERENCES episode_assets(id)
);
CREATE TABLE IF NOT EXISTS studio_tasks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER NOT NULL,
  asset_id INTEGER,
  task_type TEXT NOT NULL CHECK(task_type IN (
    'generate_keyframe','generate_motion_test','generate_clip',
    'upscale_clip','build_loop','render_final'
  )),
  status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed')),
  payload_json TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  FOREIGN KEY(episode_id) REFERENCES episodes(id),
  FOREIGN KEY(asset_id) REFERENCES episode_assets(id)
);
CREATE INDEX IF NOT EXISTS idx_episode_assets_episode
ON episode_assets(episode_id, kind, role);
CREATE INDEX IF NOT EXISTS idx_studio_tasks_status
ON studio_tasks(status, id);
"""

# Valid job statuses for migration validation
VALID_JOB_STATUSES = {
    "created", "planning", "planned", "generating",
    "generating_images", "awaiting_image_review",
    "generating_videos", "awaiting_video_review",
    "generating_audio", "rendering", "rendered",
    "uploading", "complete", "dry_run_complete", "failed",
}

VALID_VIDEO_STATUSES = {
    "planned", "dry_run", "rendering", "rendered", "uploading", "uploaded",
}


class StateDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._run_migrations()
        self.conn.commit()

    def _run_migrations(self) -> None:
        """Run versioned migrations using schema_migrations registry."""
        # Ensure the registry table exists
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "  id TEXT PRIMARY KEY,"
            "  checksum TEXT NOT NULL,"
            "  applied_at TEXT NOT NULL"
            ");"
        )
        self.conn.commit()

        checksum = hashlib.sha256(VISUAL_SCHEMA_SQL.encode("utf-8")).hexdigest()

        existing = self.conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (VISUAL_SCHEMA_MIGRATION_ID,),
        ).fetchone()

        if existing is not None and existing["checksum"] != checksum:
            raise MigrationInvariantError(
                f"Migration {VISUAL_SCHEMA_MIGRATION_ID} checksum mismatch; "
                "schema may have changed since last apply"
            )

        if existing is None:
            # Validate existing job/video statuses before applying
            bad_jobs = self.conn.execute(
                "SELECT id, status FROM jobs WHERE status NOT IN ("
                + ",".join("?" for _ in VALID_JOB_STATUSES) + ")",
                list(VALID_JOB_STATUSES),
            ).fetchall()
            if bad_jobs:
                row = bad_jobs[0]
                raise MigrationInvariantError(
                    f"jobs row id={row['id']} has unknown status "
                    f"{row['status']!r}; aborting migration"
                )

            bad_videos = self.conn.execute(
                "SELECT id, status FROM videos WHERE status NOT IN ("
                + ",".join("?" for _ in VALID_VIDEO_STATUSES) + ")",
                list(VALID_VIDEO_STATUSES),
            ).fetchall()
            if bad_videos:
                row = bad_videos[0]
                raise MigrationInvariantError(
                    f"videos row id={row['id']} has unknown status "
                    f"{row['status']!r}; aborting migration"
                )

            # Apply migration in a single transaction
            try:
                self.conn.executescript(VISUAL_SCHEMA_SQL)
                self.conn.execute(
                    "INSERT INTO schema_migrations(id, checksum, applied_at) VALUES(?,?,?)",
                    (VISUAL_SCHEMA_MIGRATION_ID, checksum, utc_now_iso()),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

        # Studio schema has no pre-existing rows to validate against -- it's
        # new tables only -- so it uses the plain checksum-tracked apply
        # instead of repeating the jobs/videos validation dance above.
        self._apply_simple_migration(STUDIO_SCHEMA_MIGRATION_ID, STUDIO_SCHEMA_SQL)

    def _apply_simple_migration(self, migration_id: str, sql: str) -> None:
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        existing = self.conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?", (migration_id,)
        ).fetchone()
        if existing is not None:
            if existing["checksum"] == checksum:
                return
            raise MigrationInvariantError(
                f"Migration {migration_id} checksum mismatch; "
                "schema may have changed since last apply"
            )
        try:
            self.conn.executescript(sql)
            self.conn.execute(
                "INSERT INTO schema_migrations(id, checksum, applied_at) VALUES(?,?,?)",
                (migration_id, checksum, utc_now_iso()),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()

    def create_job(self, channel: str, dry_run: bool, payload: dict[str, Any]) -> int:
        now = utc_now_iso()
        cur = self.conn.execute(
            "INSERT INTO jobs(created_at,updated_at,status,channel,dry_run,payload_json) VALUES(?,?,?,?,?,?)",
            (now, now, "created", channel, int(dry_run), json.dumps(payload, ensure_ascii=False)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_job(self, job_id: int, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE jobs SET status=?, error=?, updated_at=? WHERE id=?",
            (status, error, utc_now_iso(), job_id),
        )
        self.conn.commit()

    def add_track(self, job_id: int, prompt: str, signature: str, status: str = "planned") -> int:
        cur = self.conn.execute(
            "INSERT INTO tracks(job_id,prompt,signature,status,created_at) VALUES(?,?,?,?,?)",
            (job_id, prompt, signature, status, utc_now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_track(self, track_id: int, **values: Any) -> None:
        allowed = {"audio_path", "duration_seconds", "sha256", "status", "error"}
        items = [(k, v) for k, v in values.items() if k in allowed]
        if not items:
            return
        sql = "UPDATE tracks SET " + ",".join(f"{k}=?" for k, _ in items) + " WHERE id=?"
        self.conn.execute(sql, [v for _, v in items] + [track_id])
        self.conn.commit()

    def add_video(self, job_id: int, title: str, status: str = "planned") -> int:
        cur = self.conn.execute(
            "INSERT INTO videos(job_id,title,status,created_at) VALUES(?,?,?,?)",
            (job_id, title, status, utc_now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def update_video(self, video_id: int, **values: Any) -> None:
        allowed = {"video_path", "thumbnail_path", "youtube_video_id", "publish_at", "status", "error"}
        items = [(k, v) for k, v in values.items() if k in allowed]
        if not items:
            return
        sql = "UPDATE videos SET " + ",".join(f"{k}=?" for k, _ in items) + " WHERE id=?"
        self.conn.execute(sql, [v for _, v in items] + [video_id])
        self.conn.commit()

    def event(self, job_id: int | None, event_type: str, message: str, level: str = "INFO") -> None:
        self.conn.execute(
            "INSERT INTO events(job_id,level,event_type,message,created_at) VALUES(?,?,?,?,?)",
            (job_id, level, event_type, message, utc_now_iso()),
        )
        self.conn.commit()

    def recent_signatures(self, limit: int) -> set[str]:
        rows = self.conn.execute(
            "SELECT signature FROM tracks ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return {str(r["signature"]) for r in rows}

    def recent_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id,created_at,updated_at,status,channel,dry_run,error FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def promote_dry_run(self, job_id: int) -> None:
        """把 dry-run 工作轉為正式工作。沿用同一個 job_id，因為標題與 plan.json
        裡的集數編號都綁定它 —— 另開新 job 會讓成品編號與計畫對不上。"""
        self.conn.execute(
            "UPDATE jobs SET dry_run=0, status=?, error=NULL, updated_at=? WHERE id=?",
            ("planning", utc_now_iso(), job_id),
        )
        self.conn.commit()

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
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError):
            return False
        duration = info.get("format", {}).get("duration")
        return duration is not None and float(duration) > 0

    def job(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE id=?", (job_id,)
        ).fetchone()

    def video(self, video_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM videos WHERE id=?", (video_id,)
        ).fetchone()

    def resumable_job(self, job_id: int | None = None) -> dict[str, Any] | None:
        if job_id is not None:
            row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE dry_run=0 AND status NOT IN ('complete','dry_run_complete') "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # ── Visual preflight repository methods ──

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

    def acquire_side_effect_lease(
        self, scope_type: str, scope_id: int, ttl_seconds: int = 60
    ) -> SideEffectLease:
        import binascii
        import os

        now = datetime.now(UTC)
        expires = now + timedelta(seconds=ttl_seconds)
        owner = binascii.hexlify(os.urandom(16)).decode("ascii")
        
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM side_effect_leases WHERE scope_type=? AND scope_id=?",
                (scope_type, scope_id)
            ).fetchone()
            
            if row is None:
                self.conn.execute(
                    """INSERT INTO side_effect_leases
                       (scope_type, scope_id, owner_token, heartbeat_at, expires_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (scope_type, scope_id, owner, now.isoformat(), expires.isoformat())
                )
                self.conn.commit()
                return SideEffectLease(scope_type, scope_id, owner, expires.isoformat(), True)
            else:
                if datetime.fromisoformat(row["expires_at"]) < now:
                    self.conn.execute(
                        """UPDATE side_effect_leases
                           SET owner_token=?, heartbeat_at=?, expires_at=?, state_version=state_version+1
                           WHERE scope_type=? AND scope_id=? AND state_version=?""",
                        (owner, now.isoformat(), expires.isoformat(), scope_type, scope_id, row["state_version"])
                    )
                    self.conn.commit()
                    return SideEffectLease(scope_type, scope_id, owner, expires.isoformat(), True)
                else:
                    self.conn.commit()
                    return SideEffectLease(scope_type, scope_id, row["owner_token"], row["expires_at"], False)
        except Exception:
            self.conn.rollback()
            raise

    def lease_is_active_for_owner(
        self, scope_type: str, scope_id: int, owner_token: str
    ) -> bool:
        row = self.conn.execute(
            "SELECT expires_at FROM side_effect_leases WHERE scope_type=? AND scope_id=? AND owner_token=?",
            (scope_type, scope_id, owner_token)
        ).fetchone()
        if not row:
            return False
        return datetime.fromisoformat(row["expires_at"]) > datetime.now(UTC)

    def create_exact_paid_authorizations(
        self, scope_type: str, scope_id: int, stages: dict[tuple[str, str], int]
    ) -> None:
        now = utc_now_iso()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for (stage, kind), count in stages.items():
                self.conn.execute(
                    """INSERT INTO paid_stage_authorizations
                       (scope_type, scope_id, stage, kind, expected_count, allowed_count, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(scope_type, scope_id, stage, kind) DO NOTHING""",
                    (scope_type, scope_id, stage, kind, count, count, now)
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def claim_paid_start(
        self, scope_type: str, scope_id: int, stage: str, kind: str,
        identity_key: str, expected_state: str, expected_version: int, owner_token: str
    ) -> bool:
        now = utc_now_iso()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            auth = self.conn.execute(
                """SELECT id, allowed_count FROM paid_stage_authorizations
                   WHERE scope_type=? AND scope_id=? AND stage=? AND kind=?""",
                (scope_type, scope_id, stage, kind)
            ).fetchone()
            
            if not auth or auth["allowed_count"] <= 0:
                self.conn.rollback()
                return False
                
            changed = self.conn.execute(
                f"""UPDATE visual_preflight_runs
                   SET {kind}_start_state='starting', state_version=state_version+1, owner_token=?, updated_at=?
                   WHERE id=? AND {kind}_start_state=? AND state_version=?""",
                (owner_token, now, scope_id, expected_state, expected_version)
            ).rowcount
            
            if changed != 1:
                self.conn.rollback()
                return False
                
            self.conn.execute(
                """UPDATE paid_stage_authorizations SET allowed_count=allowed_count-1 WHERE id=?""",
                (auth["id"],)
            )
            
            self.conn.execute(
                """INSERT INTO paid_start_intents (authorization_id, identity_key, owner_token, consumed_at)
                   VALUES (?, ?, ?, ?)""",
                (auth["id"], identity_key, owner_token, now)
            )
            
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def mark_preflight_start_uncertain(
        self, run_id: int, expected_version: int, error_code: str | None = None, error: Any = None
    ) -> None:
        err_str = None
        if error:
            import json
            from dataclasses import asdict
            err_str = json.dumps(asdict(error)) if hasattr(error, "__dataclass_fields__") else str(error)
        self.conn.execute(
            """UPDATE visual_preflight_runs
               SET status='start_uncertain', state_version=state_version+1, error=?, updated_at=?
               WHERE id=? AND state_version=?""",
            (err_str, utc_now_iso(), run_id, expected_version)
        )
        self.conn.commit()

    def record_preflight_error(self, run_id: int, safe_error: Any) -> None:
        import json
        from dataclasses import asdict
        err_str = json.dumps(asdict(safe_error)) if hasattr(safe_error, "__dataclass_fields__") else str(safe_error)
        self.conn.execute(
            "UPDATE visual_preflight_runs SET error=?, updated_at=? WHERE id=?",
            (err_str, utc_now_iso(), run_id)
        )
        self.conn.commit()

    def record_preflight_image_result(
        self, run_id: int, expected_version: int, owner_token: str, image_sha256: str
    ) -> None:
        self.conn.execute(
            """UPDATE visual_preflight_runs
               SET image_sha256=?, image_start_state='completed', state_version=state_version+1, updated_at=?
               WHERE id=? AND state_version=? AND owner_token=?""",
            (image_sha256, utc_now_iso(), run_id, expected_version, owner_token)
        )
        self.conn.commit()

    def record_preflight_video_operation(
        self, run_id: int, expected_version: int, owner_token: str, operation_id: str
    ) -> None:
        self.conn.execute(
            """UPDATE visual_preflight_runs
               SET video_operation_id=?, video_start_state='completed', status='polling', state_version=state_version+1, updated_at=?
               WHERE id=? AND state_version=? AND owner_token=?""",
            (operation_id, utc_now_iso(), run_id, expected_version, owner_token)
        )
        self.conn.commit()
        
    def transition_preflight(
        self, run_id: int, expected_status: str, expected_version: int, status: str, error_code: str | None = None
    ) -> bool:
        changed = self.conn.execute(
            """UPDATE visual_preflight_runs
               SET status=?, state_version=state_version+1, updated_at=?
               WHERE id=? AND status=? AND state_version=?""",
            (status, utc_now_iso(), run_id, expected_status, expected_version)
        ).rowcount
        self.conn.commit()
        return changed == 1


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
            raise VisualApprovalError(
                f"preflight 尚不可審核（status={row['status']}）"
            )
        current = now or datetime.now(UTC)
        passed = image_ok and video_ok
        status = "passed_no_visible_mark" if passed else "failed_visible_mark"
        expires = (current + timedelta(days=valid_days)).isoformat() if passed else None
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
                expires,
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
        current = (now or datetime.now(UTC)).isoformat()
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

    # ── Studio repository methods ──
    #
    # episodes.status is a coarse summary field with a single writer path
    # (the studio app), so it's a plain update like jobs/tracks/videos.
    # episode_assets and studio_tasks have two concurrent actors (the
    # background worker and a human clicking approve/reject over HTTP), so
    # they use the state_version-checked transition pattern already
    # established by transition_preflight/claim_paid_start above.

    def create_episode(self, slug: str, title: str) -> int:
        now = utc_now_iso()
        cur = self.conn.execute(
            "INSERT INTO episodes(slug,title,status,created_at,updated_at) VALUES(?,?,?,?,?)",
            (slug, title, "draft", now, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def episode(self, episode_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM episodes WHERE id=?", (episode_id,)
        ).fetchone()

    def episode_by_slug(self, slug: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM episodes WHERE slug=?", (slug,)
        ).fetchone()

    def list_episodes(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_episode_status(self, episode_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE episodes SET status=?, updated_at=? WHERE id=?",
            (status, utc_now_iso(), episode_id),
        )
        self.conn.commit()

    def create_asset(
        self,
        episode_id: int,
        kind: str,
        role: str,
        *,
        variant_index: int = 0,
        source_prompt: str | None = None,
        source_seed: int | None = None,
        status: str = "queued",
    ) -> int:
        now = utc_now_iso()
        cur = self.conn.execute(
            """
            INSERT INTO episode_assets(
              episode_id,kind,role,variant_index,status,
              source_prompt,source_seed,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (episode_id, kind, role, variant_index, status,
             source_prompt, source_seed, now, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def asset(self, asset_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM episode_assets WHERE id=?", (asset_id,)
        ).fetchone()

    def assets_for_episode(
        self, episode_id: int, *, kind: str | None = None, role: str | None = None
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM episode_assets WHERE episode_id=?"
        params: list[Any] = [episode_id]
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind)
        if role is not None:
            sql += " AND role=?"
            params.append(role)
        sql += " ORDER BY id"
        return self.conn.execute(sql, params).fetchall()

    def transition_asset(
        self,
        asset_id: int,
        *,
        expected_status: str,
        expected_version: int,
        status: str,
        **extra: Any,
    ) -> bool:
        """Optimistically move an asset to a new status.

        Returns False (no exception) if the asset was concurrently modified
        since expected_version -- e.g. the worker finished writing the file
        just as a human clicked reject on the stale "running" view. Callers
        should re-read and decide whether to retry or surface a conflict,
        the same contract review_preflight's callers already rely on.
        """
        allowed_extra = {
            "path", "sha256", "width", "height", "duration_seconds", "fps",
            "comfyui_prompt_id", "source_seed", "error",
        }
        extra_items = [(k, v) for k, v in extra.items() if k in allowed_extra]
        set_prefix = "".join(f"{k}=?," for k, _ in extra_items)
        sql = (
            f"UPDATE episode_assets SET status=?, {set_prefix}"
            "state_version=state_version+1, updated_at=? "
            "WHERE id=? AND status=? AND state_version=?"
        )
        params = (
            [status] + [v for _, v in extra_items]
            + [utc_now_iso(), asset_id, expected_status, expected_version]
        )
        changed = self.conn.execute(sql, params).rowcount
        self.conn.commit()
        return changed == 1

    def enqueue_task(
        self,
        episode_id: int,
        task_type: str,
        *,
        asset_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now_iso()
        cur = self.conn.execute(
            """
            INSERT INTO studio_tasks(episode_id,asset_id,task_type,status,payload_json,created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (episode_id, asset_id, task_type, "queued",
             json.dumps(payload, ensure_ascii=False) if payload is not None else None, now),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def task(self, task_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM studio_tasks WHERE id=?", (task_id,)
        ).fetchone()

    def tasks_for_episode(self, episode_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM studio_tasks WHERE episode_id=? ORDER BY id", (episode_id,)
        ).fetchall()

    def claim_next_task(self) -> sqlite3.Row | None:
        """Atomically claim the oldest queued task for the (single) worker.

        Uses BEGIN IMMEDIATE the same way acquire_side_effect_lease does, so
        two worker threads -- or a test racing the real one -- can never
        double-claim the same row.
        """
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT * FROM studio_tasks WHERE status='queued' ORDER BY id LIMIT 1"
            ).fetchone()
            if row is None:
                self.conn.commit()
                return None
            changed = self.conn.execute(
                "UPDATE studio_tasks SET status='running', started_at=?, attempts=attempts+1 "
                "WHERE id=? AND status='queued'",
                (utc_now_iso(), row["id"]),
            ).rowcount
            self.conn.commit()
            if changed != 1:
                return None
            return self.conn.execute(
                "SELECT * FROM studio_tasks WHERE id=?", (row["id"],)
            ).fetchone()
        except Exception:
            self.conn.rollback()
            raise

    def finish_task(self, task_id: int, *, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE studio_tasks SET status=?, error=?, finished_at=? WHERE id=?",
            (status, error, utc_now_iso(), task_id),
        )
        self.conn.commit()
