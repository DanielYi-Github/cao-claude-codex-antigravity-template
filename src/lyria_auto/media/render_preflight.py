from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import MediaError, StructuredError
from ..utils import run_command

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderPreflightReport:
    """Render 前置檢查報告。"""
    ffmpeg_path: Path
    ffmpeg_version: str
    video_encoder: str
    audio_encoder: str
    has_xfade: bool
    required_bytes: int
    available_bytes: int
    temp_directory: Path
    output_directory: Path


def _find_ffmpeg() -> Path:
    path = shutil.which("ffmpeg")
    if path is None:
        raise MediaError(
            structured_error(
                code="FFMPEG_NOT_FOUND",
                problem="找不到 ffmpeg",
                cause="ffmpeg 不在 PATH 中",
                fix="安裝 ffmpeg：brew install ffmpeg (macOS) 或 choco install ffmpeg (Windows)",
                next_command="lyria-auto doctor --media",
            )
        )
    return Path(path)


def _get_ffmpeg_version(ffmpeg_path: Path) -> str:
    result = run_command([str(ffmpeg_path), "-version"], check=False)
    first_line = result.stdout.split("\n")[0] if result.stdout else ""
    return first_line.replace("ffmpeg version", "").strip()


def _check_xfade_filter(ffmpeg_path: Path) -> bool:
    result = run_command([str(ffmpeg_path), "-filters"], check=False)
    return "xfade" in result.stdout


def _check_encoder(ffmpeg_path: Path, encoder: str) -> bool:
    if "video" in encoder or encoder == "libx264":
        result = run_command([str(ffmpeg_path), "-encoders"], check=False)
        return encoder in result.stdout
    result = run_command([str(ffmpeg_path), "-encoders"], check=False)
    return encoder in result.stdout


def _estimate_required_bytes(
    duration_seconds: float,
    width: int,
    height: int,
    fps: int,
) -> int:
    """保守估計渲染所需空間（video + audio + 20% overhead）。"""
    # 假設 video bitrate ≈ width * height * fps * 0.1 bits/pixel
    video_bitrate_bps = width * height * fps * 0.1
    audio_bitrate_bps = 256_000  # 256 kbps AAC
    total_bytes = int((video_bitrate_bps + audio_bitrate_bps) * duration_seconds / 8)
    return int(total_bytes * 1.2)  # 20% overhead


def _run_smoke_test(ffmpeg_path: Path, temp_dir: Path) -> None:
    """執行 3 秒、兩色、含 2 秒 xfade 的 lavfi smoke test。"""
    smoke_output = temp_dir / "smoke_test.mp4"
    args = [
        str(ffmpeg_path), "-y",
        "-f", "lavfi", "-i", "color=c=red:s=320x180:r=10:d=2",
        "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=10:d=2",
        "-filter_complex",
        ("[0:v]trim=duration=2,setpts=PTS-STARTPTS[v0];"
         "[1:v]trim=duration=2,setpts=PTS-STARTPTS[v1];"
         "[v0][v1]xfade=transition=fade:duration=0.2:offset=1.8[out]"),
        "-map", "[out]",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-t", "3",
        str(smoke_output),
    ]
    run_command(args, check=False)
    if not smoke_output.exists() or smoke_output.stat().st_size == 0:
        raise MediaError(
            structured_error(
                code="RENDER_SMOKE_FAILED",
                problem="FFmpeg smoke test 失敗",
                cause="xfade filter 無法產生有效輸出",
                fix="檢查 ffmpeg 版本與 xfade filter 支援",
                next_command="lyria-auto doctor --media",
            )
        )


def structured_error(**kwargs: Any) -> StructuredError:
    """建立 StructuredError。"""
    return StructuredError(
        code=kwargs.get("code", "UNKNOWN"),
        problem=kwargs.get("problem", ""),
        cause=kwargs.get("cause", ""),
        fix=kwargs.get("fix", ""),
        next_command=kwargs.get("next_command", ""),
        job_id=kwargs.get("job_id"),
    )


