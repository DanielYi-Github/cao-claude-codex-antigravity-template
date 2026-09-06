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

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import AppConfig
from ..db import StateDB
from ..providers.comfyui import ComfyUIClient
from . import veo as veo_support
from .scene_composer import (
    PRESET_MATRIX_CEILING,
    T5_TRAINING_LIMIT,
    SceneComposer,
    SceneSelectionError,
)
from .stages import (
    KEYFRAME_NEGATIVE_PROMPT,
    default_keyframe_prompt,
)

WEB_ROOT = Path(__file__).resolve().parent / "web"
logger = logging.getLogger(__name__)

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

# 頁籤 2 的四個環境主題按鈕。這裡**只有**環境動態子句——姿勢、地面、開口
# 形式、光線全部由 scene_composer 從晶片選擇填，所以主題不再假設有窗、有湖
# 或是白天（artifacts/spec.md「動作提示詞與關鍵幀場景的接縫」）。
#
# 每一句都必須可循環：原本 nature_breeze 有一句
# "a few distant birds glide serenely across the sky and loop naturally"，
# 但單向飛越畫面的鳥回不到第一幀，同時牴觸 Veo 的 last_frame 循環機制、
# MOTION_NEGATIVE_PROMPT 的 "new objects"，也會推高 veo.seam_score。
MOTION_THEMES: dict[str, dict[str, str]] = {
    "nature_breeze": {
        "label": "大自然微風與湖光 (Nature Breeze & Lake)",
        "description": "微風吹拂綠樹枝葉、水面微波漣漪",
        "clause": (
            "a gentle breeze sways the foliage and greenery in a calm rhythmic "
            "motion; slow ripples shimmer across the water."
        ),
    },
    "rainy_window": {
        "label": "窗外細雨 (Rainy Window)",
        "description": "細雨緩慢滑落、濕潤綠葉微擺、水窪漣漪",
        "clause": (
            "raindrops trickle down in a slow steady rhythm; wet leaves sway "
            "gently in the cool air; small ripples form on the puddles."
        ),
    },
    "urban_sunset": {
        "label": "城市街景晚霞 (Urban Sunset & Bokeh)",
        "description": "街邊樹蔭輕擺、遠方虛焦光斑微弱呼吸",
        "clause": (
            "the distant street tree canopies sway subtly; blurred bokeh lights "
            "of far-off traffic twinkle and breathe softly; a calm atmospheric "
            "haze shifts gently."
        ),
    },
    "forest_woods": {
        "label": "林間微風 (Forest Woods)",
        "description": "針葉樹梢輕晃、林間光柱微移、微塵光斑",
        "clause": (
            "tall pines and forest canopy sway with a steady natural "
            "oscillation; soft light beams shift gently between the trunks; "
            "dust motes drift slowly through the air."
        ),
    },
}


class CreateEpisodeRequest(BaseModel):
    slug: str
    title: str


class GenerateKeyframesRequest(BaseModel):
    positive_prompt: str | None = None
    negative_prompt: str | None = None
    batch_size: int | None = None
    # 頁籤 1 的晶片選到的場景（例如 {"venue": "lakeside", "time": "golden"}）。
    # 帶上來的話，配套的兩支動作提示詞會一起存進 task payload，頁籤 2 才
    # 拿得到跟這張關鍵幀同場景的動作文字——否則就會回到「關鍵幀在湖畔
    # 露台、動作提示詞卻寫室內地板」。
    scene: dict[str, str] | None = None


class ComposeSceneRequest(BaseModel):
    scene: dict[str, str] | None = None


class GenerateMotionRequest(BaseModel):
    """Tab 2's generate controls. provider picks the backend; model and
    resolution are only meaningful (and only validated) for 'veo'."""

    provider: str = "veo"
    model: str | None = None
    resolution: str | None = None
    sleep_prompt: str | None = None
    lookup_prompt: str | None = None


