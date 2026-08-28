from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import VisualGenerationError, sanitize_exception
from .utils import sha256_file
from .visual_models import (
    PaidStageAuthorization,
    SideEffectLease,
    VisualPlan,
)
from .visual_repository import VisualRepository

logger = logging.getLogger(__name__)


class ImageGenerationWorkflow:
    """圖片生成工作流程：world anchor → scene images → thumbnail background。"""

    def __init__(
        self,
        repo: VisualRepository,
        client: Any,
        *,
        image_model: str,
        max_outputs: int,
        unit_cost: float,
        pricing_snapshot: dict[str, Any],
        authorization: PaidStageAuthorization,
        lease: SideEffectLease,
    ):
        self.repo = repo
        self.client = client
        self.image_model = image_model
        self.max_outputs = max_outputs
        self.unit_cost = unit_cost
        self.pricing_snapshot = pricing_snapshot
        self.authorization = authorization
        self.lease = lease

    def generate_scene_images(
        self,
        job_id: int,
        job_dir: Path,
        plan: VisualPlan,
    ) -> None:
        """生成所有場景的圖片候選。"""
        # First, generate world anchor image
        anchor_dir = job_dir / "visual" / "anchor"
        anchor_path = anchor_dir / "world_anchor.png"
        anchor_asset_id = self.repo.reserve_asset(
            job_id,
            None,
            "world_anchor",
            0,
            "gemini",
            self.image_model,
            plan.world_anchor_prompt,
            self.unit_cost,
            self.pricing_snapshot,
        )

        try:
            self.repo.update_asset_status(
                anchor_asset_id, "starting",
                expected_version=0,
                owner_token=self.lease.owner_token,
            )
            result = self.client.generate_image(
                plan.world_anchor_prompt,
                anchor_path,
                reference_paths=[],
            )
            self.repo.update_asset_status(
                anchor_asset_id, "ready",
                expected_version=1,
                path=str(result.path),
                mime_type=result.mime_type,
                sha256=result.sha256,
            )
        except Exception as exc:
            self.repo.update_asset_status(
                anchor_asset_id, "start_uncertain",
                expected_version=1,
                error=sanitize_exception(exc).problem,
            )
            raise

        # Generate scene images (3 candidates per scene)
        for scene in plan.scenes:
            scene_position = scene.position
            scenes = self.repo.scenes(job_id)
            scene_row = next(
                (s for s in scenes if s["position"] == scene_position),
                None,
            )
            if scene_row is None:
                raise VisualGenerationError(f"找不到 scene position {scene_position}")

            scene_id = int(scene_row["id"])
            for variant_idx in range(3):
                asset_id = self.repo.reserve_asset(
                    job_id,
                    scene_id,
                    "scene_image",
                    variant_idx,
                    "gemini",
                    self.image_model,
                    scene.image_prompt,
                    self.unit_cost,
                    self.pricing_snapshot,
                )

                try:
                    self.repo.update_asset_status(
                        asset_id, "starting",
                        expected_version=0,
                        owner_token=self.lease.owner_token,
                    )
                    output_path = job_dir / "visual" / "scenes" / f"{scene.label}_v{variant_idx}.png"
                    result = self.client.generate_image(
                        scene.image_prompt,
                        output_path,
                        reference_paths=[str(anchor_path)],
                    )
                    self.repo.update_asset_status(
                        asset_id, "ready",
                        expected_version=1,
                        path=str(result.path),
                        mime_type=result.mime_type,
                        sha256=result.sha256,
                    )
                except Exception as exc:
                    self.repo.update_asset_status(
                        asset_id, "start_uncertain",
                        expected_version=1,
                        error=sanitize_exception(exc).problem,
                    )
                    raise

    def generate_thumbnail_background(
        self,
        job_id: int,
        job_dir: Path,
        plan: VisualPlan,
    ) -> int:
        """生成縮圖背景圖片（使用第一場景核准圖片作為參考）。"""
        scenes = self.repo.scenes(job_id)
        first_scene = scenes[0]
        selected_image_asset = self.repo.asset(int(first_scene["selected_image_asset_id"]))

        asset_id = self.repo.reserve_asset(
            job_id,
            None,
            "thumbnail_background",
            0,
            "gemini",
            self.image_model,
            plan.thumbnail_prompt,
            self.unit_cost,
            self.pricing_snapshot,
        )

        try:
            self.repo.update_asset_status(
                asset_id, "starting",
                expected_version=0,
                owner_token=self.lease.owner_token,
            )
            output_path = job_dir / "visual" / "thumbnail_background.png"
            result = self.client.generate_image(
                plan.thumbnail_prompt,
                output_path,
                reference_paths=[str(selected_image_asset["path"])],
            )
            self.repo.update_asset_status(
                asset_id, "ready",
                expected_version=1,
                path=str(result.path),
                mime_type=result.mime_type,
                sha256=result.sha256,
            )
        except Exception as exc:
            self.repo.update_asset_status(
                asset_id, "start_uncertain",
                expected_version=1,
                error=sanitize_exception(exc).problem,
            )
            raise
            raise

        return asset_id


