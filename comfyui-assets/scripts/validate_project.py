"""Static checks for the commercial ComfyUI cafe-loop project.

The checks intentionally do not import ComfyUI. They catch configuration and
licensing-policy regressions before a GPU job is started.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]

OPTIONAL_MPS_WORKFLOW_NAME = "cafe-keyframe-flux-mps.json"
EXPECTED_MPS_KEYFRAME_MODEL = "flux1-schnell-Q4_K_S.gguf"
EXPECTED_KEYFRAME_BATCH_SIZE = 4

# --- Apple Silicon generation pipeline (FLF2V, any 16:9 resolution) --------
# Resolution is a tuning knob on the WanFirstLastFrameToVideo node (768x432 for
# a cheap motion test-render, 1024x576 for the official generation that ships)
# so it is deliberately not asserted here. What must hold regardless of
# resolution is the frame-interpolation arithmetic that makes the clip an
# exact 8.00s loop -- see _check_mps_generation_loop_arithmetic.
MPS_GENERATION_WORKFLOW_NAME = "cafe-flf2v-wan22-mps.json"
EXPECTED_MPS_GENERATION_MODELS = {
    "wan2.2-i2v-a14b-highnoise-q5_k_m.gguf",
    "wan2.2-i2v-a14b-lownoise-q5_k_m.gguf",
    "umt5-xxl-encoder-q8_0.gguf",
    "wan2.2_i2v_a14b_high_noise_lightning_4step.safetensors",
    "wan2.2_i2v_a14b_low_noise_lightning_4step.safetensors",
    "rife_v4.26.safetensors",
}
EXPECTED_MPS_GENERATION_STEPS = 4
EXPECTED_MPS_GENERATION_CFG = 1.0
TARGET_LOOP_SECONDS = 8.0

# --- Pure post-process upscale pipeline (separate workflow, separate file) -
# Deliberately NOT part of the generation workflow: re-diffusing at a higher
# resolution changes the motion, which would invalidate whatever a human
# already approved. Upscaling is post-process only, so approved == shipped.
MPS_UPSCALE_WORKFLOW_NAME = "cafe-upscale-1080p-mps.json"
EXPECTED_MPS_UPSCALE_MODELS = {"realesrgan_x4plus.pth"}
TARGET_OUTPUT_SIZE = (1920, 1080)
TARGET_OUTPUT_FPS = 16

# Popular community upscalers that are licensed CC-BY-NC-SA-4.0. They are a
# tempting drop-in for the ESRGAN slot and would silently make the output
# non-commercial, so the validator rejects them outright.
FORBIDDEN_NONCOMMERCIAL_MODELS = (
    "4x_foolhardy_remacri",
    "4x-ultrasharp",
    "4x_ultrasharp",
)

# --- API-format workflows (workflows/api/*.json) ---------------------------
# ComfyUI's "Save (API Format)" export uses named `inputs` instead of the
# positional `widgets_values` the checks above rely on. Only the API format
# is ever POSTed to /prompt (取捨 1 in studio-architecture-plan.md), so these
# files are what actually runs -- the UI-format checks above only protect
# the human-editable graph you'd open back up in the ComfyUI GUI.
API_KEYFRAME_WORKFLOW_NAME = "cafe-keyframe-flux-mps.json"
API_CHOWCHOW_LORA_KEYFRAME_NAME = "cafe-keyframe-flux-chowchow-lora-mps.json"
API_CHOWCHOW_COMPOSITE_KEYFRAME_NAME = "cafe-keyframe-flux-chowchow-composite-mps.json"
API_GENERATION_WORKFLOW_NAME = "cafe-flf2v-wan22-mps.json"
API_UPSCALE_WORKFLOW_NAME = "cafe-upscale-1080p-mps.json"
EXPECTED_CHOWCHOW_LORA_MODEL = "chowchow-identity-v3.safetensors"
# stages.py's generate_keyframe() special-cases `if "10" in workflow` to patch
# a character-cutout image into the composite path only; if this id drifts,
# that runtime patch silently stops firing instead of erroring.
COMPOSITE_CHARACTER_CUTOUT_NODE_ID = "10"


def _node_by_type(workflow: dict[str, Any], node_type: str) -> list[dict[str, Any]]:
    return [node for node in workflow.get("nodes", []) if node.get("type") == node_type]


def _workflow_text(workflow: dict[str, Any]) -> str:
    return json.dumps(workflow, ensure_ascii=False).lower()


def _check_json_and_links(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"{path.name}: invalid JSON ({exc})"]

    if not isinstance(workflow, dict):
        return None, [f"{path.name}: workflow root must be an object"]

    nodes = workflow.get("nodes")
    links = workflow.get("links")
    if not isinstance(nodes, list) or not isinstance(links, list):
        return workflow, [f"{path.name}: nodes and links must be arrays"]

    node_ids = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(node_ids) != len(set(node_ids)):
        errors.append(f"{path.name}: duplicate node IDs")
    known_ids = set(node_ids)

    for link in links:
        if not isinstance(link, list) or len(link) < 6:
            errors.append(f"{path.name}: malformed link {link!r}")
            continue
        source_id, target_id = link[1], link[3]
        if source_id not in known_ids or target_id not in known_ids:
            errors.append(f"{path.name}: link references missing node {link!r}")

    return workflow, errors


def _check_video_output(
    workflow: dict[str, Any], name: str, expected_frames: int | None
) -> list[str]:
    """Check the video tail of a FLF2V generation workflow.

    `expected_frames=None` skips the fixed frame-count assertion: the
    generated frame count is a tuning knob (lower it to cut peak memory), and
    what actually has to hold is the loop arithmetic in
    _check_mps_generation_loop_arithmetic, not one specific number.
    """
    errors: list[str] = []
    video_nodes = _node_by_type(workflow, "WanFirstLastFrameToVideo")
    if len(video_nodes) != 1:
        return [f"{name}: expected exactly one WanFirstLastFrameToVideo node"]

    frame_values = video_nodes[0].get("widgets_values", [])
    if expected_frames is not None:
        if len(frame_values) < 3 or frame_values[2] != expected_frames:
            found = frame_values[2] if len(frame_values) >= 3 else None
            errors.append(f"{name}: frame count must be {expected_frames} (found {found})")

    create_nodes = _node_by_type(workflow, "CreateVideo")
    save_nodes = _node_by_type(workflow, "SaveVideo")
    if len(create_nodes) != 1 or len(save_nodes) != 1:
        return errors + [f"{name}: expected one CreateVideo and one SaveVideo node"]

    create_id = create_nodes[0].get("id")
    save_id = save_nodes[0].get("id")
    links = workflow.get("links", [])
    output_link = next(
        (
            link
            for link in links
            if isinstance(link, list)
            and len(link) >= 6
            and link[1] == create_id
            and link[2] == 0
            and link[3] == save_id
            and link[4] == 0
            and link[5] == "VIDEO"
        ),
        None,
    )
    if output_link is None:
        errors.append(f"{name}: CreateVideo VIDEO output is not connected to SaveVideo")

    create_image_input = next(
        (
            item
            for item in create_nodes[0].get("inputs", [])
            if isinstance(item, dict) and item.get("name") == "images"
        ),
        None,
    )
    if not create_image_input or create_image_input.get("link") is None:
        errors.append(f"{name}: CreateVideo images input is not connected")

    save_input = next(
        (
            item
            for item in save_nodes[0].get("inputs", [])
            if isinstance(item, dict) and item.get("name") == "video"
        ),
        None,
    )
    if not save_input or save_input.get("link") is None:
        errors.append(f"{name}: SaveVideo video input is not connected")

    return errors


def _check_mps_keyframe(workflow: dict[str, Any], name: str) -> list[str]:
    errors: list[str] = []

    loaders = _node_by_type(workflow, "UnetLoaderGGUF")
    if len(loaders) != 1:
        errors.append(f"{name}: expected exactly one UnetLoaderGGUF node")
    else:
        values = loaders[0].get("widgets_values", [])
        actual = values[0] if values else None
        if actual != EXPECTED_MPS_KEYFRAME_MODEL:
            errors.append(
                f"{name}: GGUF checkpoint must be {EXPECTED_MPS_KEYFRAME_MODEL} (found {actual})"
            )

    samplers = _node_by_type(workflow, "KSampler")
    if len(samplers) != 1:
        errors.append(f"{name}: expected exactly one KSampler node")
    else:
        values = samplers[0].get("widgets_values", [])
        if len(values) < 4 or values[2] != 4:
            errors.append(f"{name}: steps must be 4 (found {values[2] if len(values) > 2 else None})")
        if len(values) < 4 or values[3] != 1.0:
            errors.append(f"{name}: CFG must be 1.0 (found {values[3] if len(values) > 3 else None})")

    latent_nodes = _node_by_type(workflow, "EmptyLatentImage")
    if len(latent_nodes) != 1:
        errors.append(f"{name}: expected exactly one EmptyLatentImage node")
    else:
        values = latent_nodes[0].get("widgets_values", [])
        batch_size = values[2] if len(values) > 2 else None
        if batch_size != EXPECTED_KEYFRAME_BATCH_SIZE:
            errors.append(
                f"{name}: keyframe batch size must be {EXPECTED_KEYFRAME_BATCH_SIZE} "
                f"(found {batch_size})"
            )

    return errors


def _widget(node: dict[str, Any], index: int) -> Any:
    values = node.get("widgets_values", [])
    return values[index] if isinstance(values, list) and len(values) > index else None


def _check_mps_generation_loop_arithmetic(workflow: dict[str, Any], name: str) -> list[str]:
    """Verify the FLF2V generation pipeline still adds up to an 8-second loop.

    The stages are coupled by arithmetic that is easy to break by editing a
    single widget: RIFE turns N frames into 2N-1, one frame is dropped because
    the FLF2V start and end frames are the same picture, and the remainder has
    to divide by the frame rate to land on exactly TARGET_LOOP_SECONDS. This
    holds at any resolution -- resolution is intentionally not checked here,
    see the module docstring on MPS_GENERATION_WORKFLOW_NAME.
    """
    errors: list[str] = []

    samplers = _node_by_type(workflow, "KSamplerAdvanced")
    if len(samplers) != 2:
        errors.append(f"{name}: expected two KSamplerAdvanced nodes (high/low noise)")
    for sampler in samplers:
        steps, cfg = _widget(sampler, 3), _widget(sampler, 4)
        if steps != EXPECTED_MPS_GENERATION_STEPS:
            errors.append(
                f"{name}: Lightning LoRA requires steps={EXPECTED_MPS_GENERATION_STEPS} (found {steps})"
            )
        if cfg != EXPECTED_MPS_GENERATION_CFG:
            errors.append(
                f"{name}: Lightning LoRA requires CFG={EXPECTED_MPS_GENERATION_CFG} (found {cfg})"
            )

    loras = _node_by_type(workflow, "LoraLoaderModelOnly")
    if len(loras) != 2:
        errors.append(f"{name}: expected two LoraLoaderModelOnly nodes (high/low noise)")

    video_nodes = _node_by_type(workflow, "WanFirstLastFrameToVideo")
    interp_nodes = _node_by_type(workflow, "FrameInterpolate")
    trim_nodes = _node_by_type(workflow, "ImageFromBatch")
    create_nodes = _node_by_type(workflow, "CreateVideo")

    if not (video_nodes and interp_nodes and trim_nodes and create_nodes):
        return errors + [f"{name}: pipeline must contain FLF2V, FrameInterpolate, "
                         f"ImageFromBatch and CreateVideo nodes"]

    generated = _widget(video_nodes[0], 2)
    multiplier = _widget(interp_nodes[0], 0)
    kept = _widget(trim_nodes[0], 1)
    fps = _widget(create_nodes[0], 0)

    if not all(isinstance(v, (int, float)) for v in (generated, multiplier, kept, fps)):
        return errors + [f"{name}: could not read frame/fps widgets for the loop-length check"]

    interpolated = (generated - 1) * multiplier + 1
    if kept > interpolated:
        errors.append(
            f"{name}: ImageFromBatch keeps {kept} frames but only {interpolated} exist "
            f"after {multiplier}x interpolation of {generated} frames"
        )
    if kept != interpolated - 1:
        errors.append(
            f"{name}: keep {interpolated - 1} frames, not {kept} — the last interpolated "
            f"frame duplicates the first and stutters at the loop point"
        )
    duration = kept / fps if fps else 0
    if abs(duration - TARGET_LOOP_SECONDS) > 1e-6:
        errors.append(
            f"{name}: loop is {duration:.3f}s, expected {TARGET_LOOP_SECONDS}s "
            f"({kept} frames at {fps} fps)"
        )

    return errors


def _check_mps_upscale(workflow: dict[str, Any], name: str) -> list[str]:
    """Verify the standalone post-process upscale workflow.

    This pipeline must not touch motion or frame count -- it takes an already
    -approved clip and only changes pixels, so what a human approved is what
    ships. LoadVideo/GetVideoComponents feed a plain ESRGAN+Lanczos chain into
    CreateVideo/SaveVideo at a fixed 16fps/1920x1080, no diffusion involved.
    """
    errors: list[str] = []

    for node_type in ("LoadVideo", "GetVideoComponents", "UpscaleModelLoader",
                      "ImageUpscaleWithModel", "ImageScale"):
        if len(_node_by_type(workflow, node_type)) != 1:
            errors.append(f"{name}: expected exactly one {node_type} node")

    loaders = _node_by_type(workflow, "UpscaleModelLoader")
    if loaders:
        model = _widget(loaders[0], 0)
        if not isinstance(model, str) or model.lower() not in EXPECTED_MPS_UPSCALE_MODELS:
            errors.append(
                f"{name}: upscale model must be one of {sorted(EXPECTED_MPS_UPSCALE_MODELS)} "
                f"(found {model})"
            )

    scale_nodes = _node_by_type(workflow, "ImageScale")
    if scale_nodes:
        size = (_widget(scale_nodes[0], 1), _widget(scale_nodes[0], 2))
        if size != TARGET_OUTPUT_SIZE:
            errors.append(
                f"{name}: final resize must be {TARGET_OUTPUT_SIZE[0]}x{TARGET_OUTPUT_SIZE[1]} "
                f"(found {size[0]}x{size[1]})"
            )

    create_nodes = _node_by_type(workflow, "CreateVideo")
    save_nodes = _node_by_type(workflow, "SaveVideo")
    if len(create_nodes) != 1 or len(save_nodes) != 1:
        return errors + [f"{name}: expected one CreateVideo and one SaveVideo node"]

    fps = _widget(create_nodes[0], 0)
    if fps != TARGET_OUTPUT_FPS:
        errors.append(f"{name}: output fps must stay {TARGET_OUTPUT_FPS} (found {fps})")

    create_id = create_nodes[0].get("id")
    save_id = save_nodes[0].get("id")
    links = workflow.get("links", [])
    output_link = next(
        (
            link
            for link in links
            if isinstance(link, list)
            and len(link) >= 6
            and link[1] == create_id
            and link[3] == save_id
            and link[5] == "VIDEO"
        ),
        None,
    )
    if output_link is None:
        errors.append(f"{name}: CreateVideo VIDEO output is not connected to SaveVideo")

    return errors


def _api_nodes_by_class(workflow: dict[str, Any], class_type: str) -> list[dict[str, Any]]:
    return [
        node for node in workflow.values()
        if isinstance(node, dict) and node.get("class_type") == class_type
    ]


def _check_api_json(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"{path.name}: invalid JSON ({exc})"]

    if not isinstance(workflow, dict):
        return None, [f"{path.name}: workflow root must be an object"]

    for node_id, node in workflow.items():
        if not isinstance(node, dict) or "class_type" not in node or "inputs" not in node:
            errors.append(
                f"{path.name}: node {node_id!r} missing class_type/inputs "
                f"(not a 'Save (API Format)' export?)"
            )

    return workflow, errors


def _check_api_chowchow_lora_keyframe(workflow: dict[str, Any], name: str) -> list[str]:
    errors: list[str] = []

    loras = _api_nodes_by_class(workflow, "LoraLoader")
    if len(loras) != 1:
        errors.append(f"{name}: expected exactly one LoraLoader node")
    else:
        lora_name = loras[0]["inputs"].get("lora_name")
        if lora_name != EXPECTED_CHOWCHOW_LORA_MODEL:
            errors.append(
                f"{name}: lora_name must be {EXPECTED_CHOWCHOW_LORA_MODEL} (found {lora_name})"
            )

    samplers = _api_nodes_by_class(workflow, "KSampler")
    if len(samplers) != 1:
        errors.append(f"{name}: expected exactly one KSampler node")
    else:
        inputs = samplers[0]["inputs"]
        if inputs.get("steps") != 4:
            errors.append(f"{name}: steps must be 4 (found {inputs.get('steps')})")
        if inputs.get("cfg") != 1:
            errors.append(f"{name}: CFG must be 1 (found {inputs.get('cfg')})")

    latents = _api_nodes_by_class(workflow, "EmptyLatentImage")
    if len(latents) != 1:
        errors.append(f"{name}: expected exactly one EmptyLatentImage node")
    elif latents[0]["inputs"].get("batch_size") != EXPECTED_KEYFRAME_BATCH_SIZE:
        errors.append(
            f"{name}: keyframe batch size must be {EXPECTED_KEYFRAME_BATCH_SIZE} "
            f"(found {latents[0]['inputs'].get('batch_size')})"
        )

    return errors


def _check_api_chowchow_composite_keyframe(workflow: dict[str, Any], name: str) -> list[str]:
    errors: list[str] = []
    node = workflow.get(COMPOSITE_CHARACTER_CUTOUT_NODE_ID)
    if not isinstance(node, dict) or node.get("class_type") != "LoadImage":
        errors.append(
            f"{name}: node {COMPOSITE_CHARACTER_CUTOUT_NODE_ID!r} must be a LoadImage node "
            f"-- stages.py's generate_keyframe() patches the character cutout image onto "
            f"this exact node id"
        )
    return errors


def _check_api_generation_loop_arithmetic(workflow: dict[str, Any], name: str) -> list[str]:
    """API-format sibling of _check_mps_generation_loop_arithmetic (named
    inputs instead of positional widgets_values, same loop-length invariant).
    """
    errors: list[str] = []

    samplers = _api_nodes_by_class(workflow, "KSamplerAdvanced")
    if len(samplers) != 2:
        errors.append(f"{name}: expected two KSamplerAdvanced nodes (high/low noise)")
    for sampler in samplers:
        inputs = sampler["inputs"]
        if inputs.get("steps") != EXPECTED_MPS_GENERATION_STEPS:
            errors.append(
                f"{name}: Lightning LoRA requires steps={EXPECTED_MPS_GENERATION_STEPS} "
                f"(found {inputs.get('steps')})"
            )
        if inputs.get("cfg") != EXPECTED_MPS_GENERATION_CFG:
            errors.append(
                f"{name}: Lightning LoRA requires CFG={EXPECTED_MPS_GENERATION_CFG} "
                f"(found {inputs.get('cfg')})"
            )

    loras = _api_nodes_by_class(workflow, "LoraLoaderModelOnly")
    if len(loras) != 2:
        errors.append(f"{name}: expected two LoraLoaderModelOnly nodes (high/low noise)")

    video_nodes = _api_nodes_by_class(workflow, "WanFirstLastFrameToVideo")
    interp_nodes = _api_nodes_by_class(workflow, "FrameInterpolate")
    trim_nodes = _api_nodes_by_class(workflow, "ImageFromBatch")
    create_nodes = _api_nodes_by_class(workflow, "CreateVideo")

    if not (video_nodes and interp_nodes and trim_nodes and create_nodes):
        return errors + [(
            f"{name}: pipeline must contain FLF2V, FrameInterpolate, "
            f"ImageFromBatch and CreateVideo nodes"
        )]

    generated = video_nodes[0]["inputs"].get("length")
    multiplier = interp_nodes[0]["inputs"].get("multiplier")
    kept = trim_nodes[0]["inputs"].get("length")
    fps = create_nodes[0]["inputs"].get("fps")

    if not all(isinstance(v, (int, float)) for v in (generated, multiplier, kept, fps)):
        return errors + [f"{name}: could not read frame/fps inputs for the loop-length check"]

    interpolated = (generated - 1) * multiplier + 1
    if kept > interpolated:
        errors.append(
            f"{name}: ImageFromBatch keeps {kept} frames but only {interpolated} exist "
            f"after {multiplier}x interpolation of {generated} frames"
        )
    if kept != interpolated - 1:
        errors.append(
            f"{name}: keep {interpolated - 1} frames, not {kept} — the last interpolated "
            f"frame duplicates the first and stutters at the loop point"
        )
    duration = kept / fps if fps else 0
    if abs(duration - TARGET_LOOP_SECONDS) > 1e-6:
        errors.append(
            f"{name}: loop is {duration:.3f}s, expected {TARGET_LOOP_SECONDS}s "
            f"({kept} frames at {fps} fps)"
        )

    return errors


def _check_api_upscale(workflow: dict[str, Any], name: str) -> list[str]:
    errors: list[str] = []

    for class_type in (
        "LoadVideo", "GetVideoComponents", "UpscaleModelLoader",
        "ImageUpscaleWithModel", "ImageScale",
    ):
        if len(_api_nodes_by_class(workflow, class_type)) != 1:
            errors.append(f"{name}: expected exactly one {class_type} node")

    loaders = _api_nodes_by_class(workflow, "UpscaleModelLoader")
    if loaders:
        model = loaders[0]["inputs"].get("model_name")
        if not isinstance(model, str) or model.lower() not in EXPECTED_MPS_UPSCALE_MODELS:
            errors.append(
                f"{name}: upscale model must be one of {sorted(EXPECTED_MPS_UPSCALE_MODELS)} "
                f"(found {model})"
            )

    scale_nodes = _api_nodes_by_class(workflow, "ImageScale")
    if scale_nodes:
        inputs = scale_nodes[0]["inputs"]
        size = (inputs.get("width"), inputs.get("height"))
        if size != TARGET_OUTPUT_SIZE:
            errors.append(
                f"{name}: final resize must be {TARGET_OUTPUT_SIZE[0]}x{TARGET_OUTPUT_SIZE[1]} "
                f"(found {size[0]}x{size[1]})"
            )

    create_nodes = _api_nodes_by_class(workflow, "CreateVideo")
    if create_nodes and create_nodes[0]["inputs"].get("fps") != TARGET_OUTPUT_FPS:
        errors.append(
            f"{name}: output fps must stay {TARGET_OUTPUT_FPS} "
            f"(found {create_nodes[0]['inputs'].get('fps')})"
        )

    return errors


def _check_ui_api_drift(
    ui_workflow: dict[str, Any], api_workflow: dict[str, Any], ui_name: str, api_name: str
) -> list[str]:
    """Compare hand-picked critical parameters between the UI-format (Save)
    and API-format (Save API Format) exports of the same workflow.

    Both formats round-trip the same ComfyUI graph through unrelated export
    code paths with no automatic sync, so a value edited in one (e.g.
    bumping cfg in the UI and re-saving) can silently drift from the other --
    and only the API file is what actually executes (取捨 1 in
    studio-architecture-plan.md).
    """
    errors: list[str] = []

    def _compare(label: str, ui_value: Any, api_value: Any) -> None:
        if ui_value is None or api_value is None:
            return
        if ui_value != api_value:
            errors.append(
                f"{api_name}: {label} drifted from {ui_name} "
                f"(ui={ui_value!r}, api={api_value!r})"
            )

    ui_unet = _node_by_type(ui_workflow, "UnetLoaderGGUF")
    api_unet = _api_nodes_by_class(api_workflow, "UnetLoaderGGUF")
    if ui_unet and api_unet:
        _compare("UnetLoaderGGUF checkpoint", _widget(ui_unet[0], 0), api_unet[0]["inputs"].get("unet_name"))

    ui_ksampler = _node_by_type(ui_workflow, "KSampler")
    api_ksampler = _api_nodes_by_class(api_workflow, "KSampler")
    if ui_ksampler and api_ksampler:
        _compare("KSampler steps", _widget(ui_ksampler[0], 2), api_ksampler[0]["inputs"].get("steps"))
        _compare("KSampler cfg", _widget(ui_ksampler[0], 3), api_ksampler[0]["inputs"].get("cfg"))

    ui_latent = _node_by_type(ui_workflow, "EmptyLatentImage")
    api_latent = _api_nodes_by_class(api_workflow, "EmptyLatentImage")
    if ui_latent and api_latent:
        _compare("EmptyLatentImage batch_size", _widget(ui_latent[0], 2), api_latent[0]["inputs"].get("batch_size"))

    ui_video = _node_by_type(ui_workflow, "WanFirstLastFrameToVideo")
    api_video = _api_nodes_by_class(api_workflow, "WanFirstLastFrameToVideo")
    if ui_video and api_video:
        _compare("WanFirstLastFrameToVideo frame count", _widget(ui_video[0], 2), api_video[0]["inputs"].get("length"))

    ui_interp = _node_by_type(ui_workflow, "FrameInterpolate")
    api_interp = _api_nodes_by_class(api_workflow, "FrameInterpolate")
    if ui_interp and api_interp:
        _compare("FrameInterpolate multiplier", _widget(ui_interp[0], 0), api_interp[0]["inputs"].get("multiplier"))

    ui_trim = _node_by_type(ui_workflow, "ImageFromBatch")
    api_trim = _api_nodes_by_class(api_workflow, "ImageFromBatch")
    if ui_trim and api_trim:
        _compare("ImageFromBatch kept frames", _widget(ui_trim[0], 1), api_trim[0]["inputs"].get("length"))

    ui_create = _node_by_type(ui_workflow, "CreateVideo")
    api_create = _api_nodes_by_class(api_workflow, "CreateVideo")
    if ui_create and api_create:
        _compare("CreateVideo fps", _widget(ui_create[0], 0), api_create[0]["inputs"].get("fps"))

    ui_upscale_loader = _node_by_type(ui_workflow, "UpscaleModelLoader")
    api_upscale_loader = _api_nodes_by_class(api_workflow, "UpscaleModelLoader")
    if ui_upscale_loader and api_upscale_loader:
        _compare(
            "UpscaleModelLoader model", _widget(ui_upscale_loader[0], 0),
            api_upscale_loader[0]["inputs"].get("model_name"),
        )

    ui_scale = _node_by_type(ui_workflow, "ImageScale")
    api_scale = _api_nodes_by_class(api_workflow, "ImageScale")
    if ui_scale and api_scale:
        _compare("ImageScale width", _widget(ui_scale[0], 1), api_scale[0]["inputs"].get("width"))
        _compare("ImageScale height", _widget(ui_scale[0], 2), api_scale[0]["inputs"].get("height"))

    return errors


def validate_project(root: Path) -> list[str]:
    """Return human-readable validation errors for a project root."""

    root = Path(root)
    errors: list[str] = []

    mps_path = root / "workflows" / OPTIONAL_MPS_WORKFLOW_NAME
    if mps_path.is_file():
        mps_workflow, mps_errors = _check_json_and_links(mps_path)
        errors.extend(mps_errors)
        if mps_workflow is not None:
            if "flux1-dev" in _workflow_text(mps_workflow):
                errors.append(f"{OPTIONAL_MPS_WORKFLOW_NAME}: forbidden flux1-dev reference")
            errors.extend(_check_mps_keyframe(mps_workflow, OPTIONAL_MPS_WORKFLOW_NAME))

    mps_generation_path = root / "workflows" / MPS_GENERATION_WORKFLOW_NAME
    if mps_generation_path.is_file():
        generation_workflow, generation_errors = _check_json_and_links(mps_generation_path)
        errors.extend(generation_errors)
        if generation_workflow is not None:
            if "flux1-dev" in _workflow_text(generation_workflow):
                errors.append(f"{MPS_GENERATION_WORKFLOW_NAME}: forbidden flux1-dev reference")
            errors.extend(
                _check_video_output(
                    generation_workflow, MPS_GENERATION_WORKFLOW_NAME, expected_frames=None
                )
            )
            errors.extend(
                _check_mps_generation_loop_arithmetic(generation_workflow, MPS_GENERATION_WORKFLOW_NAME)
            )
            for model in EXPECTED_MPS_GENERATION_MODELS:
                if model not in _workflow_text(generation_workflow):
                    errors.append(
                        f"{MPS_GENERATION_WORKFLOW_NAME}: missing expected model {model}"
                    )

    mps_upscale_path = root / "workflows" / MPS_UPSCALE_WORKFLOW_NAME
    if mps_upscale_path.is_file():
        upscale_workflow, upscale_errors = _check_json_and_links(mps_upscale_path)
        errors.extend(upscale_errors)
        if upscale_workflow is not None:
            errors.extend(_check_mps_upscale(upscale_workflow, MPS_UPSCALE_WORKFLOW_NAME))
            for model in EXPECTED_MPS_UPSCALE_MODELS:
                if model not in _workflow_text(upscale_workflow):
                    errors.append(
                        f"{MPS_UPSCALE_WORKFLOW_NAME}: missing expected model {model}"
                    )

    # --- API-format workflows (workflows/api/*.json) ------------------------
    # Only these files are ever POSTed to ComfyUI's /prompt endpoint; the
    # UI-format checks above protect the human-editable graph, these protect
    # what actually runs.
    api_dir = root / "workflows" / "api"

    api_keyframe_path = api_dir / API_KEYFRAME_WORKFLOW_NAME
    if api_keyframe_path.is_file():
        api_keyframe_workflow, api_keyframe_errors = _check_api_json(api_keyframe_path)
        errors.extend(api_keyframe_errors)
        if api_keyframe_workflow is not None and mps_path.is_file() and mps_workflow is not None:
            errors.extend(
                _check_ui_api_drift(
                    mps_workflow, api_keyframe_workflow,
                    OPTIONAL_MPS_WORKFLOW_NAME, API_KEYFRAME_WORKFLOW_NAME,
                )
            )

    api_chowchow_lora_path = api_dir / API_CHOWCHOW_LORA_KEYFRAME_NAME
    if api_chowchow_lora_path.is_file():
        chowchow_lora_workflow, chowchow_lora_errors = _check_api_json(api_chowchow_lora_path)
        errors.extend(chowchow_lora_errors)
        if chowchow_lora_workflow is not None:
            if "flux1-dev" in json.dumps(chowchow_lora_workflow).lower():
                errors.append(f"{API_CHOWCHOW_LORA_KEYFRAME_NAME}: forbidden flux1-dev reference")
            errors.extend(
                _check_api_chowchow_lora_keyframe(chowchow_lora_workflow, API_CHOWCHOW_LORA_KEYFRAME_NAME)
            )

    api_chowchow_composite_path = api_dir / API_CHOWCHOW_COMPOSITE_KEYFRAME_NAME
    if api_chowchow_composite_path.is_file():
        chowchow_composite_workflow, chowchow_composite_errors = _check_api_json(
            api_chowchow_composite_path
        )
        errors.extend(chowchow_composite_errors)
        if chowchow_composite_workflow is not None:
            errors.extend(
                _check_api_chowchow_composite_keyframe(
                    chowchow_composite_workflow, API_CHOWCHOW_COMPOSITE_KEYFRAME_NAME
                )
            )

    api_generation_path = api_dir / API_GENERATION_WORKFLOW_NAME
    if api_generation_path.is_file():
        api_generation_workflow, api_generation_errors = _check_api_json(api_generation_path)
        errors.extend(api_generation_errors)
        if api_generation_workflow is not None:
            errors.extend(
                _check_api_generation_loop_arithmetic(api_generation_workflow, API_GENERATION_WORKFLOW_NAME)
            )
            if mps_generation_path.is_file() and generation_workflow is not None:
                errors.extend(
                    _check_ui_api_drift(
                        generation_workflow, api_generation_workflow,
                        MPS_GENERATION_WORKFLOW_NAME, API_GENERATION_WORKFLOW_NAME,
                    )
                )

    api_upscale_path = api_dir / API_UPSCALE_WORKFLOW_NAME
    if api_upscale_path.is_file():
        api_upscale_workflow, api_upscale_errors = _check_api_json(api_upscale_path)
        errors.extend(api_upscale_errors)
        if api_upscale_workflow is not None:
            errors.extend(_check_api_upscale(api_upscale_workflow, API_UPSCALE_WORKFLOW_NAME))
            if mps_upscale_path.is_file() and upscale_workflow is not None:
                errors.extend(
                    _check_ui_api_drift(
                        upscale_workflow, api_upscale_workflow,
                        MPS_UPSCALE_WORKFLOW_NAME, API_UPSCALE_WORKFLOW_NAME,
                    )
                )

    # Sweep every workflow, not just the ones enumerated above: a non-commercial
    # upscaler dropped into any workflow taints the output it produces.
    workflow_globs = list((root / "workflows").glob("*.json"))
    if api_dir.is_dir():
        workflow_globs += list(api_dir.glob("*.json"))
    for path in sorted(workflow_globs):
        text = path.read_text(encoding="utf-8").lower()
        for forbidden in FORBIDDEN_NONCOMMERCIAL_MODELS:
            if forbidden in text:
                errors.append(
                    f"{path.name}: {forbidden} is CC-BY-NC-SA-4.0 (non-commercial); "
                    f"use RealESRGAN_x4plus.pth (BSD-3-Clause) instead"
                )

    downloader = root / "scripts" / "download-models.sh"
    if not downloader.is_file():
        errors.append(f"missing downloader: {downloader}")
    else:
        downloader_text = downloader.read_text(encoding="utf-8").lower()
        if "flux1-dev" in downloader_text or "download_flux" in downloader_text:
            errors.append("download script contains the non-commercial FLUX.1 Dev path")
        if mps_path.is_file() and EXPECTED_MPS_KEYFRAME_MODEL.lower() not in downloader_text:
            errors.append(
                f"download script does not contain {EXPECTED_MPS_KEYFRAME_MODEL} "
                f"(required by {OPTIONAL_MPS_WORKFLOW_NAME})"
            )
        if mps_generation_path.is_file():
            for model in EXPECTED_MPS_GENERATION_MODELS:
                if model not in downloader_text:
                    errors.append(
                        f"download script does not contain {model} "
                        f"(required by {MPS_GENERATION_WORKFLOW_NAME})"
                    )
        if mps_upscale_path.is_file():
            for model in EXPECTED_MPS_UPSCALE_MODELS:
                if model not in downloader_text:
                    errors.append(
                        f"download script does not contain {model} "
                        f"(required by {MPS_UPSCALE_WORKFLOW_NAME})"
                    )

    return errors


def main(argv: Iterable[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    root = Path(arguments[0]).resolve() if arguments else ROOT
    errors = validate_project(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"PASS: commercial project validation ({root})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