class VeoCredentialRequest(BaseModel):
    api_key: str


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
    veo_credential: veo_support.VeoCredential | None = None,
    veo_reachable_models: set[str] | None = None,
) -> FastAPI:
    app = FastAPI(title="Lyria Studio")
    # Shared with the worker's handler factory (see cli.py): tab 2's
    # credential box writes the key here and the handlers read it back at
    # call time. It never touches the database or a log line
    # (artifacts/spec.md 7).
    credential = veo_credential if veo_credential is not None else veo_support.VeoCredential()
    reachable = veo_reachable_models or set()

    # Every start_veo_* task type, counted together against the
    # per-episode cap: counting only the motion ones would let the 1080p
    # stage spend past a limit the reviewer thought applied to the whole
    # episode.
    _VEO_START_TASK_TYPES = ("start_veo_motion", "start_veo_clip")

    def _veo_preflight(episode_id: int, model: str | None, resolution: str | None,
                       *, default_resolution: str) -> tuple[dict[str, Any], str]:
        """Shared gate for both paid Veo stages.

        Order matters: an impossible model/resolution combination is
        reported before the credential check, so someone who picked one
        is told *that* rather than being sent to fix a key and hitting
        the same wall afterwards. Nothing here can reach a paid endpoint.
        """
        section = veo_support.veo_section(config)
        if not section.get("enabled", True):
            raise HTTPException(409, "設定檔已停用 Veo 路徑（studio.veo.enabled）")
        model_id = model or section.get("default_model") or ""
        chosen = resolution or default_resolution
        try:
            entry = veo_support.validate_choice(config, model_id, chosen)
        except veo_support.VeoConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not credential.is_set:
            raise HTTPException(
                409,
                "尚未設定 GEMINI_API_KEY——請在 .env 設定後重啟，"
                "或在下方金鑰欄位輸入",
            )
        started = sum(
            1 for t in db.tasks_for_episode(episode_id)
            if t["task_type"] in _VEO_START_TASK_TYPES
        )
        cap = int(section.get("max_starts_per_episode", 12))
        if started + 2 > cap:
            raise HTTPException(
                409,
                f"這一集的 Veo 付費啟動次數已達上限（{cap} 次，已用 {started} 次）"
                "——這是防手滑的保險，要繼續請調整 studio.veo.max_starts_per_episode",
            )
        return entry, chosen

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
            # Approving a keyframe unlocks tab 2 and stops there. It used
            # to immediately create both motion_test assets and enqueue
            # generate_motion_test for them, but since the Veo path landed
            # there are two possible backends with different models,
            # resolutions and costs -- choosing one on the reviewer's
            # behalf here would either start burning the local GPU time
            # they were trying to avoid or spend money they never
            # confirmed. Tab 2's own POST .../motion/generate creates the
            # assets and enqueues the work, the same shape the
            # loop_preview branch below already uses for tab 3.
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

    _composer_cache: list[SceneComposer] = []

    def _composer() -> SceneComposer:
        """場景組合器。第一次用到才讀檔並快取——但故意不用模組層級的常數，
        因為測試會用 tmp_path 當專案根目錄，一個 app 實例對應一個 config。"""
        if not _composer_cache:
            _composer_cache.append(SceneComposer.from_root(config.root))
        return _composer_cache[0]

    def _latest_keyframe_payload(episode_id: int) -> dict[str, Any]:
        """最後一次 generate_keyframe 任務的 payload；沒有就回空 dict。"""
        for task in reversed(db.tasks_for_episode(episode_id)):
            if task["task_type"] == "generate_keyframe" and task["payload_json"]:
                return json.loads(task["payload_json"]) or {}
        return {}

    def _episode_scene(episode_id: int) -> dict[str, str] | None:
        """這一集的關鍵幀是用哪組晶片選擇生的。

        舊的集數（晶片功能之前生的）payload 裡沒有這個鍵，回 None，
        compose_motion 會退回全域預設，行為跟以前一樣。
        """
        scene = _latest_keyframe_payload(episode_id).get("scene")
        return scene if isinstance(scene, dict) and scene else None

    def _scene_is_stale(episode_id: int) -> bool:
        """關鍵幀的文字被手動改過，但晶片選擇沒跟著動。

        此時動作提示詞仍會依 scene 推導，可能與畫面不符，而且不會有任何
        跡象——所以要主動在頁籤 2 講出來，而不是安靜地填進去
        （artifacts/spec.md 稽核發現 H）。
        """
        payload = _latest_keyframe_payload(episode_id)
        scene, used = payload.get("scene"), payload.get("positive_prompt")
        if not isinstance(scene, dict) or not used:
            return False
        try:
            return _composer().compose(scene).keyframe_prompt.strip() != used.strip()
        except SceneSelectionError:
            # 存下來的選擇對不上目前的 scene_presets.yaml（有人改了選項 id）。
            # 那不是「人類手改過提示詞」，回 True 會讓頁籤 2 顯示一句不正確
            # 的說明，所以回 False。
            return False

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
        composed = _composer().compose()
        return {
            "positive_prompt": composed.keyframe_prompt,
            "negative_prompt": KEYFRAME_NEGATIVE_PROMPT,
            "batch_size": config.section("studio").get("keyframe_batch_size", 12),
            "scene": composed.selection,
            "estimated_tokens": composed.estimated_tokens,
        }

    @app.get("/api/keyframes/scene-presets")
    def keyframe_scene_presets() -> dict[str, Any]:
        """頁籤 1 那幾排可點擊晶片的資料來源。

        只送 id 與中文標籤過去，英文措辭留在後端：提示詞的**語序**是這次
        修正的重點（前 77 個 token 決定 CLIP-L 看到什麼），讓前端自己拼
        接等於把那個順序交給最容易改壞的一層。
        """
        composer = _composer()
        return {
            "axes": composer.axes(),
            "defaults": composer.defaults(),
            "constraints": composer.constraints(),
            "limits": {
                "training_limit": T5_TRAINING_LIMIT,
                "preset_ceiling": PRESET_MATRIX_CEILING,
            },
        }

    @app.post("/api/keyframes/compose")
    def keyframe_compose(body: ComposeSceneRequest) -> dict[str, Any]:
        """把一組晶片選擇組成關鍵幀與兩支動作提示詞。"""
        try:
            composed = _composer().compose(body.scene)
        except SceneSelectionError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "positive_prompt": composed.keyframe_prompt,
            "motion_prompts": composed.motion_prompts,
            "scene": composed.selection,
            "labels_zh": composed.labels_zh,
            "estimated_tokens": composed.estimated_tokens,
            "warnings": composed.warnings,
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
        # 有帶場景就把配套的動作提示詞一起存進 payload，頁籤 2 的
        # /api/motion/defaults 會讀回來，讓兩支動作片跟這張關鍵幀同場景。
        motion_prompts: dict[str, str] | None = None
        if body.scene:
            try:
                motion_prompts = _composer().compose(body.scene).motion_prompts
            except SceneSelectionError as exc:
                raise HTTPException(400, str(exc)) from exc
        payload: dict[str, Any] = {
            "positive_prompt": body.positive_prompt or default_keyframe_prompt(config),
            "negative_prompt": body.negative_prompt or KEYFRAME_NEGATIVE_PROMPT,
            "batch_size": body.batch_size or config.section("studio").get("keyframe_batch_size", 12),
        }
        if body.scene:
            payload["scene"] = body.scene
        if motion_prompts:
            payload["motion_prompts"] = motion_prompts
        task_id = db.enqueue_task(episode_id, "generate_keyframe", payload=payload)
        if episode["status"] == "draft":
            db.update_episode_status(episode_id, "in_progress")
        return {"task_id": task_id}

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

    @app.get("/api/veo/config")
    def veo_config() -> dict[str, Any]:
        """Everything tab 2's dropdowns need, in one call.

        `key_masked` is the only thing ever derived from the API key --
        the key itself is never serialized anywhere (artifacts/spec.md 7).
        """
        section = veo_support.veo_section(config)
        return {
            "enabled": bool(section.get("enabled", True)),
            "key_configured": credential.is_set,
            "key_masked": credential.masked,
            "default_model": section.get("default_model"),
            "default_resolution": section.get("default_resolution", "720p"),
            "duration_seconds": veo_support.REQUIRED_DURATION_SECONDS,
            "pricing_verified": bool(section.get("pricing_verified", False)),
            "pricing_snapshot_date": section.get("pricing_snapshot_date"),
            "models": veo_support.catalog_for_ui(config, reachable),
        }

    @app.post("/api/veo/credential")
    def set_veo_credential(body: VeoCredentialRequest) -> dict[str, Any]:
        """Accept an API key typed into tab 2 and hold it in memory only.

        Used when .env / the environment did not supply one. Nothing here
        writes the key to disk, and the response echoes only the mask.
        """
        key = (body.api_key or "").strip()
        if not key:
            raise HTTPException(400, "api_key 不能是空的")
        credential.set(key)
        return {"key_configured": True, "key_masked": credential.masked}

    @app.post("/api/episodes/{episode_id}/motion/generate")
    def generate_motion(episode_id: int, body: GenerateMotionRequest) -> dict[str, Any]:
        """Tab 2's "generate both clips" action.

        Replaces the automatic fan-out that used to happen on keyframe
        approval, so the reviewer chooses the backend, model and
        resolution -- and sees the estimated cost -- before anything is
        spent. Both roles are always generated together and pinned to the
        same model and resolution: the loop builder stream-copies its
        inputs, so a sleep clip and a lookup clip with different specs
        cannot be concatenated (see stages.py's _build_loop_variant).
        """
        episode = db.episode(episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        if not any(
            a["kind"] == "keyframe" and a["status"] == "approved"
            for a in db.assets_for_episode(episode_id)
        ):
            raise HTTPException(
                409, "還沒有已核准的關鍵幀——請先在頁籤 1 選定一張"
            )
        motion_task_types = {"generate_motion_test", "start_veo_motion", "poll_veo_motion"}
        if any(
            t["task_type"] in motion_task_types and t["status"] in ("queued", "running")
            for t in db.tasks_for_episode(episode_id)
        ):
            raise HTTPException(
                409,
                "這一集已經有動作生成任務在排隊或執行中——請等它完成，"
                "或按「終止」後再試",
            )
        if any(
            a["kind"] == "motion_test" and a["status"] == "approved"
            for a in db.assets_for_episode(episode_id)
        ):
            # Same reasoning as tab 1's post-approval guard: regenerating
            # the whole pair after one role is approved would leave an
            # approved asset beside a fresh awaiting_review batch. Single-
            # role retries go through that asset's own reject action.
            raise HTTPException(
                409,
                "這一集已經有核准的動作片段——若只想重做其中一支，"
                "請用該片段自己的「退回重生成」",
            )

        payload_by_role: dict[str, dict[str, Any]] = {}
        if body.provider == "veo":
            section = veo_support.veo_section(config)
            entry, resolution = _veo_preflight(
                episode_id, body.model, body.resolution,
                default_resolution=section.get("default_resolution") or "720p",
            )
            per_clip = veo_support.estimate_cost_usd(entry, clips=1)
            for role in ("sleep", "lookup"):
                payload_by_role[role] = veo_support.payload_for_start(
                    role=role, model_id=entry["id"],
                    resolution=resolution, estimated_usd=per_clip,
                )
            task_type = "start_veo_motion"
        elif body.provider == "comfyui":
            task_type = "generate_motion_test"
            payload_by_role = {role: {"role": role} for role in ("sleep", "lookup")}
        else:
            raise HTTPException(
                400, f"未知的生成來源 {body.provider!r}；只支援 'veo' 或 'comfyui'"
            )

        # Supersede whatever is still pending before enqueuing a fresh
        # pair, the same way tab 1's regenerate does, so the UI never
        # shows two live attempts for one role side by side.
        task_ids = {}
        for role in ("sleep", "lookup"):
            db.supersede_assets(episode_id, "motion_test", role)
            custom_prompt = body.sleep_prompt if role == "sleep" else body.lookup_prompt
            source_prompt = custom_prompt.strip() if custom_prompt and custom_prompt.strip() else None
            asset_id = db.create_asset(
                episode_id, "motion_test", role,
                variant_index=db.next_variant_index(episode_id, "motion_test", role),
                source_prompt=source_prompt,
            )
            payload = dict(payload_by_role[role])
            if source_prompt:
                payload["prompt"] = source_prompt
            task_ids[role] = db.enqueue_task(
                episode_id, task_type, asset_id=asset_id, payload=payload
            )
        if episode["status"] == "draft":
            db.update_episode_status(episode_id, "in_progress")
        return {"provider": body.provider, "task_ids": task_ids}

    @app.get("/api/motion/defaults")
    def motion_defaults(episode_id: int | None = None) -> dict[str, Any]:
        """頁籤 2 的預設動作提示詞，以及四個環境主題按鈕的內容。

        帶 episode_id 時，全部依「這一集的關鍵幀實際用的那組晶片選擇」組出
        來。四個主題只覆寫**環境動態**那一段，姿勢／地面／開口／光線一律由
        場景推導——824eb3b 的按鈕是整段覆寫寫死模板，無論選了什麼都宣稱狗
        趴平在室內地板、外面有溫暖陽光（artifacts/spec.md「動作提示詞與關鍵
        幀場景的接縫」A、C、D、E）。
        """
        scene = _episode_scene(episode_id) if episode_id is not None else None
        composer = _composer()
        try:
            base = composer.compose_motion(scene)
            presets = {
                key: {
                    "label": theme["label"],
                    "description": theme["description"],
                    **composer.compose_motion(scene, env_clause=theme["clause"]),
                }
                for key, theme in MOTION_THEMES.items()
            }
        except SceneSelectionError:
            # 存下來的選擇對不上目前的 scene_presets.yaml（有人改了選項 id）。
            # 退回全域預設，而不是讓整個頁籤 2 開不起來。
            base = composer.compose_motion(None)
            presets = {
                key: {
                    "label": theme["label"],
                    "description": theme["description"],
                    **composer.compose_motion(None, env_clause=theme["clause"]),
                }
                for key, theme in MOTION_THEMES.items()
            }
            scene = None
        return {
            "sleep_prompt": base["sleep"],
            "lookup_prompt": base["lookup"],
            "presets": {
                key: {
                    "label": item["label"],
                    "description": item["description"],
                    "sleep_prompt": item["sleep"],
                    "lookup_prompt": item["lookup"],
                }
                for key, item in presets.items()
            },
            "scene": scene,
            "scene_stale": _scene_is_stale(episode_id) if episode_id is not None else False,
        }

    @app.post("/api/episodes/{episode_id}/motion/suggest-prompts")
    def suggest_motion_prompts(episode_id: int) -> dict[str, Any]:
        """看一眼已核准的關鍵幀，替這一集寫一段貼合畫面的環境動態。

        姿勢、地面、開口、光線**不經過這裡**——那四樣由 compose_motion 從
        晶片選擇填。這個端點只負責產出環境動態子句。

        原本的實作在沒有 Gemini 金鑰時用關鍵字比對猜場景，而
        `"grain"`（木紋，每張關鍵幀提示詞的結尾都有）裡面含有 `"rain"`，
        於是每一個場地都被判成下雨、city/forest 兩個分支永遠到不了。整段
        啟發法已刪除：這一集用了哪個場景是存好的事實，不需要回頭去猜自己
        剛產生的字串。
        """
        episode = db.episode(episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        approved_keyframes = [
            a for a in db.assets_for_episode(episode_id, kind="keyframe", role="shared")
            if a["status"] == "approved"
        ]
        if not approved_keyframes:
            raise HTTPException(409, "尚未有已核准的關鍵幀——請先在頁籤 1 完成關鍵幀選定")

        keyframe = approved_keyframes[-1]
        kf_path = Path(keyframe["path"]) if keyframe["path"] else None
        scene = _episode_scene(episode_id)

        env_clause = None
        scene_detected = "依頁籤 1 的場景選擇推導"
        source = "scene"
        api_key = credential.get() or os.getenv("GEMINI_API_KEY")
        if api_key and kf_path and kf_path.is_file():
            try:
                from google import genai
                from PIL import Image as PILImage
                client = genai.Client(api_key=api_key)
                with PILImage.open(kf_path) as pil_img:
                    vision_prompt = (
                        "Look at this photograph of a cafe with a chow chow dog. It may be "
                        "indoors behind glass, on a half-open terrace, or fully outdoors. "
                        "Describe ONLY the ambient environmental motion that could plausibly "
                        "occur in an 8-second seamless loop of this exact scene -- foliage, "
                        "water, weather, drifting light. Every motion must return to its "
                        "starting state, so do not include anything that crosses the frame "
                        "one way (birds, people, vehicles). Do not describe the dog, its pose, "
                        "the furniture, the camera, or the lighting direction. "
                        'Return ONLY JSON: {"scene_summary": "<brief>", '
                        '"environmental_motion": "<one or two clauses, ending with a period>"}'
                    )
                    resp = client.models.generate_content(
                        model="gemini-2.0-flash",
                        contents=[pil_img, vision_prompt],
                    )
                    text = (resp.text or "").strip()
                    text = text.removeprefix("```json").removesuffix("```").strip()
                    parsed = json.loads(text)
                    candidate = (parsed.get("environmental_motion") or "").strip()
                    if candidate:
                        env_clause = candidate
                        scene_detected = parsed.get("scene_summary") or "AI 視覺辨識環境"
                        source = "gemini-vision"
            except (ImportError, OSError, ValueError, RuntimeError) as exc:
                logger.debug("Vision prompt analysis fallback to scene selection: %s", exc)

        try:
            prompts = _composer().compose_motion(scene, env_clause=env_clause)
        except SceneSelectionError:
            prompts = _composer().compose_motion(None, env_clause=env_clause)
        return {
            "scene_detected": scene_detected,
            "source": source,
            "sleep_prompt": prompts["sleep"],
            "lookup_prompt": prompts["lookup"],
        }

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

    @app.post("/api/episodes/{episode_id}/clips/generate")
    def generate_clips(episode_id: int, body: GenerateMotionRequest) -> dict[str, Any]:
        """Stage 2 of the Veo flow: re-run both clips at full resolution.

        The reviewer's chosen workflow is "generate cheap 720p tests
        first, then re-run the keeper at 1080p". This is that second run.
        It writes straight into `clip_1080p`, skipping both `clip` and
        `upscale_clip` -- Veo produces 1080p natively, so the ComfyUI
        upscale stage (tab 3) is not on this path at all.

        Nothing new is needed downstream: _continue_after_approval's
        existing clip_1080p fan-in already fires build_loop once both
        roles are approved.
        """
        episode = db.episode(episode_id)
        if episode is None:
            raise HTTPException(404, "episode not found")
        if body.provider != "veo":
            raise HTTPException(
                400,
                f"這個端點只支援 Veo（收到 {body.provider!r}）——"
                "本機 ComfyUI 的正式片段走既有的 generate_clip/upscale_clip 流程",
            )
        assets = db.assets_for_episode(episode_id)
        approved_motion = {
            a["role"] for a in assets
            if a["kind"] == "motion_test" and a["status"] == "approved"
        }
        if {"sleep", "lookup"} - approved_motion:
            raise HTTPException(
                409,
                "需要 sleep 與 lookup 兩支低解析度測試都核准後，"
                "才能重跑 1080p 正式版",
            )
        if any(
            a["kind"] == "clip_1080p" and a["status"] in ("queued", "running", "awaiting_review", "approved")
            for a in assets
        ):
            raise HTTPException(
                409,
                "這一集已經有 1080p 正式片段（或正在生成中）——"
                "若要重做其中一支，請用該片段自己的「退回重生成」",
            )
        live_types = {"start_veo_clip", "poll_veo_clip", "generate_clip", "upscale_clip"}
        if any(
            t["task_type"] in live_types and t["status"] in ("queued", "running")
            for t in db.tasks_for_episode(episode_id)
        ):
            raise HTTPException(
                409, "這一集已經有正式片段的生成任務在排隊或執行中"
            )

        entry, resolution = _veo_preflight(
            episode_id, body.model, body.resolution, default_resolution="1080p"
        )
        per_clip = veo_support.estimate_cost_usd(entry, clips=1)
        task_ids = {}
        for role in ("sleep", "lookup"):
            # Carry the approved test's prompt across so the 1080p run
            # reproduces what the reviewer actually signed off on, not
            # the generic default.
            approved = [
                a for a in db.assets_for_episode(episode_id, kind="motion_test", role=role)
                if a["status"] == "approved"
            ][-1]
            asset_id = db.create_asset(
                episode_id, "clip_1080p", role,
                variant_index=db.next_variant_index(episode_id, "clip_1080p", role),
                source_prompt=approved["source_prompt"],
            )
            task_ids[role] = db.enqueue_task(
                episode_id, "start_veo_clip", asset_id=asset_id,
                payload=veo_support.payload_for_start(
                    role=role, model_id=entry["id"],
                    resolution=resolution, estimated_usd=per_clip,
                ),
            )
        return {
            "provider": "veo",
            "resolution": resolution,
            "estimated_cost_usd": round(per_clip * 2, 4),
            "task_ids": task_ids,
        }

    @app.post("/api/episodes/{episode_id}/clips/cancel")
    def cancel_clip_generation(episode_id: int) -> dict[str, Any]:
        """Stop the 1080p run. Same honesty rule as motion/cancel: a
        started Veo operation is already billed and cannot be called
        off, so the count of abandoned paid operations is reported
        rather than implying a refund."""
        if db.episode(episode_id) is None:
            raise HTTPException(404, "episode not found")
        live = [
            t for t in db.tasks_for_episode(episode_id)
            if t["status"] in ("queued", "running")
        ]
        abandoned = [
            t for t in live
            if t["task_type"] in ("start_veo_clip", "poll_veo_clip")
            and t["asset_id"] is not None
            and (db.asset(t["asset_id"]) or {})["operation_id"]
        ]
        cancelled = db.cancel_tasks(
            episode_id, {"start_veo_clip", "poll_veo_clip"},
            error="使用者手動終止生成",
        )
        return {"cancelled": cancelled, "abandoned_paid_operations": len(abandoned)}

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
        tasks = db.tasks_for_episode(episode_id)
        live = [t for t in tasks if t["status"] in ("queued", "running")]
        # Only meaningful for the local backend. There is no way to
        # interrupt a Veo generation: once start_video returned an
        # operation name the request is already billed, so cancelling
        # here stops us collecting the result -- it does not stop the
        # charge, and must not claim to.
        if any(t["task_type"] == "generate_motion_test" for t in live):
            local_comfyui.interrupt()
        abandoned_paid = [
            t["asset_id"] for t in live
            if t["task_type"] in ("start_veo_motion", "poll_veo_motion")
            and t["asset_id"] is not None
            and (db.asset(t["asset_id"]) or {})["operation_id"]
        ]
        cancelled = db.cancel_tasks(
            episode_id,
            {"generate_motion_test", "start_veo_motion", "poll_veo_motion"},
            error="使用者手動終止生成",
        )
        return {
            "cancelled": cancelled,
            # Surfaced so the UI can say "已停止收取結果，但這些已經計費"
            # instead of implying the spend was called off.
            "abandoned_paid_operations": len(abandoned_paid),
        }

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
        # A Veo-produced asset has to be retried through the Veo start
        # task, not the local ComfyUI one -- _TASK_TYPE_BY_KIND only
        # knows about the original local pipeline. Model and resolution
        # are carried over from the rejected asset so the replacement
        # stays concat-compatible with its sibling role.
        _VEO_RETRY_TASK_TYPE = {
            "motion_test": "start_veo_motion",
            "clip_1080p": "start_veo_clip",
        }
        if asset["provider"] == "google-veo" and asset["kind"] in _VEO_RETRY_TASK_TYPE:
            db.enqueue_task(
                episode_id, _VEO_RETRY_TASK_TYPE[asset["kind"]], asset_id=replacement_id,
                payload=veo_support.payload_for_start(
                    role=asset["role"],
                    model_id=asset["model"],
                    resolution=(
                        "1080p" if (asset["height"] or 0) >= 1080 else "720p"
                    ),
                    estimated_usd=asset["estimated_cost_usd"] or 0.0,
                ),
            )
        else:
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
