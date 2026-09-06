"""Task handlers that turn studio_tasks rows into real ComfyUI generations.

Each handler loads an API-format workflow template from the comfyui-cafe-
loop-generator repo, patches a handful of named nodes (prompt text, seed,
resolution, source image/video filenames), submits it, waits for the
result, and copies the output into this episode's workspace directory --
see studio-architecture-plan.md Part 三 (取捨 1) for why the templates stay
in API format and are only patched, never built from scratch here.

build_loop and render_final are pure ffmpeg (no ComfyUI involved): they
assemble already-approved clips with the concat demuxer's `-c copy` stream-
copy path instead of re-encoding, which is what turns a multi-hour final
render into a few minutes (studio-architecture-plan.md 取捨 3).
"""

from __future__ import annotations

import json
import math
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

from ..config import AppConfig
from ..db import StateDB
from ..errors import GenerationError, VisualPollTransientError
from ..media.audio import probe_audio
from ..media.timeline import probe_video, verify_render
from ..providers.comfyui import ComfyUIClient
from ..utils import ensure_dir, run_command, sha256_file
from . import veo as veo_support
from .scene_composer import ComposedScene, SceneComposer

# The Chow Chow LoRA workflow generates the dog natively inside each scene
# (LoraLoader on a trained chowchow_mascot identity LoRA, node id 20 -- see
# that node's _meta note for why it isn't 10) instead of pasting a pre-rendered
# cutout onto a separately-generated background. This fixes two problems the
# composite approach had: the dog's lighting/shadow/sharpness never matched the
# background plate (ColorTransfer only nudges color statistics, it can't
# relight or add contact shadows), and the dog was locked to whichever poses
# happened to be pre-rendered into the reference pack. The composite workflow
# (cafe-keyframe-flux-chowchow-composite-mps.json) remains in the ComfyUI repo
# as a fallback.
KEYFRAME_WORKFLOW = "cafe-keyframe-flux-chowchow-lora-mps.json"
MOTION_WORKFLOW = "cafe-flf2v-wan22-mps.json"
UPSCALE_WORKFLOW = "cafe-upscale-1080p-mps.json"

# node 3 in cafe-keyframe-flux-mps.json: the shared negative prompt already
# used across prompt-library.md's cafe scenes. CFG is pinned to 1.0 (schnell's
# native distilled operating point) so this text has no real effect on output --
# kept only for consistency with the other workflows' node graph. Earlier this
# also listed "dog, animal, pet, duplicate animal" to stop the old composite
# workflow's background-only FLUX pass from drawing its own dog before the
# real cutout got pasted on top; the LoRA workflow draws the dog on purpose,
# so that exclusion doesn't apply here anymore. Guarding against duplicate
# dogs now happens the same way "no person in frame" already does -- via
# explicit positive-prompt wording (see scene_presets.yaml's base.identity
# "Only one dog" clause), since CFG=1 makes negative prompts inert either way.
KEYFRAME_NEGATIVE_PROMPT = """standing dog, dog standing, dog stands up, dog getting up, dog rising, walking dog, running dog, jumping dog, dog changing position, dog leaving the floor, alert standing posture, half-sitting half-lying dog, dog transitioning between poses, dog with confused or twisted posture

dog as main subject, dog portrait, close-up dog, giant dog, oversized dog, dog too large, dog filling the frame, dog close to camera, foreground dog dominating composition, centered pet portrait, dramatic pet photography

multiple dogs, two dogs, extra dog, duplicate dog, cloned dog, repeated dog, second dog, background dog, dog reflection that looks like another dog

dog on chair, dog on sofa, dog on table, dog climbing furniture, dog jumping on furniture

barking, open mouth barking, aggressive dog, running, playing, jumping, hyperactive dog, excited dog, exaggerated tail movement, spinning, fast movement, large body motion

human visible, person visible, owner visible, human face, human head, human hands, human arms, human legs, human feet, human body, human silhouette, human reflection, person reflected in glass, person reflected in mirror, crowd

celebrity, public figure, recognizable person, identifiable person

cartoon, anime, illustration, drawing, painting, concept art, CGI, obvious CGI, 3D render, game render, plastic texture, artificial fur

bad dog anatomy, malformed dog, deformed dog, distorted face, incorrect chow chow face, long snout, thin fur, extra legs, extra paws, fused paws, missing paws, duplicate tail, multiple tails, missing tail, detached tail, twisted body, stretched body, incorrect proportions

warped furniture, deformed table, deformed chair, impossible architecture, crooked walls, floating objects, duplicated furniture, inconsistent perspective, impossible reflections

shaky camera, handheld camera, camera movement, camera pan, camera tilt, camera zoom, dolly shot, orbit shot, fast camera movement, sudden framing change, jump cut, scene transition

flickering, strobing, unstable lighting, changing architecture, morphing objects, temporal inconsistency, unstable dog appearance, changing fur color, changing dog size

blurry, soft focus, low resolution, low quality, worst quality, compression artifacts, noise, over-sharpening

overexposed, underexposed, crushed blacks, blown highlights, extreme contrast, oversaturated, neon colors, dramatic nightclub lighting, flashing lights

logo, brand logo, trademark, readable sign, readable text, letters, subtitle, caption, watermark, UI, interface, channel logo

cropped dog, partially missing dog, dog cut off by frame, out of frame

flat matte painting sky, obvious green screen backdrop, screensaver wallpaper scenery, framed picture or poster on the wall showing the outdoor view, fake-looking window, oversaturated postcard-style view, identifiable real-world landmark visible through window"""

