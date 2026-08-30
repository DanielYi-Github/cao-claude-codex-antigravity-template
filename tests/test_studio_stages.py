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
    def __init__(self, output_factory, *, output_count: int = 1):
        self.output_factory = output_factory
        self.output_count = output_count
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

    def fetch_all_outputs(self, history_entry):
        return [
            {"filename": f"fake-{i}.png", "subfolder": "", "type": "output"}
            for i in range(self.output_count)
        ]

    def download_output(self, item, dest):
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
    handlers assume the worker already flipped the asset queued -> running.

    Also asserts nothing ended up 'failed' -- run_once() returning True only
    means a task was claimed and processed, not that it succeeded (a real
    gap: this let test_generate_keyframe_uses_prompt_from_task_payload pass
    while its task actually failed on a batch_size/output-count mismatch,
    since the test's own assertions only looked at the submitted workflow,
    captured before the failure). Tests that intentionally expect a failure
    call worker.run_once() directly instead of this helper.
    """
    worker = StudioWorker(db, handlers=handlers)
    assert worker.run_once() is True
    failed = db.tasks_by_status("failed")
    assert failed == [], f"task unexpectedly failed: {failed[0]['error']}"


def test_generate_keyframe_fans_out_a_batch_into_separate_assets(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    task_id = db.enqueue_task(episode_id, "generate_keyframe")

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=_write_png, output_count=3)
    config = make_config(tmp_path)
    config.settings.setdefault("studio", {})["keyframe_batch_size"] = 3
    handlers = stages.build_handlers(config, comfyui, workflows_dir)

    _run(db, handlers)

    assets = db.assets_for_episode(episode_id, kind="keyframe")
    assert len(assets) == 3
    assert [a["variant_index"] for a in assets] == [0, 1, 2]
    for asset in assets:
        assert asset["status"] == "awaiting_review"
        assert asset["width"] == 12
        assert asset["height"] == 8
        assert Path(asset["path"]).exists()
        assert asset["comfyui_prompt_id"] == "fake-prompt-id"
        assert asset["source_prompt"] == stages.DEFAULT_KEYFRAME_PROMPT

    submitted = comfyui.submitted_workflows[0]
    assert submitted["2"]["inputs"]["text"] == stages.DEFAULT_KEYFRAME_PROMPT
    assert submitted["4"]["inputs"]["batch_size"] == 3
    assert submitted["5"]["inputs"]["seed"] == 100 + task_id


def test_generate_keyframe_raises_if_comfyui_returns_the_wrong_count(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    db.enqueue_task(
        episode_id, "generate_keyframe", payload={"batch_size": 5}
    )

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=_write_png, output_count=2)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    worker = StudioWorker(db, handlers=handlers)
    assert worker.run_once() is True

    task = db.tasks_for_episode(episode_id)[0]
    assert task["status"] == "failed"
    assert "5" in task["error"] and "2" in task["error"]
    assert db.assets_for_episode(episode_id, kind="keyframe") == []


def test_generate_keyframe_leaves_no_partial_batch_when_a_download_fails(tmp_path):
    """Regression for a real bug: a download/validation failure partway
    through the batch used to leave the images that succeeded before it
    published as awaiting_review, while the task itself showed "failed" --
    an inconsistent state where a broken batch still looked selectable."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    db.enqueue_task(episode_id, "generate_keyframe", payload={"batch_size": 3})

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)

    calls = {"n": 0}

    def _fail_on_second(dest: Path) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated download failure")
        _write_png(dest)

    comfyui = FakeComfyUIClient(output_factory=_fail_on_second, output_count=3)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    worker = StudioWorker(db, handlers=handlers)
    assert worker.run_once() is True

    task = db.tasks_for_episode(episode_id)[0]
    assert task["status"] == "failed"
    assert db.assets_for_episode(episode_id, kind="keyframe") == [], (
        "no asset row should exist -- the failed image was still being "
        "downloaded, before any create_asset() call"
    )


