from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from lyria_auto.config import AppConfig
from lyria_auto.db import StateDB
from lyria_auto.studio import stages
from lyria_auto.studio.worker import StudioWorker

_MINIMAL_KEYFRAME_WORKFLOW = {nid: {"class_type": "X", "inputs": {}} for nid in ["2", "3", "4", "5"]}
_MINIMAL_MOTION_WORKFLOW = {
    nid: {"class_type": "X", "inputs": {}} for nid in ["5", "6", "7", "8", "9", "14"]
}
_MINIMAL_UPSCALE_WORKFLOW = {"1": {"class_type": "X", "inputs": {}}}


class FakeComfyUIClient:
    def __init__(self, output_factory):
        self.output_factory = output_factory
        self.submitted_workflows: list[dict] = []
        self.staged: list[tuple[str, str]] = []

    def stage_input_file(self, src, *, name_hint):
        self.staged.append((str(src), name_hint))
        return f"staged-{name_hint}.bin"

    def submit(self, workflow):
        self.submitted_workflows.append(json.loads(json.dumps(workflow)))
        return "fake-prompt-id"

    def wait_for_result(self, prompt_id, **kwargs):
        return {"prompt_id": prompt_id}

    def fetch_output(self, history_entry, dest):
        self.output_factory(Path(dest))
        return Path(dest)


def make_config(root: Path, *, seed: int = 100) -> AppConfig:
    return AppConfig(
        settings={"project": {"random_seed": seed, "workspace": "workspace"}},
        prompts={},
        channels={},
        root=root,
    )


def _write_workflows(workflows_dir: Path) -> None:
    workflows_dir.mkdir(parents=True, exist_ok=True)
    (workflows_dir / stages.KEYFRAME_WORKFLOW).write_text(json.dumps(_MINIMAL_KEYFRAME_WORKFLOW))
    (workflows_dir / stages.MOTION_WORKFLOW).write_text(json.dumps(_MINIMAL_MOTION_WORKFLOW))
    (workflows_dir / stages.UPSCALE_WORKFLOW).write_text(json.dumps(_MINIMAL_UPSCALE_WORKFLOW))


@pytest.fixture(scope="session")
def tiny_video_bytes(tmp_path_factory) -> bytes:
    path = tmp_path_factory.mktemp("fixtures") / "tiny.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=8:d=1",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )
    return path.read_bytes()


@pytest.fixture(scope="session")
def eight_second_clip_bytes(tmp_path_factory) -> bytes:
    """A real 8.0s clip, matching what a clip_1080p asset must measure to
    build an exact 64s macro-loop (stages.SEGMENT_SECONDS)."""
    path = tmp_path_factory.mktemp("fixtures") / "eight_seconds.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:r=16:d=8",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )
    return path.read_bytes()


@pytest.fixture(scope="session")
def short_audio_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("fixtures") / "tone.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", str(path)],
        check=True, capture_output=True,
    )
    return path


def _write_png(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 8)).save(dest)


def _run(db: StateDB, handlers: dict) -> None:
    """Drive one task through the real worker, mirroring test_studio_worker.py:
    handlers assume the worker already flipped the asset queued -> running."""
    worker = StudioWorker(db, handlers=handlers)
    assert worker.run_once() is True


