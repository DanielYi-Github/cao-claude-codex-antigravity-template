from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from ..errors import (
    PaidStartUncertainError,
    VisualGenerationError,
    VisualPollTransientError,
)
from ..utils import run_command, sha256_file
from ..visual_models import GeneratedImage, VideoPoll


class GeminiVisualClient:
    def __init__(
        self,
        *,
        api_key: str | None,
        image_model: str,
        video_model: str,
        sdk_client: Any | None = None,
        video_validator: Callable[[Path], None] | None = None,
    ):
        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key and sdk_client is None:
            raise VisualGenerationError("尚未設定 GEMINI_API_KEY")
        if sdk_client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise VisualGenerationError("尚未安裝 google-genai") from exc
            sdk_client = genai.Client(api_key=key)
        self.client = sdk_client
        self.image_model = image_model
        self.video_model = video_model
        self.video_validator = video_validator or self._validate_video

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status is None:
            status = getattr(exc, "code", None)
        for candidate in (status, getattr(status, "value", None)):
            try:
                code = int(candidate)
            except (TypeError, ValueError):
                continue
            return code == 429 or 500 <= code <= 599
        return str(getattr(status, "name", status)).upper() in {
            "RESOURCE_EXHAUSTED",
            "UNAVAILABLE",
            "INTERNAL",
        }

    def _paid_start(self, call: Callable[[], Any]) -> Any:
        try:
            return call()
        except Exception as exc:
            if self._is_transient(exc):
                raise PaidStartUncertainError(
                    "付費 start 的回應不確定；本次不得自動重試，"
                    "請保留 run／asset ID 並人工 reconcile"
                ) from exc
            raise

    @staticmethod
    def _validate_video(path: Path) -> None:
        result = run_command(
            [
                "ffprobe", "-v", "error", "-show_streams", "-show_format",
                "-of", "json", str(path),
            ]
        )
        payload = json.loads(result.stdout)
        streams = [
            stream for stream in payload.get("streams", [])
            if stream.get("codec_type") == "video"
        ]
        duration = float(payload.get("format", {}).get("duration") or 0)
        if len(streams) != 1 or duration <= 0:
            raise VisualGenerationError("Veo 原始影片無法通過 FFprobe")

    @staticmethod
    def _atomic_bytes(output_path: Path, data: bytes) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial = output_path.with_name(
            output_path.stem + ".partial" + output_path.suffix
        )
        partial.write_bytes(data)
        partial.replace(output_path)
        return output_path

    def generate_image(
        self,
        prompt: str,
        output_path: str | Path,
        *,
        reference_paths: list[str | Path],
    ) -> GeneratedImage:
        inputs: list[dict[str, str]] = [{"type": "text", "text": prompt}]
        for path in reference_paths:
            ref = Path(path)
            mime = "image/png" if ref.suffix.lower() == ".png" else "image/jpeg"
            inputs.append(
                {
                    "type": "image",
                    "data": base64.b64encode(ref.read_bytes()).decode("ascii"),
                    "mime_type": mime,
                }
            )
        try:
            interaction = self._paid_start(
                lambda: self.client.interactions.create(
                    model=self.image_model,
                    input=inputs,
                    response_format={
                        "type": "image",
                        "mime_type": "image/jpeg",
                        "aspect_ratio": "16:9",
                        "image_size": "2K",
                    },
                )
            )
            output = getattr(interaction, "output_image", None)
            if output is None or getattr(output, "data", None) is None:
                raise VisualGenerationError("Gemini 圖片回應沒有 output_image")
            data = base64.b64decode(output.data)
            path = self._atomic_bytes(Path(output_path), data)
            with Image.open(path) as image:
                image.verify()
            return GeneratedImage(
                path=path,
                mime_type=getattr(output, "mime_type", None) or "image/jpeg",
                sha256=sha256_file(path),
            )
        except VisualGenerationError:
            raise
        except Exception as exc:
            raise VisualGenerationError(f"Gemini 圖片生成失敗：{exc}") from exc

    def start_video(self, prompt: str, frame_path: str | Path) -> str:
        try:
            from google.genai import types

            frame = types.Image.from_file(location=str(frame_path))
            operation = self._paid_start(
                lambda: self.client.models.generate_videos(
                    model=self.video_model,
                    prompt=prompt,
                    image=frame,
                    config=types.GenerateVideosConfig(
                        last_frame=frame,
                        number_of_videos=1,
                        duration_seconds=8,
                        resolution="1080p",
                        aspect_ratio="16:9",
                    ),
                )
            )
            if not getattr(operation, "name", None):
                raise VisualGenerationError("Veo 沒有回傳 operation name")
            return str(operation.name)
        except VisualGenerationError:
            raise
        except Exception as exc:
            raise VisualGenerationError(f"Veo 建立 operation 失敗：{exc}") from exc

    def poll_video(self, operation_id: str, output_path: str | Path) -> VideoPoll:
        try:
            from google.genai import types

            operation = types.GenerateVideosOperation(name=operation_id)
            operation = self.client.operations.get(operation)
            if not operation.done:
                return VideoPoll(done=False)
            if getattr(operation, "error", None):
                return VideoPoll(done=True, error=str(operation.error))
            generated = operation.response.generated_videos
            if not generated:
                return VideoPoll(done=True, error="Veo 完成但沒有 generated_videos")
            video = generated[0].video
            self.client.files.download(file=video)
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            partial = output.with_name(
                output.stem + ".partial" + output.suffix
            )
            video.save(str(partial))
            self.video_validator(partial)
            partial.replace(output)
            return VideoPoll(done=True, path=output, sha256=sha256_file(output))
        except Exception as exc:
            if self._is_transient(exc):
                raise VisualPollTransientError(
                    f"Veo polling／下載暫時失敗，可沿用 operation {operation_id} 續跑：{exc}"
                ) from exc
            raise VisualGenerationError(f"Veo polling／下載失敗：{exc}") from exc
