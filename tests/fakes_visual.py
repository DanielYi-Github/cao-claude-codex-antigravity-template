from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (160, 90), (80, 60, 40)).save(buffer, "PNG")
    return buffer.getvalue()


class FakeVisualSDK:
    def __init__(self):
        self.image_calls = 0
        self.video_starts = 0
        self.video_polls = 0
        self.operations_seen: list[str] = []
        self.interactions = SimpleNamespace(create=self._create_image)
        self.models = SimpleNamespace(generate_videos=self._start_video)
        self.operations = SimpleNamespace(get=self._poll_video)
        self.files = SimpleNamespace(download=self._download)

    def _create_image(self, **kwargs):
        self.image_calls += 1
        return SimpleNamespace(
            output_image=SimpleNamespace(
                data=base64.b64encode(png_bytes()).decode("ascii"),
                mime_type="image/png",
            )
        )

    def _start_video(self, **kwargs):
        self.video_starts += 1
        return SimpleNamespace(name="operations/video-1", done=False)

    def _poll_video(self, operation):
        self.video_polls += 1
        self.operations_seen.append(operation.name)
        video = SimpleNamespace(save=lambda path: Path(path).write_bytes(b"fake-mp4"))
        return SimpleNamespace(
            name=operation.name,
            done=True,
            error=None,
            response=SimpleNamespace(
                generated_videos=[SimpleNamespace(video=video)]
            ),
        )

    def _download(self, *, file):
        return None
