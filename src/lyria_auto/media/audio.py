from __future__ import annotations

import json
from pathlib import Path

from ..errors import MediaError
from ..utils import run_command


def probe_audio(path: str | Path) -> dict:
    result = run_command([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ])
    return json.loads(result.stdout)


def validate_audio(path: str | Path, minimum_duration: float, minimum_sample_rate: int, require_stereo: bool) -> dict:
    data = probe_audio(path)
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise MediaError(f"找不到音訊串流：{path}")
    stream = audio_streams[0]
    duration = float(data.get("format", {}).get("duration") or stream.get("duration") or 0)
    sample_rate = int(stream.get("sample_rate") or 0)
    channels = int(stream.get("channels") or 0)
    if duration < minimum_duration:
        raise MediaError(f"音訊過短：{duration:.1f}s < {minimum_duration}s")
    if sample_rate < minimum_sample_rate:
        raise MediaError(f"取樣率過低：{sample_rate} < {minimum_sample_rate}")
    if require_stereo and channels < 2:
        raise MediaError(f"需要立體聲，但 channels={channels}")
    return {"duration": duration, "sample_rate": sample_rate, "channels": channels}


def normalize_audio(input_path: str | Path, output_path: str | Path, lufs: float, true_peak: float) -> Path:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    run_command([
        "ffmpeg", "-y", "-i", str(input_path),
        "-af", f"loudnorm=I={lufs}:TP={true_peak}:LRA=11",
        "-ar", "48000", "-ac", "2", "-b:a", "256k", str(out)
    ])
    return out


def combine_audio(
    tracks: list[str | Path],
    output_path: str | Path,
    crossfade_seconds: float = 2.0,
) -> Path:
    """交叉淡化串接多段音訊。刻意不提供長度裁切參數：成品長度一律由完整曲目決定，
    絕不從中間切斷某一首（見 extend_audio / loop_audio 的說明）。"""
    if not tracks:
        raise MediaError("沒有可合併的音訊")
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if len(tracks) == 1:
        run_command(["ffmpeg", "-y", "-i", str(tracks[0]), "-c:a", "aac", "-b:a", "256k", str(out)])
        return out

    args = ["ffmpeg", "-y"]
    for track in tracks:
        args += ["-i", str(track)]
    filters = []
    for i in range(len(tracks)):
        filters.append(f"[{i}:a]aresample=48000,asetpts=PTS-STARTPTS[a{i}]")
    current = "a0"
    for i in range(1, len(tracks)):
        output_label = f"xf{i}"
        filters.append(f"[{current}][a{i}]acrossfade=d={crossfade_seconds}:c1=tri:c2=tri[{output_label}]")
        current = output_label
    args += ["-filter_complex", ";".join(filters), "-map", f"[{current}]", "-c:a", "aac", "-b:a", "256k", str(out)]
    run_command(args)
    return out


def loop_audio(
    input_path: str | Path,
    output_path: str | Path,
    target_seconds: float,
    crossfade_seconds: float = 2.0,
) -> Path:
    """把素材以交叉淡化循環到接近 target_seconds。

    份數取「最接近目標」的整數（round，不是 ceil），且**不做任何裁切** ——
    成品一定是完整份數的長度，可能略短或略長於目標。這是刻意的：寧可長度有幾十秒
    誤差，也不要讓最後一首在半路被切斷。份數算下來是 1 時直接回傳原素材。
    """
    info = probe_audio(input_path)
    duration = float(info.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise MediaError(f"無法讀取音訊長度：{input_path}")
    if crossfade_seconds >= duration:
        raise MediaError(f"交叉淡化秒數（{crossfade_seconds}）必須小於素材長度（{duration:.1f}s）")
    step = duration - crossfade_seconds
    copies = max(1, round(target_seconds / step))
    if copies <= 1:
        return Path(input_path)
    return combine_audio([input_path] * copies, output_path, crossfade_seconds)


def extend_audio(
    input_path: str | Path,
    output_path: str | Path,
    target_seconds: int,
    crossfade_seconds: float = 2.0,
) -> Path:
    """把串接好的素材調整到接近目標長度。

    素材已經夠長時直接原樣沿用，**不裁切** —— 使用者付費生成的曲目全部保留，
    最後一首會完整播完，成品長度因此會略超過設定值。素材不夠長才做循環補足。
    """
    info = probe_audio(input_path)
    duration = float(info.get("format", {}).get("duration") or 0)
    if duration >= target_seconds:
        return Path(input_path)
    return loop_audio(input_path, Path(output_path), float(target_seconds), crossfade_seconds)