class VideoGenerationWorkflow:
    """影片生成工作流程：start Veo operation → poll → normalize → QC。"""

    def __init__(
        self,
        repo: VisualRepository,
        client: Any,
        *,
        video_model: str,
        max_outputs: int,
        unit_cost: float,
        poll_interval_seconds: float,
        normalize: Callable,
        inspect: Callable,
        authorization: PaidStageAuthorization,
        lease: SideEffectLease,
    ):
        self.repo = repo
        self.client = client
        self.video_model = video_model
        self.max_outputs = max_outputs
        self.unit_cost = unit_cost
        self.poll_interval_seconds = poll_interval_seconds
        self.normalize = normalize
        self.inspect = inspect
        self.authorization = authorization
        self.lease = lease

    def generate(self, job_id: int, job_dir: Path) -> None:
        """生成所有場景的影片候選。"""
        scenes = self.repo.scenes(job_id)

        for scene in scenes:
            scene_id = int(scene["id"])
            label = scene["label"]

            # Get the approved image for this scene as first/last frame
            selected_image_asset = self.repo.asset(int(scene["selected_image_asset_id"]))
            frame_path = Path(selected_image_asset["path"])

            # Generate 2 video candidates per scene
            for variant_idx in range(2):
                asset_id = self.repo.reserve_asset(
                    job_id,
                    scene_id,
                    "scene_video",
                    variant_idx,
                    "gemini",
                    self.video_model,
                    scene["motion_prompt"],
                    self.unit_cost,
                    {"date": "2026-07-29"},
                )

                try:
                    # Start video generation
                    self.repo.update_asset_status(
                        asset_id, "starting",
                        expected_version=0,
                        owner_token=self.lease.owner_token,
                    )
                    operation_id = self.client.start_video(
                        scene["motion_prompt"],
                        frame_path,
                    )
                    self.repo.update_asset_status(
                        asset_id, "polling",
                        expected_version=1,
                        operation_id=operation_id,
                    )

                    # Poll for completion
                    raw_path = job_dir / "visual" / "videos" / f"{label}_v{variant_idx}_raw.mp4"
                    poll_result = self.client.poll_video(operation_id, raw_path)

                    if poll_result.error:
                        raise VisualGenerationError(f"Veo 影片生成失敗：{poll_result.error}")

                    # Normalize the loop
                    normalized_path = job_dir / "visual" / "videos" / f"{label}_v{variant_idx}.mp4"
                    self.repo.update_asset_status(
                        asset_id, "normalizing",
                        expected_version=2,
                        raw_path=str(raw_path),
                    )
                    self.normalize(raw_path, normalized_path)

                    # Run QC inspection
                    qc = self.inspect(normalized_path)
                    if qc.verdict != "ok":
                        self.repo.update_asset_status(
                            asset_id, "qc_failed",
                            expected_version=3,
                            path=str(normalized_path),
                            seam_score=qc.seam_score,
                            motion_score=qc.motion_score,
                            error=f"QC failed: {qc.verdict}",
                        )
                        continue

                    # Mark as ready
                    self.repo.update_asset_status(
                        asset_id, "ready",
                        expected_version=3,
                        path=str(normalized_path),
                        mime_type="video/mp4",
                        sha256=sha256_file(normalized_path),
                        duration_seconds=qc.duration_seconds,
                        fps=qc.fps,
                        seam_score=qc.seam_score,
                        motion_score=qc.motion_score,
                    )

                except Exception as exc:
                    current_version = self._get_current_version(asset_id)
                    # If we haven't successfully transitioned to polling (version 0), it's a start error
                    if current_version == 0:
                        status = "start_uncertain"
                    else:
                        status = "failed_generation"
                        if "transient" in str(exc).lower():
                            status = "polling"  # Can resume polling

                    self.repo.update_asset_status(
                        asset_id, status,
                        expected_version=current_version,
                        error=sanitize_exception(exc).problem,
                    )
                    raise

    def _get_current_version(self, asset_id: int) -> int:
        """Get current state_version of asset for CAS."""
        asset = self.repo.asset(asset_id)
        return int(asset.get("state_version", 0))