def test_generate_keyframe_regenerate_uses_a_different_seed_and_continues_variant_index(tmp_path):
    """The seed bug this guards against: if regenerate reused the same
    seed, ComfyUI would return the exact same images and "regenerate"
    would silently do nothing -- the same failure shape as the original
    prompt/mask bugs this whole console redesign was meant to fix."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=_write_png, output_count=2)
    config = make_config(tmp_path)
    config.settings.setdefault("studio", {})["keyframe_batch_size"] = 2
    handlers = stages.build_handlers(config, comfyui, workflows_dir)

    db.enqueue_task(episode_id, "generate_keyframe", payload={"positive_prompt": "cafe scene"})
    _run(db, handlers)
    first_batch = db.assets_for_episode(episode_id, kind="keyframe")
    assert [a["variant_index"] for a in first_batch] == [0, 1]

    db.supersede_assets(episode_id, "keyframe", "shared")
    db.enqueue_task(episode_id, "generate_keyframe", payload={"positive_prompt": "cafe scene"})
    _run(db, handlers)

    seeds = [w["5"]["inputs"]["seed"] for w in comfyui.submitted_workflows]
    assert seeds[0] != seeds[1], "regenerate must not reuse the first batch's seed"

    all_keyframes = db.assets_for_episode(episode_id, kind="keyframe")
    second_batch = [a for a in all_keyframes if a["status"] == "awaiting_review"]
    assert [a["variant_index"] for a in second_batch] == [2, 3], (
        "variant_index must continue past the superseded batch, not reset to 0 -- "
        "a reset would collide on disk (keyframe-shared-v0.png overwritten) and in "
        "the DB (two rows at the same episode/kind/role/variant_index)"
    )
    superseded = [a for a in all_keyframes if a["status"] == "superseded"]
    assert len(superseded) == 2


def test_generate_keyframe_stages_approved_chowchow_reference_for_composite_workflow(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    db.enqueue_task(episode_id, "generate_keyframe")

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

    comfyui = FakeComfyUIClient(output_factory=_write_png, output_count=1)
    config = make_config(tmp_path)
    config.settings.setdefault("studio", {})["keyframe_batch_size"] = 1
    handlers = stages.build_handlers(config, comfyui, workflows_dir)

    _run(db, handlers)

    submitted = comfyui.submitted_workflows[0]
    assert comfyui.staged == [(str(reference), "chowchow-lying")]
    assert submitted["10"]["inputs"]["image"] == "staged-chowchow-lying.bin"


def test_generate_keyframe_uses_prompt_from_task_payload(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    db.enqueue_task(
        episode_id, "generate_keyframe",
        payload={"positive_prompt": "brighter lighting", "negative_prompt": "no shadows"},
    )

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=_write_png, output_count=1)
    config = make_config(tmp_path)
    config.settings.setdefault("studio", {})["keyframe_batch_size"] = 1
    handlers = stages.build_handlers(config, comfyui, workflows_dir)

    _run(db, handlers)

    submitted = comfyui.submitted_workflows[0]
    assert submitted["2"]["inputs"]["text"] == "brighter lighting"
    assert submitted["3"]["inputs"]["text"] == "no shadows"


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

    worker = StudioWorker(db, handlers=handlers)
    assert worker.run_once() is True

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
    db.enqueue_task(episode_id, "generate_keyframe")

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    local = FakeComfyUIClient(output_factory=_write_png)
    remote = FakeComfyUIClient(output_factory=_write_png)
    config = make_config(tmp_path)
    config.settings.setdefault("studio", {})["keyframe_batch_size"] = 1
    handlers = stages.build_handlers(config, local, workflows_dir, remote_comfyui=remote)

    _run(db, handlers)

    assets = db.assets_for_episode(episode_id, kind="keyframe")
    assert len(assets) == 1
    assert assets[0]["status"] == "awaiting_review"
    assert len(local.submitted_workflows) == 1
    assert remote.submitted_workflows == []


def _approve_clip_1080p(db: StateDB, episode_id: int, role: str, path: Path) -> int:
    asset_id = db.create_asset(episode_id, "clip_1080p", role)
    db.transition_asset(
        asset_id, expected_status="queued", expected_version=0,
        status="approved", path=str(path),
    )
    return asset_id


def _approve_motion_test(db: StateDB, episode_id: int, role: str, path: Path) -> int:
    asset_id = db.create_asset(episode_id, "motion_test", role)
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

    worker = StudioWorker(db, handlers=handlers)
    assert worker.run_once() is True

    assert db.task(task_id)["status"] == "failed"
    assert "expected 8.0s" in db.task(task_id)["error"]


def test_build_loop_preview_concatenates_seven_sleep_and_one_lookup_from_motion_test(
    tmp_path, eight_second_clip_bytes
):
    """Tab 2's low-res preview -- same shape as build_loop, but fed by
    approved motion_test (768x432) instead of clip_1080p (1920x1080)."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")

    sleep_path = tmp_path / "sleep.mp4"
    sleep_path.write_bytes(eight_second_clip_bytes)
    lookup_path = tmp_path / "lookup.mp4"
    lookup_path.write_bytes(eight_second_clip_bytes)
    _approve_motion_test(db, episode_id, "sleep", sleep_path)
    _approve_motion_test(db, episode_id, "lookup", lookup_path)

    db.enqueue_task(episode_id, "build_loop_preview")

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: None)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    preview_assets = db.assets_for_episode(episode_id, kind="loop_preview")
    assert len(preview_assets) == 1
    preview = preview_assets[0]
    assert preview["status"] == "awaiting_review"
    assert preview["role"] == "shared"
    assert Path(preview["path"]).exists()
    assert preview["duration_seconds"] == pytest.approx(64.0, abs=0.5)
    assert comfyui.submitted_workflows == [], "build_loop_preview is pure ffmpeg, no ComfyUI call"
    # build_loop itself must be unaffected by adding the preview variant.
    assert db.assets_for_episode(episode_id, kind="loop") == []


