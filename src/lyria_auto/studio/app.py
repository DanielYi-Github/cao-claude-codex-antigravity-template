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

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, SecretStr

from ..config import AppConfig
from ..db import StateDB
from ..providers.comfyui import ComfyUIClient
from ..utils import ensure_dir, sha256_file
from .final import metadata_defaults, upload_studio_final
from .music import DEFAULT_MUSIC_PROMPT, generate_pending_tracks
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
_NO_PER_ASSET_REJECT = {
    "keyframe", "loop_preview", "loop", "music_track", "music_mix", "final"
}


class CreateEpisodeRequest(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: str = Field(min_length=1, max_length=160)


class GenerateKeyframesRequest(BaseModel):
    positive_prompt: str | None = None
    negative_prompt: str | None = None
    batch_size: int | None = None


class ImportKeyframeRequest(BaseModel):
    path: str


class GenerateMusicRequest(BaseModel):
    base_prompt: str = Field(default=DEFAULT_MUSIC_PROMPT, min_length=1, max_length=3500)
    api_key: SecretStr | None = None


class RegenerateMusicTrackRequest(BaseModel):
    expected_version: int
    prompt: str = Field(min_length=1, max_length=4000)
    api_key: SecretStr | None = None


class UploadFinalRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=30)
    privacy_status: Literal["private", "unlisted", "public"] = "private"
    confirm_upload: bool = False
    channel_name: str = "main"


class ApproveRequest(BaseModel):
    expected_version: int


class RejectRequest(BaseModel):
    expected_version: int
    reason: str = ""
    new_prompt: str | None = None


