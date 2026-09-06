"""Final Studio metadata and explicitly-confirmed YouTube publishing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import AppConfig
from ..db import StateDB
from ..errors import GenerationError
from ..models import Metadata
from ..providers.youtube import YouTubeClient
from ..utils import json_dump


def metadata_defaults(title: str, duration_minutes: int) -> dict[str, Any]:
    safe_title = title.strip() or "Lakeside Café with a Chow Chow"
    return {
        "title": f"{safe_title} | {duration_minutes / 60:g} Hours of Cozy Night Jazz for Study",
        "description": (
            "Settle into a warm destination café beside a quiet landscape while a beloved Chow "
            "Chow rests nearby. This long-form ambience pairs a locked scenic view with an album "
            "of original instrumental jazz for reading, studying, focused work, and unwinding.\n\n"
            "Music and visuals were created with generative AI tools, then selected, arranged, "
            "mixed, and quality-checked as part of an original production workflow. No vocals or "
            "spoken words.\n\n#CozyJazz #StudyMusic #CafeAmbience #ChowChow #NightCafe"
        ),
        "tags": [
            "cozy jazz", "study music", "cafe ambience", "night cafe", "chow chow",
            "reading music", "focus music", "instrumental jazz", "relaxing ambience",
        ],
        "privacy_status": "private",
    }


def upload_studio_final(
    db: StateDB,
    config: AppConfig,
    episode_id: int,
    *,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str,
    channel_name: str = "main",
    youtube_factory: Callable[..., Any] = YouTubeClient,
) -> dict[str, Any]:
    episode = db.episode(episode_id)
    if episode is None or episode["music_job_id"] is None:
        raise GenerationError("本集沒有可用的音樂工作")
    finals = [
        a for a in db.assets_for_episode(episode_id, kind="final")
        if a["status"] == "approved" and a["path"]
    ]
    keyframes = [
        a for a in db.assets_for_episode(episode_id, kind="keyframe")
        if a["status"] == "approved" and a["path"]
    ]
    if not finals:
        raise GenerationError("請先核准最終影片")
    if not keyframes:
        raise GenerationError("找不到已核准的縮圖來源關鍵幀")

    job_id = int(episode["music_job_id"])
    existing_rows = db.videos_for_job(job_id)
    video_id = int(existing_rows[-1]["id"]) if existing_rows else db.add_video(job_id, title)
    existing = db.video(video_id)
    if existing["youtube_video_id"]:
        youtube_id = existing["youtube_video_id"]
        return {"video_id": video_id, "youtube_video_id": youtube_id, "already_uploaded": True}

    channel = config.channel(channel_name)
    metadata = Metadata(
        title=title[:100],
        description=description[:5000],
        tags=[str(tag)[:30] for tag in tags[:30]],
        category_id=str(channel.get("category_id", "10")),
        privacy_status=privacy_status,
        publish_at=None,
        made_for_kids=False,
        contains_synthetic_media=True,
        notify_subscribers=False,
        default_language="en",
    )
    db.update_video(
        video_id,
        video_path=finals[-1]["path"],
        thumbnail_path=keyframes[-1]["path"],
        status="uploading",
        error=None,
    )
    youtube = youtube_factory(channel, config.root)

    def on_created(youtube_id: str) -> None:
        db.update_video(video_id, youtube_video_id=youtube_id)

    try:
        youtube_id = youtube.upload(
            finals[-1]["path"],
            keyframes[-1]["path"],
            metadata,
            on_video_created=on_created,
        )
    except Exception as exc:
        db.update_video(video_id, status="rendered", error=str(exc)[:800])
        raise
    db.update_video(video_id, youtube_video_id=youtube_id, status="uploaded", error=None)
    metadata_path = (
        config.root
        / config.section("project").get("workspace", "workspace")
        / "studio"
        / episode["slug"]
        / "youtube-metadata.json"
    )
    json_dump(metadata_path, metadata.__dict__)
    return {"video_id": video_id, "youtube_video_id": youtube_id, "already_uploaded": False}
