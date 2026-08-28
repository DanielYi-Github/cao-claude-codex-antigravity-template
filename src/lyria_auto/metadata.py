from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .models import Metadata, PromptPlan


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def build_metadata(
    settings: dict[str, Any],
    channel: dict[str, Any],
    plan: PromptPlan,
    duration_minutes: int,
    publish_offset_index: int = 0,
    episode: int = 1,
) -> Metadata:
    md = settings["metadata"]
    scene = plan.scene_label or plan.scene
    mood = plan.mood_label or plan.mood
    title = md["title_template"].format(scene=scene, episode=episode)
    description = md["description_template"].format(
        scene=scene,
        instrumentation=plan.instrumentation,
        mood=mood,
        duration_minutes=duration_minutes,
    )
    publish = channel.get("publish", {})
    publish_at = None
    privacy = channel.get("privacy_status", "private")
    if publish.get("mode") == "scheduled":
        privacy = "private"
        delay = int(publish.get("delay_hours", 24))
        spacing = int(publish.get("spacing_hours", 24))
        dt = datetime.now(UTC) + timedelta(hours=delay + publish_offset_index * spacing)
        publish_at = dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return Metadata(
        title=_truncate(title, 100),
        description=_truncate(description, 5000),
        tags=[_truncate(str(t), 30) for t in md.get("tags", [])][:30],
        category_id=str(channel.get("category_id", "10")),
        privacy_status=privacy,
        publish_at=publish_at,
        made_for_kids=bool(channel.get("made_for_kids", False)),
        contains_synthetic_media=bool(channel.get("contains_synthetic_media", True)),
        notify_subscribers=bool(channel.get("notify_subscribers", False)),
        default_language=str(channel.get("default_language", md.get("language", "zh-TW"))),
    )
