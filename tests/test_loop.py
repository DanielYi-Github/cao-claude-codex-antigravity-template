from __future__ import annotations

from pathlib import Path

import pytest
from conftest import write_sine_audio

from lyria_auto.errors import MediaError
from lyria_auto.media.audio import combine_audio, extend_audio, loop_audio, probe_audio


def test_combine_audio_keeps_every_track_whole(tmp_path):
    """兩段 10 秒素材以 2 秒交叉淡化串接 → 18 秒。串接後不做任何裁切，
    所以長度必須是「素材總長 − 重疊」，不是某個外部指定的目標值。"""
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    write_sine_audio(a, duration=10.0)
    write_sine_audio(b, duration=10.0)
    out = tmp_path / "combined.m4a"

    combine_audio([a, b], out, crossfade_seconds=2.0)

    duration = float(probe_audio(out)["format"]["duration"])
    assert abs(duration - 18.0) <= 1.0


def test_loop_audio_uses_whole_copies_never_truncates(tmp_path):
    """60 秒素材、目標 200 秒：step = 58，round(200/58) = 3 份 → 3×60 − 2×2 = 176 秒。
    關鍵是 176 而不是 200 —— 舊實作會硬切在 200 秒，等於把第 4 份切一半。"""
    src = tmp_path / "src.mp3"
    write_sine_audio(src, duration=60.0)
    out = tmp_path / "looped.m4a"

    loop_audio(src, out, target_seconds=200.0, crossfade_seconds=2.0)

    duration = float(probe_audio(out)["format"]["duration"])
    assert abs(duration - 176.0) <= 1.0


def test_loop_audio_returns_source_when_one_copy_is_closest(tmp_path):
    """目標只比素材長一點點時，最接近的份數是 1 —— 不該為了湊長度硬塞第 2 份。"""
    src = tmp_path / "src.mp3"
    write_sine_audio(src, duration=60.0)
    out = tmp_path / "looped.m4a"

    result = loop_audio(src, out, target_seconds=70.0, crossfade_seconds=2.0)

    assert result == src
    assert not out.exists()


def test_extend_audio_keeps_material_longer_than_target_intact(tmp_path):
    """素材已經超過目標時，原樣沿用、不裁切 —— 使用者付費生成的曲目必須全部保留，
    最後一首要完整播完。這是本次行為變更的核心。"""
    src = tmp_path / "src.mp3"
    write_sine_audio(src, duration=60.0)
    out = tmp_path / "extended.m4a"

    result = extend_audio(src, out, target_seconds=45, crossfade_seconds=2.0)

    assert result == src
    assert not out.exists()
    duration = float(probe_audio(result)["format"]["duration"])
    assert abs(duration - 60.0) <= 1.0


def test_loop_audio_crossfades_via_combine_audio(tmp_path, monkeypatch):
    """test_loop_audio_produces_target_duration 只檢查總長度，舊的 -stream_loop -1 -t 200
    實作也會通過同一個斷言。這裡改成攔截 combine_audio 呼叫，確認 loop_audio 真的是靠
    重複素材＋crossfade_seconds 餵給 combine_audio（進而產生 acrossfade filter），
    而不是走 ffmpeg 的硬切 -stream_loop。若實作被還原成 -stream_loop，combine_audio
    根本不會被呼叫，這個測試會直接失敗。"""
    src = tmp_path / "src.mp3"
    write_sine_audio(src, duration=60.0)
    out = tmp_path / "looped.m4a"

    captured: dict = {}

    def fake_combine_audio(tracks, output_path, crossfade_seconds):
        captured["tracks"] = list(tracks)
        captured["crossfade_seconds"] = crossfade_seconds
        Path(output_path).write_bytes(b"fake-output")
        return Path(output_path)

    monkeypatch.setattr("lyria_auto.media.audio.combine_audio", fake_combine_audio)

    loop_audio(src, out, target_seconds=200.0, crossfade_seconds=2.0)

    assert captured["crossfade_seconds"] == 2.0
    # step = 60 - 2 = 58s per copy after crossfade overlap; round(200 / 58) = 3 copies
    assert len(captured["tracks"]) == 3
    assert all(Path(t) == src for t in captured["tracks"])


def test_loop_audio_ffmpeg_args_contain_acrossfade_filter(tmp_path, monkeypatch):
    """Complements test_loop_audio_crossfades_via_combine_audio: that test proves loop_audio
    delegates to combine_audio, but not that combine_audio itself still builds a real
    acrossfade filter graph (a revert to e.g. a plain concat would still "delegate" and pass
    that test). This one intercepts the actual ffmpeg invocation and asserts the
    -filter_complex value contains acrossfade with the expected number of -i inputs, and lets
    ffprobe calls (duration probing) pass through to the real run_command untouched."""
    import lyria_auto.media.audio as audio_mod

    src = tmp_path / "src.mp3"
    write_sine_audio(src, duration=60.0)
    out = tmp_path / "looped.m4a"

    real_run_command = audio_mod.run_command
    ffmpeg_calls: list[list[str]] = []

    def fake_run_command(args):
        if args[0] == "ffprobe":
            return real_run_command(args)
        ffmpeg_calls.append(args)
        Path(args[-1]).write_bytes(b"fake-output")
        return None

    monkeypatch.setattr("lyria_auto.media.audio.run_command", fake_run_command)

    loop_audio(src, out, target_seconds=200.0, crossfade_seconds=2.0)

    assert len(ffmpeg_calls) == 1
    args = ffmpeg_calls[0]
    assert args.count("-i") == 3  # round(200 / (60 - 2)) copies of the source
    filter_complex = args[args.index("-filter_complex") + 1]
    assert "acrossfade" in filter_complex
    # 不得再出現長度裁切：-t 會把最後一份切在半路
    assert "-t" not in args


def test_loop_audio_rejects_crossfade_longer_than_source(tmp_path):
    src = tmp_path / "src.mp3"
    write_sine_audio(src, duration=3.0)

    with pytest.raises(MediaError):
        loop_audio(src, tmp_path / "out.m4a", target_seconds=30.0, crossfade_seconds=5.0)
