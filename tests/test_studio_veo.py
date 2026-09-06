"""Google Veo motion path: catalog validation, the start/poll split, and
the money-safety rules around both (artifacts/spec.md).

No test here reaches a real API. The fake client counts calls so the
tests can assert the thing that actually matters -- that both clips are
in flight at Google at the same time, rather than one generating after
the other, which is the whole reason this path exists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import install_scene_presets
from fastapi.testclient import TestClient

from lyria_auto.config import AppConfig
from lyria_auto.db import StateDB
from lyria_auto.studio import veo as veo_support
from lyria_auto.studio.app import create_app
from lyria_auto.studio.stages import build_handlers
from lyria_auto.studio.worker import StudioWorker
from lyria_auto.visual_models import VideoPoll

VEO_SETTINGS = {
    "enabled": True,
    "default_model": "veo-test-fast",
    "default_resolution": "720p",
    "poll_interval_seconds": 0,
    "max_starts_per_episode": 12,
    "pricing_verified": False,
    "pricing_snapshot_date": "2026-09-06",
    "models": [
        {
            "id": "veo-test-fast",
            "label": "Veo Test Fast",
            "resolutions": ["720p", "1080p"],
            "durations": [4, 6, 8],
            "supports_last_frame": True,
            "usd_per_second": 0.15,
        },
        {
            "id": "veo-test-pro",
            "label": "Veo Test Pro",
            "resolutions": ["1080p"],
            "durations": [8],
            "supports_last_frame": True,
            "usd_per_second": 0.40,
        },
        {
            # Cannot produce the 8s segment the loop arithmetic requires,
            # so it must never reach the dropdown at all.
            "id": "veo-test-shortonly",
            "label": "Veo Test Short Only",
            "resolutions": ["720p"],
            "durations": [4],
            "supports_last_frame": False,
            "usd_per_second": 0.05,
        },
    ],
}


class _FakeComfyUI:
    def __init__(self) -> None:
        self.interrupt_count = 0

    def interrupt(self) -> None:
        self.interrupt_count += 1


class _FakeVeoClient:
    """Records every call so tests can assert ordering and arguments.

    `ready_after` controls how many polls an operation needs before it
    reports done, which is how the "both clips in flight at once" test
    holds two operations open simultaneously.
    """

    def __init__(self, *, ready_after: int = 1) -> None:
        self.ready_after = ready_after
        self.starts: list[dict[str, Any]] = []
        self.poll_counts: dict[str, int] = {}
        self.call_log: list[str] = []

    def start_video(self, prompt, frame_path, **kwargs):
        index = len(self.starts)
        operation_id = f"operations/veo-{index}"
        self.starts.append({"prompt": prompt, "frame_path": str(frame_path), **kwargs})
        self.call_log.append(f"start:{operation_id}")
        return operation_id

    def poll_video(self, operation_id, output_path):
        seen = self.poll_counts.get(operation_id, 0) + 1
        self.poll_counts[operation_id] = seen
        self.call_log.append(f"poll:{operation_id}:{seen}")
        if seen < self.ready_after:
            return VideoPoll(done=False)
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-veo-mp4")
        return VideoPoll(done=True, path=dest, sha256="f" * 64)


def _config(tmp_path, *, veo=None):
    install_scene_presets(tmp_path)
    return AppConfig(
        settings={"studio": {"veo": veo if veo is not None else VEO_SETTINGS}},
        prompts={}, channels={}, root=tmp_path,
    )


def _app(tmp_path, *, veo=None, key="test-key-abcd"):
    db = StateDB(tmp_path / "state.sqlite3")
    credential = veo_support.VeoCredential(key)
    app = create_app(
        db, _config(tmp_path, veo=veo), _FakeComfyUI(), veo_credential=credential
    )
    return db, TestClient(app), credential


def _handlers(tmp_path, db, veo_client, *, veo=None, dimensions=(1280, 720)):
    """Real stage handlers with only the Veo client and clock faked.

    _video_metadata/sha256_file are patched at module level because the
    fake writes a text placeholder, not a real mp4 -- the assertions here
    are about task orchestration, not ffprobe.
    """
    from lyria_auto.studio import stages

    stages._video_metadata = lambda path: {  # type: ignore[assignment]
        "width": dimensions[0], "height": dimensions[1],
        "fps": 24.0, "duration_seconds": 8.0,
    }
    stages.sha256_file = lambda path: "a" * 64  # type: ignore[assignment]
    veo_support.seam_score = lambda video, work_dir: 0.01  # type: ignore[assignment]

    def fake_ffmpeg(argv, **kwargs):
        """Stand in for the audio-strip pass without a real encoder.

        Copies input to output so the handler's "the finished file must
        exist at dest" contract still holds -- the fake download writes a
        placeholder, not a decodable mp4, so real ffmpeg would fail.
        """
        Path(argv[-1]).write_bytes(Path(argv[argv.index("-i") + 1]).read_bytes())

    stages.run_command = fake_ffmpeg  # type: ignore[assignment]
    return build_handlers(
        _config(tmp_path, veo=veo),
        _FakeComfyUI(),
        tmp_path / "workflows",
        veo_client_factory=lambda: veo_client,
        sleeper=lambda seconds: None,
    )


def _approved_keyframe(db, episode_id, tmp_path):
    frame = tmp_path / "keyframe.png"
    frame.write_bytes(b"fake-png")
    asset_id = db.create_asset(episode_id, "keyframe", "shared")
    db.transition_asset(
        asset_id, expected_status="queued", expected_version=0,
        status="awaiting_review", path=str(frame),
    )
    db.transition_asset(
        asset_id, expected_status="awaiting_review", expected_version=1, status="approved"
    )
    return asset_id


# --------------------------------------------------------------------------
# Catalog / validation -- all of this runs before anything can be billed.
# --------------------------------------------------------------------------


def test_catalog_hides_models_that_cannot_produce_the_required_8s_segment(tmp_path):
    ids = [entry["id"] for entry in veo_support.catalog(_config(tmp_path))]
    assert ids == ["veo-test-fast", "veo-test-pro"]
    assert "veo-test-shortonly" not in ids


def test_validate_choice_rejects_a_resolution_the_model_does_not_offer(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(veo_support.VeoConfigError, match="不支援"):
        veo_support.validate_choice(config, "veo-test-pro", "720p")


def test_validate_choice_rejects_an_unknown_model(tmp_path):
    with pytest.raises(veo_support.VeoConfigError, match="未知的 Veo 模型"):
        veo_support.validate_choice(_config(tmp_path), "veo-does-not-exist", "720p")


def test_cost_estimate_scales_with_clip_count_and_the_fixed_8s_duration(tmp_path):
    entry = veo_support.resolve_model(_config(tmp_path), "veo-test-fast")
    assert veo_support.estimate_cost_usd(entry, clips=1) == pytest.approx(1.2)
    assert veo_support.estimate_cost_usd(entry, clips=2) == pytest.approx(2.4)


def test_probe_failure_reports_unknown_rather_than_nothing_available():
    class Boom:
        class models:
            @staticmethod
            def list():
                raise RuntimeError("network down")

    assert veo_support.probe_available_models(Boom()) == set()


def test_unreachable_models_are_greyed_out_but_reachable_ones_are_not(tmp_path):
    items = veo_support.catalog_for_ui(_config(tmp_path), {"veo-test-fast"})
    by_id = {item["id"]: item for item in items}
    assert by_id["veo-test-fast"]["available"] is True
    assert by_id["veo-test-pro"]["available"] is False
    assert by_id["veo-test-pro"]["unavailable_reason"]


def test_an_empty_probe_result_leaves_every_model_selectable(tmp_path):
    """Empty means "could not check", not "nothing works" -- greying out
    the whole dropdown because the key had not loaded yet would be
    actively misleading."""
    items = veo_support.catalog_for_ui(_config(tmp_path), set())
    assert all(item["available"] for item in items)


# --------------------------------------------------------------------------
# Credential handling -- the key must never be persisted anywhere.
# --------------------------------------------------------------------------


def test_credential_never_reveals_the_key_through_repr_or_str():
    credential = veo_support.VeoCredential("super-secret-key-9876")
    assert "super-secret" not in repr(credential)
    assert "super-secret" not in str(credential)
    assert credential.masked == "****9876"


def test_veo_config_endpoint_exposes_only_the_mask(tmp_path):
    _db, client, _cred = _app(tmp_path, key="key-with-tail-1234")

    body = client.get("/api/veo/config").json()

    assert body["key_configured"] is True
    assert body["key_masked"] == "****1234"
    assert "key-with-tail-1234" not in json.dumps(body)


def test_credential_endpoint_accepts_a_key_and_returns_only_the_mask(tmp_path):
    _db, client, credential = _app(tmp_path, key=None)
    assert client.get("/api/veo/config").json()["key_configured"] is False

    resp = client.post("/api/veo/credential", json={"api_key": "typed-in-key-5678"})

    assert resp.status_code == 200
    assert resp.json() == {"key_configured": True, "key_masked": "****5678"}
    assert credential.get() == "typed-in-key-5678"


def test_the_api_key_never_reaches_a_task_payload(tmp_path):
    """The hard rule from artifacts/spec.md 7: payload_json is written to
    disk, so a key there would be a leak. This asserts it directly rather
    than trusting review."""
    db, client, _cred = _app(tmp_path, key="leaky-key-0001")
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)

    client.post(
        f"/api/episodes/{episode['id']}/motion/generate",
        json={"provider": "veo", "model": "veo-test-fast", "resolution": "720p"},
    )

    for task in db.tasks_for_episode(episode["id"]):
        assert "leaky-key-0001" not in (task["payload_json"] or "")


# --------------------------------------------------------------------------
# The generate endpoint -- everything that must be refused before spending.
# --------------------------------------------------------------------------


def test_generate_refuses_an_unsupported_resolution_without_calling_the_api(tmp_path):
    db, client, _cred = _app(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)

    resp = client.post(
        f"/api/episodes/{episode['id']}/motion/generate",
        json={"provider": "veo", "model": "veo-test-pro", "resolution": "720p"},
    )

    assert resp.status_code == 400
    assert "不支援" in resp.json()["detail"]
    # Nothing was queued, so no paid start can happen later either.
    assert db.tasks_for_episode(episode["id"]) == []


def test_generate_refuses_when_no_api_key_is_loaded(tmp_path):
    db, client, _cred = _app(tmp_path, key=None)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)

    resp = client.post(
        f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"}
    )

    assert resp.status_code == 409
    assert "GEMINI_API_KEY" in resp.json()["detail"]


def test_generate_refuses_before_a_keyframe_is_approved(tmp_path):
    _db, client, _cred = _app(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()

    resp = client.post(
        f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"}
    )

    assert resp.status_code == 409
    assert "關鍵幀" in resp.json()["detail"]


def test_generate_refuses_an_unknown_provider(tmp_path):
    db, client, _cred = _app(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)

    resp = client.post(
        f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "midjourney"}
    )

    assert resp.status_code == 400


def test_generate_enqueues_one_start_task_per_role_with_the_chosen_model(tmp_path):
    db, client, _cred = _app(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)

    resp = client.post(
        f"/api/episodes/{episode['id']}/motion/generate",
        json={"provider": "veo", "model": "veo-test-fast", "resolution": "1080p"},
    )

    assert resp.status_code == 200, resp.text
    tasks = db.tasks_for_episode(episode["id"])
    assert [t["task_type"] for t in tasks] == ["start_veo_motion", "start_veo_motion"]
    payloads = [json.loads(t["payload_json"]) for t in tasks]
    assert {p["role"] for p in payloads} == {"sleep", "lookup"}
    # Both roles pinned to the same model/resolution: the loop builder
    # stream-copies, so mismatched clips could not be concatenated.
    assert {p["model"] for p in payloads} == {"veo-test-fast"}
    assert {p["resolution"] for p in payloads} == {"1080p"}


def test_generate_is_refused_while_another_motion_batch_is_still_running(tmp_path):
    db, client, _cred = _app(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"})

    resp = client.post(
        f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"}
    )

    assert resp.status_code == 409
    assert "排隊或執行中" in resp.json()["detail"]


def test_generate_is_refused_once_a_motion_clip_has_been_approved(tmp_path):
    db, client, _cred = _app(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    approved = db.create_asset(episode["id"], "motion_test", "sleep")
    db.transition_asset(
        approved, expected_status="queued", expected_version=0, status="awaiting_review"
    )
    db.transition_asset(
        approved, expected_status="awaiting_review", expected_version=1, status="approved"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"}
    )

    assert resp.status_code == 409
    assert "退回重生成" in resp.json()["detail"]


def test_the_paid_start_cap_blocks_a_runaway_episode(tmp_path):
    veo = dict(VEO_SETTINGS, max_starts_per_episode=2)
    db, client, _cred = _app(tmp_path, veo=veo)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"})
    for task in db.tasks_for_episode(episode["id"]):
        db.finish_task(task["id"], status="done")

    resp = client.post(
        f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"}
    )

    assert resp.status_code == 409
    assert "上限" in resp.json()["detail"]


# --------------------------------------------------------------------------
# The start/poll split -- the reason this whole path exists.
# --------------------------------------------------------------------------


def test_start_returns_without_polling_and_records_the_operation(tmp_path):
    """A start handler that blocked on polling would park the single
    worker thread for the entire generation. This asserts it does not
    poll at all."""
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient(ready_after=99)
    worker = StudioWorker(db, handlers=_handlers(tmp_path, db, veo_client))
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(
        f"/api/episodes/{episode['id']}/motion/generate",
        json={"provider": "veo", "model": "veo-test-fast", "resolution": "720p"},
    )

    worker.run_once()  # exactly one start task

    assert len(veo_client.starts) == 1
    assert veo_client.poll_counts == {}
    assets = db.assets_for_episode(episode["id"], kind="motion_test")
    started = [a for a in assets if a["operation_id"]]
    assert len(started) == 1
    assert started[0]["provider"] == "google-veo"
    assert started[0]["model"] == "veo-test-fast"
    assert started[0]["status"] == "running"
    # A poll task was handed off so the worker thread is free again.
    assert any(
        t["task_type"] == "poll_veo_motion" and t["status"] == "queued"
        for t in db.tasks_for_episode(episode["id"])
    )


def test_both_clips_are_in_flight_at_google_before_either_one_finishes(tmp_path):
    """The core claim of this feature. If the handler blocked, the call
    log would read start,poll...,start -- sleep finishing entirely before
    lookup began. It must read start,start,... instead."""
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient(ready_after=3)
    worker = StudioWorker(db, handlers=_handlers(tmp_path, db, veo_client))
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"})

    worker.run_once()
    worker.run_once()

    assert veo_client.call_log == ["start:operations/veo-0", "start:operations/veo-1"]
    assert veo_client.poll_counts == {}


def test_a_poll_that_is_not_done_requeues_itself_and_leaves_the_asset_running(tmp_path):
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient(ready_after=3)
    worker = StudioWorker(db, handlers=_handlers(tmp_path, db, veo_client))
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"})
    worker.run_once()
    worker.run_once()

    worker.run_once()  # first poll for sleep -- not done yet

    assert veo_client.poll_counts == {"operations/veo-0": 1}
    asset = next(
        a for a in db.assets_for_episode(episode["id"], kind="motion_test")
        if a["operation_id"] == "operations/veo-0"
    )
    assert asset["status"] == "running"
    queued = [
        t for t in db.tasks_for_episode(episode["id"])
        if t["task_type"] == "poll_veo_motion" and t["status"] == "queued"
    ]
    assert len(queued) == 2  # the requeued sleep poll plus lookup's original


def test_the_whole_pair_reaches_awaiting_review_with_files_and_a_seam_score(tmp_path):
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient(ready_after=2)
    worker = StudioWorker(db, handlers=_handlers(tmp_path, db, veo_client))
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"})

    while worker.run_once():
        pass

    assets = db.assets_for_episode(episode["id"], kind="motion_test")
    assert {a["role"] for a in assets} == {"sleep", "lookup"}
    for asset in assets:
        assert asset["status"] == "awaiting_review", asset["error"]
        assert Path(asset["path"]).is_file()
        assert asset["seam_score"] == pytest.approx(0.01)
        assert asset["estimated_cost_usd"] == pytest.approx(1.2)
    assert not [t for t in db.tasks_for_episode(episode["id"]) if t["status"] == "failed"]


def test_start_sends_only_parameters_the_developer_api_accepts(tmp_path):
    """seed and generate_audio are rejected by the Gemini Developer API
    ("only supported in Gemini Enterprise Agent Platform mode") -- the
    SDK refuses them client side, so passing either fails every
    generation before it starts. Verified against the real API with a
    throwaway key."""
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient()
    worker = StudioWorker(db, handlers=_handlers(tmp_path, db, veo_client))
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(
        f"/api/episodes/{episode['id']}/motion/generate",
        json={"provider": "veo", "model": "veo-test-fast", "resolution": "720p"},
    )
    worker.run_once()

    call = veo_client.starts[0]
    assert "generate_audio" not in call
    assert "seed" not in call
    assert call["duration_seconds"] == 8
    assert call["resolution"] == "720p"
    assert call["model"] == "veo-test-fast"


def test_the_downloaded_clip_has_its_veo_audio_track_stripped(tmp_path):
    """Veo 3.x on the Developer API always returns a soundtrack (audio
    cannot be turned off there), but the finished video's audio is the
    Lyria music -- and _concat_copy stream-copies, so a stray audio
    stream would also break the loop build. This asserts the strip pass
    runs, drops audio, and never re-encodes the video."""
    from lyria_auto.studio import stages

    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient()
    handlers = _handlers(tmp_path, db, veo_client)
    calls: list[list[str]] = []
    inner = stages.run_command

    def recording(argv, **kwargs):
        calls.append(list(argv))
        return inner(argv, **kwargs)

    stages.run_command = recording  # type: ignore[assignment]
    worker = StudioWorker(db, handlers=handlers)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"})

    while worker.run_once():
        pass

    assert calls, "the audio-strip pass never ran"
    argv = calls[0]
    assert "-an" in argv                      # drop audio
    assert argv[argv.index("-c:v") + 1] == "copy"  # never re-encode
    for asset in db.assets_for_episode(episode["id"], kind="motion_test"):
        stored = Path(asset["path"])
        assert stored.is_file()
        # The intermediate download must not be left lying around where
        # /api/artifact could serve it as a finished clip.
        assert not stored.with_name(f"{stored.stem}.raw{stored.suffix}").exists()


def test_a_clip_whose_dimensions_disagree_with_the_order_fails_loudly(tmp_path):
    """Ordering 1080p and receiving 720p would silently poison the loop
    build later, where every segment has to match."""
    from lyria_auto.studio import stages

    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient()
    handlers = _handlers(tmp_path, db, veo_client)
    stages._video_metadata = lambda path: {  # type: ignore[assignment]
        "width": 1280, "height": 720, "fps": 24.0, "duration_seconds": 8.0,
    }
    worker = StudioWorker(db, handlers=handlers)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(
        f"/api/episodes/{episode['id']}/motion/generate",
        json={"provider": "veo", "model": "veo-test-fast", "resolution": "1080p"},
    )

    while worker.run_once():
        pass

    failed = [t for t in db.tasks_for_episode(episode["id"]) if t["status"] == "failed"]
    assert failed
    assert "1080p" in failed[0]["error"]


# --------------------------------------------------------------------------
# Restart behaviour -- never throw away a generation already paid for.
# --------------------------------------------------------------------------


def test_a_restart_resumes_a_paid_poll_instead_of_failing_it(tmp_path):
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient(ready_after=99)
    worker = StudioWorker(db, handlers=_handlers(tmp_path, db, veo_client))
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"})
    worker.run_once()
    worker.run_once()
    # Simulate a crash: a poll task claimed but never finished.
    claimed = db.claim_next_task()
    assert claimed["task_type"] == "poll_veo_motion"
    asset_id = claimed["asset_id"]

    StudioWorker(db, handlers={}).recover_stale_tasks()

    assert db.asset(asset_id)["status"] == "running"  # not failed
    assert db.asset(asset_id)["operation_id"]
    assert any(
        t["task_type"] == "poll_veo_motion"
        and t["status"] == "queued"
        and t["asset_id"] == asset_id
        for t in db.tasks_for_episode(episode["id"])
    )


def test_a_restart_still_fails_an_interrupted_start_rather_than_retrying_it(tmp_path):
    """A start that was interrupted may or may not have been billed.
    Auto-retrying could charge twice, so it must fail for a human."""
    db, client, _cred = _app(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"})
    claimed = db.claim_next_task()
    assert claimed["task_type"] == "start_veo_motion"
    # StudioWorker._execute moves the asset to 'running' before invoking
    # the handler, so that is the state a real mid-start crash leaves
    # behind -- reproduce it rather than testing an impossible one.
    db.transition_asset(
        claimed["asset_id"], expected_status="queued",
        expected_version=db.asset(claimed["asset_id"])["state_version"],
        status="running",
    )

    StudioWorker(db, handlers={}).recover_stale_tasks()

    assert db.task(claimed["id"])["status"] == "failed"
    assert db.asset(claimed["asset_id"])["status"] == "failed"


def test_a_poll_with_no_operation_id_is_not_treated_as_resumable(tmp_path):
    """Only an asset that really carries an operation represents money
    already spent -- anything else follows the normal fail-on-restart
    path."""
    db, _client, _cred = _app(tmp_path)
    episode_id = db.create_episode("e", "E")
    asset_id = db.create_asset(episode_id, "motion_test", "sleep")
    db.enqueue_task(episode_id, "poll_veo_motion", asset_id=asset_id)
    db.claim_next_task()

    StudioWorker(db, handlers={}).recover_stale_tasks()

    assert db.tasks_for_episode(episode_id)[0]["status"] == "failed"


# --------------------------------------------------------------------------
# Cancellation and retry.
# --------------------------------------------------------------------------


def test_cancel_reports_paid_operations_it_could_not_call_off(tmp_path):
    """There is no way to un-charge a started Veo operation, so cancel
    must say how many were abandoned rather than implying a refund."""
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient(ready_after=99)
    worker = StudioWorker(db, handlers=_handlers(tmp_path, db, veo_client))
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"})
    worker.run_once()
    worker.run_once()

    resp = client.post(f"/api/episodes/{episode['id']}/motion/cancel")

    assert resp.status_code == 200
    assert resp.json()["abandoned_paid_operations"] == 2


def test_rejecting_a_veo_clip_retries_it_through_veo_not_local_comfyui(tmp_path):
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient()
    worker = StudioWorker(db, handlers=_handlers(tmp_path, db, veo_client))
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(
        f"/api/episodes/{episode['id']}/motion/generate",
        json={"provider": "veo", "model": "veo-test-fast", "resolution": "720p"},
    )
    while worker.run_once():
        pass
    sleep_asset = next(
        a for a in db.assets_for_episode(episode["id"], kind="motion_test", role="sleep")
        if a["status"] == "awaiting_review"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{sleep_asset['id']}/reject",
        json={"expected_version": sleep_asset["state_version"], "reason": "尾巴沒動"},
    )

    assert resp.status_code == 200, resp.text
    requeued = [
        t for t in db.tasks_for_episode(episode["id"])
        if t["task_type"] == "start_veo_motion" and t["status"] == "queued"
    ]
    assert len(requeued) == 1
    payload = json.loads(requeued[0]["payload_json"])
    assert payload["model"] == "veo-test-fast"
    assert payload["resolution"] == "720p"


# --------------------------------------------------------------------------
# Downstream protection.
# --------------------------------------------------------------------------


def test_the_loop_builder_refuses_to_concatenate_mismatched_clips(tmp_path):
    """Stream-copy concat needs identical specs. A Veo clip beside a
    ComfyUI clip would produce a broken file that the duration check --
    the only other guard -- would happily accept."""
    from lyria_auto.studio import stages

    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("e", "E")
    specs = {"sleep": (1280, 720, 24.0), "lookup": (768, 432, 16.0)}
    for role, (w, h, fps) in specs.items():
        clip = tmp_path / f"{role}.mp4"
        clip.write_bytes(b"x")
        asset_id = db.create_asset(episode_id, "motion_test", role)
        db.transition_asset(
            asset_id, expected_status="queued", expected_version=0,
            status="awaiting_review", path=str(clip), width=w, height=h, fps=fps,
        )
        db.transition_asset(
            asset_id, expected_status="awaiting_review", expected_version=1,
            status="approved",
        )

    def metadata(path):
        role = Path(path).stem
        w, h, fps = specs[role]
        return {"width": w, "height": h, "fps": fps, "duration_seconds": 8.0}

    stages._video_metadata = metadata  # type: ignore[assignment]
    handlers = build_handlers(_config(tmp_path), _FakeComfyUI(), tmp_path / "workflows")
    task_id = db.enqueue_task(episode_id, "build_loop_preview")
    worker = StudioWorker(db, handlers=handlers)
    db.claim_next_task()

    worker._execute(db.task(task_id))

    failed = db.task(task_id)
    assert failed["status"] == "failed"
    assert "規格不一致" in failed["error"]


# --------------------------------------------------------------------------
# Stage 2: re-running the approved pair at 1080p (the second half of the
# reviewer's "cheap test first, then the real thing" workflow).
# --------------------------------------------------------------------------


def _approved_motion_pair(db, episode_id, tmp_path, *, model="veo-test-fast"):
    for role in ("sleep", "lookup"):
        clip = tmp_path / f"motion-{role}.mp4"
        clip.write_bytes(b"x")
        asset_id = db.create_asset(
            episode_id, "motion_test", role, source_prompt=f"{role} prompt"
        )
        db.transition_asset(
            asset_id, expected_status="queued", expected_version=0,
            status="awaiting_review", path=str(clip),
            provider="google-veo", model=model, width=1280, height=720,
        )
        db.transition_asset(
            asset_id, expected_status="awaiting_review", expected_version=1,
            status="approved",
        )


def test_the_1080p_rerun_writes_clip_1080p_and_skips_the_upscale_stage(tmp_path):
    """Veo produces 1080p natively, so tab 3's ComfyUI upscale is not on
    this path at all -- the run goes straight to clip_1080p, which is
    what the existing build_loop fan-in already waits for."""
    db, client, _cred = _app(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    _approved_motion_pair(db, episode["id"], tmp_path)

    resp = client.post(
        f"/api/episodes/{episode['id']}/clips/generate",
        json={"provider": "veo", "model": "veo-test-fast", "resolution": "1080p"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["resolution"] == "1080p"
    tasks = [
        t for t in db.tasks_for_episode(episode["id"])
        if t["task_type"] == "start_veo_clip"
    ]
    assert len(tasks) == 2
    assert {a["role"] for a in db.assets_for_episode(episode["id"], kind="clip_1080p")} == {
        "sleep", "lookup"
    }
    # No ComfyUI upscale was scheduled.
    assert not [
        t for t in db.tasks_for_episode(episode["id"])
        if t["task_type"] in ("generate_clip", "upscale_clip")
    ]
    # The approved test's prompt is carried across, not the generic default.
    assert {
        a["source_prompt"] for a in db.assets_for_episode(episode["id"], kind="clip_1080p")
    } == {"sleep prompt", "lookup prompt"}


def test_the_1080p_rerun_is_refused_until_both_tests_are_approved(tmp_path):
    db, client, _cred = _app(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)

    resp = client.post(
        f"/api/episodes/{episode['id']}/clips/generate", json={"provider": "veo"}
    )

    assert resp.status_code == 409
    assert "低解析度測試" in resp.json()["detail"]


def test_approving_both_1080p_clips_fans_into_build_loop(tmp_path):
    """The point of writing into clip_1080p: the cascade that already
    exists takes over from here, with no new wiring."""
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient()
    worker = StudioWorker(
        db, handlers=_handlers(tmp_path, db, veo_client, dimensions=(1920, 1080))
    )
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    _approved_motion_pair(db, episode["id"], tmp_path)
    client.post(
        f"/api/episodes/{episode['id']}/clips/generate",
        json={"provider": "veo", "resolution": "1080p"},
    )
    while worker.run_once():
        pass

    for asset in db.assets_for_episode(episode["id"], kind="clip_1080p"):
        assert asset["status"] == "awaiting_review", asset["error"]
        resp = client.post(
            f"/api/episodes/{episode['id']}/assets/{asset['id']}/approve",
            json={"expected_version": db.asset(asset["id"])["state_version"]},
        )
        assert resp.status_code == 200, resp.text

    assert any(
        t["task_type"] == "build_loop" for t in db.tasks_for_episode(episode["id"])
    )


def test_the_paid_start_cap_counts_motion_and_clip_starts_together(tmp_path):
    """Counting only the motion starts would let the 1080p stage spend
    past a cap the reviewer believed covered the whole episode."""
    veo = dict(VEO_SETTINGS, max_starts_per_episode=2)
    db, client, _cred = _app(tmp_path, veo=veo)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    _approved_motion_pair(db, episode["id"], tmp_path)
    # Two motion starts already used the whole cap.
    for role in ("sleep", "lookup"):
        placeholder = db.create_asset(episode["id"], "motion_test", role)
        db.enqueue_task(
            episode["id"], "start_veo_motion", asset_id=placeholder, payload={"role": role}
        )

    resp = client.post(
        f"/api/episodes/{episode['id']}/clips/generate",
        json={"provider": "veo", "resolution": "1080p"},
    )

    assert resp.status_code == 409
    assert "上限" in resp.json()["detail"]


def test_rejecting_a_1080p_clip_retries_it_through_the_veo_clip_task(tmp_path):
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient()
    worker = StudioWorker(
        db, handlers=_handlers(tmp_path, db, veo_client, dimensions=(1920, 1080))
    )
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    _approved_motion_pair(db, episode["id"], tmp_path)
    client.post(
        f"/api/episodes/{episode['id']}/clips/generate",
        json={"provider": "veo", "resolution": "1080p"},
    )
    while worker.run_once():
        pass
    clip = db.assets_for_episode(episode["id"], kind="clip_1080p", role="sleep")[-1]

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{clip['id']}/reject",
        json={"expected_version": clip["state_version"], "reason": "重來"},
    )

    assert resp.status_code == 200, resp.text
    # Must NOT fall back to _TASK_TYPE_BY_KIND's ComfyUI upscale_clip.
    types = [t["task_type"] for t in db.tasks_for_episode(episode["id"])]
    assert "start_veo_clip" in types
    assert "upscale_clip" not in types


# --------------------------------------------------------------------------
# The remaining money-safety hole.
# --------------------------------------------------------------------------


def test_a_cancel_during_polling_does_not_silently_lose_a_paid_clip(tmp_path):
    """If a cancel lands mid-poll the asset is already 'failed', so the
    success transition fails. Without raising, the worker would mark the
    task done and a paid, downloaded clip would sit on disk with no row
    pointing at it and nothing shown in the UI."""
    db, client, _cred = _app(tmp_path)
    veo_client = _FakeVeoClient(ready_after=2)
    worker = StudioWorker(db, handlers=_handlers(tmp_path, db, veo_client))
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _approved_keyframe(db, episode["id"], tmp_path)
    client.post(f"/api/episodes/{episode['id']}/motion/generate", json={"provider": "veo"})
    worker.run_once()
    worker.run_once()
    worker.run_once()  # sleep poll #1 -- not done, requeues
    # Move the asset out from under the in-flight poll, which is exactly
    # what a cancel does to it. Done directly rather than through the
    # cancel endpoint because that also kills the queued poll task, so
    # the requeued poll would never run and the race would never happen.
    sleeping = next(
        a for a in db.assets_for_episode(episode["id"], kind="motion_test")
        if a["operation_id"] == "operations/veo-0"
    )
    db.transition_asset(
        sleeping["id"], expected_status="running",
        expected_version=sleeping["state_version"],
        status="failed", error="使用者手動終止生成",
    )
    while worker.run_once():
        pass

    failed = [
        t for t in db.tasks_for_episode(episode["id"])
        if t["task_type"] == "poll_veo_motion" and t["status"] == "failed"
    ]
    assert failed, "a poll that could not publish its result must fail loudly"
    assert "已經下載並付費" in failed[0]["error"]