# node 6 in cafe-flf2v-wan22-mps.json, and the negative_prompt sent to Veo in
# _start_veo. These two consumers do NOT behave the same way, which is the
# whole reason this constant is worth thinking about:
#
#   - WAN path: nodes 14/15 are KSamplerAdvanced at cfg=1, steps=4 (checked in
#     comfyui-assets/workflows/api/cafe-flf2v-wan22-mps.json), so this text is
#     just as inert there as KEYFRAME_NEGATIVE_PROMPT is at CFG=1.
#   - Veo path: the Gemini video API takes negative_prompt as a real argument
#     and honours it. This is the one place in the pipeline where a negative
#     prompt actually does something.
#
# The dog-behaviour exclusions below used to live only in
# KEYFRAME_NEGATIVE_PROMPT, i.e. on the one side that is inert everywhere, and
# were absent from the side Veo actually reads (artifacts/spec.md, audit
# finding G). Motion is also where they matter most: a still image cannot show
# the dog standing up mid-clip, but 8 seconds of video can, and that breaks the
# loop seam outright.
#
# Nothing here may contradict a positive prompt the composer can emit. That is
# why "passing vehicles" is absent: MOTION_THEMES["urban_sunset"] and the
# paris_street venue both describe distant traffic as out-of-focus bokeh, which
# is static and loopable. test_motion_prompts_never_contradict_the_scene keeps
# the two sides from drifting apart.
MOTION_NEGATIVE_PROMPT = (
    "dog standing up, dog getting up, dog rising, walking dog, running dog, "
    "jumping dog, dog changing position, dog climbing on furniture, dog leaving "
    "the frame, barking, open mouth barking, hyperactive dog, exaggerated tail "
    "movement, large body motion, multiple dogs, second dog, duplicate dog, "
    "human visible, person visible, person entering frame, "
    "camera shake, zoom, pan, fast camera movement, scene transition, flicker, strobing, "
    "frozen motion, static motion, object morphing, changing layout, new objects, "
    "birds flying across frame, "
    "disappearing objects, warped geometry, duplicate objects, blurry, low quality, "
    "worst quality, oversaturated, overexposed, underexposed, cropped, out of frame"
)

# 這裡以前是一段 626 字、861 個 T5 token 的字面提示詞，外加兩段寫死
# 「lying flat on the cafe floor」的動作提示詞。三個問題：
#
# 1. FLUX.1-schnell 的訓練序列長度是 256 個 token。861 個不會被截斷
#    （ComfyUI 的 T5XXLTokenizer 是 max_length=99999999），但遠離訓練
#    分布的結果是排在後段的指令實際上不被遵守——實測第 256 個 token 落
#    在窗景那段中間，也就是說「狗的位置與大小」「姿勢」「鏡頭與構圖」
#    整整三段都在模型會遵守的範圍之外。人類回報的「狗的位置不準、物理
#    不真實」就是這麼來的。
# 2. 動作提示詞寫死了室內地板，關鍵幀一換場景就跟起始幀互相矛盾。
# 3. 每換一個場景就要人工重寫整段長文字。
#
# 改成由 scene_composer 從 config/scene_presets.yaml 組出來：關鍵幀與
# 兩支動作片是**一組**產出，共用同一組姿勢與場景措辭，而且全部 39690
# 種組合都實測在 256 個 token 以內。詳見 artifacts/spec.md。


@lru_cache(maxsize=4)
def _default_scene(root: Path) -> ComposedScene:
    """預設場景。用 root 當快取鍵而不是無參數快取，是因為測試會用
    tmp_path 當專案根目錄，跨測試共用同一份快取會互相污染。"""
    return SceneComposer.from_root(root).compose()


def default_keyframe_prompt(config: AppConfig) -> str:
    """某個 episode 沒有自己的 source_prompt 時的關鍵幀提示詞。

    正常情況下每個 episode 都該有自己的一組選擇（頁籤 1 的晶片會組出來
    並寫進 task payload），這只是最後的退路。
    """
    return _default_scene(config.root).keyframe_prompt


def default_motion_prompts(config: AppConfig) -> dict[str, str]:
    """與 default_keyframe_prompt 配套的 sleep / lookup 動作提示詞。

    一定要跟關鍵幀同一次 compose() 產出，否則就會回到「關鍵幀在戶外
    露台、動作提示詞卻寫室內地板」的老問題。
    """
    return dict(_default_scene(config.root).motion_prompts)

_MOTION_DIMENSIONS = {"motion_test": (768, 432), "clip": (1024, 576)}