def test_generate_keyframe_writes_file_and_transitions_asset(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    asset_id = db.create_asset(episode_id, "keyframe", "shared")
    db.enqueue_task(episode_id, "generate_keyframe", asset_id=asset_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=_write_png)
    config = make_config(tmp_path)
    handlers = stages.build_handlers(config, comfyui, workflows_dir)

    _run(db, handlers)

    asset = db.asset(asset_id)
    assert asset["status"] == "awaiting_review"
    assert asset["width"] == 12
    assert asset["height"] == 8
    assert Path(asset["path"]).exists()
    assert asset["comfyui_prompt_id"] == "fake-prompt-id"

    submitted = comfyui.submitted_workflows[0]
    assert submitted["2"]["inputs"]["text"] == stages.DEFAULT_KEYFRAME_PROMPT
    assert submitted["4"]["inputs"]["batch_size"] == 1
    assert submitted["5"]["inputs"]["seed"] == 100 + asset_id


def test_generate_keyframe_stages_approved_chowchow_reference_for_composite_workflow(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    asset_id = db.create_asset(episode_id, "keyframe", "shared")
    db.enqueue_task(episode_id, "generate_keyframe", asset_id=asset_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    composite_workflow = {
        "2": {"class_type": "X", "inputs": {}},
        "3": {"class_type": "X", "inputs": {}},
        "4": {"class_type": "X", "inputs": {}},
        "5": {"class_type": "X", "inputs": {}},
        "10": {"class_type": "LoadImage", "inputs": {"image": "placeholder.png"}},
    }
    (workflows_dir / stages.KEYFRAME_WORKFLOW).write_text(json.dumps(composite_workflow))
    reference = (
        tmp_path
        / "character-reference"
        / "chowchow"
        / "approved"
        / "master-lying-transparent-v1.png"
    )
    reference.parent.mkdir(parents=True)
    reference.write_bytes(b"fake-transparent-png")

    comfyui = FakeComfyUIClient(output_factory=_write_png)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    submitted = comfyui.submitted_workflows[0]
    assert comfyui.staged == [(str(reference), "chowchow-lying")]
    assert submitted["10"]["inputs"]["image"] == "staged-chowchow-lying.bin"


def test_generate_keyframe_uses_edited_prompt_from_reject_flow(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    asset_id = db.create_asset(episode_id, "keyframe", "shared", source_prompt="brighter lighting")
    db.enqueue_task(episode_id, "generate_keyframe", asset_id=asset_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=_write_png)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    assert comfyui.submitted_workflows[0]["2"]["inputs"]["text"] == "brighter lighting"


def _approve_keyframe(db: StateDB, episode_id: int, path: Path) -> int:
    keyframe_id = db.create_asset(episode_id, "keyframe", "shared")
    db.transition_asset(
        keyframe_id, expected_status="queued", expected_version=0,
        status="approved", path=str(path),
    )
    return keyframe_id


def test_generate_motion_test_uses_768x432_and_approved_keyframe(tmp_path, tiny_video_bytes):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    keyframe_path = tmp_path / "keyframe.png"
    keyframe_path.write_bytes(b"fake-image-bytes")
    _approve_keyframe(db, episode_id, keyframe_path)

    asset_id = db.create_asset(episode_id, "motion_test", "sleep")
    db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: dest.write_bytes(tiny_video_bytes))
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    asset = db.asset(asset_id)
    assert asset["status"] == "awaiting_review"
    assert asset["width"] == 64
    assert asset["height"] == 64
    assert asset["duration_seconds"] == pytest.approx(1.0, abs=0.3)
    assert Path(asset["path"]).exists()

    submitted = comfyui.submitted_workflows[0]
    assert submitted["5"]["inputs"]["text"] == stages.DEFAULT_MOTION_PROMPTS["sleep"]
    assert submitted["9"]["inputs"]["width"] == 768
    assert submitted["9"]["inputs"]["height"] == 432
    assert submitted["7"]["inputs"]["image"] == submitted["8"]["inputs"]["image"]
    assert comfyui.staged[0][0] == str(keyframe_path)


def test_generate_clip_uses_1024x576_and_carried_over_prompt(tmp_path, tiny_video_bytes):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    keyframe_path = tmp_path / "keyframe.png"
    keyframe_path.write_bytes(b"fake-image-bytes")
    _approve_keyframe(db, episode_id, keyframe_path)

    clip_id = db.create_asset(
        episode_id, "clip", "lookup", source_prompt="custom lookup wording from a reject edit"
    )
    db.enqueue_task(episode_id, "generate_clip", asset_id=clip_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: dest.write_bytes(tiny_video_bytes))
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    asset = db.asset(clip_id)
    assert asset["status"] == "awaiting_review"

    submitted = comfyui.submitted_workflows[0]
    assert submitted["5"]["inputs"]["text"] == "custom lookup wording from a reject edit"
    assert submitted["9"]["inputs"]["width"] == 1024
    assert submitted["9"]["inputs"]["height"] == 576


def test_generate_clip_reuses_the_inherited_seed_instead_of_deriving_a_new_one(
    tmp_path, tiny_video_bytes
):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    keyframe_path = tmp_path / "keyframe.png"
    keyframe_path.write_bytes(b"fake-image-bytes")
    _approve_keyframe(db, episode_id, keyframe_path)

    clip_id = db.create_asset(episode_id, "clip", "sleep", source_seed=777)
    db.enqueue_task(episode_id, "generate_clip", asset_id=clip_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: dest.write_bytes(tiny_video_bytes))
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    submitted = comfyui.submitted_workflows[0]
    assert submitted["14"]["inputs"]["noise_seed"] == 777
    assert db.asset(clip_id)["source_seed"] == 777


def test_generate_motion_test_records_the_seed_it_used_when_none_was_inherited(
    tmp_path, tiny_video_bytes
):
    """No prior stage to inherit from (this *is* the first stage), so it
    derives one and records it -- that's what approving it later hands to
    generate_clip (see test_studio_app.py's carry-over test)."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    keyframe_path = tmp_path / "keyframe.png"
    keyframe_path.write_bytes(b"fake-image-bytes")
    _approve_keyframe(db, episode_id, keyframe_path)

    asset_id = db.create_asset(episode_id, "motion_test", "sleep")
    db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: dest.write_bytes(tiny_video_bytes))
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    submitted = comfyui.submitted_workflows[0]
    recorded_seed = db.asset(asset_id)["source_seed"]
    assert recorded_seed is not None
    assert submitted["14"]["inputs"]["noise_seed"] == recorded_seed


def test_generate_motion_test_without_approved_keyframe_fails_the_task(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    asset_id = db.create_asset(episode_id, "motion_test", "sleep")
    task_id = db.enqueue_task(episode_id, "generate_motion_test", asset_id=asset_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: None)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    assert db.task(task_id)["status"] == "failed"
    assert "找不到已核准" in db.task(task_id)["error"]
    assert db.asset(asset_id)["status"] == "failed"


def test_upscale_clip_stages_approved_clip_and_writes_1080p_asset(tmp_path, tiny_video_bytes):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(tiny_video_bytes)
    clip_id = db.create_asset(episode_id, "clip", "sleep")
    db.transition_asset(
        clip_id, expected_status="queued", expected_version=0,
        status="approved", path=str(clip_path),
    )

    asset_id = db.create_asset(episode_id, "clip_1080p", "sleep")
    db.enqueue_task(episode_id, "upscale_clip", asset_id=asset_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: dest.write_bytes(tiny_video_bytes))
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    asset = db.asset(asset_id)
    assert asset["status"] == "awaiting_review"
    assert Path(asset["path"]).exists()
    assert comfyui.staged[0][0] == str(clip_path)
    assert comfyui.submitted_workflows[0]["1"]["inputs"]["file"] == f"staged-clip-{clip_id}.bin"


def test_generate_clip_and_upscale_clip_use_remote_comfyui_when_configured(
    tmp_path, tiny_video_bytes
):
    """1c and 2 are the GPU-heavy stages worth a rented instance; 1a/1b are
    cheap/fast enough locally that they stay put even when remote_comfyui
    is configured (see the 2026-08-18 cloud-economics writeup)."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    keyframe_path = tmp_path / "keyframe.png"
    keyframe_path.write_bytes(b"fake-image-bytes")
    _approve_keyframe(db, episode_id, keyframe_path)
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(tiny_video_bytes)
    clip_id = db.create_asset(episode_id, "clip", "sleep")
    db.transition_asset(
        clip_id, expected_status="queued", expected_version=0,
        status="approved", path=str(clip_path),
    )

    clip_asset_id = db.create_asset(episode_id, "clip", "lookup")
    db.enqueue_task(episode_id, "generate_clip", asset_id=clip_asset_id)
    upscale_asset_id = db.create_asset(episode_id, "clip_1080p", "sleep")
    db.enqueue_task(episode_id, "upscale_clip", asset_id=upscale_asset_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    local = FakeComfyUIClient(output_factory=lambda dest: dest.write_bytes(tiny_video_bytes))
    remote = FakeComfyUIClient(output_factory=lambda dest: dest.write_bytes(tiny_video_bytes))
    handlers = stages.build_handlers(
        make_config(tmp_path), local, workflows_dir, remote_comfyui=remote
    )

    worker = StudioWorker(db, handlers=handlers)
    while worker.run_once():
        pass

    assert db.asset(clip_asset_id)["status"] == "awaiting_review"
    assert db.asset(upscale_asset_id)["status"] == "awaiting_review"
    assert len(remote.submitted_workflows) == 2, "generate_clip + upscale_clip both went remote"
    assert local.submitted_workflows == [], "nothing should have run locally"


def test_generate_keyframe_stays_local_even_when_remote_comfyui_is_configured(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    asset_id = db.create_asset(episode_id, "keyframe", "shared")
    db.enqueue_task(episode_id, "generate_keyframe", asset_id=asset_id)

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    local = FakeComfyUIClient(output_factory=_write_png)
    remote = FakeComfyUIClient(output_factory=_write_png)
    handlers = stages.build_handlers(
        make_config(tmp_path), local, workflows_dir, remote_comfyui=remote
    )

    _run(db, handlers)

    assert db.asset(asset_id)["status"] == "awaiting_review"
    assert len(local.submitted_workflows) == 1
    assert remote.submitted_workflows == []


def _approve_clip_1080p(db: StateDB, episode_id: int, role: str, path: Path) -> int:
    asset_id = db.create_asset(episode_id, "clip_1080p", role)
    db.transition_asset(
        asset_id, expected_status="queued", expected_version=0,
        status="approved", path=str(path),
    )
    return asset_id


def _approve_loop(db: StateDB, episode_id: int, path: Path) -> int:
    asset_id = db.create_asset(episode_id, "loop", "shared")
    db.transition_asset(
        asset_id, expected_status="queued", expected_version=0,
        status="approved", path=str(path),
    )
    return asset_id


def test_build_loop_concatenates_seven_sleep_and_one_lookup(tmp_path, eight_second_clip_bytes):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")

    sleep_path = tmp_path / "sleep.mp4"
    sleep_path.write_bytes(eight_second_clip_bytes)
    lookup_path = tmp_path / "lookup.mp4"
    lookup_path.write_bytes(eight_second_clip_bytes)
    _approve_clip_1080p(db, episode_id, "sleep", sleep_path)
    _approve_clip_1080p(db, episode_id, "lookup", lookup_path)

    db.enqueue_task(episode_id, "build_loop")

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: None)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    loop_assets = db.assets_for_episode(episode_id, kind="loop")
    assert len(loop_assets) == 1
    loop_asset = loop_assets[0]
    assert loop_asset["status"] == "awaiting_review"
    assert loop_asset["role"] == "shared"
    assert Path(loop_asset["path"]).exists()
    assert loop_asset["duration_seconds"] == pytest.approx(64.0, abs=0.5)
    assert comfyui.submitted_workflows == [], "build_loop is pure ffmpeg, no ComfyUI call"


def test_build_loop_rejects_wrong_length_source_clip(
    tmp_path, tiny_video_bytes, eight_second_clip_bytes
):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")

    sleep_path = tmp_path / "sleep.mp4"
    sleep_path.write_bytes(tiny_video_bytes)  # ~1s, not the required 8s
    lookup_path = tmp_path / "lookup.mp4"
    lookup_path.write_bytes(eight_second_clip_bytes)
    _approve_clip_1080p(db, episode_id, "sleep", sleep_path)
    _approve_clip_1080p(db, episode_id, "lookup", lookup_path)

    task_id = db.enqueue_task(episode_id, "build_loop")

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: None)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    assert db.task(task_id)["status"] == "failed"
    assert "expected 8.0s" in db.task(task_id)["error"]


def test_render_final_repeats_loop_to_match_audio_length(
    tmp_path, tiny_video_bytes, short_audio_path
):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")

    loop_path = tmp_path / "loop.mp4"
    loop_path.write_bytes(tiny_video_bytes)  # ~1s loop
    _approve_loop(db, episode_id, loop_path)

    db.enqueue_task(episode_id, "render_final", payload={"audio_path": str(short_audio_path)})

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: None)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    final_assets = db.assets_for_episode(episode_id, kind="final")
    assert len(final_assets) == 1
    final_asset = final_assets[0]
    assert final_asset["status"] == "awaiting_review"
    assert Path(final_asset["path"]).exists()
    assert final_asset["duration_seconds"] == pytest.approx(5.0, abs=0.5)
    assert comfyui.submitted_workflows == [], "render_final is pure ffmpeg, no ComfyUI call"


def test_render_final_requires_audio_path_in_payload(tmp_path, tiny_video_bytes):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    loop_path = tmp_path / "loop.mp4"
    loop_path.write_bytes(tiny_video_bytes)
    _approve_loop(db, episode_id, loop_path)

    task_id = db.enqueue_task(episode_id, "render_final")

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: None)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    assert db.task(task_id)["status"] == "failed"
    assert "audio_path" in db.task(task_id)["error"]
