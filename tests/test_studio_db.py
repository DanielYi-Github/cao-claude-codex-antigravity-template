from __future__ import annotations

import hashlib
import sqlite3

import pytest

from lyria_auto.db import StateDB


def test_studio_tables_exist(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    tables = {
        row[0]
        for row in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"episodes", "episode_assets", "studio_tasks"} <= tables


def test_create_and_read_episode(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")

    row = db.episode(episode_id)
    assert row["slug"] == "chowchow-001"
    assert row["title"] == "松獅犬第一集"
    assert row["status"] == "draft"

    assert db.episode_by_slug("chowchow-001")["id"] == episode_id
    assert db.episode_by_slug("does-not-exist") is None


def test_episode_slug_must_be_unique(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    db.create_episode("dup", "第一次")
    try:
        db.create_episode("dup", "第二次")
    except sqlite3.IntegrityError as exc:
        assert "UNIQUE" in str(exc)
    else:
        raise AssertionError("expected a uniqueness violation on slug")


def test_list_episodes_orders_newest_first(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    first = db.create_episode("a", "A")
    second = db.create_episode("b", "B")

    listed = db.list_episodes()
    assert [row["id"] for row in listed] == [second, first]


def test_update_episode_status(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")

    db.update_episode_status(episode_id, "in_progress")

    assert db.episode(episode_id)["status"] == "in_progress"


def test_create_and_filter_assets(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")

    keyframe_id = db.create_asset(episode_id, "keyframe", "shared")
    sleep_id = db.create_asset(
        episode_id, "clip", "sleep", source_prompt="sleeping, tail wagging"
    )
    lookup_id = db.create_asset(episode_id, "clip", "lookup")

    all_assets = db.assets_for_episode(episode_id)
    assert [row["id"] for row in all_assets] == [keyframe_id, sleep_id, lookup_id]

    clips_only = db.assets_for_episode(episode_id, kind="clip")
    assert {row["id"] for row in clips_only} == {sleep_id, lookup_id}

    sleep_only = db.assets_for_episode(episode_id, kind="clip", role="sleep")
    assert [row["id"] for row in sleep_only] == [sleep_id]

    fetched = db.asset(sleep_id)
    assert fetched["status"] == "queued"
    assert fetched["source_prompt"] == "sleeping, tail wagging"
    assert fetched["state_version"] == 0


def test_transition_asset_moves_status_and_bumps_version(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    asset_id = db.create_asset(episode_id, "clip", "sleep")

    ok = db.transition_asset(
        asset_id,
        expected_status="queued",
        expected_version=0,
        status="ready",
        path="/workspace/chowchow-001/clips/sleep.mp4",
        duration_seconds=8.0,
    )

    assert ok is True
    row = db.asset(asset_id)
    assert row["status"] == "ready"
    assert row["path"] == "/workspace/chowchow-001/clips/sleep.mp4"
    assert row["duration_seconds"] == 8.0
    assert row["state_version"] == 1


def test_transition_asset_rejects_stale_version(tmp_path):
    """A worker writing 'ready' after a human already rejected must not win."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    asset_id = db.create_asset(episode_id, "clip", "sleep")

    # Human rejects first, based on version 0.
    first = db.transition_asset(
        asset_id, expected_status="queued", expected_version=0, status="rejected"
    )
    assert first is True

    # Worker's in-flight write still thinks it's version 0 -> must lose.
    second = db.transition_asset(
        asset_id, expected_status="queued", expected_version=0, status="ready"
    )
    assert second is False
    assert db.asset(asset_id)["status"] == "rejected"


def test_transition_asset_ignores_unknown_extra_fields(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    asset_id = db.create_asset(episode_id, "clip", "sleep")

    ok = db.transition_asset(
        asset_id,
        expected_status="queued",
        expected_version=0,
        status="ready",
        episode_id=999,  # not in the extra-fields allowlist, must be dropped
    )

    assert ok is True
    assert db.asset(asset_id)["episode_id"] == episode_id


def test_enqueue_and_claim_task(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    asset_id = db.create_asset(episode_id, "motion_test", "sleep")

    task_id = db.enqueue_task(
        episode_id, "generate_motion_test", asset_id=asset_id, payload={"seed": 42}
    )

    assert db.task(task_id)["status"] == "queued"

    claimed = db.claim_next_task()
    assert claimed["id"] == task_id
    assert claimed["status"] == "running"
    assert claimed["attempts"] == 1
    assert claimed["started_at"] is not None
    assert claimed["payload_json"] == '{"seed": 42}'

    # Nothing else queued.
    assert db.claim_next_task() is None


def test_enqueue_task_rejects_asset_id_for_generate_keyframe(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    asset_id = db.create_asset(episode_id, "keyframe", "shared")

    with pytest.raises(ValueError, match="asset_id"):
        db.enqueue_task(episode_id, "generate_keyframe", asset_id=asset_id)


@pytest.mark.parametrize("task_type,kind", [
    ("build_loop_preview", "loop_preview"),
    ("build_loop", "loop"),
    ("render_final", "final"),
])
def test_enqueue_task_rejects_asset_id_for_self_creating_task_types(tmp_path, task_type, kind):
    """build_loop_preview/build_loop/render_final create their own asset
    from scratch, same shape as generate_keyframe -- see stages.py. codex_
    reviewer's Phase 2 review reproduced render_final's omission from this
    set letting a placeholder get stuck at 'running' forever."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    asset_id = db.create_asset(episode_id, kind, "shared")

    with pytest.raises(ValueError, match="asset_id"):
        db.enqueue_task(episode_id, task_type, asset_id=asset_id)


def test_claim_next_task_is_fifo_and_does_not_double_claim(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    first = db.enqueue_task(episode_id, "generate_keyframe")
    second = db.enqueue_task(episode_id, "generate_motion_test")

    claimed_first = db.claim_next_task()
    claimed_second = db.claim_next_task()

    assert claimed_first["id"] == first
    assert claimed_second["id"] == second
    assert db.claim_next_task() is None


def test_finish_task_records_status_and_error(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    task_id = db.enqueue_task(episode_id, "generate_keyframe")
    db.claim_next_task()

    db.finish_task(task_id, status="failed", error="ComfyUI unreachable")

    row = db.task(task_id)
    assert row["status"] == "failed"
    assert row["error"] == "ComfyUI unreachable"
    assert row["finished_at"] is not None


def test_tasks_for_episode_orders_by_id(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    first = db.enqueue_task(episode_id, "generate_keyframe")
    second = db.enqueue_task(episode_id, "generate_motion_test")

    assert [row["id"] for row in db.tasks_for_episode(episode_id)] == [first, second]


# --- 0003_studio_production (studio-console-v2-plan.md) ---------------------


def test_migration_0003_adds_new_kinds_and_task_types(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")

    now = "2026-08-30T00:00:00+00:00"
    db.conn.execute(
        """
        INSERT INTO episode_assets(episode_id,kind,role,status,created_at,updated_at)
        VALUES(?,'loop_preview','shared','queued',?,?)
        """,
        (episode_id, now, now),
    )
    db.conn.commit()

    db.enqueue_task(episode_id, "build_loop_preview")
    db.enqueue_task(episode_id, "generate_music_tracks")
    db.enqueue_task(episode_id, "build_music_mix")

    kinds = {row["kind"] for row in db.assets_for_episode(episode_id)}
    assert "loop_preview" in kinds
    task_types = {row["task_type"] for row in db.tasks_for_episode(episode_id)}
    assert {"build_loop_preview", "generate_music_tracks", "build_music_mix"} <= task_types


def test_music_track_asset_requires_track_id(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    now = "2026-08-30T00:00:00+00:00"

    try:
        db.conn.execute(
            """
            INSERT INTO episode_assets(episode_id,kind,role,status,created_at,updated_at)
            VALUES(?,'music_track','shared','queued',?,?)
            """,
            (episode_id, now, now),
        )
    except sqlite3.IntegrityError as exc:
        assert "CHECK" in str(exc)
    else:
        raise AssertionError("expected a CHECK violation for music_track without track_id")

    job_id = db.conn.execute(
        "INSERT INTO jobs(created_at,updated_at,status) VALUES(?,?,'created')",
        (now, now),
    ).lastrowid
    track_id = db.conn.execute(
        """
        INSERT INTO tracks(job_id,prompt,signature,status,created_at)
        VALUES(?,'rainy cafe lofi','sig-1','queued',?)
        """,
        (job_id, now),
    ).lastrowid
    db.conn.execute(
        """
        INSERT INTO episode_assets(episode_id,kind,role,variant_index,track_id,status,created_at,updated_at)
        VALUES(?,'music_track','shared',0,?,'queued',?,?)
        """,
        (episode_id, track_id, now, now),
    )
    db.conn.commit()

    music_assets = db.assets_for_episode(episode_id, kind="music_track")
    assert len(music_assets) == 1
    assert music_assets[0]["track_id"] == track_id


def test_migration_0003_preserves_rows_from_0002_only_database(tmp_path):
    from lyria_auto.db import (
        SCHEMA,
        STUDIO_SCHEMA_MIGRATION_ID,
        STUDIO_SCHEMA_SQL,
        VISUAL_SCHEMA_MIGRATION_ID,
        VISUAL_SCHEMA_SQL,
    )

    db_path = tmp_path / "state.sqlite3"
    now = "2026-08-30T00:00:00+00:00"

    # Hand-build a database with only the OLD (narrow-CHECK) schema applied,
    # to exercise the real 0002-only -> 0003 upgrade path rather than one
    # that was already widened by a prior StateDB() construction.
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    raw.executescript(SCHEMA)
    raw.executescript(VISUAL_SCHEMA_SQL)
    raw.executescript(STUDIO_SCHEMA_SQL)
    raw.executescript(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  id TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL"
        ");"
    )
    for migration_id, sql in (
        (VISUAL_SCHEMA_MIGRATION_ID, VISUAL_SCHEMA_SQL),
        (STUDIO_SCHEMA_MIGRATION_ID, STUDIO_SCHEMA_SQL),
    ):
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        raw.execute(
            "INSERT INTO schema_migrations(id, checksum, applied_at) VALUES(?,?,?)",
            (migration_id, checksum, now),
        )
    episode_id = raw.execute(
        "INSERT INTO episodes(slug,title,status,created_at,updated_at) "
        "VALUES('chowchow-001','松獅犬第一集','draft',?,?)",
        (now, now),
    ).lastrowid
    keyframe_id = raw.execute(
        "INSERT INTO episode_assets(episode_id,kind,role,status,created_at,updated_at) "
        "VALUES(?,'keyframe','shared','queued',?,?)",
        (episode_id, now, now),
    ).lastrowid
    task_id = raw.execute(
        "INSERT INTO studio_tasks(episode_id,asset_id,task_type,status,created_at) "
        "VALUES(?,?,'generate_keyframe','queued',?)",
        (episode_id, keyframe_id, now),
    ).lastrowid
    raw.commit()
    raw.close()

    # Confirm the old narrow CHECK really is in effect before upgrading --
    # otherwise this test would pass even if 0003 never ran.
    pre_upgrade = sqlite3.connect(db_path)
    try:
        pre_upgrade.execute(
            "INSERT INTO episode_assets(episode_id,kind,role,status,created_at,updated_at) "
            "VALUES(?,'loop_preview','shared','queued',?,?)",
            (episode_id, now, now),
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("fixture database should still have the OLD narrow CHECK")
    pre_upgrade.close()

    upgraded = StateDB(db_path)
    applied = {row["id"] for row in upgraded.conn.execute("SELECT id FROM schema_migrations")}
    assert "0003_studio_production" in applied

    # This upgrade path (0002 already applied, only 0003 runs fresh) is the
    # one that actually exercises the rebuilds_referenced_tables PRAGMA
    # toggle -- a fresh database where 0002 and 0003 both run in the same
    # StateDB() call did not reproduce a real bug found here empirically:
    # autocommit=False keeps a transaction continuously open across
    # commit(), so restoring autocommit and then "PRAGMA foreign_keys=ON"
    # was a silent no-op (still inside that phantom open transaction) --
    # foreign_keys stayed OFF after upgrading a real pre-existing database.
    assert upgraded.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert upgraded.conn.execute("PRAGMA foreign_key_check").fetchall() == []

    asset = upgraded.asset(keyframe_id)
    assert asset is not None
    assert asset["kind"] == "keyframe"
    assert asset["track_id"] is None

    task = upgraded.conn.execute(
        "SELECT * FROM studio_tasks WHERE id=?", (task_id,)
    ).fetchone()
    assert task["task_type"] == "generate_keyframe"

    # And the new kind is now actually accepted post-upgrade.
    upgraded.conn.execute(
        "INSERT INTO episode_assets(episode_id,kind,role,status,created_at,updated_at) "
        "VALUES(?,'loop_preview','shared','queued',?,?)",
        (episode_id, now, now),
    )
    upgraded.conn.commit()


def test_foreign_keys_are_actually_enforced(tmp_path):
    """codex_reviewer found PRAGMA foreign_keys was never turned on -- a
    music_track with a nonexistent track_id inserted successfully. This
    proves the fix, not just that nothing regressed."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    now = "2026-08-30T00:00:00+00:00"

    assert db.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    try:
        db.conn.execute(
            """
            INSERT INTO episode_assets(episode_id,kind,role,variant_index,track_id,status,created_at,updated_at)
            VALUES(?,'music_track','shared',0,999999,'queued',?,?)
            """,
            (episode_id, now, now),
        )
    except sqlite3.IntegrityError as exc:
        assert "FOREIGN KEY" in str(exc)
    else:
        raise AssertionError("expected a FOREIGN KEY violation for a nonexistent track_id")


def test_migration_failure_rolls_back_atomically(tmp_path, monkeypatch):
    """codex_reviewer found executescript()'s implicit per-statement commits
    meant a mid-rebuild failure left episode_assets_v3 present and
    episode_assets dropped, with no way back. Simulate a failure partway
    through 0003 and confirm the database is left exactly as it was before
    the migration attempt -- not half-migrated."""
    import lyria_auto.db as db_module

    db_path = tmp_path / "state.sqlite3"
    db = StateDB(db_path)
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    keyframe_id = db.create_asset(episode_id, "keyframe", "shared")
    db.conn.close()

    # Force a failure partway through 0003 (after episode_assets_v3 is
    # created and populated, before the rename) by poisoning its own SQL
    # with a statement that will fail.
    broken_sql = db_module.STUDIO_PRODUCTION_MIGRATION_SQL.replace(
        "DROP TABLE episode_assets;",
        "DROP TABLE episode_assets;\nSELECT * FROM this_table_does_not_exist;",
        1,
    )
    monkeypatch.setattr(db_module, "STUDIO_PRODUCTION_MIGRATION_SQL", broken_sql)

    raw = sqlite3.connect(db_path)
    raw.execute("DELETE FROM schema_migrations WHERE id='0003_studio_production'")
    raw.commit()
    raw.close()

    try:
        StateDB(db_path)
    except Exception:
        pass
    else:
        raise AssertionError("expected the poisoned migration to raise")

    # The database must be left exactly as it was pre-migration: the
    # original episode_assets table intact with its old narrow CHECK (not
    # dropped, not half-renamed), no stray episode_assets_v3, and the
    # migration NOT recorded as applied.
    check = sqlite3.connect(db_path)
    tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "episode_assets" in tables
    assert "episode_assets_v3" not in tables
    assert check.execute(
        "SELECT COUNT(*) FROM episode_assets WHERE id=?", (keyframe_id,)
    ).fetchone()[0] == 1
    applied = {r[0] for r in check.execute("SELECT id FROM schema_migrations")}
    assert "0003_studio_production" not in applied
    check.close()


def test_cancel_tasks_fails_a_running_task_and_its_running_asset(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    asset_id = db.create_asset(episode_id, "motion_test", "sleep")
    task_id = db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)
    db.claim_next_task()
    db.transition_asset(asset_id, expected_status="queued", expected_version=0, status="running")

    cancelled = db.cancel_tasks(episode_id, {"generate_motion_test"}, error="使用者手動終止生成")

    assert cancelled == 1
    assert db.task(task_id)["status"] == "failed"
    assert db.task(task_id)["error"] == "使用者手動終止生成"
    assert db.asset(asset_id)["status"] == "failed"
    assert db.asset(asset_id)["error"] == "使用者手動終止生成"


def test_cancel_tasks_fails_a_queued_task_and_its_queued_asset(tmp_path):
    """Unlike recover_stale_tasks (crash recovery, only ever touches
    'running'), a user-initiated cancel must also handle a task that was
    never claimed -- its asset is still 'queued', not 'running'."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    asset_id = db.create_asset(episode_id, "motion_test", "lookup")
    task_id = db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)

    cancelled = db.cancel_tasks(episode_id, {"generate_motion_test"}, error="stop")

    assert cancelled == 1
    assert db.task(task_id)["status"] == "failed"
    assert db.asset(asset_id)["status"] == "failed"


def test_cancel_tasks_handles_a_self_creating_task_with_no_asset_id(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    task_id = db.enqueue_task(episode_id, "generate_keyframe")
    db.claim_next_task()

    cancelled = db.cancel_tasks(episode_id, {"generate_keyframe"}, error="stop")

    assert cancelled == 1
    assert db.task(task_id)["status"] == "failed"


def test_cancel_tasks_ignores_other_task_types_and_already_finished_tasks(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    keyframe_task = db.enqueue_task(episode_id, "generate_keyframe")
    motion_asset = db.create_asset(episode_id, "motion_test", "sleep")
    motion_task = db.enqueue_task(episode_id, "generate_motion_test", asset_id=motion_asset)
    db.finish_task(motion_task, status="done")

    cancelled = db.cancel_tasks(episode_id, {"generate_motion_test"}, error="stop")

    assert cancelled == 0
    assert db.task(keyframe_task)["status"] == "queued", "wrong task_type, must not touch it"
    assert db.task(motion_task)["status"] == "done", "already finished, must not touch it"


def test_cancel_tasks_returns_zero_when_nothing_matches(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")

    assert db.cancel_tasks(episode_id, {"generate_keyframe"}, error="stop") == 0
