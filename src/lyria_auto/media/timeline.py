from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ..utils import run_command
from ..visual_models import TimelineSlice

logger = logging.getLogger(__name__)


def build_timeline(
    total_seconds: float,
    scenes: list[tuple[str, str | Path]],
    *,
    interval_seconds: float = 1800,
) -> list[TimelineSlice]:
    """建立重複場景的時間軸。

    每個場景在 30 分鐘（預設）邊界處切換，相鄰場景有 2 秒重疊供 xfade 使用。
    對任一內部邊界 B，前一 slice 結束於 B+1、下一 slice 開始於 B-1。
    """
    if total_seconds <= 0:
        raise ValueError("影片長度必須大於 0")
    if not scenes:
        raise ValueError("至少需要一個核准場景")
    if interval_seconds <= 0:
        raise ValueError("換景間隔必須大於 0")

    result: list[TimelineSlice] = []
    index = 0
    logical_start = 0.0

    while logical_start < total_seconds:
        logical_end = min(logical_start + interval_seconds, total_seconds)
        label, path = scenes[index % len(scenes)]

        # 內部邊界：前一 slice 結束於 B+1，下一 slice 開始於 B-1
        global_start = logical_start - 1.0 if logical_start > 0 else 0.0
        global_end = logical_end + 1.0 if logical_end < total_seconds else total_seconds

        result.append(
            TimelineSlice(label, Path(path), global_start, global_end)
        )
        logical_start = logical_end
        index += 1

    return result


def transition_offsets(timeline: list[TimelineSlice]) -> list[float]:
    """回傳每個邊界的 xfade offset（即 B-1，邊界中央為 B）。"""
    return [item.global_start_seconds for item in timeline[1:]]


def probe_video(path: str | Path) -> dict[str, Any]:
    """使用 ffprobe 取得影片資訊。"""
    result = run_command([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ])
    return json.loads(result.stdout)


def create_timeline_video(
    scenes: list[tuple[str, str | Path]],
    audio_path: str | Path,
    output_path: str | Path,
    *,
    width: int,
    height: int,
    fps: int,
    audio_bitrate: str,
    preset: str,
    interval_seconds: float = 1800,
) -> Path:
    """將核准的循環影片依時間軸渲染為長片。

    使用 chained xfade 在每個 30 分鐘邊界執行 2 秒過渡，
    最終長度與音訊時長一致。
    """
    from .audio import probe_audio
    from .render_preflight import require_render_ready

    duration = float(
        probe_audio(audio_path).get("format", {}).get("duration") or 0
    )
    timeline = build_timeline(
        duration, scenes, interval_seconds=interval_seconds
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f"{output.stem}.partial{output.suffix}")

    require_render_ready(
        audio_path=Path(audio_path),
        output_path=output,
        duration_seconds=duration,
        width=width,
        height=height,
        fps=fps,
    )

    # Build FFmpeg command with chained xfade
    args = ["ffmpeg", "-y"]
    for item in timeline:
        args += ["-stream_loop", "-1", "-i", str(item.path)]

    audio_index = len(timeline)
    args += ["-i", str(audio_path)]

    filters = []
    for index, item in enumerate(timeline):
        label = f"v{index}"
        filters.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps},trim=duration={item.duration_seconds:.3f},"
            f"setpts=PTS-STARTPTS,format=yuv420p[{label}]"
        )

    current = "v0"
    for index, offset in enumerate(transition_offsets(timeline), start=1):
        next_label = f"vx{index}"
        filters.append(
            f"[{current}][v{index}]"
            f"xfade=transition=fade:duration=2:offset={offset:.3f}"
            f"[{next_label}]"
        )
        current = next_label

    args += [
        "-filter_complex", ";".join(filters),
        "-map", f"[{current}]", "-map", f"{audio_index}:a:0",
        "-c:v", "libx264", "-preset", preset,
        "-c:a", "aac", "-b:a", audio_bitrate,
        "-pix_fmt", "yuv420p", "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", str(partial),
    ]

    run_command(args)
    verify_render(partial, expected_duration=duration, max_error_frames=1, fps=fps)
    partial.replace(output)
    return output


def verify_render(
    path: Path,
    *,
    expected_duration: float,
    max_error_frames: int = 1,
    fps: int = 24,
) -> None:
    """驗證渲染結果：音視訊串流存在、長度與預期誤差在容許範圍內。"""
    info = probe_video(path)
    actual_duration = float(info.get("format", {}).get("duration") or 0)
    streams = info.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        raise RuntimeError(f"渲染結果缺少視訊串流：{path}")
    if not audio_streams:
        raise RuntimeError(f"渲染結果缺少音訊串流：{path}")

    max_error_seconds = max_error_frames / fps
    if abs(actual_duration - expected_duration) > max_error_seconds:
        raise RuntimeError(
            f"渲染長度誤差過大：{actual_duration:.3f}s != {expected_duration:.3f}s "
            f"(容許 {max_error_seconds:.3f}s)"
        )