def test_resolve_bound_assets_prefers_pinned_ids_over_latest_approved(tmp_path):
    """The whole point of pinning ids into the task payload at assemble
    time: a build must use exactly what the reviewer saw when they clicked
    Assemble, not whatever happens to be approved by the time the worker
    gets to it (codex_reviewer design consult, studio-console-v2-plan.md
    7.8). Simulates the race by approving a *second* motion_test/sleep
    variant after the one that was pinned -- resolution must still return
    the pinned (older) one, not the new latest-approved one."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")

    pinned_sleep = _approve_motion_test(db, episode_id, "sleep", tmp_path / "sleep-v0.mp4")
    lookup = _approve_motion_test(db, episode_id, "lookup", tmp_path / "lookup-v0.mp4")
    # A second sleep variant gets approved *after* pinning -- e.g. a reject
    # + regenerate + re-approve that happened while the preview task was
    # still queued.
    newer_sleep_id = db.create_asset(episode_id, "motion_test", "sleep", variant_index=1)
    db.transition_asset(
        newer_sleep_id, expected_status="queued", expected_version=0,
        status="approved", path=str(tmp_path / "sleep-v1.mp4"),
    )

    payload = {"source_asset_ids": {"sleep": pinned_sleep, "lookup": lookup}}
    resolved = stages._resolve_bound_assets(db, episode_id, payload, "motion_test")

    assert resolved["sleep"]["id"] == pinned_sleep
    assert resolved["sleep"]["path"] == str(tmp_path / "sleep-v0.mp4")
    assert resolved["lookup"]["id"] == lookup


def test_resolve_bound_assets_rejects_a_pinned_id_whose_status_changed(tmp_path):
    """If the pinned asset stopped being approved between assemble-time and
    execution (e.g. a concurrent reject), the build must fail loudly
    instead of silently substituting something else."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")

    sleep_id = _approve_motion_test(db, episode_id, "sleep", tmp_path / "sleep.mp4")
    lookup_id = _approve_motion_test(db, episode_id, "lookup", tmp_path / "lookup.mp4")
    db.transition_asset(
        sleep_id, expected_status="approved", expected_version=1,
        status="rejected", error="changed my mind",
    )

    payload = {"source_asset_ids": {"sleep": sleep_id, "lookup": lookup_id}}

    with pytest.raises(stages.GenerationError, match="不是已核准"):
        stages._resolve_bound_assets(db, episode_id, payload, "motion_test")


