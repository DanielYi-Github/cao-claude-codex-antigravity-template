from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from lyria_auto.config import AppConfig
from lyria_auto.db import StateDB
from lyria_auto.studio.app import create_app
from lyria_auto.studio.music import DEFAULT_MUSIC_PROMPT
from lyria_auto.studio.stages import DEFAULT_KEYFRAME_PROMPT, KEYFRAME_NEGATIVE_PROMPT
from lyria_auto.studio.worker import StudioWorker


class _FakeComfyUIClient:
    """Only implements what app.py actually calls on local_comfyui --
    interrupt(), for the two Stop buttons. Duck-typed rather than a real
    ComfyUIClient pointed at an unreachable URL so cancel tests can assert
    it was actually called, not just that it silently failed to connect."""

    def __init__(self) -> None:
        self.interrupt_count = 0

    def interrupt(self) -> None:
        self.interrupt_count += 1


def _client(
    tmp_path,
    *,
    keyframe_batch_size: int = 12,
    comfyui: Any = None,
    production_comfyui: Any = None,
    music_runner: Any = None,
    youtube_uploader: Any = None,
):
    db = StateDB(tmp_path / "state.sqlite3")
    config = AppConfig(
        settings={"studio": {"keyframe_batch_size": keyframe_batch_size}},
        prompts={}, channels={}, root=tmp_path,
    )
    kwargs = {"production_comfyui": production_comfyui}
    if music_runner is not None:
        kwargs["music_runner"] = music_runner
    if youtube_uploader is not None:
        kwargs["youtube_uploader"] = youtube_uploader
    app = create_app(db, config, comfyui or _FakeComfyUIClient(), **kwargs)
    return db, TestClient(app)


def test_creating_an_episode_does_not_auto_generate(tmp_path):
    """Tab 1 needs the human to fill in a prompt and click Generate first --
    unlike the old flat console, creating an episode is no longer itself a
    trigger for generation (studio-console-v2-plan.md)."""
    _db, client = _client(tmp_path)

    resp = client.post("/api/episodes", json={"slug": "chowchow-001", "title": "第一集"})

    assert resp.status_code == 200, resp.text
    episode = resp.json()
    assert episode["status"] == "draft"

    detail = client.get(f"/api/episodes/{episode['id']}").json()
    assert detail["assets"] == []
    assert detail["tasks"] == []


def test_episode_slug_rejects_path_traversal(tmp_path):
    _db, client = _client(tmp_path)

    resp = client.post("/api/episodes", json={"slug": "../../outside", "title": "E"})

    assert resp.status_code == 422


def test_generate_keyframes_enqueues_one_task_with_the_prompt_in_its_payload(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()

    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/generate",
        json={"positive_prompt": "rainy cafe", "negative_prompt": "no people", "batch_size": 4},
    )

    assert resp.status_code == 200, resp.text
    assert client.get(f"/api/episodes/{episode['id']}").json()["episode"]["status"] == "in_progress"
    tasks = db.tasks_for_episode(episode["id"])
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "generate_keyframe"
    assert tasks[0]["asset_id"] is None
    payload = json.loads(tasks[0]["payload_json"])
    assert payload == {"positive_prompt": "rainy cafe", "negative_prompt": "no people", "batch_size": 4}


def test_import_keyframe_copies_workspace_image_as_reviewable_candidate(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    source = tmp_path / "workspace" / "temp" / "spring-sunny.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1600, 900), "#70452b").save(source)

    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/import",
        json={"path": str(source)},
    )

    assert resp.status_code == 200, resp.text
    asset = resp.json()
    assert asset["status"] == "awaiting_review"
    assert asset["kind"] == "keyframe"
    assert asset["role"] == "shared"
    assert (asset["width"], asset["height"]) == (1600, 900)
    assert asset["source_prompt"] == "Imported local image: spring-sunny.png"
    copied = tmp_path / "workspace" / "studio" / "e" / "keyframe-import-v0.png"
    assert copied.is_file()
    assert asset["path"] == str(copied)
    assert copied.read_bytes() == source.read_bytes()
    assert client.get(f"/api/artifact/{asset['id']}").status_code == 200
    assert db.episode(episode["id"])["status"] == "in_progress"


def test_import_keyframe_supersedes_an_unapproved_generated_candidate(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    old_id = db.create_asset(episode["id"], "keyframe", "shared", variant_index=2)
    db.transition_asset(
        old_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )
    source = tmp_path / "workspace" / "new.webp"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 180), "#334455").save(source)

    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/import", json={"path": str(source)}
    )

    assert resp.status_code == 200, resp.text
    assert db.asset(old_id)["status"] == "superseded"
    assert resp.json()["variant_index"] == 3
    assert resp.json()["path"].endswith("keyframe-import-v3.webp")


def test_import_keyframe_rejects_paths_outside_the_configured_workspace(tmp_path):
    _db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    source = tmp_path / "outside.png"
    Image.new("RGB", (10, 10)).save(source)

    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/import", json={"path": str(source)}
    )

    assert resp.status_code == 400
    assert "configured workspace" in resp.json()["detail"]


def test_import_keyframe_rejects_a_non_image_without_mutating_candidates(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    source = tmp_path / "workspace" / "not-an-image.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("definitely not an image", encoding="utf-8")

    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/import", json={"path": str(source)}
    )

    assert resp.status_code == 400
    assert db.assets_for_episode(episode["id"]) == []


