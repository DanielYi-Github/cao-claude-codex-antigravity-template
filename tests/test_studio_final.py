from __future__ import annotations

from lyria_auto.config import AppConfig
from lyria_auto.db import StateDB
from lyria_auto.studio.final import upload_studio_final


def _approve_file_asset(db, episode_id, kind, path):
    asset_id = db.create_asset(episode_id, kind, "shared")
    db.transition_asset(
        asset_id,
        expected_status="queued",
        expected_version=0,
        status="approved",
        path=str(path),
    )
    return asset_id


def test_upload_studio_final_persists_video_id_and_is_idempotent(tmp_path):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("e", "E")
    job_id = db.reserve_studio_music_job(episode_id, ["prompt"])
    final_path = tmp_path / "final.mp4"
    thumb_path = tmp_path / "thumb.png"
    final_path.write_bytes(b"video")
    thumb_path.write_bytes(b"png")
    _approve_file_asset(db, episode_id, "final", final_path)
    _approve_file_asset(db, episode_id, "keyframe", thumb_path)
    config = AppConfig(
        settings={"project": {"workspace": "workspace"}},
        prompts={},
        channels={"channels": {"main": {"category_id": "10"}}},
        root=tmp_path,
    )
    calls = []

    class FakeYouTube:
        def __init__(self, channel, root):
            calls.append(("init", channel, root))

        def upload(self, video, thumbnail, metadata, on_video_created):
            calls.append(("upload", video, thumbnail, metadata))
            on_video_created("youtube-abc")
            return "youtube-abc"

    kwargs = {
        "title": "Cozy Jazz",
        "description": "An original AI-assisted ambience production.",
        "tags": ["cozy jazz"],
        "privacy_status": "private",
        "youtube_factory": FakeYouTube,
    }
    first = upload_studio_final(db, config, episode_id, **kwargs)
    second = upload_studio_final(db, config, episode_id, **kwargs)

    assert first["youtube_video_id"] == "youtube-abc"
    assert first["already_uploaded"] is False
    assert second["already_uploaded"] is True
    assert len([call for call in calls if call[0] == "upload"]) == 1
    video = db.videos_for_job(job_id)[0]
    assert video["status"] == "uploaded"
    assert video["video_path"] == str(final_path)
    metadata_file = tmp_path / "workspace" / "studio" / "e" / "youtube-metadata.json"
    assert metadata_file.is_file()
    assert "contains_synthetic_media" in metadata_file.read_text(encoding="utf-8")
