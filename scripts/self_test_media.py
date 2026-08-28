import tempfile
from pathlib import Path

from lyria_auto.media.audio import (
    combine_audio,
    extend_audio,
    normalize_audio,
    validate_audio,
)
from lyria_auto.media.thumbnail import create_thumbnail
from lyria_auto.media.video import create_static_video
from lyria_auto.utils import run_command


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        tracks = []
        for i, freq in enumerate((440, 523), 1):
            raw = td / f"tone_{i}.wav"
            run_command(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=6", "-ac", "2", "-ar", "44100", str(raw)])
            validate_audio(raw, 5, 44100, True)
            tracks.append(normalize_audio(raw, td / f"tone_{i}.m4a", -16, -1.5))
        combined = combine_audio(tracks, td / "combined.m4a", 1)
        extended = extend_audio(combined, td / "extended.m4a", 15)
        thumb = create_thumbnail(td / "thumbnail.jpg", "SELF TEST", "Local media pipeline", 1280, 720, 88, "assets/backgrounds", 1)
        video = create_static_video(thumb, extended, td / "self_test.mp4", 1280, 720, 1, "128k", "ultrafast")
        assert video.exists() and video.stat().st_size > 10000
        print(f"PASS: {video} ({video.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