def require_render_ready(
    *,
    audio_path: Path,
    output_path: Path,
    duration_seconds: float,
    width: int,
    height: int,
    fps: int,
) -> RenderPreflightReport:
    """執行所有 render 前置檢查。"""
    ffmpeg_path = _find_ffmpeg()
    ffmpeg_version = _get_ffmpeg_version(ffmpeg_path)

    # Check xfade filter
    has_xfade = _check_xfade_filter(ffmpeg_path)
    if not has_xfade:
        raise MediaError(
            structured_error(
                code="RENDER_FILTER_MISSING",
                problem="ffmpeg 缺少 xfade filter",
                cause="xfade filter 不在 ffmpeg -filters 輸出中",
                fix="升級 ffmpeg 到 4.3+ 版本",
                next_command="lyria-auto doctor --media",
            )
        )

    # Check encoders
    video_encoder = "libx264"
    audio_encoder = "aac"
    if not _check_encoder(ffmpeg_path, video_encoder):
        raise MediaError(
            structured_error(
                code="VIDEO_ENCODER_MISSING",
                problem=f"ffmpeg 缺少 {video_encoder} 編碼器",
                cause=f"{video_encoder} 不在 ffmpeg -encoders 輸出中",
                fix="重新編譯 ffmpeg 並啟用 libx264",
                next_command="lyria-auto doctor --media",
            )
        )
    if not _check_encoder(ffmpeg_path, audio_encoder):
        raise MediaError(
            structured_error(
                code="AUDIO_ENCODER_MISSING",
                problem=f"ffmpeg 缺少 {audio_encoder} 編碼器",
                cause=f"{audio_encoder} 不在 ffmpeg -encoders 輸出中",
                fix="重新編譯 ffmpeg 並啟用 AAC 編碼器",
                next_command="lyria-auto doctor --media",
            )
        )

    # Check disk space
    required_bytes = _estimate_required_bytes(duration_seconds, width, height, fps)
    output_dir = output_path.parent
    temp_dir = Path(Path.cwd() / "workspace" / "temp")
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        usage = shutil.disk_usage(str(output_dir))
        available_bytes = usage.free
    except OSError:
        available_bytes = 0

    if required_bytes > available_bytes:
        raise MediaError(
            structured_error(
                code="INSUFFICIENT_DISK_SPACE",
                problem="磁碟空間不足",
                cause=f"需要 {required_bytes / 1_000_000:.1f} MB，可用 {available_bytes / 1_000_000:.1f} MB",
                fix="釋放磁碟空間或減少影片長度",
                next_command="lyria-auto report --job JOB_ID",
            )
        )

    # Check write permissions
    if not output_dir.exists():
        output_dir.mkdir(parents=True, exist_ok=True)
    test_file = output_dir / ".write_test"
    try:
        test_file.write_bytes(b"")
        test_file.unlink()
    except OSError:
        raise MediaError(
            structured_error(
                code="OUTPUT_DIR_NOT_WRITABLE",
                problem="輸出目錄不可寫",
                cause=f"無法在 {output_dir} 建立測試檔案",
                fix="檢查目錄權限",
                next_command="lyria-auto doctor --media",
            )
        )

    # Check audio input is readable
    if not audio_path.exists():
        raise MediaError(
            structured_error(
                code="AUDIO_INPUT_MISSING",
                problem="音訊輸入檔案不存在",
                cause=str(audio_path),
                fix="確認音訊生成階段已完成",
                next_command="lyria-auto resume --job JOB_ID",
            )
        )

    # Run smoke test (only once per process, cached in module-level variable)
    if not hasattr(require_render_ready, "_smoke_tested"):
        _run_smoke_test(ffmpeg_path, temp_dir)
        require_render_ready._smoke_tested = True  # type: ignore[attr-defined]

    return RenderPreflightReport(
        ffmpeg_path=ffmpeg_path,
        ffmpeg_version=ffmpeg_version,
        video_encoder=video_encoder,
        audio_encoder=audio_encoder,
        has_xfade=has_xfade,
        required_bytes=required_bytes,
        available_bytes=available_bytes,
        temp_directory=temp_dir,
        output_directory=output_dir,
    )
