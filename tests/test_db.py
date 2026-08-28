from __future__ import annotations

from conftest import write_sine_audio

from lyria_auto.db import StateDB


def test_resumable_job_finds_latest_incomplete(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        j1 = db.create_job("main", False, {})
        db.update_job(j1, "complete")
        j2 = db.create_job("main", False, {})
        db.update_job(j2, "failed", "boom")

        found = db.resumable_job()

        assert found is not None
        assert found["id"] == j2
    finally:
        db.close()


def test_resumable_job_excludes_dry_run(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        j1 = db.create_job("main", True, {})
        db.update_job(j1, "dry_run_complete")

        assert db.resumable_job() is None
    finally:
        db.close()


def test_resumable_job_returns_none_when_nothing_pending(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        assert db.resumable_job() is None
    finally:
        db.close()


def test_resumable_job_by_explicit_id(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        j1 = db.create_job("main", False, {})

        found = db.resumable_job(j1)

        assert found is not None
        assert found["id"] == j1
    finally:
        db.close()


def test_tracks_for_job_orders_by_id(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        db.add_track(job_id, "prompt 1", "sig1")
        db.add_track(job_id, "prompt 2", "sig2")

        rows = db.tracks_for_job(job_id)

        assert [r["prompt"] for r in rows] == ["prompt 1", "prompt 2"]
    finally:
        db.close()


def test_track_is_reusable_false_when_not_ready(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        db.add_track(job_id, "prompt", "sig")
        row = db.tracks_for_job(job_id)[0]

        assert db._track_is_reusable(row) is False
    finally:
        db.close()


def test_track_is_reusable_false_when_file_missing(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        track_id = db.add_track(job_id, "prompt", "sig")
        db.update_track(track_id, status="ready", audio_path=str(tmp_path / "missing.m4a"))
        row = db.tracks_for_job(job_id)[0]

        assert db._track_is_reusable(row) is False
    finally:
        db.close()


def test_track_is_reusable_false_when_file_corrupted(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        track_id = db.add_track(job_id, "prompt", "sig")
        broken = tmp_path / "broken.m4a"
        broken.write_bytes(b"")
        db.update_track(track_id, status="ready", audio_path=str(broken))
        row = db.tracks_for_job(job_id)[0]

        assert db._track_is_reusable(row) is False
    finally:
        db.close()


def test_track_is_reusable_true_for_valid_audio(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    try:
        job_id = db.create_job("main", False, {})
        track_id = db.add_track(job_id, "prompt", "sig")
        audio = tmp_path / "ok.mp3"
        write_sine_audio(audio, duration=1.0)
        db.update_track(track_id, status="ready", audio_path=str(audio))
        row = db.tracks_for_job(job_id)[0]

        assert db._track_is_reusable(row) is True
    finally:
        db.close()
