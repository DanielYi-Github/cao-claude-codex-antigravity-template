from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import AppConfig
from .pipeline import Pipeline

logger = logging.getLogger(__name__)


def start_scheduler(config: AppConfig) -> None:
    sched_cfg = config.section("scheduler")
    if not sched_cfg.get("enabled", False):
        raise RuntimeError("scheduler.enabled 目前是 false，請先修改 config/settings.yaml")
    
    # Check visual pipeline configuration
    visual_cfg = config.section("visual")
    visual_enabled = visual_cfg.get("enabled", False)
    
    # Visual pipeline requires explicit opt-in for scheduled execution
    if visual_enabled and not sched_cfg.get("visual_paid_opt_in", False):
        raise RuntimeError(
            "視覺管線已啟用，但排程器未設定 visual_paid_opt_in = true。"
            "視覺生成會產生 API 費用，請在 scheduler 設定中明確啟用。"
        )
    
    # Validate visual caps if enabled
    if visual_enabled:
        max_image_starts = int(visual_cfg.get("max_image_starts", 12))
        max_video_starts = int(visual_cfg.get("max_video_starts", 4))
        logger.info(
            "視覺管線限制：max_image_starts=%d, max_video_starts=%d",
            max_image_starts, max_video_starts,
        )
    
    timezone = config.section("project").get("timezone", "Asia/Taipei")
    scheduler = BlockingScheduler(timezone=ZoneInfo(timezone))

    def run_job():
        pipeline = Pipeline(config)
        try:
            # Preflight check for visual pipeline
            if visual_enabled:
                logger.info("執行視覺管線排程任務")
                _validate_visual_preflight(config)
            
            pipeline.run_batch(
                videos=int(sched_cfg.get("videos_per_run", 1)),
                channel_name=str(sched_cfg.get("channel", "main")),
                upload=bool(sched_cfg.get("upload", True)),
                dry_run=False,
            )
        finally:
            pipeline.close()

    trigger = CronTrigger.from_crontab(str(sched_cfg.get("cron", "0 9 * * 1,4")), timezone=ZoneInfo(timezone))
    scheduler.add_job(run_job, trigger=trigger, max_instances=1, coalesce=True, misfire_grace_time=3600)
    logger.info("排程器啟動：%s (%s)", sched_cfg.get("cron"), timezone)
    scheduler.start()


def _validate_visual_preflight(config: AppConfig) -> None:
    """Validate visual pipeline preflight requirements before scheduled execution."""
    visual_cfg = config.section("visual")
    
    # Check for required API key
    if not visual_cfg.get("api_key"):
        raise RuntimeError("視覺管線需要設定 visual.api_key")
    
    # Check model configuration
    if not visual_cfg.get("image_model"):
        raise RuntimeError("視覺管線需要設定 visual.image_model")
    
    if not visual_cfg.get("video_model"):
        raise RuntimeError("視覺管線需要設定 visual.video_model")
    
    logger.info("視覺管線預檢通過")