# Every clip_1080p asset is an 8-second segment (see comfyui-assets'
# validate_project.py TARGET_LOOP_SECONDS); the macro-loop plays sleep 7
# times and lookup once, in that order, for a 64-second loop that closes
# seamlessly because every segment both starts and ends on the same shared
# keyframe pose (chowchow-integration-plan.md).
SEGMENT_SECONDS = 8.0
LOOP_SEQUENCE = ["sleep"] * 7 + ["lookup"]


def _seed_for(config: AppConfig, asset_id: int) -> int:
    base = int(config.section("project").get("random_seed", 1))
    return base + asset_id


def _episode_dir(config: AppConfig, slug: str) -> Path:
    workspace = config.root / config.section("project").get("workspace", "workspace")
    return ensure_dir(workspace / "studio" / slug)


def _approved_asset(db: StateDB, episode_id: int, kind: str, role: str) -> Any:
    candidates = [
        a for a in db.assets_for_episode(episode_id, kind=kind, role=role)
        if a["status"] == "approved"
    ]
    if not candidates:
        raise GenerationError(f"找不到已核准的 {kind}/{role} 素材（episode_id={episode_id}）")
    return candidates[-1]


def _resolve_bound_assets(
    db: StateDB, episode_id: int, payload: dict[str, Any], source_kind: str
) -> dict[str, Any]:
    """Resolve the sleep/lookup source assets for a loop build.

    Prefers ids pinned into the task's payload_json at enqueue time (see
    app.py's assemble_motion_preview) over a fresh "whatever's approved
    now" lookup, so a build always matches what the caller saw at the
    moment they triggered it, not whatever happens to be approved by the
    time the worker gets around to it (codex_reviewer design consult,
    studio-console-v2-plan.md 7.8). Falls back to the latest-approved
    lookup when the task carries no pinned ids -- app.py's clip_1080p ->
    loop fan-in still enqueues build_loop with no payload at all.
    """
    # `is None`, not a truthiness check -- an empty {} would otherwise
    # silently take the fallback branch below instead of failing loudly
    # (codex_reviewer Phase 2 review).
    pinned = payload.get("source_asset_ids")
    if pinned is None:
        return {
            role: _approved_asset(db, episode_id, source_kind, role)
            for role in ("sleep", "lookup")
        }
    if set(pinned.keys()) != {"sleep", "lookup"}:
        raise GenerationError(
            f"source_asset_ids 的 key 必須恰好是 sleep 和 lookup，實際是 {sorted(pinned)}"
        )
    if len(set(pinned.values())) != len(pinned):
        raise GenerationError(f"source_asset_ids 裡兩個角色不能指向同一個 asset：{pinned}")
    resolved: dict[str, Any] = {}
    for role, asset_id in pinned.items():
        asset = db.asset(asset_id)
        if (
            asset is None
            or asset["episode_id"] != episode_id
            or asset["kind"] != source_kind
            or asset["role"] != role
            or asset["status"] != "approved"
        ):
            raise GenerationError(
                f"綁定的來源素材 id={asset_id}（{role}）現在不是已核准的 {source_kind}/{role}"
                "——組合期間狀態被改變了，請重新組合一次"
            )
        resolved[role] = asset
    return resolved


def _video_metadata(path: Path) -> dict[str, Any]:
    info = probe_video(path)
    video_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not video_streams:
        raise GenerationError(f"輸出檔沒有視訊串流：{path}")
    stream = video_streams[0]
    num, _, den = str(stream.get("r_frame_rate", "0/1")).partition("/")
    fps = float(num) / float(den) if float(den or 1) else 0.0
    return {
        "width": int(stream.get("width", 0)),
        "height": int(stream.get("height", 0)),
        "fps": fps,
        "duration_seconds": float(info.get("format", {}).get("duration") or 0),
    }


def _run_stage(
    comfyui: ComfyUIClient,
    workflow_path: Path,
    patches: dict[str, dict[str, Any]],
    dest: Path,
) -> str:
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    for node_id, fields in patches.items():
        workflow[node_id]["inputs"].update(fields)
    prompt_id = comfyui.submit(workflow)
    history_entry = comfyui.wait_for_result(prompt_id)
    comfyui.fetch_output(history_entry, dest)
    return prompt_id


