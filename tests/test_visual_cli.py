"""Tests for visual CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lyria_auto.cli import main


def test_review_command_approves(tmp_path: Path) -> None:
    """Review command with --approve should call pipeline.review_assets with approve=True."""
    mock_config_obj = MagicMock()
    mock_config_obj.section.return_value = {}
    mock_config_obj.root = tmp_path

    mock_pipeline = MagicMock()
    mock_pipeline.review_assets.return_value = {"job_id": 1, "approved": True, "scenes_processed": 4}

    with patch("lyria_auto.cli.load_config", return_value=mock_config_obj), \
         patch("lyria_auto.cli.Pipeline", return_value=mock_pipeline):
        main(["--root", str(tmp_path), "review", "--job", "1", "--approve"])

    mock_pipeline.review_assets.assert_called_once_with(1, approve=True)


def test_review_command_rejects(tmp_path: Path) -> None:
    """Review command with --reject should call pipeline.review_assets with approve=False."""
    mock_config_obj = MagicMock()
    mock_config_obj.section.return_value = {}
    mock_config_obj.root = tmp_path

    mock_pipeline = MagicMock()
    mock_pipeline.review_assets.return_value = {"job_id": 1, "approved": False, "scenes_processed": 4}

    with patch("lyria_auto.cli.load_config", return_value=mock_config_obj), \
         patch("lyria_auto.cli.Pipeline", return_value=mock_pipeline):
        main(["--root", str(tmp_path), "review", "--job", "1", "--reject"])

    mock_pipeline.review_assets.assert_called_once_with(1, approve=False)


def test_review_command_requires_decision(tmp_path: Path) -> None:
    """Review command without --approve or --reject should fail."""
    mock_config_obj = MagicMock()
    mock_config_obj.section.return_value = {}
    mock_config_obj.root = tmp_path

    mock_pipeline = MagicMock()

    with patch("lyria_auto.cli.load_config", return_value=mock_config_obj), \
         patch("lyria_auto.cli.Pipeline", return_value=mock_pipeline), \
         pytest.raises(SystemExit):
        main(["--root", str(tmp_path), "review", "--job", "1"])


def test_regenerate_command_all_scenes(tmp_path: Path) -> None:
    """Regenerate command without --scene should regenerate all scenes."""
    mock_config_obj = MagicMock()
    mock_config_obj.section.return_value = {}
    mock_config_obj.root = tmp_path

    mock_pipeline = MagicMock()
    mock_pipeline.regenerate_visual.return_value = {"job_id": 1, "scenes_regenerated": 4}

    with patch("lyria_auto.cli.load_config", return_value=mock_config_obj), \
         patch("lyria_auto.cli.Pipeline", return_value=mock_pipeline):
        main(["--root", str(tmp_path), "regenerate", "--job", "1"])

    mock_pipeline.regenerate_visual.assert_called_once_with(1, None)


def test_regenerate_command_specific_scene(tmp_path: Path) -> None:
    """Regenerate command with --scene should regenerate only that scene."""
    mock_config_obj = MagicMock()
    mock_config_obj.section.return_value = {}
    mock_config_obj.root = tmp_path

    mock_pipeline = MagicMock()
    mock_pipeline.regenerate_visual.return_value = {"job_id": 1, "scenes_regenerated": 1}

    with patch("lyria_auto.cli.load_config", return_value=mock_config_obj), \
         patch("lyria_auto.cli.Pipeline", return_value=mock_pipeline):
        main(["--root", str(tmp_path), "regenerate", "--job", "1", "--scene", "A"])

    mock_pipeline.regenerate_visual.assert_called_once_with(1, "A")


def test_report_command_all_jobs(tmp_path: Path) -> None:
    """Report command without --job should report metrics for all jobs."""
    mock_config_obj = MagicMock()
    mock_config_obj.section.return_value = {}
    mock_config_obj.root = tmp_path

    mock_pipeline = MagicMock()
    mock_pipeline.report_metrics.return_value = {"total_events": 10}

    with patch("lyria_auto.cli.load_config", return_value=mock_config_obj), \
         patch("lyria_auto.cli.Pipeline", return_value=mock_pipeline):
        main(["--root", str(tmp_path), "report"])

    mock_pipeline.report_metrics.assert_called_once_with(None)


def test_report_command_specific_job(tmp_path: Path) -> None:
    """Report command with --job should report metrics for that job only."""
    mock_config_obj = MagicMock()
    mock_config_obj.section.return_value = {}
    mock_config_obj.root = tmp_path

    mock_pipeline = MagicMock()
    mock_pipeline.report_metrics.return_value = {"total_events": 5}

    with patch("lyria_auto.cli.load_config", return_value=mock_config_obj), \
         patch("lyria_auto.cli.Pipeline", return_value=mock_pipeline):
        main(["--root", str(tmp_path), "report", "--job", "42"])

    mock_pipeline.report_metrics.assert_called_once_with(42)
