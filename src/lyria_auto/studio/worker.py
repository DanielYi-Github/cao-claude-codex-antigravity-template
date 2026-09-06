"""Single-threaded background worker for the studio pipeline.

One thread, one task at a time, deliberately: ComfyUI's resident models and a
multi-hour ffmpeg render are not something this machine can run concurrently
with itself (see studio-architecture-plan.md). All state lives in the
database, so stopping the worker -- or the whole process -- never loses
progress; whatever was mid-flight just sits as a 'running' row until the
worker restarts and either finishes it or a human notices and requeues it.

Handlers are injected rather than hard-imported, so the entire queue can be
driven end-to-end in tests with fakes -- no ComfyUI, no ffmpeg, no cost.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Protocol

from ..db import StateDB

logger = logging.getLogger(__name__)

# Poll task types whose asset may hold an operation the user has already
# been charged for -- see StudioWorker._resume_paid_poll.
_RESUMABLE_POLL_TASK_TYPES = {"poll_veo_motion", "poll_veo_clip"}


class TaskHandler(Protocol):
    def __call__(self, db: StateDB, task: Any) -> None:
        """Do the work for one task and transition its asset itself.

        A handler that finishes without raising is treated as success (the
        worker marks the task 'done'); the handler is responsible for moving
        its associated asset to whatever terminal-ish status is appropriate
        (e.g. 'awaiting_review') and attaching the concrete result fields
        (path, sha256, duration_seconds, ...) via StateDB.transition_asset,
        since only the handler knows what those values are.
        """


class StudioWorker:
    def __init__(
        self,
        db: StateDB,
        handlers: dict[str, TaskHandler],
        *,
        poll_interval_seconds: float = 2.0,
    ) -> None:
        self._db = db
        self._handlers = handlers
        self._poll_interval = poll_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.recover_stale_tasks()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def recover_stale_tasks(self) -> int:
        """Fail any task left at 'running' by a previous process.

        Safe to assume orphaned rather than "still running elsewhere"
        specifically because of this class's one-thread-one-task invariant
        (see module docstring): if THIS process is starting up, nothing in
        it could still be mid-handler on an already-running task. Without
        this, a process crash (e.g. ComfyUI OOM) leaves the task stuck at
        'running' forever, which as of studio-console-v2-plan.md Phase 1
        also permanently 409s app.py's keyframe regenerate/approve guards
        for that episode -- there'd be no way to ever try again.
        """
        stale = self._db.tasks_by_status("running")
        for task in stale:
            if self._resume_paid_poll(task):
                continue
            self._fail(task, "worker restarted while this task was still running -- retry")
        return len(stale)

    def _resume_paid_poll(self, task: Any) -> bool:
        """Re-queue an interrupted Veo poll instead of writing it off.

        A poll_veo_* task whose asset already carries an operation_id
        describes a generation Google has ALREADY billed for. Failing it
        the way every other stale task is failed would throw away work
        the user paid for, when the operation is still sitting on
        Google's side waiting to be collected -- so this finishes the
        dead attempt and enqueues a fresh poll against the same
        operation, leaving the asset untouched at 'running'.

        Deliberately does NOT cover start_veo_* : a start interrupted
        mid-flight may or may not have been billed, and re-running it
        could charge twice. Those still fail loudly for a human to
        reconcile, which is the same contract PaidStartUncertainError
        already sets elsewhere.
        """
        if task["task_type"] not in _RESUMABLE_POLL_TASK_TYPES:
            return False
        if task["asset_id"] is None:
            return False
        asset = self._db.asset(task["asset_id"])
        if asset is None or not asset["operation_id"]:
            return False
        self._db.finish_task(task["id"], status="done")
        payload = json.loads(task["payload_json"]) if task["payload_json"] else None
        self._db.enqueue_task(
            task["episode_id"],
            task["task_type"],
            asset_id=task["asset_id"],
            payload=payload,
        )
        logger.info(
            "resumed paid Veo poll for asset %s (operation already billed)",
            task["asset_id"],
        )
        return True

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.run_once():
                self._stop.wait(self._poll_interval)

    def run_once(self) -> bool:
        """Claim and run a single queued task, if one exists.

        Synchronous and side-effect-complete when it returns, so tests can
        drive the whole queue deterministically with
        `while worker.run_once(): pass` instead of racing a real thread.
        """
        task = self._db.claim_next_task()
        if task is None:
            return False
        self._execute(task)
        return True

    def _execute(self, task: Any) -> None:
        if task["asset_id"] is not None:
            asset = self._db.asset(task["asset_id"])
            if asset is not None and asset["status"] == "queued":
                self._db.transition_asset(
                    task["asset_id"],
                    expected_status="queued",
                    expected_version=asset["state_version"],
                    status="running",
                )

        handler = self._handlers.get(task["task_type"])
        if handler is None:
            self._fail(task, f"no handler registered for task_type={task['task_type']!r}")
            return

        try:
            handler(self._db, task)
        except Exception as exc:
            logger.exception("studio task %s (%s) failed", task["id"], task["task_type"])
            self._fail(task, str(exc))
            return

        self._db.finish_task(task["id"], status="done")

    def _fail(self, task: Any, error: str) -> None:
        self._db.finish_task(task["id"], status="failed", error=error)
        if task["asset_id"] is None:
            return
        asset = self._db.asset(task["asset_id"])
        if asset is not None and asset["status"] == "running":
            self._db.transition_asset(
                task["asset_id"],
                expected_status="running",
                expected_version=asset["state_version"],
                status="failed",
                error=error,
            )
