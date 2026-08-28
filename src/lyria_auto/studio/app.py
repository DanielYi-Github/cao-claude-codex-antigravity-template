"""FastAPI app for the studio review console.

Routes are intentionally thin: all state lives in StateDB, and this module
owns the review *policy* -- what "approved" unlocks, what "rejected"
requeues -- since that's a decision a human is actively driving over HTTP,
not generation-pipeline mechanics (that belongs in worker.py's handlers).

The pipeline auto-continues after every approval so the whole thing runs
unattended between human checkpoints, but it always stops and waits at the
next 'awaiting_review' asset -- it never skips a gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..db import StateDB

WEB_ROOT = Path(__file__).resolve().parent / "web"

# episode_assets.kind -> the studio_tasks.task_type that produces the next
# kind in the pipeline, and the kind each stage feeds into once approved.
_TASK_TYPE_BY_KIND = {
    "keyframe": "generate_keyframe",
    "motion_test": "generate_motion_test",
    "clip": "generate_clip",
    "clip_1080p": "upscale_clip",
    "loop": "build_loop",
    "final": "render_final",
}
_NEXT_KIND = {
    "motion_test": "clip",
    "clip": "clip_1080p",
}


class CreateEpisodeRequest(BaseModel):
    slug: str
    title: str


class ApproveRequest(BaseModel):
    expected_version: int


class RejectRequest(BaseModel):
    expected_version: int
    reason: str = ""
    new_prompt: str | None = None


def create_app(db: StateDB) -> FastAPI:
    app = FastAPI(title="Lyria Studio")

    def _asset_in_episode(episode_id: int, asset_id: int) -> Any:
        asset = db.asset(asset_id)
        if asset is None or asset["episode_id"] != episode_id:
            raise HTTPException(404, "asset not found in this episode")
        return asset

    def _continue_after_approval(asset: Any) -> None:
        episode_id, kind = asset["episode_id"], asset["kind"]

        if kind == "keyframe":
            # Fan out: the one shared keyframe unlocks both motion-test roles.
            for role in ("sleep", "lookup"):
                new_id = db.create_asset(episode_id, "motion_test", role)
                db.enqueue_task(episode_id, "generate_motion_test", asset_id=new_id)
            return

        if kind in _NEXT_KIND:
            new_kind = _NEXT_KIND[kind]
            new_id = db.create_asset(
                episode_id, new_kind, asset["role"],
                source_prompt=asset["source_prompt"], source_seed=asset["source_seed"],
            )
            db.enqueue_task(episode_id, _TASK_TYPE_BY_KIND[new_kind], asset_id=new_id)
            return

        if kind == "clip_1080p":
            # Fan in: build_loop needs both sleep and lookup approved first.
            siblings = db.assets_for_episode(episode_id, kind="clip_1080p")
            approved_roles = {a["role"] for a in siblings if a["status"] == "approved"}
            if {"sleep", "lookup"} <= approved_roles:
                db.enqueue_task(episode_id, "build_loop")
            return

        if kind == "final":
            db.update_episode_status(episode_id, "complete")
            return

        # kind == "loop": the macro-loop feeds into the music pipeline
        # (existing tracks/jobs tables), a later stage of this rollout --
        # see studio-architecture-plan.md Part 六. Nothing to auto-continue yet.

    @app.get("/api/episodes")
    def list_episodes() -> list[dict[str, Any]]:
        return db.list_episodes()

    @app.post("/api/episodes")
    def create_episode(body: CreateEpisodeRequest) -> dict[str, Any]:
        if db.episode_by_slug(body.slug) is not None:
            raise HTTPException(409, f"episode slug already exists: {body.slug}")
        episode_id = db.create_episode(body.slug, body.title)
        keyframe_id = db.create_asset(episode_id, "keyframe", "shared")
        db.enqueue_task(episode_id, "generate_keyframe", asset_id=keyframe_id)
        db.update_episode_status(episode_id, "in_progress")
        return dict(db.episode(episode_id))

    @app.get("/api/episodes/{episode_id}")
    def get_episode(episode_id: int) -> dict[str, Any]:
        episode = db.episode(episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        return {
            "episode": dict(episode),
            "assets": [dict(a) for a in db.assets_for_episode(episode_id)],
            "tasks": [dict(t) for t in db.tasks_for_episode(episode_id)],
        }

    @app.post("/api/episodes/{episode_id}/assets/{asset_id}/approve")
    def approve_asset(episode_id: int, asset_id: int, body: ApproveRequest) -> dict[str, Any]:
        asset = _asset_in_episode(episode_id, asset_id)
        if asset["status"] != "awaiting_review":
            raise HTTPException(409, f"asset is {asset['status']!r}, not awaiting_review")
        ok = db.transition_asset(
            asset_id,
            expected_status="awaiting_review",
            expected_version=body.expected_version,
            status="approved",
        )
        if not ok:
            raise HTTPException(409, "asset changed since you loaded it — reload and try again")
        approved = db.asset(asset_id)
        _continue_after_approval(approved)
        return dict(approved)

    @app.post("/api/episodes/{episode_id}/assets/{asset_id}/reject")
    def reject_asset(episode_id: int, asset_id: int, body: RejectRequest) -> dict[str, Any]:
        asset = _asset_in_episode(episode_id, asset_id)
        if asset["status"] != "awaiting_review":
            raise HTTPException(409, f"asset is {asset['status']!r}, not awaiting_review")
        ok = db.transition_asset(
            asset_id,
            expected_status="awaiting_review",
            expected_version=body.expected_version,
            status="rejected",
            error=body.reason or None,
        )
        if not ok:
            raise HTTPException(409, "asset changed since you loaded it — reload and try again")
        replacement_id = db.create_asset(
            episode_id,
            asset["kind"],
            asset["role"],
            variant_index=asset["variant_index"] + 1,
            source_prompt=body.new_prompt if body.new_prompt is not None else asset["source_prompt"],
        )
        db.enqueue_task(
            episode_id, _TASK_TYPE_BY_KIND[asset["kind"]], asset_id=replacement_id
        )
        return {
            "rejected": dict(db.asset(asset_id)),
            "requeued": dict(db.asset(replacement_id)),
        }

    @app.get("/api/artifact/{asset_id}")
    def artifact(asset_id: int) -> FileResponse:
        asset = db.asset(asset_id)
        if asset is None or not asset["path"]:
            raise HTTPException(404, "no artifact for this asset yet")
        path = Path(asset["path"])
        if not path.is_file():
            raise HTTPException(404, f"artifact file missing on disk: {path}")
        return FileResponse(path)

    # Mounted last: FastAPI matches routes in registration order and
    # StaticFiles(html=True) would otherwise shadow anything under it.
    app.mount("/", StaticFiles(directory=WEB_ROOT, html=True), name="web")

    return app
