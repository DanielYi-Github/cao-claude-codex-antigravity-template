"""Capability table, validation, and QC for the Google Veo motion path.

Everything here is deliberately pure/offline except probe_available_models:
the point is that an illegal model/resolution combination is rejected
*before* any paid request is sent, so a typo in the UI costs nothing.

Why a config-driven capability table instead of asking the API: the
Gemini models endpoint reports which models a key can reach, but not what
resolutions or clip durations each one accepts, and not what any of it
costs. Those live in config/settings.yaml's studio.veo.models and are
maintained by a human; probe_available_models() only crosses off entries
the key demonstrably cannot reach (artifacts/spec.md 6).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..utils import run_command

# What each Veo resolution actually comes back as at 16:9. Used to
# pre-fill an asset's width/height before the file exists, and to catch a
# clip whose real dimensions disagree with what was ordered.
VEO_DIMENSIONS = {"720p": (1280, 720), "1080p": (1920, 1080)}

# The 64s macro-loop plays sleep 7x + lookup 1x (stages.py's LOOP_SEQUENCE),
# so every segment must be exactly this long or the loop arithmetic breaks.
# Veo 3.x offers a restricted duration set; a model that cannot hit this
# number is not usable for this pipeline at all.
REQUIRED_DURATION_SECONDS = 8


class VeoConfigError(ValueError):
    """A model/resolution/duration combination the catalog forbids."""


def veo_section(config: AppConfig) -> dict[str, Any]:
    return dict(config.section("studio").get("veo") or {})


def catalog(config: AppConfig) -> list[dict[str, Any]]:
    """The configured models, filtered to those usable by this pipeline.

    A model that cannot produce a REQUIRED_DURATION_SECONDS clip is
    dropped here rather than shown-and-then-rejected: offering it in the
    dropdown would only let someone pick an option that can never work.
    """
    entries = veo_section(config).get("models") or []
    usable = []
    for entry in entries:
        durations = entry.get("durations") or []
        if REQUIRED_DURATION_SECONDS in durations:
            usable.append(dict(entry))
    return usable


def resolve_model(config: AppConfig, model_id: str) -> dict[str, Any]:
    for entry in catalog(config):
        if entry["id"] == model_id:
            return entry
    known = [e["id"] for e in catalog(config)]
    raise VeoConfigError(
        f"未知的 Veo 模型 {model_id!r}；設定檔允許的是 {known}"
    )


def validate_choice(
    config: AppConfig, model_id: str, resolution: str
) -> dict[str, Any]:
    """Check a UI selection against the catalog before spending anything.

    Returns the resolved catalog entry so callers don't look it up twice.
    """
    entry = resolve_model(config, model_id)
    if resolution not in entry.get("resolutions", []):
        raise VeoConfigError(
            f"模型 {model_id} 不支援 {resolution}；"
            f"可用的是 {entry.get('resolutions', [])}"
        )
    if resolution not in VEO_DIMENSIONS:
        raise VeoConfigError(
            f"不認得的解析度 {resolution!r}；只支援 {sorted(VEO_DIMENSIONS)}"
        )
    return entry


def estimate_cost_usd(entry: dict[str, Any], *, clips: int) -> float:
    """Estimated spend for `clips` clips at REQUIRED_DURATION_SECONDS each.

    The per-second figure is an unverified snapshot from settings.yaml
    (studio.veo.pricing_verified says so out loud) -- it exists to make
    the relative cost of the model choices visible in the UI, not to
    predict a bill.
    """
    per_second = float(entry.get("usd_per_second") or 0.0)
    return per_second * REQUIRED_DURATION_SECONDS * clips


def probe_available_models(sdk_client: Any) -> set[str]:
    """Model ids this API key can actually reach, or an empty set.

    Returns empty (rather than raising) when the key is missing or the
    call fails: the catalog is still perfectly usable offline, and a
    network hiccup at startup must not take the dropdown down with it.
    Callers treat "empty" as "unknown", not as "nothing is available".
    """
    if sdk_client is None:
        return set()
    try:
        names = set()
        for model in sdk_client.models.list():
            name = getattr(model, "name", None) or ""
            # The API returns fully-qualified names ("models/veo-3.1-...");
            # the catalog stores bare ids.
            names.add(name.split("/")[-1])
        return names
    except Exception:  # noqa: BLE001 - any failure here means "unknown", never fatal
        return set()


def catalog_for_ui(config: AppConfig, reachable: set[str]) -> list[dict[str, Any]]:
    """The dropdown payload: catalog entries plus an availability verdict.

    `reachable` being empty means the probe could not run, which is
    reported as available=True with a null reason -- greying out every
    option because the key was not loaded yet would be actively
    misleading.
    """
    items = []
    for entry in catalog(config):
        available = True
        reason = None
        if reachable and entry["id"] not in reachable:
            available = False
            reason = "這把金鑰目前取用不到這個模型"
        items.append({
            "id": entry["id"],
            "label": entry.get("label", entry["id"]),
            "resolutions": entry.get("resolutions", []),
            "supports_last_frame": bool(entry.get("supports_last_frame")),
            "usd_per_second": entry.get("usd_per_second"),
            "estimated_usd_per_pair": round(estimate_cost_usd(entry, clips=2), 4),
            "available": available,
            "unavailable_reason": reason,
        })
    return items


def _extract_frame(video: Path, dest: Path, *, at_end: bool) -> None:
    """Write one frame of `video` to `dest` as PNG.

    sseof (seek from end) rather than a computed timestamp so this does
    not need the clip's duration and cannot land past the last frame on a
    clip that is a few milliseconds short.
    """
    seek = ["-sseof", "-0.1"] if at_end else ["-ss", "0"]
    run_command(
        ["ffmpeg", "-y", *seek, "-i", str(video), "-frames:v", "1", str(dest)]
    )


def seam_score(video: Path, work_dir: Path) -> float | None:
    """Normalized mean absolute difference between first and last frame.

    0.0 means the clip ends exactly where it started -- a perfect loop.
    This is the only thing that actually checks whether `last_frame` did
    anything: if a chosen model silently ignores it, the clip will not
    loop and the 64s macro-loop premise collapses, and nothing else
    downstream would notice (artifacts/spec.md 10).

    Returns None if the check itself could not run (missing ffmpeg, an
    unreadable frame). A failed measurement must not fail a generation
    the user has already paid for -- it is shown as "unknown" instead.
    """
    from PIL import Image, ImageChops, ImageStat

    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        first = work_dir / f"{video.stem}.seam-first.png"
        last = work_dir / f"{video.stem}.seam-last.png"
        _extract_frame(video, first, at_end=False)
        _extract_frame(video, last, at_end=True)
        with Image.open(first) as a, Image.open(last) as b:
            a_rgb, b_rgb = a.convert("RGB"), b.convert("RGB")
            if a_rgb.size != b_rgb.size:
                return None
            diff = ImageChops.difference(a_rgb, b_rgb)
            mean = sum(ImageStat.Stat(diff).mean) / len(ImageStat.Stat(diff).mean)
        return round(mean / 255.0, 5)
    except Exception:  # noqa: BLE001 - a failed measurement must not fail a paid generation
        return None
    finally:
        for leftover in (
            work_dir / f"{video.stem}.seam-first.png",
            work_dir / f"{video.stem}.seam-last.png",
        ):
            leftover.unlink(missing_ok=True)


def payload_for_start(
    *, role: str, model_id: str, resolution: str, estimated_usd: float
) -> dict[str, Any]:
    """The studio_tasks.payload_json body for a Veo start task.

    Deliberately hand-built from scalars rather than dumping a config
    slice: payload_json is written to disk, and a config blob would carry
    an api_key straight into the database (artifacts/spec.md 7).
    """
    return {
        "role": role,
        "provider": "google-veo",
        "model": model_id,
        "resolution": resolution,
        "estimated_cost_usd": estimated_usd,
    }


def load_payload(task: Any) -> dict[str, Any]:
    return json.loads(task["payload_json"]) if task["payload_json"] else {}


class VeoCredential:
    """Holds the Gemini API key for the lifetime of the process only.

    This repository has had one real API-key leak, so the rule is hard:
    the usable key lives in memory and nowhere else -- not in
    studio_tasks.payload_json, not in episode_assets, not in a log line,
    not in artifacts/ (artifacts/spec.md 7). Only `masked` is ever sent
    to a client or written anywhere.

    __repr__/__str__ are overridden because the most likely way a secret
    escapes is not a deliberate write but an exception traceback or a
    debug print of some object that happens to hold one.
    """

    __slots__ = ("_key",)

    def __init__(self, key: str | None = None) -> None:
        self._key = key or None

    def set(self, key: str | None) -> None:
        self._key = (key or "").strip() or None

    def get(self) -> str | None:
        return self._key

    @property
    def is_set(self) -> bool:
        return self._key is not None

    @property
    def masked(self) -> str | None:
        """Last 4 characters only, for "which key is loaded?" display."""
        if not self._key:
            return None
        return f"****{self._key[-4:]}" if len(self._key) > 4 else "****"

    def __repr__(self) -> str:
        return f"<VeoCredential set={self.is_set}>"

    __str__ = __repr__
