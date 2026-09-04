from __future__ import annotations

from pathlib import Path

from lyria_auto.config import AppConfig
from lyria_auto.db import StateDB
from lyria_auto.studio import music


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        settings={
            "project": {"workspace": "workspace"},
            "generation": {
                "model": "fake-lyria",
                "max_generation_attempts": 1,
                "request_delay_seconds": 0,
            },
            "quality": {"normalize_audio": True},
        },
        prompts={"blocked_reference_terms": ["in the style of"]},
        channels={},
        root=tmp_path,
    )


class _FakeLyria:
    def __init__(self, calls, **kwargs):
        self.calls = calls
        self.calls.append(("init", kwargs))

    def generate(self, prompt, output_path):
        self.calls.append(("generate", prompt))
        Path(output_path).write_bytes(prompt.encode("utf-8") + b"x" * 2048)


def test_generate_pending_tracks_publishes_twelve_ordered_review_assets(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("e", "E")
    calls = []

    monkeypatch.setattr(
        music,
        "_validated_audio",
        lambda path, quality: {"duration": 173.0, "sample_rate": 48000, "channels": 2},
    )

    def fake_normalize(src, dest, lufs, true_peak):
        Path(dest).write_bytes(Path(src).read_bytes())
        return Path(dest)

    monkeypatch.setattr(music, "normalize_audio", fake_normalize)

    result = music.generate_pending_tracks(
        db,
        _config(tmp_path),
        episode_id,
        base_prompt=music.DEFAULT_MUSIC_PROMPT,
        api_key="request-only-secret",
        client_factory=lambda **kwargs: _FakeLyria(calls, **kwargs),
    )

    assert len(result["generated"]) == 12
    assert result["failed"] == []
    assets = db.assets_for_episode(episode_id, kind="music_track")
    assert [a["variant_index"] for a in assets] == list(range(12))
    assert all(a["status"] == "awaiting_review" for a in assets)
    assert all(Path(a["path"]).is_file() for a in assets)
    assert all(t["status"] == "ready" for t in db.tracks_for_job(result["job_id"]))
    assert db.job(result["job_id"])["status"] == "complete"
    assert len([call for call in calls if call[0] == "generate"]) == 12
    assert b"request-only-secret" not in (tmp_path / "state.sqlite3").read_bytes()


def test_generate_pending_tracks_recovers_complete_file_without_second_paid_call(
    tmp_path, monkeypatch
):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("e", "E")
    job_id = db.reserve_studio_music_job(episode_id, ["one original track"])
    asset = db.assets_for_episode(episode_id, kind="music_track")[0]
    track = db.track(asset["track_id"])
    music_dir = tmp_path / "workspace" / "studio" / "e" / "music"
    music_dir.mkdir(parents=True)
    recovered = music_dir / f"track-01-{track['id']}.m4a"
    recovered.write_bytes(b"already-finished" * 100)
    calls = []
    monkeypatch.setattr(
        music,
        "_validated_audio",
        lambda path, quality: {"duration": 170.0, "sample_rate": 48000, "channels": 2},
    )

    result = music.generate_pending_tracks(
        db,
        _config(tmp_path),
        episode_id,
        base_prompt=music.DEFAULT_MUSIC_PROMPT,
        api_key="secret",
        client_factory=lambda **kwargs: _FakeLyria(calls, **kwargs),
    )

    assert result["job_id"] == job_id
    assert result["generated"] == [asset["id"]]
    assert not [call for call in calls if call[0] == "generate"]
    assert db.asset(asset["id"])["path"] == str(recovered)


def test_music_generation_redacts_reflected_key_from_persisted_failure(tmp_path, monkeypatch):
    db = StateDB(tmp_path / "state.sqlite3")
    episode_id = db.create_episode("e", "E")
    monkeypatch.setattr(music, "_validated_audio", lambda path, quality: (_ for _ in ()).throw(ValueError("bad")))

    class Failing:
        def __init__(self, **kwargs):
            pass

        def generate(self, prompt, output_path):
            raise RuntimeError("server echoed secret-token")

    result = music.generate_pending_tracks(
        db,
        _config(tmp_path),
        episode_id,
        base_prompt=music.DEFAULT_MUSIC_PROMPT,
        api_key="secret-token",
        client_factory=Failing,
    )

    assert len(result["failed"]) == 12
    assert all("secret-token" not in item["error"] for item in result["failed"])
    assert b"secret-token" not in (tmp_path / "state.sqlite3").read_bytes()