def create_app(
    db: StateDB,
    config: AppConfig,
    local_comfyui: ComfyUIClient,
    *,
    production_comfyui: ComfyUIClient | None = None,
    music_runner: Callable[..., dict[str, Any]] = generate_pending_tracks,
    youtube_uploader: Callable[..., dict[str, Any]] = upload_studio_final,
) -> FastAPI:
    app = FastAPI(title="Lyria Studio")
    production_client = production_comfyui or local_comfyui
    workspace_root = ensure_dir(
        config.root / config.section("project").get("workspace", "workspace")
    ).resolve()

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
            # Deliberately does NOT enqueue generate_clip here. Tab 3 is the
            # production compute boundary: auto-advancing on approval would
            # start expensive work before the reviewer reaches the explicit
            # confirmation. Approval only unlocks tab 3; its own "start
            # production" action enqueues both roles (studio-console-v2-plan
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
    def keyframe_defaults() -> dict[str, Any]:
        """What tab 1 pre-fills with before the reviewer has ever clicked
        Generate for an episode -- at that point there's no generate_
        keyframe task yet to read a payload_json off of (the mechanism the
        frontend otherwise uses to show "what actually produced these
        candidates"), so without this the boxes/controls just stay blank
        on a brand-new episode with nothing to edit before the first
        click. Not episode-scoped: these are the same values generate_
        keyframes() below falls back to.
        """
        return {
            "positive_prompt": DEFAULT_KEYFRAME_PROMPT,
            "negative_prompt": KEYFRAME_NEGATIVE_PROMPT,
            "batch_size": config.section("studio").get("keyframe_batch_size", 12),
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
        if body.batch_size is not None and body.batch_size <= 0:
            raise HTTPException(400, f"batch_size must be positive, got {body.batch_size}")
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
            "batch_size": body.batch_size or config.section("studio").get("keyframe_batch_size", 12),
        }
        task_id = db.enqueue_task(episode_id, "generate_keyframe", payload=payload)
        if episode["status"] == "draft":
            db.update_episode_status(episode_id, "in_progress")
        return {"task_id": task_id}

    @app.post("/api/episodes/{episode_id}/keyframes/import")
    def import_keyframe(episode_id: int, body: ImportKeyframeRequest) -> dict[str, Any]:
        """Register an existing local image as a Tab 1 candidate.

        The Studio is intentionally allowed to read only from the configured
        project workspace.  This keeps the local-web endpoint from becoming
        a general-purpose arbitrary-file reader while still covering images
        created in ``workspace/temp`` by the project's generation tools.
        """
        episode = db.episode(episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")

        raw_path = body.path.strip()
        if not raw_path:
            raise HTTPException(400, "path is required")
        try:
            source = Path(raw_path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            raise HTTPException(404, "image file not found") from None
        if not source.is_file():
            raise HTTPException(400, "path must point to an image file")
        if not source.is_relative_to(workspace_root):
            raise HTTPException(
                400,
                f"image must be inside the configured workspace: {workspace_root}",
            )

        try:
            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                width, height = image.size
                image_format = (image.format or "").lower()
        except (UnidentifiedImageError, OSError, ValueError):
            raise HTTPException(400, "the selected file is not a valid image") from None
        if width <= 0 or height <= 0:
            raise HTTPException(400, "the selected image has invalid dimensions")

        existing = db.assets_for_episode(episode_id)
        if any(a["kind"] == "keyframe" and a["status"] == "approved" for a in existing):
            raise HTTPException(
                409,
                "a keyframe has already been approved for this episode -- "
                "importing another image is not supported after approval",
            )
        if any(
            t["task_type"] == "generate_keyframe" and t["status"] in ("queued", "running")
            for t in db.tasks_for_episode(episode_id)
        ):
            raise HTTPException(
                409,
                "a keyframe generation task is queued or running -- stop or "
                "wait for it before importing an image",
            )

        suffix = {
            "jpeg": ".jpg",
            "png": ".png",
            "webp": ".webp",
        }.get(image_format)
        if suffix is None:
            raise HTTPException(400, "supported image formats are PNG, JPEG, and WebP")

        db.supersede_assets(episode_id, "keyframe", "shared")
        variant_index = db.next_variant_index(episode_id, "keyframe", "shared")
        episode_dir = ensure_dir(workspace_root / "studio" / episode["slug"])
        destination = episode_dir / f"keyframe-import-v{variant_index}{suffix}"
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            raise HTTPException(500, f"could not copy the image into Studio: {exc}") from None

        asset_id = db.create_asset(
            episode_id,
            "keyframe",
            "shared",
            variant_index=variant_index,
            source_prompt=f"Imported local image: {source.name}",
        )
        changed = db.transition_asset(
            asset_id,
            expected_status="queued",
            expected_version=0,
            status="awaiting_review",
            path=str(destination),
            sha256=sha256_file(destination),
            width=width,
            height=height,
        )
        if not changed:
            destination.unlink(missing_ok=True)
            raise HTTPException(409, "the imported asset changed before it could be published")
        if episode["status"] == "draft":
            db.update_episode_status(episode_id, "in_progress")
        return dict(db.asset(asset_id))

    @app.post("/api/episodes/{episode_id}/keyframes/cancel")
    def cancel_keyframe_generation(episode_id: int) -> dict[str, Any]:
        """Tab 1's "Stop" button -- lets the reviewer abandon a batch that's
        already running because they spotted a mistake in the prompt,
        instead of waiting the ~3 minutes for it to finish first.

        generate_keyframe always runs on local_comfyui (never remote --
        see build_handlers' docstring), so interrupting that one client is
        always the right target for this task type. Interrupting is what
        actually unblocks the single worker thread: it's currently blocked
        inside wait_for_result()'s poll loop for whatever's running, and
        with nothing telling ComfyUI to stop, the worker would stay stuck
        there -- unable to pick up a fresh Generate click -- until that
        abandoned job eventually finished on its own.
        """
        if db.episode(episode_id) is None:
            raise HTTPException(404, "episode not found")
        local_comfyui.interrupt()
        cancelled = db.cancel_tasks(
            episode_id, {"generate_keyframe"}, error="使用者手動終止生成"
        )
        return {"cancelled": cancelled}

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

    @app.post("/api/episodes/{episode_id}/motion/cancel")
    def cancel_motion_generation(episode_id: int) -> dict[str, Any]:
        """Tab 2's "Stop" button -- same reasoning as cancel_keyframe_
        generation above, scoped to generate_motion_test (always local_
        comfyui too -- see build_handlers' docstring). Deliberately does
        NOT touch build_loop_preview: that stage is pure ffmpeg concat of
        already-downloaded clips, seconds not minutes, so there's nothing
        worth interrupting there and no ComfyUI job to stop it with anyway.

        Cancelling turns the affected motion_test asset(s) from 'running'/
        'queued' to 'failed' -- reject_asset() below accepts 'failed' as
        well as 'awaiting_review' precisely so the reviewer can immediately
        hit that role's Regenerate button with a corrected prompt instead
        of being stuck with a dead placeholder and no way back in.
        """
        if db.episode(episode_id) is None:
            raise HTTPException(404, "episode not found")
        local_comfyui.interrupt()
        cancelled = db.cancel_tasks(
            episode_id, {"generate_motion_test"}, error="使用者手動終止生成"
        )
        return {"cancelled": cancelled}

    @app.post("/api/episodes/{episode_id}/production/start")
    def start_production(episode_id: int) -> dict[str, Any]:
        """Start the reviewed sleep/lookup pair at production resolution.

        Clicking this endpoint is the explicit compute boundary: approving
        the cheap 64-second preview never starts the heavier render by
        itself.  The configured production ComfyUI client may point at the
        local MPS instance or at a remote instance configured before Studio
        starts; no credential is accepted or persisted by this route.
        """
        if db.episode(episode_id) is None:
            raise HTTPException(404, "episode not found")
        if not any(
            a["kind"] == "loop_preview" and a["status"] == "approved"
            for a in db.assets_for_episode(episode_id)
        ):
            raise HTTPException(
                409,
                "the 64-second motion preview must be approved before production starts",
            )

        sources: dict[str, Any] = {}
        for role in ("sleep", "lookup"):
            approved = [
                a
                for a in db.assets_for_episode(episode_id, kind="motion_test", role=role)
                if a["status"] == "approved"
            ]
            if not approved:
                raise HTTPException(409, f"no approved {role} motion test")
            sources[role] = approved[-1]

        active_clips = [
            a
            for a in db.assets_for_episode(episode_id, kind="clip")
            if a["status"] not in ("rejected", "superseded")
        ]
        active_roles = {a["role"] for a in active_clips}
        task_ids: list[int] = []
        asset_ids: list[int] = []
        for role in ("sleep", "lookup"):
            if role in active_roles:
                continue
            source = sources[role]
            asset_id = db.create_asset(
                episode_id,
                "clip",
                role,
                variant_index=db.next_variant_index(episode_id, "clip", role),
                source_prompt=source["source_prompt"],
                source_seed=source["source_seed"],
            )
            task_ids.append(
                db.enqueue_task(episode_id, "generate_clip", asset_id=asset_id)
            )
            asset_ids.append(asset_id)
        if not task_ids:
            raise HTTPException(
                409,
                "production clips already exist -- review them or use Regenerate on a failed result",
            )
        return {"task_ids": task_ids, "asset_ids": asset_ids}

    @app.post("/api/episodes/{episode_id}/production/cancel")
    def cancel_production(episode_id: int) -> dict[str, Any]:
        if db.episode(episode_id) is None:
            raise HTTPException(404, "episode not found")
        production_client.interrupt()
        cancelled = db.cancel_tasks(
            episode_id,
            {"generate_clip", "upscale_clip"},
            error="使用者手動終止正式片段處理",
        )
        return {"cancelled": cancelled}

    @app.get("/api/music/defaults")
    def music_defaults() -> dict[str, Any]:
        return {"base_prompt": DEFAULT_MUSIC_PROMPT, "track_count": 12}

    @app.post("/api/episodes/{episode_id}/music/generate")
    def generate_music(episode_id: int, body: GenerateMusicRequest) -> dict[str, Any]:
        if db.episode(episode_id) is None:
            raise HTTPException(404, "episode not found")
        if not any(
            a["kind"] == "loop" and a["status"] == "approved"
            for a in db.assets_for_episode(episode_id)
        ):
            raise HTTPException(409, "approve the 1080p loop before generating music")
        task_id = db.start_synchronous_task(episode_id, "generate_music_tracks")
        if task_id is None:
            raise HTTPException(409, "music generation is already running for this episode")
        try:
            result = music_runner(
                db,
                config,
                episode_id,
                base_prompt=body.base_prompt,
                api_key=body.api_key.get_secret_value() if body.api_key else None,
            )
            failed_count = len(result.get("failed", []))
            db.finish_task(
                task_id,
                status="failed" if failed_count else "done",
                error=f"{failed_count} music track(s) failed" if failed_count else None,
            )
            return result
        except Exception as exc:  # noqa: BLE001 - request boundary redacts provider errors
            # SecretStr keeps request reprs safe; never echo the supplied
            # value in an HTTP error either, even if an upstream provider
            # reflected it in its own exception text.
            message = str(exc)
            if body.api_key:
                message = message.replace(body.api_key.get_secret_value(), "[REDACTED]")
            db.finish_task(task_id, status="failed", error=message[:800])
            raise HTTPException(400, message[:800]) from None

    @app.post("/api/episodes/{episode_id}/music/tracks/{asset_id}/regenerate")
    def regenerate_music_track(
        episode_id: int, asset_id: int, body: RegenerateMusicTrackRequest
    ) -> dict[str, Any]:
        _asset_in_episode(episode_id, asset_id)
        task_id = db.start_synchronous_task(episode_id, "generate_music_tracks")
        if task_id is None:
            raise HTTPException(409, "music generation is already running for this episode")
        try:
            replacement_id = db.replace_studio_music_track(
                episode_id,
                asset_id,
                expected_version=body.expected_version,
                prompt=body.prompt,
            )
        except ValueError as exc:
            db.finish_task(task_id, status="failed", error=str(exc)[:800])
            raise HTTPException(409, str(exc)) from None
        try:
            result = music_runner(
                db,
                config,
                episode_id,
                base_prompt=body.prompt,
                api_key=body.api_key.get_secret_value() if body.api_key else None,
            )
            failed_count = len(result.get("failed", []))
            db.finish_task(
                task_id,
                status="failed" if failed_count else "done",
                error=f"{failed_count} music track(s) failed" if failed_count else None,
            )
        except Exception as exc:  # noqa: BLE001 - request boundary redacts provider errors
            message = str(exc)
            if body.api_key:
                message = message.replace(body.api_key.get_secret_value(), "[REDACTED]")
            db.finish_task(task_id, status="failed", error=message[:800])
            raise HTTPException(400, message[:800]) from None
        return {"replacement_asset_id": replacement_id, **result}

    @app.post("/api/episodes/{episode_id}/music/build-mix")
    def build_music_mix(episode_id: int) -> dict[str, Any]:
        if db.episode(episode_id) is None:
            raise HTTPException(404, "episode not found")
        approved = [
            a for a in db.assets_for_episode(episode_id, kind="music_track")
            if a["status"] == "approved"
        ]
        slots = {int(a["variant_index"]) for a in approved}
        if slots != set(range(12)):
            raise HTTPException(409, "all 12 music slots must be approved before building the mix")
        existing = db.assets_for_episode(episode_id, kind="music_mix")
        if any(a["status"] in ("queued", "running", "awaiting_review", "approved") for a in existing):
            raise HTTPException(409, "a music mix already exists or is being built")
        if any(
            t["task_type"] == "build_music_mix" and t["status"] in ("queued", "running")
            for t in db.tasks_for_episode(episode_id)
        ):
            raise HTTPException(409, "a music mix task is already queued or running")
        task_id = db.enqueue_task(episode_id, "build_music_mix")
        return {"task_id": task_id}

    @app.post("/api/episodes/{episode_id}/final/render")
    def start_final_render(episode_id: int) -> dict[str, Any]:
        if db.episode(episode_id) is None:
            raise HTTPException(404, "episode not found")
        if not any(
            a["kind"] == "loop" and a["status"] == "approved"
            for a in db.assets_for_episode(episode_id)
        ):
            raise HTTPException(409, "approve the 1080p loop before rendering the final video")
        if not any(
            a["kind"] == "music_mix" and a["status"] == "approved"
            for a in db.assets_for_episode(episode_id)
        ):
            raise HTTPException(409, "approve the music mix before rendering the final video")
        if any(
            a["kind"] == "final" and a["status"] in ("queued", "running", "awaiting_review", "approved")
            for a in db.assets_for_episode(episode_id)
        ):
            raise HTTPException(409, "a final video already exists or is rendering")
        if any(
            t["task_type"] == "render_final" and t["status"] in ("queued", "running")
            for t in db.tasks_for_episode(episode_id)
        ):
            raise HTTPException(409, "a final render is already queued or running")
        return {"task_id": db.enqueue_task(episode_id, "render_final")}

    @app.get("/api/episodes/{episode_id}/final/metadata-defaults")
    def final_metadata_defaults(episode_id: int) -> dict[str, Any]:
        episode = db.episode(episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        minutes = int(config.section("video").get("target_duration_minutes", 120))
        return metadata_defaults(episode["title"], minutes)

    @app.post("/api/episodes/{episode_id}/final/upload-youtube")
    def upload_final_to_youtube(episode_id: int, body: UploadFinalRequest) -> dict[str, Any]:
        if not body.confirm_upload:
            raise HTTPException(400, "explicit upload confirmation is required")
        if not any(
            a["kind"] == "final" and a["status"] == "approved" and a["path"]
            for a in db.assets_for_episode(episode_id)
        ):
            raise HTTPException(409, "approve the final video before uploading")
        try:
            return youtube_uploader(
                db,
                config,
                episode_id,
                title=body.title,
                description=body.description,
                tags=body.tags,
                privacy_status=body.privacy_status,
                channel_name=body.channel_name,
            )
        except Exception as exc:  # noqa: BLE001 - upload adapter errors become safe HTTP errors
            raise HTTPException(400, str(exc)[:800]) from None

    @app.get("/api/episodes/{episode_id}")
    def get_episode(episode_id: int) -> dict[str, Any]:
        episode = db.episode(episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        publication = None
        if episode["music_job_id"] is not None:
            videos = db.videos_for_job(int(episode["music_job_id"]))
            if videos:
                publication = dict(videos[-1])
        return {
            "episode": dict(episode),
            "assets": [dict(a) for a in db.assets_for_episode(episode_id)],
            "tasks": [dict(t) for t in db.tasks_for_episode(episode_id)],
            "publication": publication,
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
        if asset["status"] not in ("awaiting_review", "failed"):
            # 'failed' is here alongside the normal awaiting_review path so
            # a cancelled-mid-generation asset (see cancel_keyframe_
            # generation/cancel_motion_generation above) can be requeued
            # the exact same way as a completed one the reviewer didn't
            # like -- otherwise cancelling would be a dead end with no way
            # back in for that role.
            raise HTTPException(
                409, f"asset is {asset['status']!r}, not awaiting_review or failed"
            )
        ok = db.transition_asset(
            asset_id,
            expected_status=asset["status"],
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
