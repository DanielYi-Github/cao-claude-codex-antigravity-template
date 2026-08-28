from __future__ import annotations

from pathlib import Path

import pytest
from fakes_visual import FakeVisualSDK, png_bytes

from lyria_auto.db import StateDB
from lyria_auto.errors import VisualPreflightError
from lyria_auto.visual_preflight import VisualPreflightService


def service(project_config, fake):
    db = StateDB(project_config.root / "workspace" / "preflight.sqlite3")
    return db, VisualPreflightService(
        project_config,
        db,
        sdk_client=fake,
        sleep=lambda _: None,
        video_validator=lambda _: None,
    )


def test_exact_paid_allowance_is_required(project_config):
    fake = FakeVisualSDK()
    db, preflight = service(project_config, fake)
    try:
        with pytest.raises(VisualPreflightError, match="精確授權"):
            preflight.start(allow_image_outputs=0, allow_video_outputs=1)
        assert fake.image_calls == 0
        assert fake.video_starts == 0
    finally:
        db.close()


def test_preflight_never_retries_transient_paid_call(project_config):
    fake = FakeVisualSDK()
    attempts = 0

    class TooManyRequests(RuntimeError):
        status_code = 429

    def transient(**kwargs):
        nonlocal attempts
        attempts += 1
        raise TooManyRequests("temporary")

    fake.interactions.create = transient
    db, preflight = service(project_config, fake)
    try:
        with pytest.raises(VisualPreflightError, match="VISUAL_START_UNCERTAIN"):
            preflight.start(allow_image_outputs=1, allow_video_outputs=1)
        assert attempts == 1
        assert fake.video_starts == 0
    finally:
        db.close()


def test_smoke_test_saves_raw_outputs_and_waits_for_review(project_config):
    fake = FakeVisualSDK()
    db, preflight = service(project_config, fake)
    try:
        run_id = preflight.start(allow_image_outputs=1, allow_video_outputs=1)
        row = db.preflight_run(run_id)

        assert row["status"] == "awaiting_review"
        assert Path(row["raw_image_path"]).exists()
        assert Path(row["raw_video_path"]).read_bytes() == b"fake-mp4"
        assert row["video_operation_id"] == "operations/video-1"
        assert fake.image_calls == 1
        assert fake.video_starts == 1
    finally:
        db.close()


def test_resume_polls_existing_operation_without_second_start(project_config):
    fake = FakeVisualSDK()
    db, preflight = service(project_config, fake)
    try:
        run_dir = project_config.root / "workspace" / "manual_preflight"
        run_dir.mkdir(parents=True)
        run_id = db.create_preflight_run(
            preflight.source,
            str(run_dir / "raw.png"),
            str(run_dir / "raw.mp4"),
            1.061,
            {},
        )
        row = db.preflight_run(run_id)
        Path(row["raw_image_path"]).write_bytes(png_bytes())
        
        db.conn.execute(
            "UPDATE visual_preflight_runs SET image_sha256=?, video_operation_id=?, status='polling', owner_token=? WHERE id=?",
            ("existing-image", "operations/video-1", "fake-token", run_id)
        )
        db.conn.commit()

        # Mock acquire_side_effect_lease to return a lease with our fake-token
        # so that finish_preflight_generation's owner_token check passes.
        original_acquire = db.acquire_side_effect_lease
        def fake_acquire(scope_type, scope_id, ttl_seconds=60):
            return original_acquire(scope_type, scope_id, ttl_seconds).__class__(
                scope_type=scope_type, scope_id=scope_id, owner_token="fake-token", expires_at="2099-01-01T00:00:00+00:00", acquired=True
            )
        db.acquire_side_effect_lease = fake_acquire

        preflight.resume(run_id)

        assert fake.video_starts == 0
        assert fake.video_polls == 1
    finally:
        db.close()
