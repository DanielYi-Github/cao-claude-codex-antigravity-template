from __future__ import annotations

import json
from typing import Any

from conftest import install_scene_presets
from fastapi.testclient import TestClient

from lyria_auto.config import AppConfig
from lyria_auto.db import StateDB
from lyria_auto.studio.app import create_app
from lyria_auto.studio.stages import KEYFRAME_NEGATIVE_PROMPT, default_keyframe_prompt
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


def _config_for(tmp_path, *, keyframe_batch_size: int = 12) -> AppConfig:
    return AppConfig(
        settings={"studio": {"keyframe_batch_size": keyframe_batch_size}},
        prompts={}, channels={}, root=tmp_path,
    )


def _client(tmp_path, *, keyframe_batch_size: int = 12, comfyui: Any = None):
    install_scene_presets(tmp_path)
    db = StateDB(tmp_path / "state.sqlite3")
    config = _config_for(tmp_path, keyframe_batch_size=keyframe_batch_size)
    app = create_app(db, config, comfyui or _FakeComfyUIClient())
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
        "positive_prompt": default_keyframe_prompt(_config_for(tmp_path)),
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
    assert resp.json() == {"cancelled": 1, "abandoned_paid_operations": 0}
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

    assert resp.json() == {"cancelled": 2, "abandoned_paid_operations": 0}
    assert db.task(lookup_task)["status"] == "failed"
    assert db.asset(lookup_id)["status"] == "failed"


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
    body = resp.json()
    assert body["positive_prompt"] == default_keyframe_prompt(_config_for(tmp_path))
    assert body["negative_prompt"] == KEYFRAME_NEGATIVE_PROMPT
    assert body["batch_size"] == 8
    # 頁籤 1 的晶片要知道預設是哪一組選擇才能把對應的晶片標成 active。
    assert body["scene"]["venue"] and body["scene"]["time"]
    assert body["estimated_tokens"] <= 256


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
    # Seeded directly rather than via the keyframe fan-out: approving a
    # keyframe no longer creates motion_test assets, because tab 2 now
    # chooses the backend/model/resolution first (see
    # test_visual_pipeline_walks_keyframe_through_loop_preview_with_
    # fake_generators). What this test is about -- approving a
    # motion_test must not cascade into a clip -- is unchanged.
    motion_test_id = db.create_asset(episode["id"], "motion_test", "sleep")
    motion_test = {"id": motion_test_id}
    db.transition_asset(
        motion_test_id, expected_status="queued", expected_version=0,
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

    # Approving a keyframe no longer enqueues motion generation by
    # itself: tab 2 now picks the backend (local ComfyUI or Google Veo)
    # along with its model/resolution, so the reviewer has to press its
    # own generate button. This walk exercises the local backend.
    resp = client.post(
        f"/api/episodes/{episode['id']}/motion/generate",
        json={"provider": "comfyui"},
    )
    assert resp.status_code == 200, resp.text
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


def test_motion_defaults_and_custom_prompts(tmp_path):
    db, client = _client(tmp_path)
    # 1. Test GET /api/motion/defaults
    defaults_resp = client.get("/api/motion/defaults")
    assert defaults_resp.status_code == 200
    data = defaults_resp.json()
    assert "sleep_prompt" in data
    assert "lookup_prompt" in data
    assert "presets" in data
    assert "nature_breeze" in data["presets"]

    # 2. Setup episode with approved keyframe
    episode = client.post("/api/episodes", json={"slug": "ep-motion-prompts", "title": "Motion Prompts Test"}).json()
    kf_path = tmp_path / "fake_keyframe.png"
    kf_path.write_bytes(b"fake-image")
    asset_id = db.create_asset(
        episode["id"],
        "keyframe",
        "shared",
        source_prompt="cafe with window view of a calm lake and green hills",
    )
    db.transition_asset(
        asset_id,
        expected_status="queued",
        expected_version=0,
        status="approved",
        path=str(kf_path),
    )

    # 3. Test POST /api/episodes/{id}/motion/suggest-prompts
    suggest_resp = client.post(f"/api/episodes/{episode['id']}/motion/suggest-prompts")
    assert suggest_resp.status_code == 200
    s_data = suggest_resp.json()
    assert "scene_detected" in s_data
    assert "sleep_prompt" in s_data
    assert "lookup_prompt" in s_data

    # 4. Test POST /api/episodes/{id}/motion/generate with custom sleep & lookup prompts
    custom_sleep = "Custom sleep motion with gentle breeze and birds"
    custom_lookup = "Custom lookup motion with lake ripples"
    resp = client.post(
        f"/api/episodes/{episode['id']}/motion/generate",
        json={
            "provider": "comfyui",
            "sleep_prompt": custom_sleep,
            "lookup_prompt": custom_lookup,
        },
    )
    assert resp.status_code == 200
    assets = db.assets_for_episode(episode["id"], kind="motion_test")
    sleep_a = next(a for a in assets if a["role"] == "sleep")
    lookup_a = next(a for a in assets if a["role"] == "lookup")
    assert sleep_a["source_prompt"] == custom_sleep
    assert lookup_a["source_prompt"] == custom_lookup



# --- 場景組合器的 HTTP 介面 ------------------------------------------


def test_scene_presets_endpoint_feeds_the_chip_rows(tmp_path):
    _, client = _client(tmp_path)
    body = client.get("/api/keyframes/scene-presets").json()
    axis_ids = [axis["id"] for axis in body["axes"]]
    assert axis_ids == ["venue", "aspect", "season", "weather", "time", "pose", "camera"]
    assert set(body["defaults"]) == set(axis_ids)
    assert body["limits"]["training_limit"] == 256
    # 英文措辭刻意不外送：提示詞的語序留在後端（見 app.py 該路由的註解）。
    first = body["axes"][0]["options"][0]
    assert set(first) == {"id", "zh"}


def test_composing_a_scene_returns_matching_motion_prompts(tmp_path):
    _, client = _client(tmp_path)
    resp = client.post(
        "/api/keyframes/compose",
        json={"scene": {"venue": "sea_cliff", "aspect": "outdoor_seat", "pose": "sitting"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "open ocean" in body["positive_prompt"]
    assert body["estimated_tokens"] <= 256
    # 關鍵幀與兩支動作片必須共用同一組場景措辭，否則起始幀會跟文字互相矛盾。
    for prompt in body["motion_prompts"].values():
        assert "worn stone terrace" in prompt
    assert body["labels_zh"]["venue"] == "海崖"


def test_composing_a_physically_impossible_scene_is_rejected(tmp_path):
    _, client = _client(tmp_path)
    resp = client.post(
        "/api/keyframes/compose",
        json={"scene": {"weather": "snow", "season": "summer"}},
    )
    assert resp.status_code == 400
    assert "雪" in resp.json()["detail"]


def test_generating_with_a_scene_stores_the_matching_motion_prompts(tmp_path):
    """頁籤 2 靠這個 payload 才拿得到跟關鍵幀同場景的動作文字。"""
    db, client = _client(tmp_path)
    episode_id = client.post(
        "/api/episodes", json={"slug": "chowchow-scene", "title": "場景"}
    ).json()["id"]
    resp = client.post(
        f"/api/episodes/{episode_id}/keyframes/generate",
        json={"scene": {"venue": "lakeside", "aspect": "outdoor_seat", "pose": "curled"}},
    )
    assert resp.status_code == 200, resp.text

    payload = json.loads(db.tasks_for_episode(episode_id)[-1]["payload_json"])
    assert payload["scene"] == {
        "venue": "lakeside", "aspect": "outdoor_seat", "pose": "curled",
    }
    assert set(payload["motion_prompts"]) == {"sleep", "lookup"}

    scoped = client.get(f"/api/motion/defaults?episode_id={episode_id}").json()
    assert scoped["sleep_prompt"] == payload["motion_prompts"]["sleep"]
    assert "stays curled" in scoped["sleep_prompt"]
    assert "worn stone terrace" in scoped["sleep_prompt"]
    # 不帶 episode_id 時仍回全域預設（趴睡、室內木地板），舊行為不變。
    assert "stays lying" in client.get("/api/motion/defaults").json()["sleep_prompt"]


def test_motion_defaults_falls_back_for_episodes_made_before_scenes_existed(tmp_path):
    """這個功能之前生的集數 payload 裡沒有 motion_prompts，必須安靜地退回
    全域預設，而不是爆掉或回空字串。"""
    db, client = _client(tmp_path)
    episode_id = client.post(
        "/api/episodes", json={"slug": "chowchow-legacy", "title": "舊集數"}
    ).json()["id"]
    db.enqueue_task(
        episode_id, "generate_keyframe", payload={"positive_prompt": "手寫的舊提示詞"}
    )
    scoped = client.get(f"/api/motion/defaults?episode_id={episode_id}").json()
    assert scoped["sleep_prompt"] == client.get("/api/motion/defaults").json()["sleep_prompt"]
    assert scoped["sleep_prompt"]


# --- 動作提示詞與關鍵幀場景的接縫（artifacts/spec.md 2026-09-06）---------
#
# 這一組全部是回歸測試。824eb3b 的四個環境預設按鈕與 suggest 端點都是整段
# 覆寫寫死的模板，無論頁籤 1 挑了什麼場景，送給 Veo 的文字都宣稱狗趴平在
# 室內咖啡館地板、外面有溫暖陽光。


def _episode_with_scene(client, db, tmp_path, slug, scene):
    """建一集、用指定的晶片場景送出關鍵幀任務，並核准一張關鍵幀。"""
    episode = client.post(
        "/api/episodes", json={"slug": slug, "title": slug}
    ).json()
    composed = client.post("/api/keyframes/compose", json={"scene": scene}).json()
    resp = client.post(
        f"/api/episodes/{episode['id']}/keyframes/generate",
        json={"positive_prompt": composed["positive_prompt"], "scene": scene},
    )
    assert resp.status_code == 200, resp.text
    kf_path = tmp_path / f"{slug}.png"
    kf_path.write_bytes(b"fake-image")
    asset_id = db.create_asset(
        episode["id"], "keyframe", "shared",
        source_prompt=composed["positive_prompt"],
    )
    db.transition_asset(
        asset_id, expected_status="queued", expected_version=0,
        status="approved", path=str(kf_path),
    )
    return episode


_OUTDOOR_NIGHT = {
    "venue": "lakeside", "aspect": "outdoor_seat", "season": "winter",
    "weather": "snow", "time": "night", "pose": "curled",
}


def test_motion_presets_follow_the_episode_scene(tmp_path):
    """驗收 1、2、3：四個環境主題按鈕不得寫死姿勢、地面、有窗、白天。"""
    db, client = _client(tmp_path)
    episode = _episode_with_scene(client, db, tmp_path, "ep-outdoor-night", _OUTDOOR_NIGHT)

    data = client.get(f"/api/motion/defaults?episode_id={episode['id']}").json()
    assert set(data["presets"]) == {
        "nature_breeze", "rainy_window", "urban_sunset", "forest_woods"
    }
    pairs = [(data["sleep_prompt"], data["lookup_prompt"])]
    for preset in data["presets"].values():
        # 人類的產品文案不動
        assert preset["label"] and preset["description"]
        pairs.append((preset["sleep_prompt"], preset["lookup_prompt"]))

    for sleep, lookup in pairs:
        # 1. 姿勢與地面跟著晶片走（蜷睡的 lookup 是抬頭後把鼻子塞回毛裡，
        #    措辭本來就與 sleep 不同——所以兩者分開驗，不是找同一個字）
        assert "stays curled in the exact same pose" in sleep
        assert "tucks its nose back into its fur" in lookup
        for text in (sleep, lookup):
            assert "worn stone terrace" in text
            assert "lying flat on the cafe floor" not in text
            # 2. 全戶外座位沒有窗
            assert "window" not in text.lower()
            # 3. 入夜不得出現日光措辭
            assert "daylight" not in text.lower()
            assert "sunlight" not in text.lower()
            assert "the lamplight holds steady" in text


def test_motion_presets_follow_a_daytime_indoor_scene(tmp_path):
    """同一組按鈕換成室內白天，措辭必須跟著換——證明上一條不是靠寫死另一組字通過的。"""
    db, client = _client(tmp_path)
    episode = _episode_with_scene(
        client, db, tmp_path, "ep-indoor-day",
        {"venue": "forest_cabin", "aspect": "inside_glass", "time": "afternoon", "pose": "lying"},
    )
    data = client.get(f"/api/motion/defaults?episode_id={episode['id']}").json()
    for text in [data["sleep_prompt"], data["presets"]["forest_woods"]["sleep_prompt"]]:
        assert "wide plank floor" in text
        assert "stays lying in the exact same pose" in text
        assert "Beyond the glass," in text
        assert "the daylight shifts almost imperceptibly" in text


def test_suggest_prompts_uses_the_scene_not_keyword_matching(tmp_path):
    """驗收 4：`"grain"`（每張關鍵幀提示詞結尾都有）裡含有 `"rain"`，
    舊的關鍵字啟發法因此把每一個場地都判成下雨。現在改讀存好的選擇。"""
    db, client = _client(tmp_path)
    episode = _episode_with_scene(client, db, tmp_path, "ep-suggest", _OUTDOOR_NIGHT)

    data = client.post(f"/api/episodes/{episode['id']}/motion/suggest-prompts").json()
    assert data["source"] == "scene"
    for text in (data["sleep_prompt"], data["lookup_prompt"]):
        assert "worn stone terrace" in text
        assert "raindrop" not in text.lower()
        assert "window" not in text.lower()
        # 場地是湖畔，推導出來的環境動態就該是湖
        assert "lake surface" in text


def test_motion_prompts_contain_no_one_way_motion(tmp_path):
    """驗收 5：單向飛越畫面的鳥回不到第一幀，會直接推高 veo.seam_score，
    也牴觸 MOTION_NEGATIVE_PROMPT 的 "new objects"。"""
    db, client = _client(tmp_path)
    episode = _episode_with_scene(client, db, tmp_path, "ep-birds", _OUTDOOR_NIGHT)
    data = client.get(f"/api/motion/defaults?episode_id={episode['id']}").json()
    texts = [data["sleep_prompt"], data["lookup_prompt"]]
    for preset in data["presets"].values():
        texts += [preset["sleep_prompt"], preset["lookup_prompt"]]
    for text in texts:
        assert "bird" not in text.lower()


def test_scene_stale_flag_when_keyframe_prompt_was_hand_edited(tmp_path):
    """稽核發現 H：手改頁籤 1 的文字、晶片沒動時，動作提示詞仍依晶片推導，
    可能與畫面不符且毫無跡象——所以要主動講出來。"""
    db, client = _client(tmp_path)
    episode = client.post(
        "/api/episodes", json={"slug": "ep-stale", "title": "stale"}
    ).json()

    # 照晶片原樣送出：不算 stale
    composed = client.post("/api/keyframes/compose", json={"scene": _OUTDOOR_NIGHT}).json()
    client.post(
        f"/api/episodes/{episode['id']}/keyframes/generate",
        json={"positive_prompt": composed["positive_prompt"], "scene": _OUTDOOR_NIGHT},
    )
    assert client.get(f"/api/motion/defaults?episode_id={episode['id']}").json()["scene_stale"] is False

    # 手改文字、晶片不動：要標成 stale。直接 enqueue 而不再打一次
    # keyframes/generate——前一個任務還在排隊，那個端點會擋 409（刻意的）。
    db.enqueue_task(
        episode["id"], "generate_keyframe",
        payload={"positive_prompt": "a completely different hand written prompt",
                 "scene": _OUTDOOR_NIGHT},
    )
    assert client.get(f"/api/motion/defaults?episode_id={episode['id']}").json()["scene_stale"] is True


def test_motion_defaults_survive_an_unknown_stored_scene(tmp_path):
    """有人改了 scene_presets.yaml 的選項 id，舊集數存的選擇就對不上了。
    頁籤 2 必須還能開，不能整個 500。"""
    db, client = _client(tmp_path)
    episode = client.post(
        "/api/episodes", json={"slug": "ep-legacy", "title": "legacy"}
    ).json()
    db.enqueue_task(
        episode["id"], "generate_keyframe",
        payload={"positive_prompt": "x", "scene": {"venue": "no_such_venue"}},
    )
    resp = client.get(f"/api/motion/defaults?episode_id={episode['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["sleep_prompt"]
    # 「選項 id 對不上」不是「人類手改過提示詞」——不能顯示那句提示。
    assert data["scene_stale"] is False
