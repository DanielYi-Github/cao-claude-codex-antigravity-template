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
import shutil
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

from ..config import AppConfig
from ..db import StateDB
from ..errors import GenerationError
from ..media.audio import combine_audio, extend_audio_at_least, probe_audio
from ..media.timeline import probe_video, verify_render
from ..providers.comfyui import ComfyUIClient
from ..utils import ensure_dir, run_command, sha256_file

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
# explicit positive-prompt wording (see DEFAULT_KEYFRAME_PROMPT's "Exactly one"
# clause), since CFG=1 makes negative prompts inert either way.
KEYFRAME_NEGATIVE_PROMPT = (
    "blurry, low quality, worst quality, cartoon, illustration, CGI, 3D render, surreal, "
    "impossible geometry, unstable architecture, camera shake, zoom, fast camera movement, "
    "flicker, strobing, frozen motion, static motion, brand logo, trademark, readable sign, "
    "watermark, subtitle, text, celebrity, public figure, "
    "recognizable face, malformed hands, "
    "extra fingers, fused fingers, extra limbs, deformed face, bad anatomy, bad proportions, "
    "duplicate objects, oversaturated, overexposed, underexposed, cropped, out of frame"
)

# node 6 in cafe-flf2v-wan22-mps.json: same "inert under CFG=1" caveat as above.
MOTION_NEGATIVE_PROMPT = (
    "camera shake, zoom, pan, fast camera movement, scene transition, flicker, strobing, "
    "frozen motion, static motion, object morphing, changing layout, new objects, "
    "disappearing objects, warped geometry, duplicate objects, blurry, low quality, "
    "worst quality, oversaturated, overexposed, underexposed, cropped, out of frame"
)

# Fallback used only when an asset has no per-episode source_prompt of its
# own (see generate_keyframe() below). Each new episode should normally write
# its own source_prompt with a different pose/setting -- the LoRA no longer
# ties the dog to one pre-rendered cutout, so there's no reason every episode
# should reuse this exact scene. Keep the identity clause (breed, fur color,
# build -- matching character-reference/chowchow/approved/identity-notes-v1.md)
# and the chowchow_mascot trigger word at the front of any new variant; only
# the pose/setting sentence in the middle is meant to change. The "Exactly one"
# clause matters: subject LoRAs can duplicate their subject, and with
# CFG pinned to 1.0 a negative prompt can't fix that after the fact -- it has
# to be ruled out in the positive text instead, the same way "no person in
# frame" already is.
DEFAULT_KEYFRAME_PROMPT = (
    "High-end architectural editorial photography of a luxurious biophilic destination cafe "
    "integrated into a spectacular natural landscape at blue hour. An extra-wide 16:9 establishing "
    "view from deep inside an open-sided lakeside pavilion looks outward through a wall-sized opening "
    "toward a calm mountain lake, forested cliffs, misty peaks, and mature trees. The lake and "
    "wilderness are the main subject and command at least sixty percent of the visual attention. "
    "Natural timber beams, pale stone, a bare wooden floor, an espresso bar, linen lounge seating, low "
    "wood tables, and a few elegant cafe tables frame the foreground and sides without blocking the "
    "view. Subtle amber lanterns and warm practical lights inside balance the cool blue evening "
    "landscape outside, creating a tranquil premium jazz-cafe ambience without a stage or performers. "
    "The entire cafe is empty except for exactly one adult chow chow dog, chowchow_mascot, with fluffy "
    "reddish-brown fur, thick rounded mane, black nose, small triangular ears, and a sturdy compact "
    "body. The dog rests directly on the bare wooden floor in the middle distance near the lower-left "
    "third, occupying only about twenty to twenty-five percent of the image width and remaining "
    "secondary to the scenery. Bare wood planks are visible beneath and around its paws and belly; no "
    "rug, mat, carpet, blanket, cushion, mattress, or dog bed is beneath it. The unobstructed dog has "
    "one continuous body and exactly one head, seen in a natural three-quarter side view, lying calmly "
    "with two separate front paws, four anatomically correct legs total, and one curled tail. No "
    "furniture overlaps the dog. No people, human figures, portraits, laptop, bookshelves, library, "
    "urban skyline, signs, logos, watermark, or readable text. Photorealistic natural materials, deep "
    "spatial composition, locked-off cinematic camera, peaceful long-form ambience."
)

# Motion 1 -- plays 7 of the 8 segments in the 64s macro-loop: the dog holds
# its resting pose the whole clip, only breathing/tail/ambient motion.
_SLEEP_PROMPT = (
    "A continuous seamless 8-second loop video based on the image. The fluffy chow chow dog "
    "remains lying flat on the cafe floor in the exact same pose throughout the clip, only its "
    "tail wags gently a few times and its chest and back rise and fall slowly with calm "
    "breathing, fur shifting subtly. Ambient environmental motion only: steam continues "
    "curling gently from the coffee mug on the table, soft daylight shifts almost "
    "imperceptibly. The owner stays completely out of frame throughout, only the chair, "
    "laptop, and mug are visible. The dog's head and body position at the end of the clip "
    "matches the very first frame exactly. No camera movement, no scene change, no new "
    "objects, smooth continuous loop returning to the same composition, photorealistic, "
    "physically plausible motion"
)

