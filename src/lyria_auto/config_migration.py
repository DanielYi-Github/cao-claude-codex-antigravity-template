from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

VISUAL_SECTION: dict[str, Any] = {
    "enabled": False,
    "provider": "gemini-developer-api",
    "image_model": "gemini-3.1-flash-image",
    "video_model": "veo-3.1-fast-generate-preview",
    "image_size": "2K",
    "aspect_ratio": "16:9",
    "video_resolution": "1080p",
    "video_duration_seconds": 8,
    "video_fps": 24,
    "max_unique_scenes": 4,
    "scene_interval_minutes": 30,
    "image_candidates_per_scene": 3,
    "video_candidates_per_scene": 2,
    "max_world_anchor_images": 1,
    "max_thumbnail_backgrounds": 1,
    "max_image_outputs": 14,
    "max_video_outputs": 8,
    "estimated_budget_usd": 10.00,
    "image_2k_estimated_usd": 0.101,
    "video_1080p_second_estimated_usd": 0.12,
    "pricing_snapshot_date": "2026-07-29",
    "poll_interval_seconds": 10,
    "quality": {
        "seam_max_normalized_mae": 0.06,
        "motion_min_normalized_mae": 0.002,
        "motion_max_normalized_mae": 0.10,
        "duration_tolerance_seconds": 0.25,
    },
    "preflight": {
        "max_image_outputs": 1,
        "max_video_outputs": 1,
        "valid_days": 30,
    },
}


@dataclass
class MigrationPreview:
    missing_sections: tuple[str, ...]
    would_create_backup: bool


SUPPORTED_PYTHON = ["python3.13", "python3.12", "python3.11"]


def choose_python(path_dir: str | Path) -> str:
    d = Path(path_dir)
    for name in SUPPORTED_PYTHON:
        if (d / name).exists():
            return name
    raise RuntimeError(
        "找不到 python3.11～3.13。執行: brew install python@3.11"
    )


def preview_settings_migration(settings_path: str | Path) -> MigrationPreview:
    path = Path(settings_path)
    settings = yaml.safe_load(path.read_text(encoding="utf-8"))
    missing = [s for s in ("visual",) if s not in settings]
    return MigrationPreview(
        missing_sections=tuple(missing),
        would_create_backup=len(missing) > 0,
    )


def apply_settings_migration(settings_path: str | Path) -> None:
    path = Path(settings_path)
    settings = yaml.safe_load(path.read_text(encoding="utf-8"))

    if "visual" in settings:
        existing = settings["visual"]
        for k, v in VISUAL_SECTION.items():
            if k not in existing:
                existing[k] = v
        path.write_text(
            yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8"
        )
        return

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(f".yaml.{ts}.bak")
    shutil.copy2(str(path), str(backup))

    settings["visual"] = dict(VISUAL_SECTION)
    path.write_text(
        yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8"
    )


def enable_visual(settings_path: str | Path) -> None:
    path = Path(settings_path)
    settings = yaml.safe_load(path.read_text(encoding="utf-8"))
    settings["visual"]["enabled"] = True
    path.write_text(
        yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8"
    )


def fingerprint_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def check_sdk_capabilities() -> dict[str, Any]:
    from google import genai

    client = genai.Client(api_key="capability-check-placeholder")
    has_interactions = hasattr(client, "interactions") and hasattr(client.interactions, "create")
    has_generate_videos = hasattr(client, "models") and hasattr(client.models, "generate_videos")
    has_operations = hasattr(client, "operations") and hasattr(client.operations, "get")

    return {
        "sdk_version": getattr(genai, "__version__", "unknown"),
        "api_mode": "gemini-developer-api",
        "interactions.create": has_interactions,
        "models.generate_videos": has_generate_videos,
        "operations.get": has_operations,
    }
