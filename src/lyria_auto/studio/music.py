"""Request-scoped Studio music generation.

The API key is passed directly to an ephemeral LyriaClient and is never put
in SQLite, a task payload, a path, or an error message.  Generation is
synchronous on purpose: retaining a secret for a later background worker
would violate that lifetime guarantee.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..db import StateDB
from ..errors import GenerationError
from ..media.audio import normalize_audio, validate_audio
from ..providers.lyria import LyriaClient
from ..safety import PromptSafety
from ..utils import ensure_dir, sha256_file

TRACK_COUNT = 12

DEFAULT_MUSIC_PROMPT = (
    "A cohesive album of original instrumental late-night destination-cafe jazz for long study "
    "sessions. Warm Rhodes and upright piano, upright bass, delicate brushed drums, occasional "
    "vibraphone or mellow guitar, intimate natural room ambience, restrained dynamics, soft "
    "analog warmth, peaceful and sophisticated. No vocals, no spoken words, no recognizable "
    "melody, and no reference to any artist or existing song."
)

_VARIATIONS = (
    "slow opening, warm Rhodes and sparse brushed drums",
    "upright piano lead with gentle bass movement",
    "subtle vibraphone accents and spacious pauses",
    "mellow nylon-string guitar answering soft piano",
    "rainy-window atmosphere with restrained piano voicings",
    "deeper midnight mood and quiet walking bass",
    "minimal trio arrangement with airy room tone",
    "slightly brighter lounge harmony without raising energy",
    "soft electric piano and delicate cymbal brushes",
    "intimate acoustic piano with long comfortable rests",
    "calm pre-dawn mood with subtle harmonic resolution",
    "gentle closing piece that settles without a dramatic ending",
)


def build_album_prompts(base_prompt: str) -> list[str]:
    base = " ".join(base_prompt.split()).strip()
    if not base:
        raise GenerationError("音樂提示詞不可空白")
    if len(base) > 3500:
        raise GenerationError("音樂提示詞過長（上限 3500 字元）")
    return [
        f"{base} Track {index + 1} of {TRACK_COUNT}: {variation}. Keep the same album identity "
        "while composing a distinct original piece. Instrumental only."
        for index, variation in enumerate(_VARIATIONS)
    ]


def _safe_error(exc: Exception, api_key: str | None) -> str:
    message = str(exc)
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    return message[:800]


def _audio_quality(config: AppConfig) -> dict[str, Any]:
    return config.section("quality")


def _validated_audio(path: Path, quality: dict[str, Any]) -> dict[str, Any]:
    return validate_audio(
        path,
        float(quality.get("minimum_duration_seconds", 20)),
        int(quality.get("minimum_sample_rate", 44100)),
        bool(quality.get("require_stereo", True)),
    )


def generate_pending_tracks(
    db: StateDB,
    config: AppConfig,
    episode_id: int,
    *,
    base_prompt: str,
    api_key: str | None,
    client_factory: Callable[..., Any] = LyriaClient,
) -> dict[str, Any]:
    """Generate every queued/failed active slot, publishing each one atomically."""
    safety = PromptSafety(config.prompts.get("blocked_reference_terms", []))
    safety.require_safe(base_prompt)
    prompts = build_album_prompts(base_prompt)
    job_id = db.reserve_studio_music_job(episode_id, prompts)
    episode = db.episode(episode_id)
    if episode is None:
        raise GenerationError(f"找不到 episode_id={episode_id}")

    all_assets = db.assets_for_episode(episode_id, kind="music_track")
    active_by_slot: dict[int, Any] = {}
    for asset in all_assets:
        if asset["status"] != "rejected":
            active_by_slot[int(asset["variant_index"])] = asset
    pending = [
        asset for _, asset in sorted(active_by_slot.items())
        if asset["status"] in ("queued", "failed")
    ]
    if not pending:
        return {"job_id": job_id, "generated": [], "failed": [], "already_complete": True}

    generation = config.section("generation")
    quality = _audio_quality(config)
    client = client_factory(
        model=generation.get("model", "lyria-3-pro-preview"),
        max_attempts=int(generation.get("max_generation_attempts", 3)),
        request_delay_seconds=float(generation.get("request_delay_seconds", 3)),
        api_key=api_key,
    )
    music_dir = ensure_dir(
        config.root
        / config.section("project").get("workspace", "workspace")
        / "studio"
        / episode["slug"]
        / "music"
    )
    generated: list[int] = []
    failed: list[dict[str, Any]] = []

    for stale_asset in pending:
        asset = db.asset(stale_asset["id"])
        if asset is None or asset["status"] not in ("queued", "failed"):
            continue
        if not db.transition_asset(
            asset["id"],
            expected_status=asset["status"],
            expected_version=asset["state_version"],
            status="running",
        ):
            continue
        track = db.track(asset["track_id"])
        slot = int(asset["variant_index"])
        raw = music_dir / f"track-{slot + 1:02d}-{track['id']}.raw.partial.mp3"
        normalized = music_dir / f"track-{slot + 1:02d}-{track['id']}.m4a"
        try:
            # Crash recovery: if a complete output reached disk before the
            # process stopped, validate and publish it rather than paying for
            # the same generation again.
            try:
                final_info = _validated_audio(normalized, quality) if normalized.is_file() else None
            except Exception:  # noqa: BLE001 - invalid recovery candidates are regenerated
                final_info = None
            if final_info is None:
                try:
                    raw_info = _validated_audio(raw, quality) if raw.is_file() else None
                except Exception:  # noqa: BLE001 - invalid partial files are regenerated
                    raw_info = None
                if raw_info is None:
                    client.generate(track["prompt"], raw)
                    raw_info = _validated_audio(raw, quality)
                if quality.get("normalize_audio", True):
                    partial_normalized = normalized.with_name(
                        f"{normalized.stem}.partial{normalized.suffix}"
                    )
                    normalize_audio(
                        raw,
                        partial_normalized,
                        float(quality.get("loudness_target_lufs", -16)),
                        float(quality.get("true_peak_db", -1.5)),
                    )
                    partial_normalized.replace(normalized)
                    final_path = normalized
                    final_info = _validated_audio(final_path, quality)
                else:
                    final_path = raw
                    final_info = raw_info
            else:
                final_path = normalized

            db.publish_studio_music_track(
                episode_id,
                asset["id"],
                path=str(final_path),
                duration_seconds=float(final_info["duration"]),
                sha256=sha256_file(final_path),
            )
            generated.append(asset["id"])
        except Exception as exc:  # noqa: BLE001 - isolate each paid track and persist safe failure
            message = _safe_error(exc, api_key)
            db.fail_studio_music_track(episode_id, asset["id"], message)
            failed.append({"asset_id": asset["id"], "error": message})

    if failed:
        db.update_job(job_id, "failed", f"{len(failed)} music track(s) failed")
    else:
        db.update_job(job_id, "complete")
    return {
        "job_id": job_id,
        "generated": generated,
        "failed": failed,
        "already_complete": False,
    }