def _concat_copy(source_paths: list[str | Path], dest: Path) -> None:
    """Losslessly concatenate video files with the ffmpeg concat demuxer.

    Every input here is h264-in-mp4 that ComfyUI's own CreateVideo/SaveVideo
    nodes already encoded the same way (same resolution, codec, and keyframe
    layout), so a plain stream copy is safe -- no re-encoding, no quality
    loss, and repeating a 64s loop ~113 times to fill a two-hour render takes
    minutes instead of the hour-plus a full re-encode would (取捨 3).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(f"{dest.stem}.partial{dest.suffix}")
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as list_file:
        for source in source_paths:
            escaped = str(Path(source).resolve()).replace("'", "'\\''")
            list_file.write(f"file '{escaped}'\n")
        list_path = Path(list_file.name)
    try:
        run_command([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-c", "copy", str(partial),
        ])
    finally:
        list_path.unlink(missing_ok=True)
    partial.replace(dest)


def _verify_loop_duration(path: Path, expected_seconds: float) -> None:
    info = probe_video(path)
    if not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
        raise GenerationError(f"loop 輸出缺少視訊串流：{path}")
    actual = float(info.get("format", {}).get("duration") or 0)
    if abs(actual - expected_seconds) > 0.5:
        raise GenerationError(
            f"loop 長度是 {actual:.2f}s，預期 {expected_seconds:.2f}s（{path}）"
        )


def _character_reference_path(config: AppConfig, workflows_dir: Path) -> Path:
    """Resolve the approved lying cutout used by the composite keyframe graph."""
    configured = config.section("studio").get("chowchow_reference_path")
    if configured:
        path = Path(str(configured)).expanduser()
        if not path.is_absolute():
            path = config.root / path
        return path.resolve()
    # Default layout: Lyria-Auto-Publisher and comfyui-cafe-loop-generator are
    # sibling directories, and workflows_dir normally points to
    # comfyui/.../workflows/api. The simpler parent case keeps isolated tests
    # and alternate local workflow directories usable too.
    comfy_root = (
        workflows_dir.parent.parent if workflows_dir.name == "api" else workflows_dir.parent
    )
    return (
        comfy_root
        / "character-reference"
        / "chowchow"
        / "approved"
        / "master-lying-transparent-v1.png"
    ).resolve()


def build_handlers(
    config: AppConfig,
    local_comfyui: ComfyUIClient,
    workflows_dir: Path,
    *,
    remote_comfyui: ComfyUIClient | None = None,
    veo_client_factory: Any | None = None,
    sleeper: Any | None = None,
) -> dict[str, Any]:
    """Wire up the four ComfyUI-backed task handlers for StudioWorker.

    generate_clip and upscale_clip run remote_comfyui when it's given --
    those are the two stages heavy enough that a rented GPU is worth the
    trouble (see the 2026-08-18 cloud-economics writeup: keyframe and
    motion-test candidates are cheap/fast enough locally that moving them
    off-machine isn't worth the extra hop). generate_keyframe and
    generate_motion_test always stay on local_comfyui.

    veo_client_factory is a zero-argument callable returning a client with
    start_video/poll_video (GeminiVisualClient in production, a fake in
    tests). It is a factory rather than an instance because the API key
    may arrive from tab 2's credential box *after* this function ran, and
    because the key must never be captured into anything long-lived --
    the factory reads it from an in-memory VeoCredential at call time
    (artifacts/spec.md 7).

    sleeper is injected purely so tests don't actually wait: see
    poll_veo_motion for why the pacing lives inside the handler.
    """
    clip_comfyui = remote_comfyui or local_comfyui
    wait = sleeper if sleeper is not None else time.sleep

    def _veo_client() -> Any:
        if veo_client_factory is None:
            raise GenerationError(
                "Veo 生成路徑未啟用（沒有提供 veo_client_factory）"
            )
        client = veo_client_factory()
        if client is None:
            raise GenerationError(
                "尚未設定 GEMINI_API_KEY——請在 .env 設定，或在頁籤 2 的"
                "金鑰欄位輸入後再試一次"
            )
        return client

    def generate_keyframe(db: StateDB, task: Any) -> None:
        """Fan out one ComfyUI batch submission into N candidate assets.

        Unlike every other stage, this one carries no asset_id (same
        no-pre-existing-row pattern build_loop/render_final already use) --
        studio-console-v2-plan.md's tab 1 wants N candidates shown together,
        not one placeholder filled in. The prompt comes from the task's
        payload_json (set by app.py's generate_keyframes endpoint), not from
        an asset row, since there isn't one until this handler creates them.
        """
        episode_id = task["episode_id"]
        episode = db.episode(episode_id)
        payload = json.loads(task["payload_json"]) if task["payload_json"] else {}
        prompt = payload.get("positive_prompt") or default_keyframe_prompt(config)
        negative_prompt = payload.get("negative_prompt") or KEYFRAME_NEGATIVE_PROMPT
        batch_size = int(
            payload.get("batch_size")
            or config.section("studio").get("keyframe_batch_size", 12)
        )
        if batch_size <= 0:
            raise GenerationError(f"keyframe_batch_size 必須大於 0，目前是 {batch_size}")
        patches = {
            "2": {"text": prompt},
            "3": {"text": negative_prompt},
            "4": {"batch_size": batch_size},
            # Seeded from the task id, not the episode id: a "regenerate
            # all" click enqueues a new task for the same episode, and must
            # get a different seed or it would silently return the exact
            # same 12 images -- regenerate would look like it did nothing.
            "5": {"seed": _seed_for(config, task["id"])},
        }
        # The composite workflow has node 10 as its character input. Keep the
        # conditional so unit-test/minimal workflows and the legacy text-only
        # fallback remain usable without a local character asset.
        workflow = json.loads((workflows_dir / KEYFRAME_WORKFLOW).read_text(encoding="utf-8"))
        if "10" in workflow:
            reference = _character_reference_path(config, workflows_dir)
            if not reference.is_file():
                raise GenerationError(f"找不到已核准的松獅犬透明參考圖：{reference}")
            staged_name = local_comfyui.stage_input_file(
                reference, name_hint="chowchow-lying"
            )
            patches["10"] = {"image": staged_name}
        for node_id, fields in patches.items():
            workflow[node_id]["inputs"].update(fields)
        prompt_id = local_comfyui.submit(workflow)
        history_entry = local_comfyui.wait_for_result(prompt_id)
        items = local_comfyui.fetch_all_outputs(history_entry)
        if len(items) != batch_size:
            raise GenerationError(
                f"要求 batch_size={batch_size}，但 ComfyUI 只回傳了 {len(items)} 張圖片"
                f"（prompt_id={prompt_id}）"
            )

        # Continues past every prior attempt for this episode (including
        # superseded/rejected ones) rather than resetting to 0 per batch --
        # see StateDB.next_variant_index for why a reset would collide.
        start_index = db.next_variant_index(episode_id, "keyframe", "shared")
        episode_dir = _episode_dir(config, episode["slug"])
        seed = _seed_for(config, task["id"])

        # Download and validate every image to disk BEFORE creating any DB
        # rows. If image k fails, only files -- never half a batch of
        # awaiting_review rows -- exist on disk, so the caller never sees a
        # task marked "failed" next to candidates that are still selectable.
        downloaded: list[tuple[int, Path, int, int]] = []
        for offset, item in enumerate(items):
            variant_index = start_index + offset
            dest = episode_dir / f"keyframe-shared-v{variant_index}.png"
            local_comfyui.download_output(item, dest)
            with Image.open(dest) as image:
                width, height = image.size
            downloaded.append((variant_index, dest, width, height))

        for variant_index, dest, width, height in downloaded:
            asset_id = db.create_asset(
                episode_id, "keyframe", "shared",
                variant_index=variant_index, source_prompt=prompt, source_seed=seed,
            )
            ok = db.transition_asset(
                asset_id,
                expected_status="queued",
                expected_version=0,
                status="awaiting_review",
                path=str(dest),
                sha256=sha256_file(dest),
                width=width,
                height=height,
                comfyui_prompt_id=prompt_id,
            )
            if not ok:
                raise GenerationError(
                    f"無法把 asset {asset_id} 轉成 awaiting_review"
                    "（expected_status/expected_version 不符，狀態可能被別的流程改動過）"
                )

    def _generate_motion(db: StateDB, task: Any, *, comfyui: ComfyUIClient, dir_kind: str) -> None:
        asset = db.asset(task["asset_id"])
        episode = db.episode(asset["episode_id"])
        role = asset["role"]
        width, height = _MOTION_DIMENSIONS[dir_kind]
        keyframe = _approved_asset(db, asset["episode_id"], "keyframe", "shared")
        staged_name = comfyui.stage_input_file(
            keyframe["path"], name_hint=f"keyframe-{keyframe['id']}"
        )
        prompt = asset["source_prompt"] or default_motion_prompts(config)[role]
        # Carried over from the approved motion_test on advance (see app.py
        # _continue_after_approval), same as source_prompt -- so the 1024x576
        # production render starts from the exact noise the reviewer already
        # saw at 768x432, not an unrelated roll. A plain reject still gets a
        # fresh seed: the replacement asset app.py creates has no
        # source_seed of its own, so this falls through to _seed_for below.
        seed = asset["source_seed"] or _seed_for(config, asset["id"])
        dest = (
            _episode_dir(config, episode["slug"])
            / f"{dir_kind}-{role}-v{asset['variant_index']}.mp4"
        )
        patches = {
            "5": {"text": prompt},
            "6": {"text": MOTION_NEGATIVE_PROMPT},
            "7": {"image": staged_name},
            "8": {"image": staged_name},
            "9": {"width": width, "height": height},
            "14": {"noise_seed": seed},
        }
        prompt_id = _run_stage(comfyui, workflows_dir / MOTION_WORKFLOW, patches, dest)
        metadata = _video_metadata(dest)
        db.transition_asset(
            asset["id"],
            expected_status="running",
            expected_version=asset["state_version"],
            status="awaiting_review",
            path=str(dest),
            sha256=sha256_file(dest),
            comfyui_prompt_id=prompt_id,
            source_seed=seed,
            **metadata,
        )

    def generate_motion_test(db: StateDB, task: Any) -> None:
        _generate_motion(db, task, comfyui=local_comfyui, dir_kind="motion_test")

    def generate_clip(db: StateDB, task: Any) -> None:
        _generate_motion(db, task, comfyui=clip_comfyui, dir_kind="clip")

    def upscale_clip(db: StateDB, task: Any) -> None:
        asset = db.asset(task["asset_id"])
        episode = db.episode(asset["episode_id"])
        role = asset["role"]
        clip = _approved_asset(db, asset["episode_id"], "clip", role)
        staged_name = clip_comfyui.stage_input_file(clip["path"], name_hint=f"clip-{clip['id']}")
        dest = (
            _episode_dir(config, episode["slug"])
            / f"clip_1080p-{role}-v{asset['variant_index']}.mp4"
        )
        patches = {"1": {"file": staged_name}}
        prompt_id = _run_stage(clip_comfyui, workflows_dir / UPSCALE_WORKFLOW, patches, dest)
        metadata = _video_metadata(dest)
        db.transition_asset(
            asset["id"],
            expected_status="running",
            expected_version=asset["state_version"],
            status="awaiting_review",
            path=str(dest),
            sha256=sha256_file(dest),
            comfyui_prompt_id=prompt_id,
            **metadata,
        )

    def _build_loop_variant(db: StateDB, task: Any, *, source_kind: str, dest_kind: str) -> None:
        """Shared implementation behind build_loop_preview (motion_test,
        768x432) and build_loop (clip_1080p, 1920x1080) -- same concat +
        verify shape, differing only in which approved asset kind feeds it
        and what kind of asset it produces. Both stay 8s/segment and 64s
        total: motion_test and clip_1080p assets come from the same
        MOTION_WORKFLOW frame count regardless of resolution (see
        _MOTION_DIMENSIONS), so SEGMENT_SECONDS/LOOP_SEQUENCE apply to
        either source kind unchanged.

        Enqueued by app.py with no asset_id (there's nothing to transition
        to 'running' yet -- the asset doesn't exist until this handler
        creates it), unlike every ComfyUI stage above.
        """
        episode_id = task["episode_id"]
        episode = db.episode(episode_id)
        payload = json.loads(task["payload_json"]) if task["payload_json"] else {}
        clips = _resolve_bound_assets(db, episode_id, payload, source_kind)
        # _concat_copy stream-copies; that is only safe while every input
        # shares a resolution, frame rate and codec (see its docstring).
        # Since the Veo path landed, an episode can hold a 1280x720@24fps
        # Veo clip next to a 768x432@16fps ComfyUI one, and concatenating
        # those produces a broken file that _verify_loop_duration -- which
        # only checks length -- would happily wave through.
        specs: dict[str, tuple[Any, ...]] = {}
        for role, clip in clips.items():
            info = _video_metadata(Path(clip["path"]))
            duration = info["duration_seconds"]
            if abs(duration - SEGMENT_SECONDS) > 0.5:
                raise GenerationError(
                    f"{role} {source_kind} is {duration:.2f}s, expected {SEGMENT_SECONDS}s "
                    f"-- can't build an exact {SEGMENT_SECONDS * len(LOOP_SEQUENCE):.0f}s loop"
                )
            specs[role] = (info["width"], info["height"], round(info["fps"], 2))
        if len(set(specs.values())) > 1:
            detail = "、".join(
                f"{role}={w}x{h}@{fps}fps" for role, (w, h, fps) in specs.items()
            )
            raise GenerationError(
                f"要串接的片段規格不一致（{detail}）——串流複製需要完全相同的"
                "解析度與影格率。請讓同一集的所有片段都用同一個生成來源、"
                "同一個模型與同一個解析度重新產生"
            )

        dest = _episode_dir(config, episode["slug"]) / f"{dest_kind}-shared-v0.mp4"
        _concat_copy([clips[role]["path"] for role in LOOP_SEQUENCE], dest)
        _verify_loop_duration(dest, SEGMENT_SECONDS * len(LOOP_SEQUENCE))

        metadata = _video_metadata(dest)
        asset_id = db.create_asset(episode_id, dest_kind, "shared", status="running")
        db.transition_asset(
            asset_id,
            expected_status="running",
            expected_version=0,
            status="awaiting_review",
            path=str(dest),
            sha256=sha256_file(dest),
            **metadata,
        )

    def build_loop_preview(db: StateDB, task: Any) -> None:
        _build_loop_variant(db, task, source_kind="motion_test", dest_kind="loop_preview")

    def build_loop(db: StateDB, task: Any) -> None:
        _build_loop_variant(db, task, source_kind="clip_1080p", dest_kind="loop")

    def render_final(db: StateDB, task: Any) -> None:
        """Repeat the approved loop to match a music track's length and mux it in.

        The audio to match comes from task['payload_json']['audio_path'] --
        wiring that up to a specific finished track from the existing
        tracks/jobs tables is the cross-pipeline integration studio-
        architecture-plan.md Part 六 leaves for a later stage; this handler's
        job is just "given a loop and an audio file, render the final video."
        """
        episode_id = task["episode_id"]
        episode = db.episode(episode_id)
        loop_asset = _approved_asset(db, episode_id, "loop", "shared")
        loop_duration = _video_metadata(Path(loop_asset["path"]))["duration_seconds"]
        if loop_duration <= 0:
            raise GenerationError(f"loop asset 沒有可用的長度：{loop_asset['path']}")

        payload = json.loads(task["payload_json"]) if task["payload_json"] else {}
        audio_path = payload.get("audio_path")
        if not audio_path:
            raise GenerationError(
                "render_final 需要 payload_json.audio_path（要對齊的最終混音檔案）"
            )
        audio_duration = float(probe_audio(audio_path).get("format", {}).get("duration") or 0)
        if audio_duration <= 0:
            raise GenerationError(f"無法讀取音訊長度：{audio_path}")

        repeats = max(1, math.ceil(audio_duration / loop_duration))
        dest = _episode_dir(config, episode["slug"]) / "final-shared-v0.mp4"
        video_only = dest.with_name(f"{dest.stem}.video-only.mp4")
        try:
            _concat_copy([loop_asset["path"]] * repeats, video_only)
            partial = dest.with_name(f"{dest.stem}.partial{dest.suffix}")
            audio_bitrate = config.section("video").get("audio_bitrate", "256k")
            run_command([
                "ffmpeg", "-y",
                "-i", str(video_only), "-i", str(audio_path),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", str(audio_bitrate),
                "-shortest", "-movflags", "+faststart",
                str(partial),
            ])
            fps = _video_metadata(video_only)["fps"] or 16
            verify_render(partial, expected_duration=audio_duration, max_error_frames=1, fps=int(fps))
            partial.replace(dest)
        finally:
            video_only.unlink(missing_ok=True)

        metadata = _video_metadata(dest)
        asset_id = db.create_asset(episode_id, "final", "shared", status="running")
        db.transition_asset(
            asset_id,
            expected_status="running",
            expected_version=0,
            status="awaiting_review",
            path=str(dest),
            sha256=sha256_file(dest),
            **metadata,
        )

    # ------------------------------------------------------------------
    # Google Veo path (artifacts/spec.md). Deliberately split into a
    # start task and a poll task instead of one blocking handler:
    # StudioWorker is a single thread running one task to completion, so
    # a handler that looped on poll_video() would park that thread for
    # the entire generation. Sleep and lookup would then generate one
    # after the other and every other task would stall behind them --
    # the exact wait this path exists to remove. With the split, both
    # operations sit in flight at Google simultaneously and the thread
    # only cycles cheap status checks.
    # ------------------------------------------------------------------

    def _strip_audio(source: Path, dest: Path) -> None:
        """Copy the video stream only, dropping Veo's generated audio.

        Veo 3.x on the Gemini Developer API always returns a soundtrack
        -- generate_audio=False is rejected there (see _start_veo). That
        track has to go before the clip is stored, for two reasons: the
        finished video's audio is the Lyria music, not whatever Veo
        invented; and _concat_copy stream-copies its inputs, so clips
        whose stream layout differs cannot be concatenated safely.

        -c:v copy means the video is never re-encoded, so this costs a
        second or two and loses no quality.
        """
        run_command([
            "ffmpeg", "-y", "-i", str(source), "-an", "-c:v", "copy", str(dest),
        ])

    def _veo_dest(db: StateDB, asset: Any, dir_kind: str) -> Path:
        episode = db.episode(asset["episode_id"])
        return (
            _episode_dir(config, episode["slug"])
            / f"{dir_kind}-{asset['role']}-v{asset['variant_index']}.mp4"
        )

    def _start_veo(db: StateDB, task: Any, *, dir_kind: str, poll_task_type: str) -> None:
        asset = db.asset(task["asset_id"])
        if asset is None:
            raise GenerationError(f"找不到 asset id={task['asset_id']}")
        payload = veo_support.load_payload(task)
        entry = veo_support.validate_choice(
            config, payload.get("model", ""), payload.get("resolution", "")
        )
        keyframe = _approved_asset(db, asset["episode_id"], "keyframe", "shared")
        prompt = asset["source_prompt"] or default_motion_prompts(config)[asset["role"]]
        client = _veo_client()
        # No try/except around this call on purpose. A
        # PaidStartUncertainError means the start may or may not have
        # been billed; retrying could charge twice, so it propagates and
        # the worker fails the task for a human to reconcile -- which is
        # exactly the contract GeminiVisualClient._paid_start sets.
        operation_id = client.start_video(
            prompt,
            keyframe["path"],
            model=entry["id"],
            resolution=payload["resolution"],
            duration_seconds=veo_support.REQUIRED_DURATION_SECONDS,
            negative_prompt=MOTION_NEGATIVE_PROMPT,
            # Neither `seed` nor `generate_audio` is passed: the Gemini
            # Developer API rejects both outright ("... is only supported
            # in Gemini Enterprise Agent Platform mode"). Found by
            # calling the real API with a throwaway key -- the SDK
            # validates these client side, before authenticating, so it
            # cost nothing to discover and no fake could have caught it.
            #
            # Consequences, both handled elsewhere: Veo clips on this
            # path are not reproducible (the ComfyUI path still seeds
            # normally), and they arrive WITH a Veo-generated audio
            # track, which _poll_veo strips before the clip is stored --
            # see _strip_audio there for why that matters.
        )
        width, height = veo_support.VEO_DIMENSIONS[payload["resolution"]]
        ok = db.transition_asset(
            asset["id"],
            expected_status="running",
            expected_version=asset["state_version"],
            status="running",
            operation_id=operation_id,
            provider="google-veo",
            model=entry["id"],
            estimated_cost_usd=veo_support.estimate_cost_usd(entry, clips=1),
            width=width,
            height=height,
        )
        if not ok:
            raise GenerationError(
                f"無法為 asset {asset['id']} 記錄 Veo operation "
                f"{operation_id}——狀態被其他流程改動過。這次生成已經計費，"
                "請人工比對後再決定是否重跑"
            )
        # Hand off to the poll task and return immediately: this is what
        # frees the worker thread so the sibling role can start too.
        db.enqueue_task(
            asset["episode_id"], poll_task_type, asset_id=asset["id"], payload=payload
        )

    def _poll_veo(db: StateDB, task: Any, *, dir_kind: str, poll_task_type: str) -> None:
        asset = db.asset(task["asset_id"])
        if asset is None:
            raise GenerationError(f"找不到 asset id={task['asset_id']}")
        operation_id = asset["operation_id"]
        if not operation_id:
            raise GenerationError(
                f"asset {asset['id']} 沒有 Veo operation id，無法輪詢"
            )
        payload = veo_support.load_payload(task)

        def requeue() -> None:
            db.enqueue_task(
                asset["episode_id"], poll_task_type,
                asset_id=asset["id"], payload=payload,
            )

        # Pacing lives here rather than between re-enqueues because the
        # worker loops immediately whenever the queue is non-empty --
        # re-enqueuing without a wait would spin the API. One thread
        # sleeping this long does briefly delay other queued tasks; that
        # is the accepted cost of not adding a scheduled-task column.
        wait(float(veo_support.veo_section(config).get("poll_interval_seconds", 15)))

        dest = _veo_dest(db, asset, dir_kind)
        # Downloaded to a .raw file first so `dest` only ever exists as
        # the finished, audio-free clip -- a half-processed file at the
        # real path would be served by /api/artifact as if it were done.
        raw = dest.with_name(f"{dest.stem}.raw{dest.suffix}")
        client = _veo_client()
        try:
            poll = client.poll_video(operation_id, raw)
        except VisualPollTransientError:
            # The operation is still valid and already paid for -- keep
            # polling it rather than failing the asset.
            requeue()
            return
        if not poll.done:
            requeue()
            return
        if poll.error:
            raise GenerationError(f"Veo 生成失敗（operation {operation_id}）：{poll.error}")

        try:
            _strip_audio(raw, dest)
        finally:
            raw.unlink(missing_ok=True)

        metadata = _video_metadata(dest)
        expected = veo_support.VEO_DIMENSIONS[payload["resolution"]]
        if (metadata["width"], metadata["height"]) != expected:
            raise GenerationError(
                f"Veo 回傳的影片是 {metadata['width']}x{metadata['height']}，"
                f"但下單的是 {payload['resolution']}（{expected[0]}x{expected[1]}）"
            )
        # Measures whether the clip actually ends where it began. This is
        # the only check that catches a model silently ignoring
        # last_frame, which would leave a clip that does not loop.
        seam = veo_support.seam_score(dest, dest.parent)
        ok = db.transition_asset(
            asset["id"],
            expected_status="running",
            expected_version=asset["state_version"],
            status="awaiting_review",
            path=str(dest),
            sha256=sha256_file(dest),
            seam_score=seam,
            **metadata,
        )
        if not ok:
            # Most likely a cancel landed while this poll was in flight,
            # which moves the asset to 'failed'. Without raising, the
            # worker would mark the task 'done' and this clip -- paid
            # for, downloaded and processed -- would sit on disk with no
            # asset row pointing at it and nothing shown in the UI.
            raise GenerationError(
                f"asset {asset['id']} 的狀態在輪詢期間被改動（可能是被終止），"
                f"無法標記為待審核。影片已經下載並付費，檔案留在 {dest}，"
                "請人工確認後再決定是否重跑"
            )

    def start_veo_motion(db: StateDB, task: Any) -> None:
        _start_veo(db, task, dir_kind="motion_test", poll_task_type="poll_veo_motion")

    def poll_veo_motion(db: StateDB, task: Any) -> None:
        _poll_veo(db, task, dir_kind="motion_test", poll_task_type="poll_veo_motion")

    def start_veo_clip(db: StateDB, task: Any) -> None:
        _start_veo(db, task, dir_kind="clip_1080p", poll_task_type="poll_veo_clip")

    def poll_veo_clip(db: StateDB, task: Any) -> None:
        _poll_veo(db, task, dir_kind="clip_1080p", poll_task_type="poll_veo_clip")

    return {
        "generate_keyframe": generate_keyframe,
        "generate_motion_test": generate_motion_test,
        "generate_clip": generate_clip,
        "upscale_clip": upscale_clip,
        "build_loop_preview": build_loop_preview,
        "build_loop": build_loop,
        "render_final": render_final,
        "start_veo_motion": start_veo_motion,
        "poll_veo_motion": poll_veo_motion,
        "start_veo_clip": start_veo_clip,
        "poll_veo_clip": poll_veo_clip,
    }
