"""Tests for render preflight checks."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lyria_auto.errors import MediaError
from lyria_auto.media.render_preflight import require_render_ready


def test_require_render_ready_missing_ffmpeg(tmp_path: Path) -> None:
    """When ffmpeg is not found, the report should have an error."""
    with (
        patch("shutil.which", return_value=None),
        patch("lyria_auto.media.render_preflight._check_encoder"),
        patch("lyria_auto.media.render_preflight._estimate_required_bytes", return_value=1_000_000),
        patch("shutil.disk_usage", return_value=MagicMock(total=10_000_000_000, free=500_000)),
        patch("lyria_auto.media.render_preflight._run_smoke_test"),
    ):
        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"dummy")
        with pytest.raises(MediaError) as exc_info:
            require_render_ready(
                audio_path=audio_path,
                output_path=tmp_path / "output.mp4",
                duration_seconds=3600,
                width=1920,
                height=1080,
                fps=30,
            )

    assert "FFMPEG_NOT_FOUND" in str(exc_info.value)


def test_require_render_ready_missing_xfade(tmp_path: Path) -> None:
    """When xfade filter is not available, require_render_ready should raise MediaError."""
    with (
        patch("lyria_auto.media.render_preflight._find_ffmpeg", return_value=Path("ffmpeg")),
        patch("lyria_auto.media.render_preflight._get_ffmpeg_version", return_value="6.0"),
        patch("lyria_auto.media.render_preflight._check_xfade_filter", return_value=False),
        pytest.raises(MediaError) as exc_info,
    ):
        require_render_ready(
            audio_path=tmp_path / "audio.mp3",
            output_path=tmp_path / "output.mp4",
            duration_seconds=3600,
            width=1920,
            height=1080,
            fps=30,
        )

    assert "RENDER_FILTER_MISSING" in str(exc_info.value)


def test_require_render_ready_insufficient_disk_space(tmp_path: Path) -> None:
    """When disk space is insufficient, the report should have an error."""
    with (
        patch("lyria_auto.media.render_preflight._find_ffmpeg", return_value=Path("ffmpeg")),
        patch("lyria_auto.media.render_preflight._get_ffmpeg_version", return_value="6.0"),
        patch("lyria_auto.media.render_preflight._check_xfade_filter"),
        patch("lyria_auto.media.render_preflight._check_encoder"),
        patch("lyria_auto.media.render_preflight._estimate_required_bytes", return_value=10_000_000_000),
        patch("shutil.disk_usage", return_value=MagicMock(total=1_000_000, free=500_000)),
        patch("lyria_auto.media.render_preflight._run_smoke_test"),
    ):
        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"dummy")
        with pytest.raises(MediaError) as exc_info:
            require_render_ready(
                audio_path=audio_path,
                output_path=tmp_path / "output.mp4",
                duration_seconds=3600,
                width=1920,
                height=1080,
                fps=30,
            )

    assert "INSUFFICIENT_DISK_SPACE" in str(exc_info.value)


def test_require_render_ready_success(tmp_path: Path) -> None:
    """When all checks pass, the report should be ok."""
    with (
        patch("lyria_auto.media.render_preflight._find_ffmpeg", return_value=Path("ffmpeg")),
        patch("lyria_auto.media.render_preflight._get_ffmpeg_version", return_value="6.0"),
        patch("lyria_auto.media.render_preflight._check_xfade_filter", return_value=True),
        patch("lyria_auto.media.render_preflight._check_encoder", return_value=True),
        patch("lyria_auto.media.render_preflight._estimate_required_bytes", return_value=1_000_000),
        patch("shutil.disk_usage", return_value=MagicMock(total=10_000_000_000, free=8_000_000_000)),
        patch("lyria_auto.media.render_preflight._run_smoke_test"),
    ):
        audio_path = tmp_path / "audio.mp3"
        audio_path.write_bytes(b"dummy")
        report = require_render_ready(
            audio_path=audio_path,
            output_path=tmp_path / "output.mp4",
            duration_seconds=3600,
            width=1920,
            height=1080,
            fps=30,
        )

    assert report.has_xfade is True
    assert report.ffmpeg_version == "6.0"
