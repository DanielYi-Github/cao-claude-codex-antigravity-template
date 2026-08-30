from __future__ import annotations

import json

from fastapi.testclient import TestClient

from lyria_auto.db import StateDB
from lyria_auto.studio.app import create_app
from lyria_auto.studio.stages import DEFAULT_KEYFRAME_PROMPT, KEYFRAME_NEGATIVE_PROMPT
from lyria_auto.studio.worker import StudioWorker


def _client(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    app = create_app(db)
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


def test_generate_keyframes_enqueues_one_task_with_the_prompt_in_its_payload(tmp_path):
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()

    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/generate",
        json={"positive_prompt": "rainy cafe", "negative_prompt": "no people"},
    )

    assert resp.status_code == 200, resp.text
    assert client.get(f"/api/episodes/{episode['id']}").json()["episode"]["status"] == "in_progress"
    tasks = db.tasks_for_episode(episode["id"])
    assert len(tasks) == 1
    assert tasks[0]["task_type"] == "generate_keyframe"
    assert tasks[0]["asset_id"] is None
    payload = json.loads(tasks[0]["payload_json"])
    assert payload == {"positive_prompt": "rainy cafe", "negative_prompt": "no people"}


def test_generate_keyframes_with_no_prompt_still_enqueues_a_task(tmp_path):
    """Omitted prompts aren't an error -- the endpoint resolves them to the
    same DEFAULT_KEYFRAME_PROMPT/KEYFRAME_NEGATIVE_PROMPT the handler would
    fall back to anyway, and always persists the concrete values (never
    null) so the frontend can show/pre-fill "what actually generated these
    candidates" even for a default-prompt batch."""
    db, client = _client(tmp_path)
    episode = client.post("/api/episodes", json={"slug": "e", "title": "E"}).json()

    resp = client.post(f"/api/episodes/{episode['id']}/keyframes/generate", json={})

    assert resp.status_code == 200, resp.text
    tasks = db.tasks_for_episode(episode["id"])
    assert len(tasks) == 1
    payload = json.loads(tasks[0]["payload_json"])
    assert payload == {
        "positive_prompt": DEFAULT_KEYFRAME_PROMPT,
        "negative_prompt": KEYFRAME_NEGATIVE_PROMPT,
    }


def test_keyframe_defaults_endpoint_returns_the_same_constants_generate_falls_back_to(tmp_path):
    """A brand-new episode has no generate_keyframe task yet, so tab 1's
    prompt boxes have no payload_json to pre-fill from -- this is the
    endpoint the frontend calls instead so the boxes show something before
    the first Generate click (user-reported gap, 2026-08-31). Not episode-
    scoped, so no episode needs to exist to call it."""
    _db, client = _client(tmp_path)

    resp = client.get("/api/keyframes/defaults")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "positive_prompt": DEFAULT_KEYFRAME_PROMPT,
        "negative_prompt": KEYFRAME_NEGATIVE_PROMPT,
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
    "build_loop": "loop", "render_final": "final",
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
        "upscale_clip", "build_loop_preview", "build_loop", "render_final",
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
