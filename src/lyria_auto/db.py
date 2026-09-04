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

STUDIO_PRODUCTION_MIGRATION_ID = "0003_studio_production"

# Adds the asset kinds / task types the 5-tab console needs (studio-console-
# v2-plan.md): a low-res 7x-sleep+1x-lookup preview loop distinct from the
# existing 1080p 'loop', 12 per-episode music tracks, and an optional mixed-
# and-extended music_mix asset so the ~1hr mix is independently reviewable
# instead of being recomputed inline during render_final. SQLite can't ALTER
# a CHECK constraint, so this rebuilds both tables (temp copy -> drop ->
# rename) rather than editing STUDIO_SCHEMA_SQL in place -- editing that SQL
# string directly would change its checksum and make _apply_simple_migration
# raise MigrationInvariantError against every database that already applied
# 0002_studio_foundation.
#
# The CHECK constraint only enforces "music_track rows have a track_id and
# nothing else does" -- it cannot express "that track_id's job_id equals
# this episode's music_job_id" (SQLite CHECK can't reference other tables).
# That cross-episode ownership rule belongs in whatever Phase 4 method
# creates music_track assets (studio-console-v2-plan.md), not here.
STUDIO_PRODUCTION_MIGRATION_SQL = """
CREATE TABLE episode_assets_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN (
    'keyframe','motion_test','clip','clip_1080p',
    'loop_preview','loop','music_track','music_mix','final'
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
  track_id INTEGER,
  error TEXT,
  state_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(episode_id) REFERENCES episodes(id),
  FOREIGN KEY(parent_asset_id) REFERENCES episode_assets(id),
  FOREIGN KEY(track_id) REFERENCES tracks(id),
  CHECK(
    (kind = 'music_track' AND track_id IS NOT NULL)
    OR
    (kind <> 'music_track' AND track_id IS NULL)
  )
);
INSERT INTO episode_assets_v3 (
  id, episode_id, kind, role, variant_index, status, path, sha256,
  width, height, duration_seconds, fps, source_prompt, source_seed,
  comfyui_prompt_id, parent_asset_id, track_id, error, state_version,
  created_at, updated_at
)
SELECT
  id, episode_id, kind, role, variant_index, status, path, sha256,
  width, height, duration_seconds, fps, source_prompt, source_seed,
  comfyui_prompt_id, parent_asset_id, NULL, error, state_version,
  created_at, updated_at
FROM episode_assets;
DROP TABLE episode_assets;
ALTER TABLE episode_assets_v3 RENAME TO episode_assets;
CREATE INDEX IF NOT EXISTS idx_episode_assets_episode
ON episode_assets(episode_id, kind, role);

CREATE TABLE studio_tasks_v3 (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  episode_id INTEGER NOT NULL,
  asset_id INTEGER,
  task_type TEXT NOT NULL CHECK(task_type IN (
    'generate_keyframe','generate_motion_test','generate_clip',
    'upscale_clip','build_loop_preview','build_loop',
    'generate_music_tracks','build_music_mix','render_final'
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
INSERT INTO studio_tasks_v3 (
  id, episode_id, asset_id, task_type, status, payload_json,
  attempts, error, created_at, started_at, finished_at
)
SELECT
  id, episode_id, asset_id, task_type, status, payload_json,
  attempts, error, created_at, started_at, finished_at
FROM studio_tasks;
DROP TABLE studio_tasks;
ALTER TABLE studio_tasks_v3 RENAME TO studio_tasks;
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

# studio_tasks.task_type values whose handler (src/lyria_auto/studio/
# stages.py) creates its own episode_assets row(s) from scratch instead of
# filling in a caller-supplied placeholder -- enqueue_task() rejects a
# non-null asset_id for these, see the guard below. render_final has no
# HTTP-reachable enqueue path yet (Phase 5), but belongs in this set for
# the same reason as the other three -- codex_reviewer's Phase 2 review
# reproduced enqueue_task(..., "render_final", asset_id=...) being silently
# accepted, leaving that placeholder stuck at "running" once claimed.
_SELF_CREATING_TASK_TYPES = {
    "generate_keyframe", "build_loop_preview", "build_loop", "build_music_mix", "render_final",
}


class StateDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Declared FKs (episode_assets.episode_id/parent_asset_id/track_id,
        # studio_tasks.episode_id/asset_id, tracks.job_id, ...) were pure
        # documentation until now -- SQLite does not enforce them unless this
        # pragma is set, and it must be set outside any transaction to take
        # effect, so this has to happen before executescript(SCHEMA) opens
        # one. codex_reviewer verified this was previously a no-op (a
        # music_track row with a nonexistent track_id inserted successfully).
        self.conn.execute("PRAGMA foreign_keys=ON")
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
        # Must run after 0002: rebuilds the two tables 0002 just created (or
        # confirmed exist) to widen their CHECK constraints. Safe to run
        # against either a brand-new empty database or one with real rows --
        # it's a copy-then-rename, not an in-place ALTER.
        self._apply_simple_migration(
            STUDIO_PRODUCTION_MIGRATION_ID,
            STUDIO_PRODUCTION_MIGRATION_SQL,
            rebuilds_referenced_tables=True,
        )

    def _apply_simple_migration(
        self, migration_id: str, sql: str, *, rebuilds_referenced_tables: bool = False
    ) -> None:
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
        # executescript() implicitly commits before running, and under the
        # default (legacy) isolation mode each DDL statement inside it also
        # implicitly commits whatever came before -- so a failure partway
        # through a multi-statement rebuild (e.g. 0003's drop-and-rename)
        # cannot be rolled back by the except branch below; it would leave
        # the database in a half-migrated state needing manual repair.
        # autocommit=False (Python 3.12+) disables that per-statement
        # implicit commit, so plain execute() calls -- DDL included --
        # participate in one real transaction that commit()/rollback() can
        # act on, the same way running these statements via the sqlite3 CLI
        # inside an explicit BEGIN/COMMIT would.
        #
        # rebuilds_referenced_tables=True (0003 needs this, 0002 doesn't):
        # DROP TABLE on a table another live table still FK-references fails
        # with foreign_keys=ON (verified empirically -- studio_tasks.asset_id
        # -> episode_assets(id) blocks dropping the old episode_assets).
        # PRAGMA foreign_keys is also a documented no-op while a transaction
        # is open, so it must be toggled OFF before autocommit=False starts
        # one, and back ON only after that transaction ends -- not inside
        # the try block below. PRAGMA foreign_key_check has no such
        # restriction (it's a plain read of current state, not an
        # enforcement toggle) and runs from *inside* the transaction, before
        # the schema_migrations insert and commit: codex_reviewer flagged
        # that running it after commit (as an earlier version of this code
        # did) meant a discovered orphan would raise, but 0003 would already
        # be recorded as applied -- a future startup would then skip
        # re-running it and silently leave the orphan unresolved forever.
        # Checking first means a real orphan aborts the whole transaction,
        # exactly like any other migration failure.
        if rebuilds_referenced_tables:
            self.conn.execute("PRAGMA foreign_keys=OFF")
        previous_autocommit = self.conn.autocommit
        self.conn.autocommit = False
        try:
            for statement in sql.split(";"):
                statement = statement.strip()
                if statement:
                    self.conn.execute(statement)
            if rebuilds_referenced_tables:
                orphans = self.conn.execute("PRAGMA foreign_key_check").fetchall()
                if orphans:
                    raise MigrationInvariantError(
                        f"Migration {migration_id} would leave {len(orphans)} "
                        f"orphaned foreign-key reference(s): "
                        f"{[tuple(row) for row in orphans]}"
                    )
            self.conn.execute(
                "INSERT INTO schema_migrations(id, checksum, applied_at) VALUES(?,?,?)",
                (migration_id, checksum, utc_now_iso()),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.conn.autocommit = previous_autocommit
            # autocommit=False keeps a transaction continuously open --
            # commit() (or rollback()) ends one and immediately starts the
            # next rather than leaving the connection idle -- so
            # in_transaction is still True here even right after a commit.
            # PRAGMA foreign_keys is a silent no-op inside any open
            # transaction, so without this the PRAGMA below would appear to
            # succeed but leave enforcement off (found empirically:
            # restoring autocommit alone was not enough, foreign_keys stayed
            # 0 after an explicit "=ON").
            if self.conn.in_transaction:
                self.conn.commit()
            if rebuilds_referenced_tables:
                self.conn.execute("PRAGMA foreign_keys=ON")

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

    def track(self, track_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM tracks WHERE id=?", (track_id,)
        ).fetchone()

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

    def videos_for_job(self, job_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM videos WHERE job_id=? ORDER BY id", (job_id,)
        ).fetchall()

    def resumable_job(self, job_id: int | None = None) -> dict[str, Any] | None:
        if job_id is not None:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE id=? AND NOT EXISTS ("
                "SELECT 1 FROM episodes WHERE episodes.music_job_id=jobs.id)",
                (job_id,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE dry_run=0 AND status NOT IN ('complete','dry_run_complete') "
                "AND NOT EXISTS (SELECT 1 FROM episodes WHERE episodes.music_job_id=jobs.id) "
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

    def supersede_assets(self, episode_id: int, kind: str, role: str) -> int:
        """Mark every non-terminal asset of this kind/role as superseded.

        Used before a fresh batch (e.g. 12 new keyframe candidates) replaces
        whatever candidates existed before, so stale awaiting_review rows
        don't linger in the UI next to the new batch. Already-terminal rows
        (approved/rejected/failed/already-superseded) are left alone -- in
        particular this is safe to call right after approving one candidate
        from a batch, since the approved row's status is no longer one of
        the ones matched here.
        """
        now = utc_now_iso()
        cur = self.conn.execute(
            """
            UPDATE episode_assets
            SET status='superseded', updated_at=?, state_version=state_version+1
            WHERE episode_id=? AND kind=? AND role=?
              AND status IN ('queued','running','ready','awaiting_review')
            """,
            (now, episode_id, kind, role),
        )
        self.conn.commit()
        return cur.rowcount

    def next_variant_index(self, episode_id: int, kind: str, role: str) -> int:
        """The next variant_index for a fresh batch, continuing past every
        attempt ever made (including superseded/rejected ones) rather than
        resetting to 0 -- a reset would let two batches collide on the same
        (episode, kind, role, variant_index) row identity (the index isn't
        DB-unique) and, worse, on the same output filename on disk, so a
        superseded row's artifact would silently start serving the new
        batch's image instead of the one it was actually reviewed with.

        NOT atomic across independent connections: this read and the
        later create_asset() writes are separate statements, not one
        transaction. Safe today only because cli.py starts exactly one
        StudioWorker thread that serializes handler execution end to end
        (see worker.py's module docstring) -- two worker PROCESSES reading
        this concurrently would both compute the same MAX+1 and collide.
        If a second worker process is ever added, this needs a reservation
        table or an atomic UPDATE...RETURNING-style counter instead.
        """
        row = self.conn.execute(
            "SELECT MAX(variant_index) AS m FROM episode_assets "
            "WHERE episode_id=? AND kind=? AND role=?",
            (episode_id, kind, role),
        ).fetchone()
        return (row["m"] + 1) if row["m"] is not None else 0

    def reserve_studio_music_job(self, episode_id: int, prompts: list[str]) -> int:
        """Atomically reserve one music job and its ordered track slots.

        The episode foreign key is the duplicate-generation guard: once a
        music job is attached, every retry reuses it instead of silently
        creating another paid 12-track batch.
        """
        if not prompts:
            raise ValueError("at least one music prompt is required")
        now = utc_now_iso()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            episode = self.conn.execute(
                "SELECT * FROM episodes WHERE id=?", (episode_id,)
            ).fetchone()
            if episode is None:
                raise ValueError(f"episode not found: {episode_id}")
            if episode["music_job_id"] is not None:
                self.conn.commit()
                return int(episode["music_job_id"])

            job_payload = json.dumps(
                {"origin": "studio", "track_count": len(prompts)}, ensure_ascii=False
            )
            job_id = int(
                self.conn.execute(
                    "INSERT INTO jobs(created_at,updated_at,status,channel,dry_run,payload_json) "
                    "VALUES(?,?,?,'studio',0,?)",
                    (now, now, "generating_audio", job_payload),
                ).lastrowid
            )
            changed = self.conn.execute(
                "UPDATE episodes SET music_job_id=?, updated_at=?, state_version=state_version+1 "
                "WHERE id=? AND music_job_id IS NULL",
                (job_id, now, episode_id),
            ).rowcount
            if changed != 1:
                raise RuntimeError("episode music job was reserved concurrently")

            for slot, prompt in enumerate(prompts):
                signature = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:20]
                track_id = int(
                    self.conn.execute(
                        "INSERT INTO tracks(job_id,prompt,signature,status,created_at) "
                        "VALUES(?,?,?,'planned',?)",
                        (job_id, prompt, signature, now),
                    ).lastrowid
                )
                self.conn.execute(
                    "INSERT INTO episode_assets("
                    "episode_id,kind,role,variant_index,track_id,status,source_prompt,created_at,updated_at"
                    ") VALUES(?,'music_track','shared',?,?,'queued',?,?,?)",
                    (episode_id, slot, track_id, prompt, now, now),
                )
            self.conn.commit()
            return job_id
        except Exception:
            self.conn.rollback()
            raise

    def publish_studio_music_track(
        self,
        episode_id: int,
        asset_id: int,
        *,
        path: str,
        duration_seconds: float,
        sha256: str,
    ) -> None:
        """Atomically publish the track row and its review asset."""
        now = utc_now_iso()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT a.*, e.music_job_id, t.job_id AS track_job_id "
                "FROM episode_assets a JOIN episodes e ON e.id=a.episode_id "
                "JOIN tracks t ON t.id=a.track_id WHERE a.id=? AND a.episode_id=?",
                (asset_id, episode_id),
            ).fetchone()
            if (
                row is None
                or row["kind"] != "music_track"
                or row["status"] != "running"
                or row["track_job_id"] != row["music_job_id"]
            ):
                raise ValueError("music asset no longer belongs to this episode/job or is not running")
            self.conn.execute(
                "UPDATE tracks SET audio_path=?,duration_seconds=?,sha256=?,status='ready',error=NULL "
                "WHERE id=?",
                (path, duration_seconds, sha256, row["track_id"]),
            )
            self.conn.execute(
                "UPDATE episode_assets SET status='awaiting_review',path=?,duration_seconds=?,sha256=?,"
                "error=NULL,state_version=state_version+1,updated_at=? WHERE id=?",
                (path, duration_seconds, sha256, now, asset_id),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def fail_studio_music_track(self, episode_id: int, asset_id: int, error: str) -> None:
        now = utc_now_iso()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute(
                "SELECT track_id,status FROM episode_assets WHERE id=? AND episode_id=? "
                "AND kind='music_track'",
                (asset_id, episode_id),
            ).fetchone()
            if row is None:
                raise ValueError("music asset not found")
            self.conn.execute(
                "UPDATE tracks SET status='failed',error=? WHERE id=?",
                (error, row["track_id"]),
            )
            self.conn.execute(
                "UPDATE episode_assets SET status='failed',error=?,state_version=state_version+1,"
                "updated_at=? WHERE id=?",
                (error, now, asset_id),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def replace_studio_music_track(
        self,
        episode_id: int,
        asset_id: int,
        *,
        expected_version: int,
        prompt: str,
    ) -> int:
        """Reject one reviewed track and reserve a replacement in its slot."""
        now = utc_now_iso()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            old = self.conn.execute(
                "SELECT a.*,e.music_job_id,t.job_id AS track_job_id FROM episode_assets a "
                "JOIN episodes e ON e.id=a.episode_id JOIN tracks t ON t.id=a.track_id "
                "WHERE a.id=? AND a.episode_id=? AND a.kind='music_track'",
                (asset_id, episode_id),
            ).fetchone()
            if (
                old is None
                or old["status"] not in ("awaiting_review", "failed")
                or old["state_version"] != expected_version
                or old["track_job_id"] != old["music_job_id"]
            ):
                raise ValueError("music asset changed or is not replaceable")
            changed = self.conn.execute(
                "UPDATE episode_assets SET status='rejected',state_version=state_version+1,updated_at=? "
                "WHERE id=? AND state_version=?",
                (now, asset_id, expected_version),
            ).rowcount
            if changed != 1:
                raise ValueError("music asset changed")
            signature = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:20]
            track_id = int(
                self.conn.execute(
                    "INSERT INTO tracks(job_id,prompt,signature,status,created_at) "
                    "VALUES(?,?,?,'planned',?)",
                    (old["music_job_id"], prompt, signature, now),
                ).lastrowid
            )
            replacement_id = int(
                self.conn.execute(
                    "INSERT INTO episode_assets("
                    "episode_id,kind,role,variant_index,track_id,status,source_prompt,created_at,updated_at"
                    ") VALUES(?,'music_track','shared',?,?,'queued',?,?,?)",
                    (episode_id, old["variant_index"], track_id, prompt, now, now),
                ).lastrowid
            )
            self.conn.execute(
                "UPDATE jobs SET status='generating_audio',error=NULL,updated_at=? WHERE id=?",
                (now, old["music_job_id"]),
            )
            self.conn.commit()
            return replacement_id
        except Exception:
            self.conn.rollback()
            raise

    def enqueue_task(
        self,
        episode_id: int,
        task_type: str,
        *,
        asset_id: int | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        if task_type in _SELF_CREATING_TASK_TYPES and asset_id is not None:
            # These handlers all create their own asset row(s) from scratch
            # (see stages.py) and never read task["asset_id"] -- a non-null
            # asset_id here would create a placeholder row the handler can
            # never fill in, stuck at "running" forever once claimed.
            raise ValueError(
                f"{task_type} tasks don't take asset_id -- "
                "the handler creates its own asset row(s)"
            )
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

    def start_synchronous_task(self, episode_id: int, task_type: str) -> int | None:
        """Atomically claim a request-scoped task without storing secrets.

        Music generation cannot use the background queue because its API key
        must not outlive the HTTP request. This running-only row is a durable
        duplicate guard and progress marker; it never contains a payload.
        """
        now = utc_now_iso()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            in_flight = self.conn.execute(
                "SELECT id FROM studio_tasks WHERE episode_id=? AND task_type=? "
                "AND status IN ('queued','running') LIMIT 1",
                (episode_id, task_type),
            ).fetchone()
            if in_flight is not None:
                self.conn.commit()
                return None
            task_id = int(
                self.conn.execute(
                    "INSERT INTO studio_tasks("
                    "episode_id,task_type,status,attempts,created_at,started_at"
                    ") VALUES(?,?,'running',1,?,?)",
                    (episode_id, task_type, now, now),
                ).lastrowid
            )
            self.conn.commit()
            return task_id
        except Exception:
            self.conn.rollback()
            raise

    def task(self, task_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM studio_tasks WHERE id=?", (task_id,)
        ).fetchone()

    def tasks_for_episode(self, episode_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM studio_tasks WHERE episode_id=? ORDER BY id", (episode_id,)
        ).fetchall()

    def tasks_by_status(self, status: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM studio_tasks WHERE status=? ORDER BY id", (status,)
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

    def cancel_tasks(self, episode_id: int, task_types: set[str], error: str) -> int:
        """Fail every queued/running task of the given types for this
        episode, plus each one's linked asset if it has one -- for user-
        initiated cancellation (a "stop" button), not crash recovery.

        Deliberately handles 'queued' as well as 'running': unlike
        recover_stale_tasks() (which only ever touches 'running' tasks left
        over from a crashed process, since nothing in a freshly-starting
        process could legitimately still be running), a queued task here
        was never claimed, so its linked asset -- if any -- is still
        'queued' too, not 'running'. The caller is responsible for actually
        interrupting whatever's executing on ComfyUI first; this only
        updates state, it has no way to stop a running generation itself.
        """
        cancelled = 0
        for task in self.tasks_for_episode(episode_id):
            if task["task_type"] not in task_types or task["status"] not in ("queued", "running"):
                continue
            self.finish_task(task["id"], status="failed", error=error)
            if task["asset_id"] is not None:
                asset = self.asset(task["asset_id"])
                if asset is not None and asset["status"] in ("queued", "running"):
                    self.transition_asset(
                        task["asset_id"],
                        expected_status=asset["status"],
                        expected_version=asset["state_version"],
                        status="failed",
                        error=error,
                    )
            cancelled += 1
        return cancelled
