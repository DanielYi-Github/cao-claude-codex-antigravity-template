from __future__ import annotations

from fastapi.testclient import TestClient

from lyria_auto.db import StateDB
from lyria_auto.studio.app import create_app
from lyria_auto.studio.worker import StudioWorker


def _client(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    app = create_app(db)
    return db, TestClient(app)


def test_creating_an_episode_seeds_a_keyframe_task(tmp_path):
    _db, client = _client(tmp_path)

    resp = client.post("/api/episodes", json={"slug": "chowchow-001", "title": "第一集"})

    assert resp.status_code == 200, resp.text
    episode = resp.json()
    assert episode["status"] == "in_progress"

    detail = client.get(f"/api/episodes/{episode['id']}").json()
    assert len(detail["assets"]) == 1
    assert detail["assets"][0]["kind"] == "keyframe"
    assert detail["assets"][0]["status"] == "queued"
    assert len(detail["tasks"]) == 1
    assert detail["tasks"][0]["task_type"] == "generate_keyframe"


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
    _db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe = client.get(f"/api/episodes/{episode['id']}").json()["assets"][0]

    # Still 'queued', not 'awaiting_review' yet -- nothing has generated it.
    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{keyframe['id']}/approve",
        json={"expected_version": keyframe["state_version"]},
    )

    assert resp.status_code == 409


def test_approve_conflict_on_stale_version(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = client.get(f"/api/episodes/{episode['id']}").json()["assets"][0]["id"]
    db.transition_asset(
        keyframe_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{keyframe_id}/approve",
        json={"expected_version": 0},  # stale: the transition above already bumped it to 1
    )

    assert resp.status_code == 409


def test_reject_requeues_a_new_variant_with_the_same_kind_and_role(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = client.get(f"/api/episodes/{episode['id']}").json()["assets"][0]["id"]
    db.transition_asset(
        keyframe_id, expected_status="queued", expected_version=0, status="awaiting_review"
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{keyframe_id}/reject",
        json={"expected_version": 1, "reason": "too dark", "new_prompt": "brighter lighting"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rejected"]["status"] == "rejected"
    assert body["rejected"]["error"] == "too dark"
    assert body["requeued"]["kind"] == "keyframe"
    assert body["requeued"]["variant_index"] == 1
    assert body["requeued"]["source_prompt"] == "brighter lighting"

    tasks = client.get(f"/api/episodes/{episode['id']}").json()["tasks"]
    assert [t["task_type"] for t in tasks] == ["generate_keyframe", "generate_keyframe"]


def test_reject_does_not_carry_source_seed_so_a_retry_gets_a_fresh_roll(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = client.get(f"/api/episodes/{episode['id']}").json()["assets"][0]["id"]
    db.transition_asset(
        keyframe_id, expected_status="queued", expected_version=0,
        status="awaiting_review", source_seed=999999,
    )

    resp = client.post(
        f"/api/episodes/{episode['id']}/assets/{keyframe_id}/reject",
        json={"expected_version": 1, "reason": "not it"},
    )

    assert resp.json()["requeued"]["source_seed"] is None


def test_approving_motion_test_carries_its_source_seed_into_the_clip_it_creates(tmp_path):
    """Mirrors source_prompt's carry-over: the 1024x576 production render
    should start from the same noise the reviewer approved at 768x432, not
    an unrelated seed -- see stages.py::_generate_motion."""
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = client.get(f"/api/episodes/{episode['id']}").json()["assets"][0]["id"]
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

    clip = next(
        a for a in client.get(f"/api/episodes/{episode['id']}").json()["assets"]
        if a["kind"] == "clip" and a["role"] == motion_test["role"]
    )
    assert resp.status_code == 200
    assert clip["source_seed"] == 424242


def test_artifact_404s_when_nothing_has_been_generated_yet(tmp_path):
    _db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = client.get(f"/api/episodes/{episode['id']}").json()["assets"][0]["id"]

    assert client.get(f"/api/artifact/{keyframe_id}").status_code == 404


def test_artifact_serves_bytes_and_supports_range_requests(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()
    keyframe_id = client.get(f"/api/episodes/{episode['id']}").json()["assets"][0]["id"]

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


_SYNTHESIS_KIND = {"build_loop": "loop", "render_final": "final"}


def _fake_handlers(tmp_path):
    def handler(db, task):
        # build_loop/render_final synthesize a new artifact from already
        # -approved inputs rather than filling in a pre-created placeholder,
        # so they carry no asset_id -- the handler creates the row itself.
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
        "upscale_clip", "build_loop", "render_final",
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


def test_full_visual_pipeline_walks_all_six_gates_with_fake_generators(tmp_path):
    """End-to-end: keyframe -> motion test -> official clip -> upscale -> loop,
    entirely with fakes -- no ComfyUI, no ffmpeg, no cost, matching the
    Stage 2 promise in studio-architecture-plan.md.
    """
    db, client = _client(tmp_path)
    worker = StudioWorker(db, handlers=_fake_handlers(tmp_path))

    episode = client.post("/api/episodes", json={"slug": "chowchow-001", "title": "第一集"}).json()

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

    # Gate 1c: official clip generation, one per role.
    while worker.run_once():
        pass
    detail = client.get(f"/api/episodes/{episode['id']}").json()
    clips = [a for a in detail["assets"] if a["kind"] == "clip"]
    assert {a["role"] for a in clips} == {"sleep", "lookup"}
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
