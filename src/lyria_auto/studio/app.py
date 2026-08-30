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
from .stages import DEFAULT_KEYFRAME_PROMPT, KEYFRAME_NEGATIVE_PROMPT

WEB_ROOT = Path(__file__).resolve().parent / "web"

# episode_assets.kind -> the studio_tasks.task_type that produces the next
# kind in the pipeline, and the kind each stage feeds into once approved.
_TASK_TYPE_BY_KIND = {
    "keyframe": "generate_keyframe",
    "motion_test": "generate_motion_test",
    "clip": "generate_clip",
    "clip_1080p": "upscale_clip",
    "loop_preview": "build_loop_preview",
    "loop": "build_loop",
    "final": "render_final",
}
# motion_test deliberately has no entry: approving a sleep/lookup clip no
# longer auto-advances to clip generation (see the loop_preview branch in
# _continue_after_approval below for why -- studio-console-v2-plan.md 7.1/7.8).
_NEXT_KIND = {
    "clip": "clip_1080p",
}
# Kinds whose task handler creates its own asset row from scratch (no
# caller-supplied asset_id) instead of filling in a pre-created placeholder
# -- generic reject's "create a replacement asset, hand its id to a
# re-enqueued task" contract doesn't fit any of these: the handler would
# just create ANOTHER asset and ignore the replacement, leaving it stuck at
# running forever (codex_reviewer design consult, studio-console-v2-plan.md
# 7.8 -- flagged for the new loop_preview, but loop/final already had the
# same shape and were already unreachable through this path for the same
# reason).
_NO_PER_ASSET_REJECT = {"keyframe", "loop_preview", "loop", "final"}


class CreateEpisodeRequest(BaseModel):
    slug: str
    title: str


