from __future__ import annotations

from pathlib import Path

from ..utils import run_command
from .audio import probe_audio


def create_static_video(
    image_path: str | Path,
    audio_path: str | Path,
    output_path: str | Path,
    width: int,
    height: int,
    fps: int,
    audio_bitrate: str,
    preset: str,
) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path), "-i", str(audio_path),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
        "-r", str(fps), "-c:v", "libx264", "-preset", preset, "-tune", "stillimage",
        "-c:a", "aac", "-b:a", audio_bitrate, "-pix_fmt", "yuv420p", "-shortest",
        "-movflags", "+faststart",
    ]
    # -shortest 不夠：低 fps 下 x264 的 lookahead 佇列會被沖出，導致視訊比音訊長數十秒。
    duration = float(probe_audio(audio_path).get("format", {}).get("duration") or 0)
    if duration > 0:
        args += ["-t", f"{duration:.3f}"]
    run_command(args + [str(out)])
    return out