def test_import_keyframe_is_blocked_after_approval_or_during_generation(tmp_path):
    db, client = _client(tmp_path)
    source = tmp_path / "workspace" / "candidate.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 18)).save(source)

    approved_episode = client.post(
        "/api/episodes", json={"slug": "approved", "title": "Approved"}
    ).json()
    approved_id = db.create_asset(approved_episode["id"], "keyframe", "shared")
    db.transition_asset(
        approved_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )
    db.transition_asset(
        approved_id, expected_status="awaiting_review", expected_version=1, status="approved"
    )
    approved_resp = client.post(
        f"/api/episodes/{approved_episode['id']}/keyframes/import",
        json={"path": str(source)},
    )
    assert approved_resp.status_code == 409

    running_episode = client.post(
        "/api/episodes", json={"slug": "running", "title": "Running"}
    ).json()
    db.enqueue_task(running_episode["id"], "generate_keyframe")
    running_resp = client.post(
        f"/api/episodes/{running_episode['id']}/keyframes/import",
        json={"path": str(source)},
    )
    assert running_resp.status_code == 409


def test_generate_keyframes_with_no_prompt_still_enqueues_a_task(tmp_path):
    """Omitted prompts/batch_size aren't an error -- the endpoint resolves
    them to the same defaults the handler would fall back to anyway, and
    always persists the concrete values (never null) so the frontend can
    show/pre-fill "what actually generated these candidates" even for a
    default batch."""
    db, client = _client(tmp_path, keyframe_batch_size=12)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()

    resp = client.post(f"/api/episodes/{episode['id']}/keyframes/generate", json={})

    assert resp.status_code == 200, resp.text
    tasks = db.tasks_for_episode(episode["id"])
    assert len(tasks) == 1
    payload = json.loads(tasks[0]["payload_json"])
    assert payload == {
        "positive_prompt": DEFAULT_KEYFRAME_PROMPT,
        "negative_prompt": KEYFRAME_NEGATIVE_PROMPT,
        "batch_size": 12,
    }


def test_generate_keyframes_rejects_a_non_positive_batch_size(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()

    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/generate", json={"batch_size": 0}
    )

    assert resp.status_code == 400
    assert db.tasks_for_episode(episode["id"]) == []


def test_cancel_keyframe_generation_interrupts_comfyui_and_fails_the_task(tmp_path):
    comfyui = _FakeComfyUIClient()
    db, client = _client(tmp_path, comfyui=comfyui)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    client.post(f"/api/episodes/{episode['id']}/keyframes/generate", json={})
    task_id = db.tasks_for_episode(episode["id"])[0]["id"]

    resp = client.post(f"/api/episodes/{episode['id']}/keyframes/cancel")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cancelled": 1}
    assert comfyui.interrupt_count == 1
    assert db.task(task_id)["status"] == "failed"

    # The in-flight guard is now clear -- a fresh Generate must succeed
    # immediately, not 409 on a task that's actually dead.
    retry = client.post(
        f"/api/episodes/{episode['id']}/keyframes/generate",
        json={"positive_prompt": "corrected prompt"},
    )
    assert retry.status_code == 200, retry.text


def test_cancel_motion_generation_interrupts_comfyui_and_lets_reject_pick_it_back_up(tmp_path):
    comfyui = _FakeComfyUIClient()
    db, client = _client(tmp_path, comfyui=comfyui)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    sleep_id = db.create_asset(episode["id"], "motion_test", "sleep")
    db.enqueue_task(episode["id"], "generate_motion_test", asset_id=sleep_id)
    db.claim_next_task()
    db.transition_asset(sleep_id, expected_status="queued", expected_version=0, status="running")

    resp = client.post(f"/api/episodes/{episode['id']}/motion/cancel")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cancelled": 1}
    assert comfyui.interrupt_count == 1
    assert db.asset(sleep_id)["status"] == "failed"

    # A cancelled-mid-generation asset must not be a dead end -- the
    # reviewer needs to be able to hit Regenerate with a corrected prompt
    # immediately, the same as they could for an awaiting_review asset.
    reject_resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{sleep_id}/reject",
        json={"expected_version": 2, "reason": "cancelled", "new_prompt": "fixed prompt"},
    )
    assert reject_resp.status_code == 200, reject_resp.text
    assert reject_resp.json()["requeued"]["source_prompt"] == "fixed prompt"


def test_cancel_motion_generation_also_clears_a_queued_sibling(tmp_path):
    """Stopping tab 2 must stop the whole in-flight batch, not just
    whichever single task ComfyUI happened to be executing -- otherwise the
    worker picks the queued one up right after and the reviewer is
    surprised by generation continuing anyway."""
    comfyui = _FakeComfyUIClient()
    db, client = _client(tmp_path, comfyui=comfyui)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    sleep_id = db.create_asset(episode["id"], "motion_test", "sleep")
    db.enqueue_task(episode["id"], "generate_motion_test", asset_id=sleep_id)
    db.claim_next_task()
    db.transition_asset(sleep_id, expected_status="queued", expected_version=0, status="running")
    lookup_id = db.create_asset(episode["id"], "motion_test", "lookup")
    lookup_task = db.enqueue_task(episode["id"], "generate_motion_test", asset_id=lookup_id)

    resp = client.post(f"/api/episodes/{episode['id']}/motion/cancel")

    assert resp.json() == {"cancelled": 2}
    assert db.task(lookup_task)["status"] == "failed"
    assert db.asset(lookup_id)["status"] == "failed"