class GenerateKeyframesRequest(BaseModel):
    positive_prompt: str | None = None
    negative_prompt: str | None = None


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
            # The other candidates from this same batch are still sitting
            # at awaiting_review -- picking one supersedes the rest so the
            # UI doesn't keep showing 11 stale candidates next to the one
            # that's now moving through the pipeline.
            db.supersede_assets(episode_id, "keyframe", "shared")
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

        if kind == "loop_preview":
            # Deliberately does NOT enqueue generate_clip here. clip and
            # upscale_clip share one remote ComfyUI client (stages.py's
            # clip_comfyui), and tab 3 (not built yet) is what collects the
            # cloud credential that client needs -- auto-advancing on
            # approval would fire a request that needs a token before the
            # reviewer ever reaches the tab that provides one. Approving
            # this asset just unlocks tab 3's UI; its own "start cloud
            # processing" action is what enqueues generate_clip for both
            # roles (codex_reviewer design consult, studio-console-v2-plan.md
            # 7.1/7.8).
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
        return dict(db.episode(episode_id))

    @app.get("/api/keyframes/defaults")
    def keyframe_defaults() -> dict[str, str]:
        """What tab 1's prompt boxes pre-fill with before the reviewer has
        ever clicked Generate for an episode -- at that point there's no
        generate_keyframe task yet to read a payload_json off of (the
        mechanism the frontend otherwise uses to show "what actually
        produced these candidates"), so without this the boxes just stay
        blank on a brand-new episode with nothing to edit before the first
        click. Not episode-scoped: these are the same two module-level
        constants generate_keyframes() below falls back to.
        """
        return {
            "positive_prompt": DEFAULT_KEYFRAME_PROMPT,
            "negative_prompt": KEYFRAME_NEGATIVE_PROMPT,
        }

    @app.post("/api/episodes/{episode_id}/keyframes/generate")
    def generate_keyframes(episode_id: int, body: GenerateKeyframesRequest) -> dict[str, Any]:
        """Tab 1's "Generate" button -- also what "regenerate all" calls.

        One task_type, called however many times the reviewer wants to
        retry the prompt; each call supersedes whatever candidates are
        still awaiting_review from a previous call before enqueuing a fresh
        batch, so the grid always reflects only the latest attempt.
        """
        episode = db.episode(episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        existing = db.assets_for_episode(episode_id)
        if any(a["kind"] == "keyframe" and a["status"] == "approved" for a in existing):
            # Without this, "Regenerate All" after a pick would supersede
            # nothing (approved isn't in supersede_assets's non-terminal
            # set), leaving the approved row AND a fresh awaiting_review
            # batch both live -- approving again then produces two approved
            # keyframes and double motion-test fan-out.
            raise HTTPException(
                409,
                "a keyframe has already been approved for this episode -- "
                "regenerating the batch is not supported after approval",
            )
        pending = db.tasks_for_episode(episode_id)
        if any(
            t["task_type"] == "generate_keyframe" and t["status"] in ("queued", "running")
            for t in pending
        ):
            # Without this, a second "regenerate" click while the first
            # batch is still running supersedes today's candidates, but the
            # in-flight task doesn't know it was superseded -- it publishes
            # its own batch as awaiting_review afterward, so both batches
            # end up selectable side by side.
            raise HTTPException(
                409,
                "a keyframe generation task is already queued or running "
                "for this episode -- wait for it to finish before "
                "regenerating again",
            )
        db.supersede_assets(episode_id, "keyframe", "shared")
        # Always resolve to the concrete prompt actually used, even when the
        # caller left a field blank -- otherwise payload_json stays null for
        # a default-prompt batch and there'd be no way for the frontend to
        # show "what generated these candidates" or pre-fill the prompt
        # boxes for editing.
        payload: dict[str, Any] = {
            "positive_prompt": body.positive_prompt or DEFAULT_KEYFRAME_PROMPT,
            "negative_prompt": body.negative_prompt or KEYFRAME_NEGATIVE_PROMPT,
        }
        task_id = db.enqueue_task(episode_id, "generate_keyframe", payload=payload)
        if episode["status"] == "draft":
            db.update_episode_status(episode_id, "in_progress")
        return {"task_id": task_id}

    @app.post("/api/episodes/{episode_id}/motion/assemble-preview")
    def assemble_motion_preview(episode_id: int) -> dict[str, Any]:
        """Tab 2's "Assemble 64s Preview" button.

        Binds to whichever sleep/lookup motion_test assets are approved
        *right now* by writing their ids into the enqueued task's
        payload_json. build_loop_preview's handler re-validates against
        exactly these ids instead of re-resolving "whatever's newest
        approved" when it actually runs, so the built preview always
        matches what the reviewer saw at the moment they clicked -- not
        whatever happened to be approved by the time the worker got to it
        (codex_reviewer design consult, studio-console-v2-plan.md 7.8).
        """
        episode = db.episode(episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        sources: dict[str, Any] = {}
        for role in ("sleep", "lookup"):
            candidates = [
                a for a in db.assets_for_episode(episode_id, kind="motion_test", role=role)
                if a["status"] == "approved"
            ]
            if not candidates:
                raise HTTPException(
                    409,
                    f"no approved {role} motion test yet -- both sleep and "
                    "lookup need to be approved before assembling a preview",
                )
            sources[role] = candidates[-1]
        existing = db.assets_for_episode(episode_id)
        if any(
            a["kind"] == "loop_preview" and a["status"] in ("awaiting_review", "approved")
            for a in existing
        ):
            # Unlike keyframe's 12-candidate grid, a preview has no
            # "supersede and pick a different one" UX -- it's a single
            # confirm-then-move-on gate, and every build writes the same
            # fixed loop_preview-shared-v0.mp4 path (no per-build variant
            # index). Without blocking on awaiting_review too (not just
            # approved), a second assemble call after the first one
            # finished -- but before anyone reviewed it -- would pass both
            # guards, overwrite that file out from under the first result,
            # and leave two episode_assets rows pointing at one path whose
            # content only matches whichever build ran last (codex_reviewer
            # Phase 2 review, reproduced with a real second POST).
            raise HTTPException(
                409,
                "a 64s preview already exists for this episode -- approve "
                "it before assembling another one",
            )
        pending = db.tasks_for_episode(episode_id)
        if any(
            t["task_type"] == "build_loop_preview" and t["status"] in ("queued", "running")
            for t in pending
        ):
            raise HTTPException(
                409,
                "a preview assembly task is already queued or running for "
                "this episode -- wait for it to finish before trying again",
            )
        payload = {"source_asset_ids": {role: asset["id"] for role, asset in sources.items()}}
        task_id = db.enqueue_task(episode_id, "build_loop_preview", payload=payload)
        return {"task_id": task_id}

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
        if asset["kind"] == "keyframe":
            # generate_keyframe publishes its batch one row at a time, so a
            # candidate can go awaiting_review before its siblings finish
            # publishing (codex_reviewer reproduced: approve candidate 0,
            # then 1/2 publish afterward -- approved + 2 more awaiting_review,
            # plus motion tasks already fanned out from the premature
            # approval). Block approval entirely while the batch's own task
            # is still generating, same guard shape as the regenerate 409.
            in_flight = any(
                t["task_type"] == "generate_keyframe" and t["status"] in ("queued", "running")
                for t in db.tasks_for_episode(episode_id)
            )
            if in_flight:
                raise HTTPException(
                    409,
                    "this episode's keyframe batch is still generating -- "
                    "wait for it to finish before picking a candidate",
                )
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
        if asset["kind"] in _NO_PER_ASSET_REJECT:
            # These handlers all create their own asset row from scratch
            # instead of filling in the one this reject would create and
            # hand them via asset_id -- they'd ignore it and build yet
            # another row, leaving the reject's replacement stuck at
            # 'queued'/'running' forever while its task shows 'done'. Each
            # of these kinds needs its own whole-batch-or-whole-stage
            # regenerate action instead (tab 1's .../keyframes/generate,
            # tab 2's .../motion/assemble-preview, etc.).
            raise HTTPException(
                400,
                f"{asset['kind']} assets don't support per-asset reject -- "
                "use this stage's own regenerate/reassemble action instead",
            )
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
