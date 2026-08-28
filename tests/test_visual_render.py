from __future__ import annotations

import subprocess
from pathlib import Path

from lyria_auto.media.timeline import create_timeline_video, probe_video


def make_loop(path: Path, color: str):
    """建立 2 秒純色循環影片。"""
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c={color}:s=320x180:r=24:d=2",
            "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
        capture_output=True,
    )


def make_audio(path: Path, duration: float):
    """建立測試音訊。"""
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-ac", "2", "-c:a", "aac", str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_short_timeline_video_matches_audio_and_has_no_loop_audio(tmp_path: Path):
    """驗證渲染結果與音訊長度一致，且只有一個 video/audio stream。"""
    a = tmp_path / "a.mp4"
    b = tmp_path / "b.mp4"
    audio = tmp_path / "audio.m4a"
    output = tmp_path / "output.mp4"
    make_loop(a, "red")
    make_loop(b, "blue")
    make_audio(audio, 7)

    create_timeline_video(
        [("A", a), ("B", b)],
        audio,
        output,
        width=320,
        height=180,
        fps=24,
        audio_bitrate="128k",
        preset="veryfast",
        interval_seconds=2,
    )

    info = probe_video(output)
    duration = float(info["format"]["duration"])
    streams = info["streams"]
    assert abs(duration - 7.0) <= (1 / 24)
    assert len([s for s in streams if s["codec_type"] == "video"]) == 1
    assert len([s for s in streams if s["codec_type"] == "audio"]) == 1


def test_renderer_reuses_four_paths_for_ten_slices(monkeypatch, tmp_path: Path):
    """驗證 5 小時影片使用 A B C D A B C D A B 的場景順序。"""
    captured = {}

    def fake_run(args, *, on_progress=None):
        captured["args"] = args
        Path(args[-1]).write_bytes(b"fake mp4")

    monkeypatch.setattr(
        "lyria_auto.media.audio.probe_audio",
        lambda path: {"format": {"duration": str(5 * 60 * 60)}},
    )
    monkeypatch.setattr(
        "lyria_auto.media.render_preflight.require_render_ready",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "lyria_auto.media.timeline.verify_render",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "lyria_auto.media.timeline.run_command", fake_run,
    )

    create_timeline_video(
        [("A", "a.mp4"), ("B", "b.mp4"), ("C", "c.mp4"), ("D", "d.mp4")],
        "audio.m4a",
        tmp_path / "out.mp4",
        width=1920,
        height=1080,
        fps=24,
        audio_bitrate="256k",
        preset="veryfast",
    )

    args = captured["args"]
    inputs = [args[index + 1] for index, value in enumerate(args) if value == "-i"]
    assert inputs[:-1] == [
        "a.mp4", "b.mp4", "c.mp4", "d.mp4",
        "a.mp4", "b.mp4", "c.mp4", "d.mp4",
        "a.mp4", "b.mp4",
    ]
    graph = args[args.index("-filter_complex") + 1]
    assert "concat=" not in graph
    assert graph.count("xfade=") == 9
    assert "duration=2:offset=1799.000" in graph
    assert "duration=2:offset=16199.000" in graph
    assert args[args.index("-t") + 1] == "18000.000"
    assert str(tmp_path / "out.partial.mp4") == args[-1]


def test_timeline_video_45_minutes_command(monkeypatch, tmp_path: Path):
    """驗證 45 分鐘影片的 FFmpeg 命令結構。"""
    captured = {}

    def fake_run(args, *, on_progress=None):
        captured["args"] = args
        Path(args[-1]).write_bytes(b"fake mp4")

    monkeypatch.setattr(
        "lyria_auto.media.audio.probe_audio",
        lambda path: {"format": {"duration": str(45 * 60)}},
    )
    monkeypatch.setattr(
        "lyria_auto.media.render_preflight.require_render_ready",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "lyria_auto.media.timeline.verify_render",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "lyria_auto.media.timeline.run_command", fake_run,
    )

    create_timeline_video(
        [("A", "a.mp4"), ("B", "b.mp4")],
        "audio.m4a",
        tmp_path / "out.mp4",
        width=1920,
        height=1080,
        fps=24,
        audio_bitrate="256k",
        preset="veryfast",
    )

    args = captured["args"]
    graph = args[args.index("-filter_complex") + 1]
    assert graph.count("xfade=") == 1
    assert "duration=2:offset=1799.000" in graph
    assert args[args.index("-t") + 1] == "2700.000"


def test_timeline_video_2_hours_command(monkeypatch, tmp_path: Path):
    """驗證 2 小時影片的 FFmpeg 命令結構。"""
    captured = {}

    def fake_run(args, *, on_progress=None):
        captured["args"] = args
        Path(args[-1]).write_bytes(b"fake mp4")

    monkeypatch.setattr(
        "lyria_auto.media.audio.probe_audio",
        lambda path: {"format": {"duration": str(2 * 60 * 60)}},
    )
    monkeypatch.setattr(
        "lyria_auto.media.render_preflight.require_render_ready",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "lyria_auto.media.timeline.verify_render",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "lyria_auto.media.timeline.run_command", fake_run,
    )

    create_timeline_video(
        [("A", "a.mp4"), ("B", "b.mp4"), ("C", "c.mp4"), ("D", "d.mp4")],
        "audio.m4a",
        tmp_path / "out.mp4",
        width=1920,
        height=1080,
        fps=24,
        audio_bitrate="256k",
        preset="veryfast",
    )

    args = captured["args"]
    graph = args[args.index("-filter_complex") + 1]
    assert graph.count("xfade=") == 3
    assert "duration=2:offset=1799.000" in graph
    assert "duration=2:offset=3599.000" in graph
    assert "duration=2:offset=5399.000" in graph
    assert args[args.index("-t") + 1] == "7200.000"
