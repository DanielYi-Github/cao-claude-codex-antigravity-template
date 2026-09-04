from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path

from ..errors import GenerationError, SafetyBlockedError

logger = logging.getLogger(__name__)


class LyriaClient:
    def __init__(
        self,
        model: str,
        max_attempts: int = 3,
        request_delay_seconds: float = 2.0,
        *,
        api_key: str | None = None,
    ):
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise GenerationError("尚未設定 GEMINI_API_KEY")
        try:
            from google import genai
        except ImportError as exc:
            raise GenerationError("尚未安裝 google-genai，請執行 pip install -e .") from exc
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.max_attempts = max_attempts
        self.request_delay_seconds = request_delay_seconds

    @staticmethod
    def _audio_bytes(interaction) -> bytes:
        audio = getattr(interaction, "output_audio", None)
        if audio is None:
            raise GenerationError("Lyria 回應未包含 output_audio")
        data = getattr(audio, "data", None)
        if data is None:
            raise GenerationError("Lyria 回應中的音訊資料為空")
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return base64.b64decode(data)
        try:
            return bytes(data)
        except Exception as exc:
            raise GenerationError("無法解析 Lyria 音訊資料") from exc

    @staticmethod
    def _classify_error(exc: Exception) -> Exception:
        text = str(exc).lower()
        if any(k in text for k in ("safety", "blocked", "policy", "artist intent", "copyright")):
            return SafetyBlockedError(str(exc))
        return GenerationError(str(exc))

    def generate(self, prompt: str, output_path: str | Path) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                interaction = self.client.interactions.create(model=self.model, input=prompt)
                output.write_bytes(self._audio_bytes(interaction))
                if output.stat().st_size < 1024:
                    raise GenerationError("生成音訊檔案過小")
                return output
            except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                classified = self._classify_error(exc)
                # 實測：完全相同的 prompt 會一次被擋、一次成功，所以 content_blocked 有相當
                # 比例是隨機的，不是內容真的違規。以前只要命中關鍵字就立刻放棄重試、改用
                # neutral_rewrite 把 prompt 拆掉重組，等於為了一次隨機失敗就永久換掉那首曲子的
                # 內容（而且改寫後的 prompt 不會寫回 plan.json，成品與計畫對不上）。
                # 改成一樣走完重試；只有每一次都被擋，才當作內容真的有問題往上拋。
                if isinstance(classified, SafetyBlockedError) and attempt == self.max_attempts:
                    raise classified
                last_error = classified
                logger.warning("Lyria 生成失敗，第 %s/%s 次：%s", attempt, self.max_attempts, classified)
                if attempt < self.max_attempts:
                    time.sleep(self.request_delay_seconds * (2 ** (attempt - 1)))
        raise GenerationError(f"Lyria 生成失敗：{last_error}")
