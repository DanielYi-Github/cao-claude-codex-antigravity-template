from __future__ import annotations

import inspect
import json
from pathlib import Path

from lyria_auto.models import JobContext, Metadata
from lyria_auto.pipeline import Pipeline
from lyria_auto.providers.youtube import YouTubeClient


class _FakeYouTubeClient:
    """Records calls so tests can assert whether upload() was actually invoked, and
    exercises the on_video_created callback the same way the real client does."""

    calls: list[str]

    def __init__(self, channel_cfg, root):
        self.channel_cfg = channel_cfg

    def channel_identity(self):
        return {"id": "UCFAKE", "title": "fake channel"}

    def upload(self, video_path, thumbnail_path, metadata, on_video_created=None):
        _FakeYouTubeClient.calls.append("upload")
        if on_video_created is not None:
            on_video_created("FAKE_VIDEO_ID")
        return "FAKE_VIDEO_ID"


def test_youtube_client_upload_accepts_on_video_created_callback():
    # _FakeYouTubeClient above re-implements the on_video_created contract rather than
    # verifying the real one, so pin the real YouTubeClient.upload signature here to catch
    # drift if the callback parameter is ever renamed or dropped.
    params = inspect.signature(YouTubeClient.upload).parameters
    assert "on_video_created" in params
    assert params["on_video_created"].default is None


def test_upload_persists_video_id_via_callback(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    monkeypatch.setattr("lyria_auto.pipeline.YouTubeClient", _FakeYouTubeClient)
    _FakeYouTubeClient.calls = []

    pipeline = Pipeline(project_config)
    try:
        result = pipeline.run_one("main", upload=True, dry_run=False)
        assert result["youtube_video_id"] == "FAKE_VIDEO_ID"

        video_row = pipeline.db.conn.execute(
            "SELECT youtube_video_id, status FROM videos WHERE job_id=?", (result["job_id"],)
        ).fetchone()
        assert video_row["youtube_video_id"] == "FAKE_VIDEO_ID"
        assert video_row["status"] == "uploaded"
    finally:
        pipeline.close()


def test_upload_skips_when_video_already_has_youtube_id(project_config, fake_lyria, monkeypatch):
    # Simulates the scenario Fix 1 targets: a video row that already has a youtube_video_id
    # (e.g. set by the on_video_created callback before a later step failed) must not be
    # re-uploaded, since that would publish a duplicate video.
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    monkeypatch.setattr("lyria_auto.pipeline.YouTubeClient", _FakeYouTubeClient)
    _FakeYouTubeClient.calls = []

    pipeline = Pipeline(project_config)
    try:
        result = pipeline.run_one("main", upload=True, dry_run=False)
        assert _FakeYouTubeClient.calls == ["upload"]

        job_id = result["job_id"]
        job_dir = Path(result["directory"])
        video_row = pipeline.db.conn.execute(
            "SELECT id FROM videos WHERE job_id=?", (job_id,)
        ).fetchone()
        metadata_payload = json.loads((job_dir / "metadata.json").read_text(encoding="utf-8"))
        ctx = JobContext(
            job_id=job_id,
            job_dir=job_dir,
            metadata=Metadata(**metadata_payload),
            video_row_id=int(video_row["id"]),
            thumbnail=job_dir / "thumbnail.jpg",
            channel_cfg={},
        )

        second_id = pipeline._upload(ctx, Path(result["video"]))

        assert second_id == "FAKE_VIDEO_ID"
        # No second call reached YouTubeClient.upload -- the existing DB id short-circuited it.
        assert _FakeYouTubeClient.calls == ["upload"]
    finally:
        pipeline.close()