# Motion 2 -- plays 1 of the 8 segments, deliberately rare so it doesn't read
# as mechanical repetition across a long-running video. Must end on exactly
# the same pose as the start frame so the 64s loop closes seamlessly.
_LOOKUP_PROMPT = (
    "A continuous seamless 8-second loop video based on the image. The fluffy chow chow dog is "
    "lying flat on the cafe floor. Partway through the clip, the dog slowly lifts its head up "
    "from its front paws and turns to glance toward the empty chair and table where its owner "
    "would be sitting, holds the glance for a brief moment, then gently lowers its head back "
    "down onto its front paws and closes its eyes, returning to the exact same resting pose as "
    "the very first frame. The owner remains completely out of frame throughout, only the "
    "chair, laptop, and steaming mug are visible. Subtle ambient motion: soft daylight shifting "
    "gently, steam rising from the mug. No camera movement, no scene change, no new objects, "
    "smooth continuous loop where the end frame connects seamlessly back to the start frame, "
    "photorealistic, physically plausible motion"
)

DEFAULT_MOTION_PROMPTS = {"sleep": _SLEEP_PROMPT, "lookup": _LOOKUP_PROMPT}

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
) -> dict[str, Any]:
    """Wire up the four ComfyUI-backed task handlers for StudioWorker.

    generate_clip and upscale_clip run remote_comfyui when it's given --
    those are the two stages heavy enough that a rented GPU is worth the
    trouble (see the 2026-08-18 cloud-economics writeup: keyframe and
    motion-test candidates are cheap/fast enough locally that moving them
    off-machine isn't worth the extra hop). generate_keyframe and
    generate_motion_test always stay on local_comfyui.
    """
    clip_comfyui = remote_comfyui or local_comfyui

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
        prompt = payload.get("positive_prompt") or DEFAULT_KEYFRAME_PROMPT
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
        prompt = asset["source_prompt"] or DEFAULT_MOTION_PROMPTS[role]
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
        for role, clip in clips.items():
            duration = _video_metadata(Path(clip["path"]))["duration_seconds"]
            if abs(duration - SEGMENT_SECONDS) > 0.5:
                raise GenerationError(
                    f"{role} {source_kind} is {duration:.2f}s, expected {SEGMENT_SECONDS}s "
                    f"-- can't build an exact {SEGMENT_SECONDS * len(LOOP_SEQUENCE):.0f}s loop"
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

    def build_music_mix(db: StateDB, task: Any) -> None:
        episode_id = task["episode_id"]
        episode = db.episode(episode_id)
        approved = [
            a for a in db.assets_for_episode(episode_id, kind="music_track")
            if a["status"] == "approved"
        ]
        by_slot = {int(a["variant_index"]): a for a in approved}
        if set(by_slot) != set(range(12)) or len({a["track_id"] for a in approved}) != 12:
            raise GenerationError("音樂混音需要 12 個不同且已核准的曲目槽位")
        music_job_id = episode["music_job_id"]
        paths: list[str] = []
        for slot in range(12):
            asset = by_slot[slot]
            track = db.track(asset["track_id"])
            if (
                track is None
                or track["job_id"] != music_job_id
                or track["status"] != "ready"
                or not track["audio_path"]
                or track["audio_path"] != asset["path"]
            ):
                raise GenerationError(f"第 {slot + 1} 首曲目不屬於本集或尚未就緒")
            paths.append(track["audio_path"])

        episode_dir = _episode_dir(config, episode["slug"])
        album = episode_dir / "music-album-shared-v0.flac"
        combine_audio(
            paths,
            album,
            crossfade_seconds=float(config.section("video").get("crossfade_seconds", 2)),
            codec="flac",
        )
        crossfade = float(config.section("video").get("crossfade_seconds", 2))
        target_seconds = int(config.section("video").get("target_duration_minutes", 120)) * 60
        dest = episode_dir / "music-mix-shared-v0.flac"
        mixed = extend_audio_at_least(
            album,
            dest,
            target_seconds,
            crossfade,
            codec="flac",
        )
        if mixed != dest:
            # The album already exceeds the configured target. Keep the
            # complete album but give the review asset its stable mix name.
            shutil.copy2(mixed, dest)
        info = probe_audio(dest)
        duration = float(info.get("format", {}).get("duration") or 0)
        if duration <= 0:
            raise GenerationError(f"混音檔沒有可用長度：{dest}")
        asset_id = db.create_asset(episode_id, "music_mix", "shared", status="running")
        db.transition_asset(
            asset_id,
            expected_status="running",
            expected_version=0,
            status="awaiting_review",
            path=str(dest),
            sha256=sha256_file(dest),
            duration_seconds=duration,
        )

    def render_final(db: StateDB, task: Any) -> None:
        """Repeat the approved loop to match a music track's length and mux it in.

        Studio normally resolves the approved music_mix automatically.
        payload_json.audio_path remains as a backwards-compatible override
        for direct task callers and focused tests.
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
            mixes = [
                a for a in db.assets_for_episode(episode_id, kind="music_mix")
                if a["status"] == "approved" and a["path"]
            ]
            if mixes:
                audio_path = mixes[-1]["path"]
        if not audio_path:
            raise GenerationError(
                "render_final 需要已核准的 music_mix 或 payload_json.audio_path"
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

    return {
        "generate_keyframe": generate_keyframe,
        "generate_motion_test": generate_motion_test,
        "generate_clip": generate_clip,
        "upscale_clip": upscale_clip,
        "build_loop_preview": build_loop_preview,
        "build_loop": build_loop,
        "build_music_mix": build_music_mix,
        "render_final": render_final,
    }
