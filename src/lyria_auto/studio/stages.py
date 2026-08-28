"""Task handlers that turn studio_tasks rows into real ComfyUI generations.

Each handler loads an API-format workflow template from the comfyui-cafe-
loop-generator repo, patches a handful of named nodes (prompt text, seed,
resolution, source image/video filenames), submits it, waits for the
result, and copies the output into this episode's workspace directory --
see studio-architecture-plan.md Part 三 (取捨 1) for why the templates stay
in API format and are only patched, never built from scratch here.

build_loop / render_final are not handled here yet (see studio-architecture-
plan.md 六, Stage 4/5); an episode reaching those tasks with no handler
registered fails loudly, which is the correct behaviour until that stage
lands.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from ..config import AppConfig
from ..db import StateDB
from ..errors import GenerationError
from ..media.timeline import probe_video
from ..providers.comfyui import ComfyUIClient
from ..utils import ensure_dir, sha256_file

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
# explicit positive-prompt wording (see DEFAULT_KEYFRAME_PROMPT's "only one
# dog" clause), since CFG=1 makes negative prompts inert either way.
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
# the pose/setting sentence in the middle is meant to change. The "only one
# dog" clause matters: subject LoRAs can duplicate their subject, and with
# CFG pinned to 1.0 a negative prompt can't fix that after the fact -- it has
# to be ruled out in the positive text instead, the same way "no person in
# frame" already is.
DEFAULT_KEYFRAME_PROMPT = (
    "A single chow chow dog, chowchow_mascot, fluffy reddish-brown fur, thick mane, black nose, "
    "sturdy compact build -- only one dog is visible in the frame, no duplicate or additional "
    "dogs. A fictional independent neighborhood cafe interior, no recognizable location. The dog "
    "lies flat on the wooden floor, head resting low on its front paws, eyes half-closed, calm "
    "and content posture. Beside the dog, a wooden chair is pulled out from a small table "
    "as if someone just sat down, a laptop on the table glowing softly, a ceramic coffee mug "
    "beside it with gentle steam rising -- the owner is implied by these objects but is "
    "completely out of frame, no person or human body part visible anywhere in the shot. Soft "
    "warm daylight through a nearby window, realistic wood and stone textures, documentary "
    "photography, photorealistic, physically plausible materials and lighting, locked-off "
    "camera, stable centered composition, no logos, no readable text, no identifiable people"
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
        asset = db.asset(task["asset_id"])
        episode = db.episode(asset["episode_id"])
        prompt = asset["source_prompt"] or DEFAULT_KEYFRAME_PROMPT
        dest = _episode_dir(config, episode["slug"]) / f"keyframe-shared-v{asset['variant_index']}.png"
        patches = {
            "2": {"text": prompt},
            "3": {"text": KEYFRAME_NEGATIVE_PROMPT},
            "4": {"batch_size": 1},
            "5": {"seed": _seed_for(config, asset["id"])},
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
        prompt_id = _run_stage(local_comfyui, workflows_dir / KEYFRAME_WORKFLOW, patches, dest)
        with Image.open(dest) as image:
            width, height = image.size
        db.transition_asset(
            asset["id"],
            expected_status="running",
            expected_version=asset["state_version"],
            status="awaiting_review",
            path=str(dest),
            sha256=sha256_file(dest),
            width=width,
            height=height,
            comfyui_prompt_id=prompt_id,
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

    return {
        "generate_keyframe": generate_keyframe,
        "generate_motion_test": generate_motion_test,
        "generate_clip": generate_clip,
        "upscale_clip": upscale_clip,
    }
