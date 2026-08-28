from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromptPlan:
    prompt: str
    signature: str
    scene: str
    mood: str
    instrumentation: str
    scene_label: str = ""
    mood_label: str = ""


@dataclass(frozen=True)
class Metadata:
    title: str
    description: str
    tags: list[str]
    category_id: str
    privacy_status: str
    publish_at: str | None
    made_for_kids: bool
    contains_synthetic_media: bool
    notify_subscribers: bool
    default_language: str


@dataclass(frozen=True)
class JobContext:
    job_id: int
    job_dir: Path
    metadata: Metadata
    video_row_id: int
    thumbnail: Path
    channel_cfg: dict
