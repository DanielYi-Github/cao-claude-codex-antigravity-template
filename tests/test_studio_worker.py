from __future__ import annotations

import threading
import time

from lyria_auto.db import StateDB
from lyria_auto.studio.worker import StudioWorker


def _episode_with_asset(db, *, kind="motion_test", role="sleep"):
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    asset_id = db.create_asset(episode_id, kind, role)
    return episode_id, asset_id


def test_run_once_returns_false_when_queue_is_empty(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    worker = StudioWorker(db, handlers={})

    assert worker.run_once() is False


def test_run_once_marks_asset_running_then_lets_handler_finish_it(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id, asset_id = _episode_with_asset(db)
    task_id = db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)

    seen_status_when_handler_ran = {}

    def fake_generate_motion_test(db, task):
        seen_status_when_handler_ran["status"] = db.asset(task["asset_id"])["status"]
        db.transition_asset(
            task["asset_id"],
            expected_status="running",
            expected_version=db.asset(task["asset_id"])["state_version"],
            status="awaiting_review",
            path="/workspace/chowchow-001/keyframe.png",
            sha256="deadbeef",
        )

    worker = StudioWorker(db, handlers={"generate_motion_test": fake_generate_motion_test})

    handled = worker.run_once()

    assert handled is True
    assert seen_status_when_handler_ran["status"] == "running"
    assert db.task(task_id)["status"] == "done"
    asset = db.asset(asset_id)
    assert asset["status"] == "awaiting_review"
    assert asset["path"] == "/workspace/chowchow-001/keyframe.png"
    assert asset["sha256"] == "deadbeef"


def test_handler_exception_fails_task_and_asset_with_the_error_message(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id, asset_id = _episode_with_asset(db, kind="clip", role="sleep")
    task_id = db.enqueue_task(episode_id, "generate_clip", asset_id=asset_id)

    def broken_handler(db, task):
        raise RuntimeError("ComfyUI unreachable")

    worker = StudioWorker(db, handlers={"generate_clip": broken_handler})
    worker.run_once()

    assert db.task(task_id)["status"] == "failed"
    assert db.task(task_id)["error"] == "ComfyUI unreachable"
    asset = db.asset(asset_id)
    assert asset["status"] == "failed"
    assert asset["error"] == "ComfyUI unreachable"


def test_unknown_task_type_fails_cleanly_without_a_handler(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id, asset_id = _episode_with_asset(db)
    task_id = db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)

    worker = StudioWorker(db, handlers={})  # no handler registered
    worker.run_once()

    task = db.task(task_id)
    assert task["status"] == "failed"
    assert "no handler registered" in task["error"]
    assert db.asset(asset_id)["status"] == "failed"


def test_task_without_an_asset_is_handled_without_crashing(tmp_path):
    """build_loop / render_final tasks may not map to a single asset row."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "松獅犬第一集")
    task_id = db.enqueue_task(episode_id, "build_loop")

    calls = []

    def fake_build_loop(db, task):
        calls.append(task["id"])

    worker = StudioWorker(db, handlers={"build_loop": fake_build_loop})
    worker.run_once()

    assert calls == [task_id]
    assert db.task(task_id)["status"] == "done"


def test_run_once_processes_tasks_in_fifo_order_across_multiple_calls(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id, asset_id = _episode_with_asset(db)
    db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)
    clip_asset = db.create_asset(episode_id, "clip", "sleep")
    db.enqueue_task(episode_id, "generate_clip", asset_id=clip_asset)

    order = []

    def record(name):
        def handler(db, task):
            order.append(name)
        return handler

    worker = StudioWorker(
        db,
        handlers={
            "generate_motion_test": record("motion_test"),
            "generate_clip": record("clip"),
        },
    )

    while worker.run_once():
        pass

    assert order == ["motion_test", "clip"]


def test_start_stop_runs_a_real_background_thread(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id, asset_id = _episode_with_asset(db)
    task_id = db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)

    threading_event = threading.Event()

    def fake_handler(db, task):
        db.transition_asset(
            task["asset_id"],
            expected_status="running",
            expected_version=db.asset(task["asset_id"])["state_version"],
            status="awaiting_review",
        )
        threading_event.set()

    worker = StudioWorker(
        db, handlers={"generate_motion_test": fake_handler}, poll_interval_seconds=0.05
    )
    worker.start()
    try:
        finished = threading_event.wait(timeout=5)
        assert finished, "worker thread never processed the queued task"
        # Give the worker loop a moment to write finish_task after the handler returns.
        for _ in range(50):
            if db.task(task_id)["status"] == "done":
                break
            time.sleep(0.02)
        assert db.task(task_id)["status"] == "done"
    finally:
        worker.stop()


def test_recover_stale_tasks_fails_tasks_left_running_by_a_crashed_process(tmp_path):
    """Regression for a real gap: app.py's keyframe regenerate/approve
    guards 409 while a generate_keyframe task is queued/running for the
    episode. Without recovery, a crashed process (e.g. ComfyUI OOM) leaves
    a task stuck at 'running' forever -- the episode could never be
    regenerated or approved again."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id, asset_id = _episode_with_asset(db)
    task_id = db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)
    db.claim_next_task()  # simulate the crashed process having claimed the task
    db.transition_asset(
        asset_id, expected_status="queued", expected_version=0, status="running",
    )  # ... and having flipped the asset to running, same as _execute() does
    assert db.task(task_id)["status"] == "running"
    assert db.asset(asset_id)["status"] == "running"

    worker = StudioWorker(db, handlers={})
    recovered = worker.recover_stale_tasks()

    assert recovered == 1
    assert db.task(task_id)["status"] == "failed"
    assert db.asset(asset_id)["status"] == "failed"


def test_start_recovers_stale_tasks_before_launching_the_thread(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id, asset_id = _episode_with_asset(db)
    task_id = db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)
    db.claim_next_task()

    worker = StudioWorker(db, handlers={}, poll_interval_seconds=0.05)
    worker.start()
    try:
        assert db.task(task_id)["status"] == "failed"
    finally:
        worker.stop()
