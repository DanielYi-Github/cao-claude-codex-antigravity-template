from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class VisualSource:
    provider: str
    image_model: str
    video_model: str
    credential_fingerprint: str
    sdk_version: str
    billing_project_id: str | None = None

    def identity_key(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GeneratedImage:
    path: Path
    mime_type: str
    sha256: str


@dataclass(frozen=True)
class VideoPoll:
    done: bool
    path: Path | None = None
    sha256: str | None = None
    error: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class SideEffectLease:
    scope_type: str
    scope_id: int
    owner_token: str
    expires_at: str
    acquired: bool


@dataclass(frozen=True)
class PaidStageAuthorization:
    id: int
    scope_type: str
    scope_id: int
    stage: str
    kind: str
    expected_count: int
    allowed_count: int


@dataclass(frozen=True)
class MediaSnapshot:
    path: Path
    sha256: str
    size_bytes: int
    probe: dict[str, Any]


@dataclass(frozen=True)
class WorldBible:
    """固定咖啡館世界的視覺設定，同一 job 內不會漂移。"""
    architecture: str
    materials: str
    color_palette: str
    time_of_day: str
    weather: str
    window_view: str
    props: str


@dataclass(frozen=True)
class ScenePlan:
    """單一場景的規劃：鏡位、圖片提示詞、影片提示詞。"""
    position: int
    label: str
    role: str
    allowed_motion: str
    image_prompt: str
    motion_prompt: str


@dataclass(frozen=True)
class VisualPlan:
    """視覺規劃：世界設定、場景、縮圖提示詞。"""
    version: int
    world: WorldBible
    world_anchor_prompt: str
    scenes: tuple[ScenePlan, ...]
    thumbnail_prompt: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "world": asdict(self.world),
            "world_anchor_prompt": self.world_anchor_prompt,
            "scenes": [asdict(s) for s in self.scenes],
            "thumbnail_prompt": self.thumbnail_prompt,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> VisualPlan:
        return cls(
            version=int(value["version"]),
            world=WorldBible(**value["world"]),
            world_anchor_prompt=str(value["world_anchor_prompt"]),
            scenes=tuple(ScenePlan(**scene) for scene in value["scenes"]),
            thumbnail_prompt=str(value["thumbnail_prompt"]),
        )


@dataclass(frozen=True)
class TimelineSlice:
    """時間軸上的一個場景切片，包含全域時間範圍。"""
    label: str
    path: Path
    global_start_seconds: float
    global_end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.global_end_seconds - self.global_start_seconds


@dataclass(frozen=True)
class VideoQC:
    """影片品質檢查結果。"""
    is_looping: bool
    width: int
    height: int
    duration_seconds: float
    fps: float
    seam_score: float
    motion_score: float
    verdict: str


@dataclass(frozen=True)
class StageAllowances:
    """本次 CLI 輸入的付費輸出授權數量。"""
    images: int = 0
    videos: int = 0


@dataclass(frozen=True)
class SchedulerAllowances:
    """Scheduler 專用的付費上限，每次 run 歸零。"""
    max_image_starts: int = 0
    max_video_starts: int = 0


@dataclass(frozen=True)
class RenderManifest:
    """Render 輸入的不可變 manifest。"""
    audio_snapshot_path: Path
    scenes: tuple[TimelineSlice, ...]
    manifest_sha256: str


@dataclass(frozen=True)
class FinalizedRender:
    """Render 完成後的不可變輸出。"""
    video_snapshot_path: Path
    video_sha256: str
    thumbnail_snapshot_path: Path
    thumbnail_sha256: str


@dataclass(frozen=True)
class PublishIdentity:
    """上傳前的不可變身份。"""
    render_manifest_sha256: str
    output_snapshot_sha256: str
    metadata_sha256: str
    thumbnail_sha256: str
    video_snapshot_path: Path
    thumbnail_snapshot_path: Path

def credential_fingerprint(api_key: str, secret_path: str | Path) -> str:
    path = Path(secret_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(os.urandom(32))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    secret = path.read_bytes()
    return hmac.new(secret, api_key.encode("utf-8"), hashlib.sha256).hexdigest()