def test_resolve_bound_assets_rejects_ids_swapped_between_roles(tmp_path):
    """codex_reviewer Phase 2 review: the original version keyed resolution
    purely off the payload dict's own keys and never cross-checked the
    asset's actual `role` column, so a payload with sleep/lookup's ids
    swapped was silently accepted and returned wrong-role assets under
    each key."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    sleep_id = _approve_motion_test(db, episode_id, "sleep", tmp_path / "sleep.mp4")
    lookup_id = _approve_motion_test(db, episode_id, "lookup", tmp_path / "lookup.mp4")

    swapped = {"source_asset_ids": {"sleep": lookup_id, "lookup": sleep_id}}

    with pytest.raises(stages.GenerationError, match="不是已核准"):
        stages._resolve_bound_assets(db, episode_id, swapped, "motion_test")


def test_resolve_bound_assets_rejects_a_non_empty_but_incomplete_key_set(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    sleep_id = _approve_motion_test(db, episode_id, "sleep", tmp_path / "sleep.mp4")

    payload = {"source_asset_ids": {"sleep": sleep_id}}  # missing "lookup"

    with pytest.raises(stages.GenerationError, match="source_asset_ids"):
        stages._resolve_bound_assets(db, episode_id, payload, "motion_test")


def test_resolve_bound_assets_rejects_the_same_asset_pinned_to_both_roles(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    sleep_id = _approve_motion_test(db, episode_id, "sleep", tmp_path / "sleep.mp4")

    payload = {"source_asset_ids": {"sleep": sleep_id, "lookup": sleep_id}}

    with pytest.raises(stages.GenerationError, match="同一個"):
        stages._resolve_bound_assets(db, episode_id, payload, "motion_test")


def test_resolve_bound_assets_treats_an_empty_dict_as_pinned_not_as_no_payload(tmp_path):
    """An empty {} must NOT be treated the same as "no payload given" (a
    plain truthiness check on the dict would do exactly that, silently
    falling back to "latest approved" instead of failing loudly) --
    codex_reviewer Phase 2 review."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")
    _approve_motion_test(db, episode_id, "sleep", tmp_path / "sleep.mp4")
    _approve_motion_test(db, episode_id, "lookup", tmp_path / "lookup.mp4")

    payload = {"source_asset_ids": {}}

    with pytest.raises(stages.GenerationError, match="source_asset_ids"):
        stages._resolve_bound_assets(db, episode_id, payload, "motion_test")


def test_build_loop_preview_uses_pinned_asset_ids_from_task_payload(
    tmp_path, eight_second_clip_bytes
):
    """End-to-end version of test_resolve_bound_assets_prefers_pinned_ids:
    proves app.py's assemble-preview payload actually reaches the handler
    and is honored, not just that the helper function is correct in
    isolation."""
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("chowchow-001", "第一集")

    sleep_path = tmp_path / "sleep-v0.mp4"
    sleep_path.write_bytes(eight_second_clip_bytes)
    lookup_path = tmp_path / "lookup.mp4"
    lookup_path.write_bytes(eight_second_clip_bytes)
    pinned_sleep = _approve_motion_test(db, episode_id, "sleep", sleep_path)
    lookup_id = _approve_motion_test(db, episode_id, "lookup", lookup_path)

    # Newer sleep variant approved after pinning -- must be ignored.
    newer_sleep_path = tmp_path / "sleep-v1.mp4"
    newer_sleep_path.write_bytes(eight_second_clip_bytes)
    newer_sleep_id = db.create_asset(episode_id, "motion_test", "sleep", variant_index=1)
    db.transition_asset(
        newer_sleep_id, expected_status="queued", expected_version=0,
        status="approved", path=str(newer_sleep_path),
    )

    db.enqueue_task(
        episode_id, "build_loop_preview",
        payload={"source_asset_ids": {"sleep": pinned_sleep, "lookup": lookup_id}},
    )

    workflows_dir = tmp_path / "workflows"
    _write_workflows(workflows_dir)
    comfyui = FakeComfyUIClient(output_factory=lambda dest: None)
    handlers = stages.build_handlers(make_config(tmp_path), comfyui, workflows_dir)

    _run(db, handlers)

    preview = db.assets_for_episode(episode_id, kind="loop_preview")[0]
    assert preview["status"] == "awaiting_review"


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

    worker = StudioWorker(db, handlers=handlers)
    assert worker.run_once() is True

    assert db.task(task_id)["status"] == "failed"
    assert "audio_path" in db.task(task_id)["error"]
