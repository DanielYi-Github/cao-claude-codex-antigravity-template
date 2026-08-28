"""Tests for visual pipeline integration."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from lyria_auto.pipeline import Pipeline
from lyria_auto.visual_models import ScenePlan, VisualPlan, WorldBible


def test_visual_pipeline_enabled_calls_visual_execute(tmp_path: Path) -> None:
    """When visual is enabled, _execute should call _execute_visual."""
    from lyria_auto.config import AppConfig

    config = MagicMock(spec=AppConfig)
    config.section.return_value = {"enabled": True}
    config.settings = {"video": {"target_duration_minutes": 10}}
    config.root = tmp_path
    config.channels = None

    pipeline = Pipeline(config)

    job_id = 1
    job_dir = tmp_path / "job_000001"
    job_dir.mkdir()
    ctx = MagicMock()
    ctx.job_id = job_id
    ctx.job_dir = job_dir

    with (
        patch.object(pipeline, "_execute_visual", return_value=job_dir / "output.mp4") as mock_visual,
        patch.object(pipeline, "_generate") as mock_generate,
        patch.object(pipeline, "_render") as mock_render,
        patch.object(pipeline.db, "update_job"),
    ):
        pipeline._execute(job_id, job_dir, ctx, upload=False)

    mock_visual.assert_called_once()
    mock_generate.assert_not_called()
    mock_render.assert_not_called()


def test_visual_pipeline_disabled_uses_legacy(tmp_path: Path) -> None:
    """When visual is disabled, _execute should use legacy audio pipeline."""
    from lyria_auto.config import AppConfig

    config = MagicMock(spec=AppConfig)
    config.section.return_value = {"enabled": False}
    config.settings = {"video": {"target_duration_minutes": 10}}
    config.root = tmp_path
    config.channels = None

    pipeline = Pipeline(config)

    job_id = 1
    job_dir = tmp_path / "job_000001"
    job_dir.mkdir()
    ctx = MagicMock()
    ctx.job_id = job_id
    ctx.job_dir = job_dir

    with (
        patch.object(pipeline, "_execute_visual") as mock_visual,
        patch.object(pipeline, "_generate", return_value=[tmp_path / "track.mp3"]) as mock_generate,
        patch.object(pipeline, "_render", return_value=job_dir / "output.mp4") as mock_render,
        patch.object(pipeline.db, "update_job"),
    ):
        pipeline._execute(job_id, job_dir, ctx, upload=False)

    mock_visual.assert_not_called()
    mock_generate.assert_called_once()
    mock_render.assert_called_once()


def test_review_assets_approves_scenes(tmp_path: Path) -> None:
    """Review assets should approve all scenes when approve=True."""
    from lyria_auto.config import AppConfig

    config = MagicMock(spec=AppConfig)
    config.section.return_value = {"enabled": False}
    config.settings = {"video": {"target_duration_minutes": 10}}
    config.root = tmp_path
    config.channels = None

    pipeline = Pipeline(config)

    job_id = 1
    job_dir = pipeline.workspace / f"job_{job_id:06d}"
    job_dir.mkdir(parents=True, exist_ok=True)

    visual_plan = VisualPlan(
        version=1,
        world=WorldBible(
            architecture="coffeehouse",
            materials="wood",
            color_palette="warm",
            time_of_day="afternoon",
            weather="sunny",
            window_view="garden",
            props="books",
        ),
        world_anchor_prompt="cozy coffeehouse with warm lighting",
        scenes=[
            ScenePlan(position=1, label="A", role="main", allowed_motion="subtle", image_prompt="window", motion_prompt="still"),
            ScenePlan(position=2, label="B", role="secondary", allowed_motion="moderate", image_prompt="books", motion_prompt="page_turn"),
        ],
        thumbnail_prompt="cozy coffeehouse",
    )

    from lyria_auto.utils import json_dump
    json_dump(job_dir / "visual_plan.json", visual_plan.to_dict())

    with patch.object(pipeline.db, "job", return_value={"id": job_id}):
        result = pipeline.review_assets(job_id, approve=True)

    assert result["job_id"] == job_id
    assert result["approved"] is True
    assert result["scenes_processed"] == 2


def test_regenerate_visual_regenerates_specific_scene(tmp_path: Path) -> None:
    """Regenerate visual should regenerate only the specified scene."""
    from lyria_auto.config import AppConfig

    config = MagicMock(spec=AppConfig)
    config.section.return_value = {"enabled": False}
    config.settings = {"video": {"target_duration_minutes": 10}}
    config.root = tmp_path
    config.channels = None

    pipeline = Pipeline(config)

    job_id = 1
    job_dir = pipeline.workspace / f"job_{job_id:06d}"
    job_dir.mkdir(parents=True, exist_ok=True)

    visual_plan = VisualPlan(
        version=1,
        world=WorldBible(
            architecture="coffeehouse",
            materials="wood",
            color_palette="warm",
            time_of_day="afternoon",
            weather="sunny",
            window_view="garden",
            props="books",
        ),
        world_anchor_prompt="cozy coffeehouse with warm lighting",
        scenes=[
            ScenePlan(position=1, label="A", role="main", allowed_motion="subtle", image_prompt="window", motion_prompt="still"),
            ScenePlan(position=2, label="B", role="secondary", allowed_motion="moderate", image_prompt="books", motion_prompt="page_turn"),
        ],
        thumbnail_prompt="cozy coffeehouse",
    )

    from lyria_auto.utils import json_dump
    json_dump(job_dir / "visual_plan.json", visual_plan.to_dict())

    with (
        patch.object(pipeline.db, "job", return_value={"id": job_id}),
        patch.object(pipeline, "_create_image_workflow"),
        patch.object(pipeline, "_create_video_workflow"),
    ):
        result = pipeline.regenerate_visual(job_id, scene_label="A")

    assert result["job_id"] == job_id
    assert result["scenes_regenerated"] == 1
    assert result["scene_labels"] == ["A"]


def test_report_metrics_returns_summary(tmp_path: Path) -> None:
    """Report metrics should return a metrics summary."""
    from lyria_auto.config import AppConfig

    config = MagicMock(spec=AppConfig)
    config.section.return_value = {"enabled": False}
    config.settings = {"video": {"target_duration_minutes": 10}}
    config.root = tmp_path
    config.channels = None

    pipeline = Pipeline(config)

    result = pipeline.report_metrics()
    assert isinstance(result, dict)
    assert "total_events" in result
    assert "total_cost_usd" in result

def test_exception_sanitization_in_workflow(tmp_path):
    from lyria_auto.db import StateDB
    from lyria_auto.visual_models import (
        PaidStageAuthorization,
        SideEffectLease,
        VisualPlan,
    )
    from lyria_auto.visual_repository import VisualRepository
    from lyria_auto.visual_workflow import ImageGenerationWorkflow
    
    db = StateDB(tmp_path / 'state.sqlite3')
    try:
        repo = VisualRepository(db)
        job_id = db.create_job('main', False, {})
        
        class FakeClient:
            def generate_image(self, *args, **kwargs):
                raise ValueError('API Error: Bearer my_secret_token_123 failed')
                
        lease = SideEffectLease(scope_type='job', scope_id=1, owner_token='abc', expires_at='2099', acquired=True)
        auth = PaidStageAuthorization(id=1, scope_type='job', scope_id=1, stage='img', kind='image', expected_count=1, allowed_count=1)
        wf = ImageGenerationWorkflow(repo, FakeClient(), image_model='test', max_outputs=1, unit_cost=0.1, pricing_snapshot={}, authorization=auth, lease=lease)
        
        plan = VisualPlan(version=1, world={}, world_anchor_prompt='world', thumbnail_prompt='thumb', scenes=[])
        
        import pytest
        with pytest.raises(ValueError):
            wf.generate_scene_images(job_id, tmp_path, plan)
            
        assets = repo.assets(job_id, 'world_anchor')
        assert len(assets) == 1
        error_msg = assets[0]['error']
        assert error_msg is not None
        assert 'Bearer ' not in error_msg, f'Secret leaked: {error_msg}'
        assert '[REDACTED]' in error_msg
    finally:
        db.close()
