from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import AppConfig
from .db import StateDB
from .errors import GenerationError, LyriaAutoError, SafetyBlockedError
from .media.audio import combine_audio, extend_audio, normalize_audio, validate_audio
from .media.thumbnail import create_thumbnail
from .media.timeline import create_timeline_video
from .media.video import create_static_video
from .metadata import build_metadata
from .metrics import MetricEvent, MetricsCollector
from .models import JobContext, Metadata
from .originality import OriginalityGate
from .providers.lyria import LyriaClient
from .providers.youtube import YouTubeClient
from .review_server import ReviewServer
from .safety import PromptSafety
from .utils import ensure_dir, json_dump, sha256_file, slugify
from .visual_models import VisualPlan
from .visual_planner import VisualPlanner
from .visual_repository import VisualRepository
from .visual_workflow import ImageGenerationWorkflow, VideoGenerationWorkflow

logger = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config: AppConfig):
        self.config = config
        project = config.section("project")
        self.workspace = ensure_dir(config.root / project.get("workspace", "workspace"))
        self.db = StateDB(config.root / project.get("database", "workspace/state.sqlite3"))
        
        # Visual pipeline components
        self._metrics = MetricsCollector(self.workspace / "metrics")
        self._originality_gate = OriginalityGate(
            hash_store_path=self.workspace / "originality_hashes.json"
        )
        self._review_server = ReviewServer()

    def close(self):
        if self._review_server.session is not None:
            self._review_server.stop()
        self.db.close()

    def _material_target_seconds(self) -> int:
        """要生成多少秒的「原創素材」。超過上限的部分靠循環補足，不再多花錢生成 ——
        4 小時的影片與 1 小時的影片素材成本相同。"""
        video_seconds = int(self.config.settings["video"].get("target_duration_minutes", 10)) * 60
        cap = int(self.config.section("generation").get("max_material_minutes", 60)) * 60
        return min(video_seconds, cap)

    def _planned_track_count(self) -> int:
        """要先規劃幾則 Prompt。實際生成幾首由累加長度決定，用不到的就停在 planned 不花錢，
        所以這裡寧可多規劃一些當緩衝（實測單曲約 144~180 秒，取保守值估算）。"""
        import math
        generation = self.config.section("generation")
        max_tracks = int(generation.get("max_tracks_per_video", 40))
        estimate = math.ceil(self._material_target_seconds() / 140) + 3
        return max(1, min(max_tracks, estimate))

    def _engine(self, offset: int = 0) -> Any:
        from .prompt_engine import PromptEngine
        project = self.config.section("project")
        generation = self.config.section("generation")
        return PromptEngine(
            self.config.prompts,
            generation.get("prompt_profile", "coffeehouse_lofi_jazz"),
            int(project.get("random_seed", 1)) + offset,
        )

    def _is_visual_enabled(self) -> bool:
        """Check if visual pipeline is enabled in configuration."""
        visual_cfg = self.config.section("visual")
        return visual_cfg.get("enabled", False)

    def _get_visual_config(self) -> dict:
        """Get visual pipeline configuration with defaults."""
        return self.config.section("visual")

    def _execute_visual(self, job_id: int, job_dir: Path, ctx: JobContext) -> Path:
        """Execute the visual pipeline: plan → generate images → review → generate videos → review → render.
        
        Returns the path to the final rendered video.
        """
        visual_cfg = self._get_visual_config()
        target_minutes = int(self.config.settings["video"].get("target_duration_minutes", 10))
        
        # Phase 1: Visual Planning
        self.db.update_job(job_id, "generating_images")
        logger.info("Phase 1: Visual planning for job %d", job_id)
        
        planner = VisualPlanner(seed=job_id)
        representative_prompt = self._get_representative_prompt(job_id)
        visual_plan = planner.compose(representative_prompt, target_minutes)
        
        # Save visual plan
        json_dump(job_dir / "visual_plan.json", visual_plan.to_dict())
        
        repo = VisualRepository(self.db)
        for scene in visual_plan.scenes:
            repo.upsert_scene(
                job_id=job_id,
                position=scene.position,
                label=scene.label,
                world_json=visual_plan.world,
                image_prompt=scene.image_prompt,
                motion_prompt=scene.motion_prompt,
            )
        
        # Phase 2: Image Generation
        logger.info("Phase 2: Generating images for %d scenes", len(visual_plan.scenes))
        image_workflow = self._create_image_workflow(job_id, visual_cfg)
        image_workflow.generate_scene_images(job_id, job_dir, visual_plan)
        
        # Phase 3: Image Review Gate
        self.db.update_job(job_id, "awaiting_image_review")
        logger.info("Phase 3: Awaiting image review for job %d", job_id)
        self._await_review(job_id, job_dir, "image")
        
        # Phase 4: Video Generation
        self.db.update_job(job_id, "generating_videos")
        logger.info("Phase 4: Generating videos for %d scenes", len(visual_plan.scenes))
        video_workflow = self._create_video_workflow(job_id, visual_cfg)
        video_workflow.generate(job_id, job_dir)
        
        # Phase 5: Video Review Gate
        self.db.update_job(job_id, "awaiting_video_review")
        logger.info("Phase 5: Awaiting video review for job %d", job_id)
        self._await_review(job_id, job_dir, "video")
        
        # Phase 6: Render Timeline
        self.db.update_job(job_id, "rendering")
        logger.info("Phase 6: Rendering timeline for job %d", job_id)
        video_path = self._render_visual(ctx, visual_plan, repo)
        
        # Record metrics
        self._metrics.record(MetricEvent(
            timestamp=datetime.now(UTC).isoformat(),
            event_type="render_completed",
            job_id=str(job_id),
            status="success",
        ))
        
        return video_path

    def _create_image_workflow(self, job_id: int, visual_cfg: dict) -> ImageGenerationWorkflow:
        """Create an image generation workflow with proper configuration."""

        from .visual_models import PaidStageAuthorization
        
        lease = self.db.acquire_side_effect_lease("job", job_id, 3600)
        auth = PaidStageAuthorization(
            id=job_id,
            scope_type="job",
            scope_id=job_id,
            stage="image_generation",
            kind="image",
            expected_count=visual_cfg.get("max_outputs_per_scene", 3) * 5,
            allowed_count=visual_cfg.get("max_outputs_per_scene", 3) * 5,
        )
        
        from .providers.gemini_visual import GeminiVisualClient
        
        api_key = visual_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY")
        image_model = visual_cfg.get("image_model", "gemini-3.1-flash-image")
        video_model = visual_cfg.get("video_model", "veo-3.1-fast-generate-preview")

        client = GeminiVisualClient(
            api_key=api_key,
            image_model=image_model,
            video_model=video_model,
        )
        
        return ImageGenerationWorkflow(
            repo=VisualRepository(self.db),
            client=client,
            image_model=image_model,
            max_outputs=visual_cfg.get("max_outputs_per_scene", 3),
            unit_cost=visual_cfg.get("image_cost_usd", 0.04),
            pricing_snapshot={"model": image_model, "cost": visual_cfg.get("image_cost_usd", 0.04)},
            authorization=auth,
            lease=lease,
        )

    def _create_video_workflow(self, job_id: int, visual_cfg: dict) -> VideoGenerationWorkflow:
        """Create a video generation workflow with proper configuration."""
        from .providers.gemini_visual import GeminiVisualClient
        from .visual_models import PaidStageAuthorization
        
        lease = self.db.acquire_side_effect_lease("job", job_id, 3600)
        auth = PaidStageAuthorization(
            id=job_id,
            scope_type="job",
            scope_id=job_id,
            stage="video_generation",
            kind="video",
            expected_count=4,
            allowed_count=4,
        )
        
        api_key = visual_cfg.get("api_key") or os.environ.get("GEMINI_API_KEY")
        image_model = visual_cfg.get("image_model", "gemini-3.1-flash-image")
        video_model = visual_cfg.get("video_model", "veo-3.1-fast-generate-preview")

        client = GeminiVisualClient(
            api_key=api_key,
            image_model=image_model,
            video_model=video_model,
        )
        
        return VideoGenerationWorkflow(
            repo=VisualRepository(self.db),
            client=client,
            video_model=video_model,
            max_outputs=visual_cfg.get("max_outputs_per_scene", 1),
            unit_cost=visual_cfg.get("video_cost_usd", 2.50),
            poll_interval_seconds=visual_cfg.get("poll_interval_seconds", 30),
            normalize=lambda path: path,  # Placeholder for video normalization
            inspect=lambda path: {},  # Placeholder for video QC
            authorization=auth,
            lease=lease,
        )

    def _get_representative_prompt(self, job_id: int) -> str:
        """Get the representative prompt from the job's tracks."""
        tracks = self.db.tracks_for_job(job_id)
        if tracks:
            return tracks[0]["prompt"]
        return "cozy jazz coffeehouse atmosphere"

    def _await_review(self, job_id: int, job_dir: Path, review_type: str) -> None:
        """Wait for manual review approval."""

    def _render_visual(self, ctx: JobContext, visual_plan: VisualPlan, repo: VisualRepository) -> Path:
        """Render the final video using the timeline with approved visual assets."""
        video_cfg = self.config.settings["video"]
        
        scenes = []
        db_scenes = repo.scenes(ctx.job_id)
        for s in db_scenes:
            if s.get("selected_video_asset_id"):
                asset = repo.asset(s["selected_video_asset_id"])
                if asset and asset.get("path"):
                    scenes.append((s["label"], Path(asset["path"])))
        
        if not scenes:
            for s in db_scenes:
                video_assets = repo.assets(ctx.job_id, s["label"])
                if video_assets:
                    scenes.append((s["label"], Path(video_assets[-1]["path"])))
        
        if not scenes:
            raise GenerationError("No approved video assets available for rendering")
        
        output_name = slugify(ctx.metadata.title) + "_visual.mp4"
        output_path = ctx.job_dir / output_name
        
        create_timeline_video(
            scenes=scenes,
            audio_path=ctx.job_dir / "compilation_extended.m4a",
            output_path=output_path,
            width=int(video_cfg.get("width", 1920)),
            height=int(video_cfg.get("height", 1080)),
            fps=int(video_cfg.get("fps", 24)),
            audio_bitrate=str(video_cfg.get("audio_bitrate", "256k")),
            preset=str(video_cfg.get("video_preset", "veryfast")),
            interval_seconds=int(video_cfg.get("visual_interval_seconds", 1800)),
        )
        return output_path
        
        # Update video record
        self.db.update_video(
            ctx.video_row_id,
            video_path=str(output_path),
            thumbnail_path=str(ctx.thumbnail),
            publish_at=ctx.metadata.publish_at,
            status="rendered",
        )
        
        # Save metadata
        json_dump(ctx.job_dir / "metadata.json", ctx.metadata.__dict__)
        
        return output_path

    def review_assets(self, job_id: int, approve: bool = True) -> dict[str, Any]:
        """Manually review and approve/reject visual assets for a job."""
        job_row = self.db.job(job_id)
        if job_row is None:
            raise LyriaAutoError(f"找不到 job {job_id}")
        
        job_dir = self.workspace / f"job_{job_id:06d}"
        
        # Load visual plan
        visual_plan_path = job_dir / "visual_plan.json"
        if not visual_plan_path.exists():
            raise LyriaAutoError(f"job {job_id} 沒有視覺計畫")
        
        visual_plan = VisualPlan.from_dict(json.loads(visual_plan_path.read_text(encoding="utf-8")))
        
        # Process review decision
        repo = VisualRepository(self.db)
        db_scenes = repo.scenes(job_id)
        scene_info = {s["position"]: (s["id"], s["state_version"]) for s in db_scenes}
        for scene in visual_plan.scenes:
            if scene.position not in scene_info:
                continue
            scene_id, version = scene_info[scene.position]
            if approve:
                repo.update_scene_status(scene_id, "image_approved", expected_version=version)
                repo.update_scene_status(scene_id, "video_approved", expected_version=version + 1)
            else:
                repo.update_scene_status(scene_id, "needs_regeneration", expected_version=version)
        
        # Record metric
        self._metrics.record(MetricEvent(
            timestamp=datetime.now(UTC).isoformat(),
            event_type="review_approved" if approve else "review_rejected",
            job_id=str(job_id),
            status="success",
        ))
        
        return {
            "job_id": job_id,
            "approved": approve,
            "scenes_processed": len(visual_plan.scenes),
        }

    def regenerate_visual(self, job_id: int, scene_label: str | None = None) -> dict[str, Any]:
        """Regenerate visual assets for a job or specific scene."""
        job_row = self.db.job(job_id)
        if job_row is None:
            raise LyriaAutoError(f"找不到 job {job_id}")
        
        job_dir = self.workspace / f"job_{job_id:06d}"
        
        # Load visual plan
        visual_plan_path = job_dir / "visual_plan.json"
        if not visual_plan_path.exists():
            raise LyriaAutoError(f"job {job_id} 沒有視覺計畫")
        
        visual_plan = VisualPlan.from_dict(json.loads(visual_plan_path.read_text(encoding="utf-8")))
        
        # Determine which scenes to regenerate
        scenes_to_regenerate = []
        if scene_label:
            scenes_to_regenerate = [s for s in visual_plan.scenes if s.label == scene_label]
        else:
            scenes_to_regenerate = visual_plan.scenes
        
        if not scenes_to_regenerate:
            raise LyriaAutoError(f"找不到場景 {scene_label}")
        
        # Regenerate images and videos for selected scenes
        visual_cfg = self._get_visual_config()
        image_workflow = self._create_image_workflow(job_id, visual_cfg)
        video_workflow = self._create_video_workflow(job_id, visual_cfg)
        repo = VisualRepository(self.db)
        
        for scene in scenes_to_regenerate:
            image_workflow.generate_scene_images(repo, scene)
            video_workflow.generate(repo, scene)
        
        return {
            "job_id": job_id,
            "scenes_regenerated": len(scenes_to_regenerate),
            "scene_labels": [s.label for s in scenes_to_regenerate],
        }

    def report_metrics(self, job_id: int | None = None) -> dict[str, Any]:
        """Generate a metrics report for a job or all jobs."""
        return self._metrics.export_json(str(job_id) if job_id else None)

    def run_batch(self, videos: int, channel_name: str, upload: bool, dry_run: bool) -> list[dict[str, Any]]:
        results = []
        for index in range(videos):
            results.append(self.run_one(channel_name, upload, dry_run, publish_offset_index=index))
        return results

    def run_one(self, channel_name: str, upload: bool, dry_run: bool, publish_offset_index: int = 0) -> dict[str, Any]:
        # channel 驗證必須在 create_job 之前執行：這樣無效頻道名稱會在任何 job 紀錄
        # 建立前就拋出 ConfigurationError，DB 裡不會留下這次失敗的痕跡（跟重構前行為一致）。
        channel_cfg = self.config.channel(channel_name) if self.config.channels else {}
        tracks_count = self._planned_track_count()
        job_id = self.db.create_job(channel_name, dry_run, {"tracks": tracks_count, "upload": upload})
        job_dir = ensure_dir(self.workspace / f"job_{job_id:06d}")
        try:
            ctx = self._plan(job_id, job_dir, channel_cfg, publish_offset_index, tracks_count)

            if dry_run:
                self.db.update_video(ctx.video_row_id, status="dry_run")
                self.db.update_job(job_id, "dry_run_complete")
                return {"job_id": job_id, "dry_run": True, "directory": str(job_dir), "plan": str(job_dir / "plan.json")}

            return self._execute(job_id, job_dir, ctx, upload)
        except Exception as exc:
            logger.exception("Job %s 失敗", job_id)
            self.db.update_job(job_id, "failed", str(exc))
            self.db.event(job_id, "job_failed", str(exc), "ERROR")
            raise

    def resume_one(self, job_id: int | None = None, upload: bool = False) -> dict[str, Any]:
        job_row = self.db.resumable_job(job_id)
        if job_row is None:
            raise LyriaAutoError("找不到可續跑的工作。用 lyria-auto status 查看歷史。")
        if job_row["dry_run"]:
            raise LyriaAutoError(f"job {job_row['id']} 是 dry-run，不需要續跑。")
        # resumable_job 的明確 id 分支不過濾 status（讓使用者能查詢任何 job），所以已完成的
        # job 要在這裡擋下來，而不是讓它被當成「未完成」重跑一次、甚至覆寫掉完成紀錄。
        if job_row["status"] in ("complete", "dry_run_complete"):
            raise LyriaAutoError(f"job {job_row['id']} 已經完成（status={job_row['status']}），不需要續跑。")

        ctx = self._load_job_context(job_row)
        try:
            return self._execute(ctx.job_id, ctx.job_dir, ctx, upload)
        except Exception as exc:
            logger.exception("Job %s 續跑失敗", ctx.job_id)
            self.db.update_job(ctx.job_id, "failed", str(exc))
            self.db.event(ctx.job_id, "job_failed", str(exc), "ERROR")
            raise

    def run_from_job(self, job_id: int, upload: bool = False) -> dict[str, Any]:
        """把既有的 dry-run 工作「轉正」：沿用它已規劃好的 Prompt、文案與縮圖實際生成。

        `run --dry-run` 與後續的 `run` 之所以會產出不同內容，是因為 Prompt 的隨機種子
        取自 job_id（每次執行都會建新 job），而且 dry-run 寫入的曲目簽章還會進入
        recent_signatures 的避重清單，把下一次的組合往別處推。這個方法完全不重新規劃，
        直接用 plan.json 裡既有的計畫，所以成品與你在 dry-run 看到的一字不差。
        """
        job_row = self.db.resumable_job(job_id)
        if job_row is None:
            raise LyriaAutoError(f"找不到 job {job_id}。用 lyria-auto status 查看歷史。")
        if not job_row["dry_run"]:
            raise LyriaAutoError(
                f"job {job_id} 不是 dry-run 工作（dry_run=0），無法轉正。"
                "未完成的正式工作請用 lyria-auto resume 續跑。"
            )

        ctx = self._load_job_context(job_row)
        self.db.promote_dry_run(ctx.job_id)
        self.db.update_video(ctx.video_row_id, status="planned")
        try:
            return self._execute(ctx.job_id, ctx.job_dir, ctx, upload)
        except Exception as exc:
            logger.exception("Job %s 轉正失敗", ctx.job_id)
            self.db.update_job(ctx.job_id, "failed", str(exc))
            self.db.event(ctx.job_id, "job_failed", str(exc), "ERROR")
            raise

    def _load_job_context(self, job_row) -> JobContext:
        """從既有 job 的落地產物重建 JobContext，完全不重新規劃。"""
        job_id = int(job_row["id"])
        job_dir = self.workspace / f"job_{job_id:06d}"
        plan_path = job_dir / "plan.json"
        if not plan_path.exists():
            raise LyriaAutoError(f"job {job_id} 找不到 plan.json，規劃階段就已失敗，請重新執行 run。")
        plan_payload = json.loads(plan_path.read_text(encoding="utf-8"))
        metadata = Metadata(**plan_payload["metadata"])

        video_row = self.db.conn.execute(
            "SELECT id FROM videos WHERE job_id=? ORDER BY id DESC LIMIT 1", (job_id,)
        ).fetchone()
        if video_row is None:
            raise LyriaAutoError(f"job {job_id} 找不到對應的 video 紀錄。")

        thumbnail = job_dir / "thumbnail.jpg"
        if not thumbnail.exists():
            raise LyriaAutoError(f"job {job_id} 找不到縮圖。")

        # 用 job 自己存的 channel（建立時記在 jobs.channel），不是呼叫端傳入的 channel_name——
        # 避免因為忘記帶 --channel 而誤用預設值上傳到錯的頻道。
        channel_cfg = self.config.channel(job_row["channel"]) if self.config.channels else {}
        return JobContext(
            job_id=job_id,
            job_dir=job_dir,
            metadata=metadata,
            video_row_id=int(video_row["id"]),
            thumbnail=thumbnail,
            channel_cfg=channel_cfg,
        )

    def _execute(self, job_id: int, job_dir: Path, ctx: JobContext, upload: bool) -> dict[str, Any]:
        # Check if visual pipeline is enabled
        if self._is_visual_enabled():
            # Visual pipeline: plan → images → review → videos → review → render
            video_path = self._execute_visual(job_id, job_dir, ctx)
        else:
            # Legacy audio-only pipeline
            tracks = self._generate(job_id, job_dir)
            video_path = self._render(ctx, tracks)

        youtube_id = None
        if upload:
            youtube_id = self._upload(ctx, video_path)

        self.db.update_job(job_id, "complete")
        return {
            "job_id": job_id,
            "directory": str(job_dir),
            "video": str(video_path),
            "youtube_video_id": youtube_id,
            "publish_at": ctx.metadata.publish_at,
        }

    def _plan(
        self,
        job_id: int,
        job_dir: Path,
        channel_cfg: dict,
        publish_offset_index: int,
        tracks_count: int,
    ) -> JobContext:
        settings = self.config.settings
        video_cfg = settings["video"]
        quality = settings["quality"]
        recent = self.db.recent_signatures(int(quality.get("max_recent_prompt_signatures", 100)))

        self.db.update_job(job_id, "planning")
        plans = self._engine(job_id).compose_album(tracks_count, recent)
        for plan in plans:
            self.db.add_track(job_id, plan.prompt, plan.signature)
        representative = plans[0]
        target_minutes = int(video_cfg.get("target_duration_minutes", 10))
        metadata = build_metadata(settings, channel_cfg, representative, target_minutes, publish_offset_index, episode=job_id)
        video_row_id = self.db.add_video(job_id, metadata.title)
        plan_payload = {
            "job_id": job_id,
            "material_target_seconds": self._material_target_seconds(),
            "planned_prompt_count": len(plans),
            "prompts": [p.__dict__ for p in plans],
            "metadata": metadata.__dict__,
        }
        json_dump(job_dir / "plan.json", plan_payload)

        thumb = create_thumbnail(
            job_dir / "thumbnail.jpg",
            title="COZY JAZZ",
            subtitle=representative.scene_label or representative.scene,
            width=int(video_cfg.get("thumbnail_width", 1280)),
            height=int(video_cfg.get("thumbnail_height", 720)),
            quality=int(video_cfg.get("thumbnail_quality", 88)),
            background_dir=self.config.root / video_cfg.get("background_directory", "assets/backgrounds"),
            seed=job_id,
        )
        self.db.update_video(video_row_id, thumbnail_path=str(thumb), status="planned")

        return JobContext(
            job_id=job_id,
            job_dir=job_dir,
            metadata=metadata,
            video_row_id=video_row_id,
            thumbnail=thumb,
            channel_cfg=channel_cfg,
        )

    def _generate_track(
        self,
        lyria: LyriaClient,
        safety: PromptSafety,
        generation: dict,
        quality: dict,
        job_dir: Path,
        job_id: int,
        track_id: int,
        idx: int,
        prompt: str,
    ) -> float | None:
        """成功時回傳這首的實際秒數（供累加判斷是否已生成足夠素材），失敗回傳 None。"""
        raw = job_dir / f"track_{idx:02d}_raw.mp3"
        try:
            try:
                lyria.generate(prompt, raw)
            except SafetyBlockedError:
                if not generation.get("safe_rewrite_on_block", True):
                    raise
                prompt = safety.neutral_rewrite(prompt)
                safety.require_safe(prompt)
                lyria.generate(prompt, raw)
            info = validate_audio(
                raw,
                float(quality.get("minimum_duration_seconds", 20)),
                int(quality.get("minimum_sample_rate", 44100)),
                bool(quality.get("require_stereo", True)),
            )
            final_track = raw
            if quality.get("normalize_audio", True):
                final_track = normalize_audio(
                    raw,
                    job_dir / f"track_{idx:02d}_normalized.m4a",
                    float(quality.get("loudness_target_lufs", -16)),
                    float(quality.get("true_peak_db", -1.5)),
                )
            self.db.update_track(
                track_id,
                audio_path=str(final_track),
                duration_seconds=info["duration"],
                sha256=sha256_file(final_track),
                status="ready",
                error=None,
            )
            time.sleep(float(generation.get("request_delay_seconds", 3)))
            return float(info["duration"])
        except (LyriaAutoError, OSError) as exc:
            self.db.update_track(track_id, status="failed", error=str(exc))
            self.db.event(job_id, "track_failed", f"track {idx}: {exc}", "ERROR")
            return None

    def _generate(self, job_id: int, job_dir: Path) -> list[Path]:
        settings = self.config.settings
        generation = settings["generation"]
        quality = settings["quality"]
        self.db.update_job(job_id, "generating")
        lyria = LyriaClient(
            model=generation.get("model", "lyria-3-pro-preview"),
            max_attempts=int(generation.get("max_generation_attempts", 3)),
            request_delay_seconds=float(generation.get("request_delay_seconds", 3)),
        )
        safety = PromptSafety(self.config.prompts.get("blocked_reference_terms", []))
        track_rows = self.db.tracks_for_job(job_id)
        idx_by_id = {int(row["id"]): idx for idx, row in enumerate(track_rows, start=1)}

        # 一首一首生成並累加實際長度，湊夠素材就停 —— 不再靠公式預估曲目數。
        # 曲目實際長度會浮動（實測 144~180 秒），用預估值算出來的固定曲目數不是生太多
        # （多付的錢被裁掉丟棄）就是生太少。剩下沒用到的 Prompt 停在 planned，不花錢。
        target = self._material_target_seconds()
        accumulated = 0.0

        for idx, row in enumerate(track_rows, start=1):
            if accumulated >= target:
                break
            if self.db._track_is_reusable(row):
                accumulated += float(row["duration_seconds"] or 0)
                continue
            seconds = self._generate_track(
                lyria, safety, generation, quality, job_dir, job_id,
                int(row["id"]), idx, row["prompt"],
            )
            if seconds:
                accumulated += seconds
                logger.info("已生成素材 %.1f/%.0f 分鐘（第 %s 首）", accumulated / 60, target / 60, idx)

        # 只有素材還不夠時才需要回頭重試失敗的曲目；已經湊夠就不必再花錢補。
        if accumulated < target:
            for row in [r for r in self.db.tracks_for_job(job_id) if r["status"] == "failed"]:
                if accumulated >= target:
                    break
                track_id = int(row["id"])
                seconds = self._generate_track(
                    lyria, safety, generation, quality, job_dir, job_id,
                    track_id, idx_by_id[track_id], row["prompt"],
                )
                if seconds:
                    accumulated += seconds

        ready_rows = [r for r in self.db.tracks_for_job(job_id) if r["status"] == "ready" and r["audio_path"]]
        if not ready_rows:
            summary = "沒有任何曲目生成成功"
            self.db.update_job(job_id, "failed", summary)
            raise GenerationError(f"{summary}，可用 lyria-auto resume 續跑")

        if accumulated < target:
            still_failed = [r for r in self.db.tracks_for_job(job_id) if r["status"] == "failed"]
            if still_failed:
                summary = f"素材只湊到 {accumulated / 60:.1f}/{target / 60:.0f} 分鐘，{len(still_failed)} 首曲目生成失敗"
                self.db.update_job(job_id, "failed", summary)
                raise GenerationError(f"{summary}，可用 lyria-auto resume 續跑")
            # 沒有失敗、只是規劃的 Prompt 用完了：不算錯誤，後續會用循環補足影片長度。
            logger.warning(
                "規劃的 %s 則 Prompt 已用盡，素材 %.1f 分鐘 < 目標 %.0f 分鐘，將以循環補足。"
                "若想要更多原創素材，請調高 generation.max_tracks_per_video。",
                len(track_rows), accumulated / 60, target / 60,
            )

        return [Path(r["audio_path"]) for r in ready_rows]

    def _render(self, ctx: JobContext, tracks: list[Path]) -> Path:
        video_cfg = self.config.settings["video"]
        crossfade = float(video_cfg.get("crossfade_seconds", 2))
        self.db.update_job(ctx.job_id, "rendering")
        compilation = combine_audio(tracks, ctx.job_dir / "compilation.m4a", crossfade)
        target_minutes = int(video_cfg.get("target_duration_minutes", 10))
        target_seconds = target_minutes * 60
        audio_final = extend_audio(compilation, ctx.job_dir / "compilation_extended.m4a", target_seconds, crossfade)
        output_name = slugify(ctx.metadata.title) + ".mp4"
        video_path = create_static_video(
            ctx.thumbnail,
            audio_final,
            ctx.job_dir / output_name,
            int(video_cfg.get("width", 1920)),
            int(video_cfg.get("height", 1080)),
            int(video_cfg.get("fps", 1)),
            str(video_cfg.get("audio_bitrate", "256k")),
            str(video_cfg.get("video_preset", "veryfast")),
        )
        self.db.update_video(
            ctx.video_row_id,
            video_path=str(video_path),
            thumbnail_path=str(ctx.thumbnail),
            publish_at=ctx.metadata.publish_at,
            status="rendered",
        )
        json_dump(ctx.job_dir / "metadata.json", ctx.metadata.__dict__)
        return video_path

    def _upload(self, ctx: JobContext, video_path: Path) -> str:
        existing = self.db.conn.execute(
            "SELECT youtube_video_id FROM videos WHERE id=?", (ctx.video_row_id,)
        ).fetchone()
        if existing and existing["youtube_video_id"]:
            youtube_id = existing["youtube_video_id"]
            logger.info("job %s 先前已上傳（youtube_video_id=%s），略過重複上傳", ctx.job_id, youtube_id)
            return youtube_id

        self.db.update_job(ctx.job_id, "uploading")
        yt = YouTubeClient(ctx.channel_cfg, self.config.root)
        identity = yt.channel_identity()
        logger.info("上傳至頻道：%s (%s)", identity["title"], identity["id"])

        def _on_video_created(video_id: str) -> None:
            # 影片一建立就先落地 DB：就算後續縮圖／播放清單步驟失敗，video_id 也不會遺失，
            # 不會導致使用者被引導去 resume --upload 而重複發佈同一支影片。
            self.db.update_video(ctx.video_row_id, youtube_video_id=video_id)

        youtube_id = yt.upload(video_path, ctx.thumbnail, ctx.metadata, on_video_created=_on_video_created)
        self.db.update_video(ctx.video_row_id, youtube_video_id=youtube_id, status="uploaded")
        return youtube_id
