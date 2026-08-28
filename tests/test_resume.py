from __future__ import annotations

import json
from pathlib import Path

import pytest

from lyria_auto.errors import GenerationError, LyriaAutoError
from lyria_auto.pipeline import Pipeline


def test_single_track_failure_is_retried_and_succeeds(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 1

    pipeline = Pipeline(project_config)
    try:
        result = pipeline.run_one("main", upload=False, dry_run=False)

        assert Path(result["video"]).exists()
        rows = pipeline.db.tracks_for_job(result["job_id"])
        assert all(r["status"] == "ready" for r in rows)
        assert fake_lyria.calls.count("track_02_raw.mp3") == 2
    finally:
        pipeline.close()


def test_permanent_track_failure_aborts_job_without_producing_video(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 99

    pipeline = Pipeline(project_config)
    try:
        try:
            pipeline.run_one("main", upload=False, dry_run=False)
            assert False, "應該要拋出 GenerationError"
        except GenerationError:
            pass

        assert fake_lyria.calls.count("track_02_raw.mp3") == 2

        jobs = pipeline.db.recent_jobs(1)
        assert jobs[0]["status"] == "failed"
        job_id = jobs[0]["id"]
        job_dir = pipeline.workspace / f"job_{job_id:06d}"
        assert list(job_dir.glob("*.mp4")) == []
    finally:
        pipeline.close()


def test_resume_only_regenerates_remaining_tracks(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 99

    pipeline = Pipeline(project_config)
    try:
        with pytest.raises(GenerationError):
            pipeline.run_one("main", upload=False, dry_run=False)

        first_run_calls = list(fake_lyria.calls)
        assert first_run_calls.count("track_01_raw.mp3") == 1
        assert first_run_calls.count("track_03_raw.mp3") == 1
        assert first_run_calls.count("track_02_raw.mp3") == 2  # 主流程失敗一次 + 批次重試失敗一次

        fake_lyria.calls.clear()
        fake_lyria.fail_targets["track_02_raw.mp3"] = 0  # 這次讓它成功

        result = pipeline.resume_one()

        # 核心驗收：只呼叫剩餘那一軌一次，不是總軌數
        assert fake_lyria.calls == ["track_02_raw.mp3"]
        assert Path(result["video"]).exists()
    finally:
        pipeline.close()


def test_resume_regenerates_truncated_file(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 99

    pipeline = Pipeline(project_config)
    try:
        with pytest.raises(GenerationError):
            pipeline.run_one("main", upload=False, dry_run=False)

        jobs = pipeline.db.recent_jobs(1)
        job_id = jobs[0]["id"]
        rows = pipeline.db.tracks_for_job(job_id)
        id_to_idx = {int(r["id"]): i for i, r in enumerate(rows, start=1)}
        ready_row = next(r for r in rows if r["status"] == "ready")
        Path(ready_row["audio_path"]).write_bytes(b"")  # 模擬寫到一半被中斷

        fake_lyria.calls.clear()
        fake_lyria.fail_targets["track_02_raw.mp3"] = 0

        pipeline.resume_one(job_id)

        expected_raw_name = f"track_{id_to_idx[int(ready_row['id'])]:02d}_raw.mp3"
        assert expected_raw_name in fake_lyria.calls
    finally:
        pipeline.close()


def test_resume_preserves_original_metadata(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    fake_lyria.fail_targets["track_02_raw.mp3"] = 99

    pipeline = Pipeline(project_config)
    try:
        with pytest.raises(GenerationError):
            pipeline.run_one("main", upload=False, dry_run=False)

        jobs = pipeline.db.recent_jobs(1)
        job_id = jobs[0]["id"]
        job_dir = pipeline.workspace / f"job_{job_id:06d}"
        plan_before = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))

        fake_lyria.calls.clear()
        fake_lyria.fail_targets["track_02_raw.mp3"] = 0

        pipeline.resume_one(job_id)

        # Verify metadata.json was written correctly during resume with reconstructed metadata
        metadata_after = json.loads((job_dir / "metadata.json").read_text(encoding="utf-8"))
        assert metadata_after == plan_before["metadata"]
    finally:
        pipeline.close()


def test_resume_one_raises_when_nothing_to_resume(project_config):
    pipeline = Pipeline(project_config)
    try:
        with pytest.raises(LyriaAutoError):
            pipeline.resume_one()
    finally:
        pipeline.close()


def test_resume_rejects_explicit_job_id_that_already_completed(project_config, fake_lyria, monkeypatch):
    # resumable_job(job_id) 的明確 id 分支不過濾 status，所以 resume --job 指向一個已經
    # complete 的 job 時，必須在 resume_one 裡被明確擋下，而不是把它當成「未完成」重新
    # 生成/渲染一次（並且用 --upload 的話還會重複發佈）。
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())

    pipeline = Pipeline(project_config)
    try:
        result = pipeline.run_one("main", upload=False, dry_run=False)
        job_id = result["job_id"]
        jobs = pipeline.db.recent_jobs(1)
        assert jobs[0]["status"] == "complete"

        fake_lyria.calls.clear()
        with pytest.raises(LyriaAutoError):
            pipeline.resume_one(job_id)

        # 沒有任何一軌被重新生成——job 完全沒有被重跑。
        assert fake_lyria.calls == []
    finally:
        pipeline.close()
