from __future__ import annotations

import sqlite3

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
    asset_id = db.create_asset(episode_id, "keyframe", "shared")

    task_id = db.enqueue_task(
        episode_id, "generate_keyframe", asset_id=asset_id, payload={"seed": 42}
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
