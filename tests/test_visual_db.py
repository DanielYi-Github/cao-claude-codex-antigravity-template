from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lyria_auto.db import StateDB
from lyria_auto.errors import MigrationInvariantError, VisualApprovalError
from lyria_auto.visual_models import MediaSnapshot, VisualSource


def source() -> VisualSource:
    return VisualSource(
        provider="gemini-developer-api",
        image_model="gemini-3.1-flash-image",
        video_model="veo-3.1-fast-generate-preview",
        credential_fingerprint="fingerprint-a",
        sdk_version="2.13.0",
    )


def finish_preflight_in_fixture(db, run_id, image_sha256, video_sha256):
    db.conn.execute(
        """
        UPDATE visual_preflight_runs
        SET image_sha256=?,video_sha256=?,status='awaiting_review',
            owner_token='test-token',updated_at=?
        WHERE id=?
        """,
        (image_sha256, video_sha256, datetime.now(UTC).isoformat(), run_id),
    )
    db.conn.commit()


def review_preflight_in_fixture(db, run_id, image_ok, video_ok, now=None):
    current = now or datetime.now(UTC)
    image_snap = MediaSnapshot(
        path=Path("/tmp/img.png"),
        sha256="img-sha",
        size_bytes=1024,
        probe={},
    )
    video_snap = MediaSnapshot(
        path=Path("/tmp/vid.mp4"),
        sha256="vid-sha",
        size_bytes=2048,
        probe={},
    )
    db.review_preflight(
        run_id,
        image_ok=image_ok,
        video_ok=video_ok,
        expected_status="awaiting_review",
        expected_version=0,
        image_snapshot=image_snap,
        video_snapshot=video_snap,
        now=current,
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


def test_migration_rejects_unknown_preexisting_status_without_rewriting_it(tmp_path):
    legacy_path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(legacy_path)
    conn.executescript(
        """
        CREATE TABLE jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          status TEXT NOT NULL,
          channel TEXT,
          dry_run INTEGER NOT NULL DEFAULT 0,
          error TEXT,
          payload_json TEXT
        );
        INSERT INTO jobs(created_at,updated_at,status) VALUES('now','now','legacy_custom');
        CREATE TABLE tracks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL,
          prompt TEXT NOT NULL,
          signature TEXT NOT NULL,
          audio_path TEXT,
          duration_seconds REAL,
          sha256 TEXT,
          status TEXT NOT NULL,
          error TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE videos (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          video_path TEXT,
          thumbnail_path TEXT,
          youtube_video_id TEXT,
          publish_at TEXT,
          status TEXT NOT NULL,
          error TEXT,
          created_at TEXT NOT NULL
        );
        CREATE TABLE events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER,
          level TEXT NOT NULL,
          event_type TEXT NOT NULL,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE schema_migrations (
          id TEXT PRIMARY KEY,
          checksum TEXT NOT NULL,
          applied_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    with pytest.raises(MigrationInvariantError, match=r"jobs row .* unknown status"):
        StateDB(legacy_path)

    raw = sqlite3.connect(legacy_path)
    try:
        assert raw.execute("SELECT status FROM jobs").fetchone()[0] == "legacy_custom"
    finally:
        raw.close()


def test_asset_identity_is_unique_even_when_scene_is_null(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        now = datetime.now(UTC).isoformat()
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
            "reserved",
            now,
            now,
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
        run_id = db.create_preflight_run(
            source(), "/tmp/raw.png", "/tmp/raw.mp4", 1.061, {}
        )
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
        now = datetime.now(UTC)
        run_id = db.create_preflight_run(
            source(), "/tmp/raw.png", "/tmp/raw.mp4", 1.061, {}
        )
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

def test_update_asset_status_parameter_order(tmp_path):
    from lyria_auto.visual_repository import VisualRepository
    db = StateDB(tmp_path / 'state.sqlite3')
    try:
        repo = VisualRepository(db)
        job_id = db.create_job('main', False, {})
        asset_id = repo.reserve_asset(job_id, None, 'scene_image', 0, 'gemini', 'model', 'prompt', 0.1, {})
        repo.update_asset_status(asset_id, 'starting', expected_version=0, owner_token='xyz')
        asset = repo.asset(asset_id)
        assert asset['owner_token'] == 'xyz'
        assert asset['state_version'] == 1
    finally:
        db.close()

def test_subprocess_crash_recovery_for_leases(tmp_path):
    import subprocess
    import sys

    from lyria_auto.db import StateDB
    
    db_path = tmp_path / 'state.sqlite3'
    db = StateDB(db_path)
    db.close()
    
    # Process A acquires a lease and immediately exits with os._exit(1)
    code_a = f"""
import os, sys
from lyria_auto.db import StateDB
db = StateDB('{db_path}')
lease = db.acquire_side_effect_lease('job', 1, 60)
assert lease.acquired
os._exit(1)
"""
    proc_a = subprocess.run([sys.executable, '-c', code_a], capture_output=True, text=True, check=False)
    assert proc_a.returncode == 1
    
    # Process B should NOT be able to acquire the lease since it hasn't expired yet,
    # despite Process A crashing without closing the SQLite connection
    code_b = f"""
import sys
from lyria_auto.db import StateDB
db = StateDB('{db_path}')
lease = db.acquire_side_effect_lease('job', 1, 60)
assert not lease.acquired
"""
    proc_b = subprocess.run([sys.executable, '-c', code_b], capture_output=True, text=True, check=False)
    assert proc_b.returncode == 0
    
    # Let's forcefully expire the lease in Process C to test recovery
    code_c = f"""
import sys
from lyria_auto.db import StateDB
db = StateDB('{db_path}')
db.conn.execute("UPDATE side_effect_leases SET expires_at='2000-01-01T00:00:00+00:00'")
db.conn.commit()
lease = db.acquire_side_effect_lease('job', 1, 60)
assert lease.acquired
"""
    proc_c = subprocess.run([sys.executable, '-c', code_c], capture_output=True, text=True, check=False)
    assert proc_c.returncode == 0