def _seed_approved_motion_preview(db, episode_id):
    for index, role in enumerate(("sleep", "lookup")):
        asset_id = db.create_asset(
            episode_id,
            "motion_test",
            role,
            source_prompt=f"{role} prompt",
            source_seed=700 + index,
        )
        db.transition_asset(
            asset_id,
            expected_status="queued",
            expected_version=0,
            status="approved",
        )
    preview_id = db.create_asset(episode_id, "loop_preview", "shared")
    db.transition_asset(
        preview_id,
        expected_status="queued",
        expected_version=0,
        status="approved",
    )


def test_start_production_enqueues_both_roles_from_approved_motion_preview(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _seed_approved_motion_preview(db, episode["id"])

    resp = client.post(f"/api/episodes/{episode['id']}/production/start")

    assert resp.status_code == 200, resp.text
    clips = db.assets_for_episode(episode["id"], kind="clip")
    assert {a["role"] for a in clips} == {"sleep", "lookup"}
    assert {a["source_prompt"] for a in clips} == {"sleep prompt", "lookup prompt"}
    assert {a["source_seed"] for a in clips} == {700, 701}
    tasks = db.tasks_for_episode(episode["id"])
    assert [t["task_type"] for t in tasks] == ["generate_clip", "generate_clip"]
    assert set(resp.json()["asset_ids"]) == {a["id"] for a in clips}


def test_start_production_requires_approved_preview_and_does_not_duplicate_clips(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()

    missing_preview = client.post(f"/api/episodes/{episode['id']}/production/start")
    assert missing_preview.status_code == 409
    assert db.assets_for_episode(episode["id"], kind="clip") == []

    _seed_approved_motion_preview(db, episode["id"])
    assert client.post(f"/api/episodes/{episode['id']}/production/start").status_code == 200
    duplicate = client.post(f"/api/episodes/{episode['id']}/production/start")
    assert duplicate.status_code == 409
    assert len(db.assets_for_episode(episode["id"], kind="clip")) == 2


def test_cancel_production_interrupts_the_production_client_and_clears_tasks(tmp_path):
    remote = _FakeComfyUIClient()
    db, client = _client(tmp_path, production_comfyui=remote)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _seed_approved_motion_preview(db, episode["id"])
    client.post(f"/api/episodes/{episode['id']}/production/start")

    resp = client.post(f"/api/episodes/{episode['id']}/production/cancel")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"cancelled": 2}
    assert remote.interrupt_count == 1
    assert all(a["status"] == "failed" for a in db.assets_for_episode(episode["id"], kind="clip"))


def _seed_approved_loop(db, episode_id):
    loop_id = db.create_asset(episode_id, "loop", "shared")
    db.transition_asset(
        loop_id, expected_status="queued", expected_version=0, status="approved"
    )


def test_music_defaults_returns_editable_late_night_album_prompt(tmp_path):
    _db, client = _client(tmp_path)

    resp = client.get("/api/music/defaults")

    assert resp.status_code == 200
    assert resp.json() == {"base_prompt": DEFAULT_MUSIC_PROMPT, "track_count": 12}


def test_generate_music_requires_loop_and_passes_secret_only_to_request_runner(tmp_path):
    captured = {}

    def runner(db, config, episode_id, *, base_prompt, api_key):
        captured.update(episode_id=episode_id, base_prompt=base_prompt, api_key=api_key)
        return {"job_id": 7, "generated": list(range(12)), "failed": [], "already_complete": False}

    db, client = _client(tmp_path, music_runner=runner)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    blocked = client.post(
        f"/api/episodes/{episode['id']}/music/generate",
        json={"base_prompt": "night jazz", "api_key": "super-secret"},
    )
    assert blocked.status_code == 409

    _seed_approved_loop(db, episode["id"])
    resp = client.post(
        f"/api/episodes/{episode['id']}/music/generate",
        json={"base_prompt": "night jazz", "api_key": "super-secret"},
    )

    assert resp.status_code == 200, resp.text
    assert captured == {
        "episode_id": episode["id"],
        "base_prompt": "night jazz",
        "api_key": "super-secret",
    }
    serialized_db = (tmp_path / "state.sqlite3").read_bytes()
    assert b"super-secret" not in serialized_db


def test_music_provider_error_redacts_request_api_key(tmp_path):
    def runner(*args, **kwargs):
        raise RuntimeError(f"upstream reflected Authorization: {kwargs['api_key']}")

    db, client = _client(tmp_path, music_runner=runner)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    _seed_approved_loop(db, episode["id"])

    resp = client.post(
        f"/api/episodes/{episode['id']}/music/generate",
        json={"base_prompt": "night jazz", "api_key": "do-not-leak"},
    )

    assert resp.status_code == 400
    assert "do-not-leak" not in resp.text
    assert "[REDACTED]" in resp.text


def test_regenerate_music_track_reserves_replacement_in_same_slot(tmp_path):
    calls = []

    def runner(db, config, episode_id, *, base_prompt, api_key):
        calls.append((base_prompt, api_key))
        return {"job_id": db.episode(episode_id)["music_job_id"], "generated": [], "failed": [], "already_complete": False}

    db, client = _client(tmp_path, music_runner=runner)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    db.reserve_studio_music_job(episode["id"], [f"prompt {i}" for i in range(12)])
    old = db.assets_for_episode(episode["id"], kind="music_track")[4]
    db.transition_asset(
        old["id"], expected_status="queued", expected_version=0, status="awaiting_review"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/music/tracks/{old['id']}/regenerate",
        json={"expected_version": 1, "prompt": "new mellow guitar prompt", "api_key": "secret"},
    )

    assert resp.status_code == 200, resp.text
    assert db.asset(old["id"])["status"] == "rejected"
    replacement = db.asset(resp.json()["replacement_asset_id"])
    assert replacement["variant_index"] == 4
    assert replacement["source_prompt"] == "new mellow guitar prompt"
    assert calls == [("new mellow guitar prompt", "secret")]


def test_build_music_mix_requires_all_twelve_approved_slots(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    db.reserve_studio_music_job(episode["id"], [f"prompt {i}" for i in range(12)])
    assets = db.assets_for_episode(episode["id"], kind="music_track")
    for asset in assets[:-1]:
        db.transition_asset(
            asset["id"], expected_status="queued", expected_version=0, status="approved"
        )
    assert client.post(f"/api/episodes/{episode['id']}/music/build-mix").status_code == 409

    last = assets[-1]
    db.transition_asset(
        last["id"], expected_status="queued", expected_version=0, status="approved"
    )
    resp = client.post(f"/api/episodes/{episode['id']}/music/build-mix")
    assert resp.status_code == 200, resp.text
    assert db.tasks_for_episode(episode["id"])[0]["task_type"] == "build_music_mix"


def test_final_render_requires_approved_loop_and_mix_then_enqueues_once(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    assert client.post(f"/api/episodes/{episode['id']}/final/render").status_code == 409

    _seed_approved_loop(db, episode["id"])
    mix_id = db.create_asset(episode["id"], "music_mix", "shared")
    db.transition_asset(
        mix_id, expected_status="queued", expected_version=0, status="approved"
    )
    resp = client.post(f"/api/episodes/{episode['id']}/final/render")

    assert resp.status_code == 200, resp.text
    assert db.task(resp.json()["task_id"])["task_type"] == "render_final"
    assert client.post(f"/api/episodes/{episode['id']}/final/render").status_code == 409


def test_final_metadata_defaults_are_english_and_disclose_ai(tmp_path):
    _db, client = _client(tmp_path)
    episode = client.post(
        "/api/episodes", json={"slug": "e", "title": "Lakeside Chow Chow Café"}
    ).json()

    resp = client.get(f"/api/episodes/{episode['id']}/final/metadata-defaults")

    assert resp.status_code == 200
    assert "Lakeside Chow Chow Café" in resp.json()["title"]
    assert "generative AI" in resp.json()["description"]
    assert resp.json()["privacy_status"] == "private"


def test_youtube_upload_requires_explicit_confirmation_and_approved_final(tmp_path):
    calls = []

    def uploader(db, config, episode_id, **kwargs):
        calls.append((episode_id, kwargs))
        return {"video_id": 1, "youtube_video_id": "yt123", "already_uploaded": False}

    db, client = _client(tmp_path, youtube_uploader=uploader)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    body = {
        "title": "Cozy Jazz",
        "description": "Original AI-assisted ambience production.",
        "tags": ["cozy jazz"],
        "privacy_status": "private",
        "confirm_upload": False,
    }
    denied = client.post(f"/api/episodes/{episode['id']}/final/upload-youtube", json=body)
    assert denied.status_code == 400
    assert calls == []

    body["confirm_upload"] = True
    final_path = tmp_path / "final.mp4"
    final_path.write_bytes(b"final")
    final_id = db.create_asset(episode["id"], "final", "shared")
    db.transition_asset(
        final_id,
        expected_status="queued",
        expected_version=0,
        status="approved",
        path=str(final_path),
    )
    accepted = client.post(f"/api/episodes/{episode['id']}/final/upload-youtube", json=body)
    assert accepted.status_code == 200, accepted.text
    assert calls[0][0] == episode["id"]
    assert calls[0][1]["privacy_status"] == "private"


def test_rejecting_a_failed_asset_requeues_it_the_same_as_awaiting_review(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    motion_id = db.create_asset(episode["id"], "motion_test", "sleep")
    db.transition_asset(
        motion_id, expected_status="queued", expected_version=0,
        status="failed", error="ComfyUI 生成失敗",
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{motion_id}/reject",
        json={"expected_version": 1, "reason": "retry", "new_prompt": "brighter lighting"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["rejected"]["status"] == "rejected"
    assert resp.json()["requeued"]["source_prompt"] == "brighter lighting"


def test_keyframe_defaults_endpoint_returns_the_same_constants_generate_falls_back_to(tmp_path):
    """A brand-new episode has no generate_keyframe task yet, so tab 1's
    prompt boxes/batch-size buttons have no payload_json to pre-fill from --
    this is the endpoint the frontend calls instead so there's something to
    show before the first Generate click (user-reported gap, 2026-08-31).
    Not episode-scoped, so no episode needs to exist to call it."""
    _db, client = _client(tmp_path, keyframe_batch_size=8)

    resp = client.get("/api/keyframes/defaults")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "positive_prompt": DEFAULT_KEYFRAME_PROMPT,
        "negative_prompt": KEYFRAME_NEGATIVE_PROMPT,
        "batch_size": 8,
    }


def test_regenerate_supersedes_the_previous_awaiting_review_batch(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    old_id = db.create_asset(episode["id"], "keyframe", "shared", variant_index=0)
    db.transition_asset(
        old_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/generate", json={"positive_prompt": "try again"}
    )

    assert resp.status_code == 200, resp.text
    assert db.asset(old_id)["status"] == "superseded"


def test_generate_keyframes_404s_for_unknown_episode(tmp_path):
    _db, client = _client(tmp_path)

    resp = client.post("/api/episodes/999/keyframes/generate", json={})

    assert resp.status_code == 404


def test_rejecting_a_keyframe_candidate_is_not_supported(tmp_path):
    """Per-candidate reject-and-requeue doesn't make sense for a 12-up grid
    (there's no "replace candidate #7") -- use regenerate-all instead."""
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = db.create_asset(episode["id"], "keyframe", "shared")
    db.transition_asset(
        keyframe_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{keyframe_id}/reject",
        json={"expected_version": 1, "reason": "not it"},
    )

    assert resp.status_code == 400
    assert db.asset(keyframe_id)["status"] == "awaiting_review", "must not be touched"


def test_approving_a_keyframe_candidate_supersedes_its_siblings(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    chosen = db.create_asset(episode["id"], "keyframe", "shared", variant_index=0)
    other = db.create_asset(episode["id"], "keyframe", "shared", variant_index=1)
    for asset_id in (chosen, other):
        db.transition_asset(
            asset_id, expected_status="queued", expected_version=0, status="awaiting_review"
        )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{chosen}/approve",
        json={"expected_version": 1},
    )

    assert resp.status_code == 200, resp.text
    assert db.asset(chosen)["status"] == "approved"
    assert db.asset(other)["status"] == "superseded"


def test_approve_is_rejected_while_the_batch_is_still_generating(tmp_path):
    """Regression: generate_keyframe publishes candidates one row at a
    time, so a candidate can be awaiting_review before its siblings finish.
    Approving it mid-batch used to produce approved + more awaiting_review
    rows side by side, with motion tasks already fanned out from the
    premature approval."""
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    db.enqueue_task(episode["id"], "generate_keyframe")
    candidate_id = db.create_asset(episode["id"], "keyframe", "shared", variant_index=0)
    db.transition_asset(
        candidate_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{candidate_id}/approve",
        json={"expected_version": 1},
    )

    assert resp.status_code == 409
    assert db.asset(candidate_id)["status"] == "awaiting_review", "must not be touched"


def test_regenerate_is_rejected_once_a_keyframe_is_approved(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    approved_id = db.create_asset(episode["id"], "keyframe", "shared", variant_index=0)
    db.transition_asset(
        approved_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )
    db.transition_asset(
        approved_id, expected_status="awaiting_review", expected_version=1, status="approved"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/generate", json={}
    )

    assert resp.status_code == 409
    assert db.asset(approved_id)["status"] == "approved", "must not be superseded"
    assert db.tasks_for_episode(episode["id"]) == [], "must not enqueue a new batch"


def test_regenerate_is_rejected_while_a_batch_is_still_in_flight(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    first = client.post(
        f"/api/episodes/{episode['id']}/keyframes/generate", json={}
    )
    assert first.status_code == 200, first.text

    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/generate", json={}
    )

    assert resp.status_code == 409
    assert len(db.tasks_for_episode(episode["id"])) == 1, "must not enqueue a second batch"


def test_assemble_preview_requires_both_motion_test_roles_approved(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()

    resp = client.post(f"/api/episodes/{episode['id']}/motion/assemble-preview")
    assert resp.status_code == 409

    sleep_id = db.create_asset(episode["id"], "motion_test", "sleep")
    db.transition_asset(
        sleep_id, expected_status="queued", expected_version=0,
        status="approved", path="sleep.mp4",
    )

    # Only sleep approved -- lookup is still missing.
    resp = client.post(f"/api/episodes/{episode['id']}/motion/assemble-preview")
    assert resp.status_code == 409
    assert db.tasks_for_episode(episode["id"]) == []


def test_assemble_preview_enqueues_build_loop_preview_pinned_to_the_approved_pair(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    sleep_id = db.create_asset(episode["id"], "motion_test", "sleep")
    db.transition_asset(
        sleep_id, expected_status="queued", expected_version=0,
        status="approved", path="sleep.mp4",
    )
    lookup_id = db.create_asset(episode["id"], "motion_test", "lookup")
    db.transition_asset(
        lookup_id, expected_status="queued", expected_version=0,
        status="approved", path="lookup.mp4",
    )

    resp = client.post(f"/api/episodes/{episode['id']}/motion/assemble-preview")

    assert resp.status_code == 200, resp.text
    tasks = db.tasks_for_episode(episode["id"])
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "build_loop_preview"
    assert tasks[0]["asset_id"] is None
    payload = json.loads(tasks[0]["payload_json"])
    assert payload == {"source_asset_ids": {"sleep": sleep_id, "lookup": lookup_id}}


def test_assemble_preview_is_rejected_once_a_preview_is_approved(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    for role in ("sleep", "lookup"):
        motion_id = db.create_asset(episode["id"], "motion_test", role)
        db.transition_asset(
            motion_id, expected_status="queued", expected_version=0,
            status="approved", path=f"{role}.mp4",
        )
    preview_id = db.create_asset(episode["id"], "loop_preview", "shared")
    db.transition_asset(
        preview_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )
    db.transition_asset(
        preview_id, expected_status="awaiting_review", expected_version=1, status="approved"
    )

    resp = client.post(f"/api/episodes/{episode['id']}/motion/assemble-preview")

    assert resp.status_code == 409
    assert db.tasks_for_episode(episode["id"]) == []


def test_assemble_preview_is_rejected_while_a_preview_is_awaiting_review(tmp_path):
    """codex_reviewer Phase 2 review: a preview that already finished
    generating but hasn't been approved yet was neither 'approved' (the old
    guard's only check) nor 'in-flight' (its task is 'done', not queued/
    running) -- a second assemble call passed both guards, and because
    every build writes the same fixed loop_preview-shared-v0.mp4 path with
    no per-build variant index, it silently overwrote the first result out
    from under a second episode_assets row. Reproduced: a second POST
    returned 200 and doubled the task count."""
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    for role in ("sleep", "lookup"):
        motion_id = db.create_asset(episode["id"], "motion_test", role)
        db.transition_asset(
            motion_id, expected_status="queued", expected_version=0,
            status="approved", path=f"{role}.mp4",
        )
    preview_id = db.create_asset(episode["id"], "loop_preview", "shared")
    db.transition_asset(
        preview_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )
    task_id = db.enqueue_task(episode["id"], "build_loop_preview")
    db.claim_next_task()
    db.finish_task(task_id, status="done")

    resp = client.post(f"/api/episodes/{episode['id']}/motion/assemble-preview")

    assert resp.status_code == 409
    assert len(db.tasks_for_episode(episode["id"])) == 1, "must not enqueue a second build"


def test_assemble_preview_is_rejected_while_one_is_still_in_flight(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    for role in ("sleep", "lookup"):
        motion_id = db.create_asset(episode["id"], "motion_test", role)
        db.transition_asset(
            motion_id, expected_status="queued", expected_version=0,
            status="approved", path=f"{role}.mp4",
        )
    first = client.post(f"/api/episodes/{episode['id']}/motion/assemble-preview")
    assert first.status_code == 200, first.text

    resp = client.post(f"/api/episodes/{episode['id']}/motion/assemble-preview")

    assert resp.status_code == 409
    assert len(db.tasks_for_episode(episode["id"])) == 1


def test_rejecting_a_loop_preview_loop_or_final_asset_is_not_supported(tmp_path):
    """Same reasoning as test_rejecting_a_keyframe_candidate_is_not_supported --
    build_loop_preview/build_loop/render_final all create their own asset
    row(s) from scratch and ignore any asset_id a reject-and-requeue would
    hand them."""
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()

    for kind in ("loop_preview", "loop", "final"):
        asset_id = db.create_asset(episode["id"], kind, "shared")
        db.transition_asset(
            asset_id, expected_status="queued", expected_version=0, status="awaiting_review"
        )

        resp = client.post(
            f"/api/episodes/{episode['id']}/assets/{asset_id}/reject",
            json={"expected_version": 1, "reason": "not it"},
        )

        assert resp.status_code == 400, f"kind={kind}: {resp.text}"


def test_duplicate_slug_is_rejected(tmp_path):
    _db, client = _client(tmp_path)
    client.post("/api/episodes", json={"slug": "dup", "title": "A"})

    resp = client.post("/api/episodes", json={"slug": "dup", "title": "B"})

    assert resp.status_code == 409


def test_unknown_episode_and_asset_are_404(tmp_path):
    _db, client = _client(tmp_path)

    assert client.get("/api/episodes/999").status_code == 404
    assert client.post(
        "/api/episodes/999/assets/1/approve", json={"expected_version": 0}
    ).status_code == 404


def test_approve_requires_awaiting_review_status(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = db.create_asset(episode["id"], "keyframe", "shared")

    # Still 'queued', not 'awaiting_review' yet -- nothing has generated it.
    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{keyframe_id}/approve",
        json={"expected_version": 0},
    )

    assert resp.status_code == 409


def test_approve_conflict_on_stale_version(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = db.create_asset(episode["id"], "keyframe", "shared")
    db.transition_asset(
        keyframe_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{keyframe_id}/approve",
        json={"expected_version": 0},  # stale: the transition above already bumped it to 1
    )

    assert resp.status_code == 409


def test_reject_requeues_a_new_variant_with_the_same_kind_and_role(tmp_path):
    """Keyframe no longer supports per-asset reject (see
    test_rejecting_a_keyframe_candidate_is_not_supported) -- motion_test
    still uses the original single-asset reject-and-requeue mechanism
    unchanged, so this exercises that generic path instead."""
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    motion_test_id = db.create_asset(episode["id"], "motion_test", "sleep")
    db.enqueue_task(episode["id"], "generate_motion_test", asset_id=motion_test_id)
    db.transition_asset(
        motion_test_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{motion_test_id}/reject",
        json={"expected_version": 1, "reason": "too dark", "new_prompt": "brighter lighting"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rejected"]["status"] == "rejected"
    assert body["rejected"]["error"] == "too dark"
    assert body["requeued"]["kind"] == "motion_test"
    assert body["requeued"]["variant_index"] == 1
    assert body["requeued"]["source_prompt"] == "brighter lighting"

    tasks = client.get(f"/api/episodes/{episode['id']}").json()["tasks"]
    assert [t["task_type"] for t in tasks] == ["generate_motion_test", "generate_motion_test"]


def test_reject_does_not_carry_source_seed_so_a_retry_gets_a_fresh_roll(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    motion_test_id = db.create_asset(episode["id"], "motion_test", "sleep")
    db.transition_asset(
        motion_test_id, expected_status="queued", expected_version=0,
        status="awaiting_review", source_seed=999999,
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{motion_test_id}/reject",
        json={"expected_version": 1, "reason": "not it"},
    )

    assert resp.json()["requeued"]["source_seed"] is None


def test_approving_motion_test_no_longer_auto_creates_a_clip(tmp_path):
    """As of studio-console-v2-plan.md's Phase 2 restructure, approving a
    sleep/lookup motion_test no longer auto-advances to clip generation --
    clip and upscale_clip share one remote ComfyUI client (stages.py's
    clip_comfyui), and tab 3 (not built yet) is what collects the cloud
    credential that client needs. Auto-advancing here would fire a request
    needing a token before the reviewer ever reaches tab 3
    (codex_reviewer design consult, 2026-08-30). Approving motion_test now
    just sits there -- assembling the low-res preview (tab 2's own
    endpoint, see test_studio_stages.py for its handler coverage) is the
    next actual step, and it's what tab 2's design gates clip generation
    behind once tab 3 exists. Seed/prompt carry-over into a clip asset is
    still covered directly at the handler level in test_studio_stages.py's
    test_generate_clip_reuses_the_inherited_seed_instead_of_deriving_a_new_one
    -- that logic didn't change, only what triggers it."""
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = db.create_asset(episode["id"], "keyframe", "shared")
    db.transition_asset(
        keyframe_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )
    client.post(
        f"/api/episodes/{episode['id']}/assets/{keyframe_id}/approve",
        json={"expected_version": 1},
    )
    motion_test = next(
        a for a in client.get(f"/api/episodes/{episode['id']}").json()["assets"]
        if a["kind"] == "motion_test"
    )
    db.transition_asset(
        motion_test["id"], expected_status="queued", expected_version=0,
        status="awaiting_review", source_seed=424242,
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{motion_test['id']}/approve",
        json={"expected_version": 1},
    )

    assert resp.status_code == 200
    detail = client.get(f"/api/episodes/{episode['id']}").json()
    assert not [a for a in detail["assets"] if a["kind"] == "clip"]
    assert not [t for t in detail["tasks"] if t["task_type"] == "generate_clip"]


def test_artifact_404s_when_nothing_has_been_generated_yet(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = db.create_asset(episode["id"], "keyframe", "shared")

    assert client.get(f"/api/artifact/{keyframe_id}").status_code == 404


def test_artifact_serves_bytes_and_supports_range_requests(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = db.create_asset(episode["id"], "keyframe", "shared")

    payload = bytes(range(256)) * 4  # 1024 bytes, content is position-addressable
    media_path = tmp_path / "keyframe.bin"
    media_path.write_bytes(payload)
    db.transition_asset(
        keyframe_id, expected_status="queued", expected_version=0,
        status="awaiting_review", path=str(media_path),
    )

    full = client.get(f"/api/artifact/{keyframe_id}")
    assert full.status_code == 200
    assert full.content == payload

    partial = client.get(f"/api/artifact/{keyframe_id}", headers={"Range": "bytes=10-19"})
    assert partial.status_code == 206, partial.headers
    assert partial.content == payload[10:20]
    assert partial.headers["content-range"] == f"bytes 10-19/{len(payload)}"


def test_frontend_index_is_served(tmp_path):
    _db, client = _client(tmp_path)

    resp = client.get("/")

    assert resp.status_code == 200
    assert "Lyria Studio" in resp.text


_SYNTHESIS_KIND = {
    "generate_keyframe": "keyframe", "build_loop_preview": "loop_preview",
    "build_loop": "loop", "build_music_mix": "music_mix", "render_final": "final",
}


def _fake_handlers(tmp_path):
    def handler(db, task):
        # build_loop/render_final/generate_keyframe synthesize a new
        # artifact from scratch (generate_keyframe fans a real batch out
        # into several rows -- this fake only needs one to exercise the
        # rest of the pipeline, see test_studio_stages.py for real batching
        # coverage) rather than filling in a pre-created placeholder, so
        # they carry no asset_id -- the handler creates the row itself.
        asset_id = task["asset_id"]
        if asset_id is None:
            asset_id = db.create_asset(
                task["episode_id"], _SYNTHESIS_KIND[task["task_type"]], "shared",
                status="running",
            )
        media_path = tmp_path / f"asset-{asset_id}.bin"
        media_path.write_bytes(b"fake-media-bytes")
        db.transition_asset(
            asset_id,
            expected_status="running",
            expected_version=db.asset(asset_id)["state_version"],
            status="awaiting_review",
            path=str(media_path),
            sha256="deadbeef",
        )

    return {task_type: handler for task_type in (
        "generate_keyframe", "generate_motion_test", "generate_clip",
        "upscale_clip", "build_loop_preview", "build_loop", "build_music_mix", "render_final",
    )}


def _approve_all_awaiting(client, episode_id):
    detail = client.get(f"/api/episodes/{episode_id}").json()
    approved_any = False
    for asset in detail["assets"]:
        if asset["status"] == "awaiting_review":
            resp = client.post(
                f"/api/episodes/{episode_id}/assets/{asset['id']}/approve",
                json={"expected_version": asset["state_version"]},
            )
            assert resp.status_code == 200, resp.text
            approved_any = True
    return approved_any


def test_visual_pipeline_walks_keyframe_through_loop_preview_with_fake_generators(tmp_path):
    """keyframe -> motion test (sleep+lookup) -> assembled 64s low-res
    preview, entirely with fakes -- no ComfyUI, no ffmpeg, no cost. This is
    as far as the automatic/HTTP-reachable pipeline goes as of Phase 2:
    approving the assembled loop_preview intentionally does NOT cascade
    into clip generation (tab 3's not-yet-built "start cloud processing"
    action is what does that -- see test_approving_motion_test_no_longer_
    auto_creates_a_clip and app.py's loop_preview branch in
    _continue_after_approval). The clip -> clip_1080p -> loop leg is
    unchanged by Phase 2 and is covered separately by
    test_clip_to_loop_fan_in_still_walks_from_directly_approved_clips,
    entering directly at an approved clip pair since there's no HTTP
    trigger for clip generation yet.
    """
    db, client = _client(tmp_path)
    worker = StudioWorker(db, handlers=_fake_handlers(tmp_path))

    episode = client.post("/api/episodes", json={"slug": "chowchow-001", "title": "第一集"}).json()
    client.post(f"/api/episodes/{episode['id']}/keyframes/generate", json={})

    # Gate 1a: keyframe.
    while worker.run_once():
        pass
    assert _approve_all_awaiting(client, episode["id"])

    # Fan-out created two motion_test tasks (sleep + lookup).
    while worker.run_once():
        pass
    detail = client.get(f"/api/episodes/{episode['id']}").json()
    motion_tests = [a for a in detail["assets"] if a["kind"] == "motion_test"]
    assert {a["role"] for a in motion_tests} == {"sleep", "lookup"}
    assert all(a["status"] == "awaiting_review" for a in motion_tests)
    assert _approve_all_awaiting(client, episode["id"])

    # Approving both motion_test roles must NOT auto-create a clip.
    detail = client.get(f"/api/episodes/{episode['id']}").json()
    assert not [a for a in detail["assets"] if a["kind"] == "clip"]

    # Tab 2's "Assemble 64s Preview" button.
    resp = client.post(f"/api/episodes/{episode['id']}/motion/assemble-preview")
    assert resp.status_code == 200, resp.text
    while worker.run_once():
        pass
    detail = client.get(f"/api/episodes/{episode['id']}").json()
    previews = [a for a in detail["assets"] if a["kind"] == "loop_preview"]
    assert len(previews) == 1
    assert previews[0]["status"] == "awaiting_review"

    # Approving the preview still doesn't create a clip -- that's tab 3's job.
    client.post(
        f"/api/episodes/{episode['id']}/assets/{previews[0]['id']}/approve",
        json={"expected_version": previews[0]["state_version"]},
    )
    detail = client.get(f"/api/episodes/{episode['id']}").json()
    assert not [a for a in detail["assets"] if a["kind"] == "clip"]
    assert not [t for t in detail["tasks"] if t["task_type"] == "generate_clip"]


def test_clip_to_loop_fan_in_still_walks_from_directly_approved_clips(tmp_path):
    """clip -> clip_1080p -> loop is unchanged by the Phase 2 restructure --
    only motion_test's auto-advance into clip was removed (see
    test_visual_pipeline_walks_keyframe_through_loop_preview_with_fake_
    generators for what replaced it). There's no HTTP-reachable way to
    create a `clip` asset yet (that's tab 3's not-yet-built job), so this
    enters directly at two already-approved clip assets, created straight
    through StateDB the same way other tests in this file seed pre-existing
    state -- everything from here on exercises the real app.py cascade.
    """
    db, client = _client(tmp_path)
    worker = StudioWorker(db, handlers=_fake_handlers(tmp_path))
    episode = client.post("/api/episodes", json={"slug": "chowchow-001", "title": "第一集"}).json()

    for role in ("sleep", "lookup"):
        clip_id = db.create_asset(episode["id"], "clip", role)
        db.enqueue_task(episode["id"], "generate_clip", asset_id=clip_id)
        db.transition_asset(
            clip_id, expected_status="queued", expected_version=0, status="awaiting_review"
        )

    # Gate 1c: approving both official clips fans out to upscale_clip.
    assert _approve_all_awaiting(client, episode["id"])

    # Gate 2: upscale, one per role.
    while worker.run_once():
        pass
    detail = client.get(f"/api/episodes/{episode['id']}").json()
    upscaled = [a for a in detail["assets"] if a["kind"] == "clip_1080p"]
    assert {a["role"] for a in upscaled} == {"sleep", "lookup"}

    # Approving only one role must NOT fan in to build_loop yet.
    sleep_asset = next(a for a in upscaled if a["role"] == "sleep")
    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{sleep_asset['id']}/approve",
        json={"expected_version": sleep_asset["state_version"]},
    )
    assert resp.status_code == 200
    assert db.tasks_for_episode(episode["id"])[-1]["task_type"] != "build_loop"

    # Approving the second role fans in to build_loop.
    lookup_asset = next(a for a in upscaled if a["role"] == "lookup")
    client.post(
        f"/api/episodes/{episode['id']}/assets/{lookup_asset['id']}/approve",
        json={"expected_version": lookup_asset["state_version"]},
    )
    tasks = db.tasks_for_episode(episode["id"])
    assert tasks[-1]["task_type"] == "build_loop"

    # Gate: the stitched 64s loop itself.
    while worker.run_once():
        pass
    detail = client.get(f"/api/episodes/{episode['id']}").json()
    loop_assets = [a for a in detail["assets"] if a["kind"] == "loop"]
    assert len(loop_assets) == 1
    assert loop_assets[0]["status"] == "awaiting_review"

    # The artifact for the finished loop is actually fetchable.
    artifact_resp = client.get(f"/api/artifact/{loop_assets[0]['id']}")
    assert artifact_resp.status_code == 200
    assert artifact_resp.content == b"fake-media-bytes"
