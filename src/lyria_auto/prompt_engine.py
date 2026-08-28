from __future__ import annotations

import hashlib
import random
from typing import Any

from .errors import ConfigurationError
from .models import PromptPlan
from .safety import PromptSafety


class PromptEngine:
    def __init__(self, prompt_config: dict[str, Any], profile_name: str, seed: int):
        profiles = prompt_config.get("profiles", {})
        if profile_name not in profiles:
            raise ConfigurationError(f"找不到 Prompt profile：{profile_name}")
        self.profile = profiles[profile_name]
        self.rng = random.Random(seed)
        self.safety = PromptSafety(prompt_config.get("blocked_reference_terms", []))

    def _choice(self, key: str):
        values = self.profile.get(key, [])
        if not values:
            raise ConfigurationError(f"Prompt profile 缺少 {key}")
        return self.rng.choice(values)

    @staticmethod
    def _en_zh(value) -> tuple[str, str]:
        """回傳 (餵給 Lyria 的英文, 顯示用中文短名)。未提供 zh 時退回英文。"""
        raw = value["en"] if isinstance(value, dict) else value
        english = ", ".join(raw) if isinstance(raw, list) else str(raw)
        if isinstance(value, dict) and value.get("zh"):
            return english, str(value["zh"])
        return english, english

    def compose_album(self, count: int, recent: set[str] | None = None) -> list[PromptPlan]:
        """組出「一張專輯」的 Prompt：整支影片共用同一個曲風、場景、錄音質感與混音風格，
        只有樂器編制、情緒、速度、環境音逐首變化。

        先前的做法是每首獨立重抽全部 8 個屬性，結果 25 首橫跨 11 個場景、5 種曲風，
        串起來像隨機播放清單而不是一張專輯；而且標題與縮圖取自第一首的場景，
        卻與其餘 20 幾首無關，等於名不符實。固定的部分就是「這支影片是什麼」，
        變化的部分讓每首之間仍有區別、不至於單調。
        """
        recent_work = set(recent or set())
        # 整張專輯共用：這幾項決定「這支影片是什麼」
        genre = self._choice("genres")
        scene, scene_label = self._en_zh(self._choice("scenes"))
        texture = self._choice("textures")
        production = self._choice("productions")
        output = self._choice("outputs")

        plans: list[PromptPlan] = []
        for _ in range(count):
            for _attempt in range(200):
                # 逐首變化：讓專輯內部仍有層次
                mood, mood_label = self._en_zh(self._choice("moods"))
                tempo = self._choice("tempos")
                instruments = self._choice("instrumentations")
                atmosphere = self._choice("atmospheres")
                instrumentation = ", ".join(instruments)
                components = [genre, scene, mood, tempo, instrumentation, texture, atmosphere, production]
                signature = hashlib.sha256("|".join(components).encode("utf-8")).hexdigest()[:20]
                if signature in recent_work:
                    continue
                prompt = (
                    f"Genre: {genre}. Scene: {scene}. Mood: {mood}. Tempo: {tempo}. "
                    f"Instrumentation: {instrumentation}. Texture: {texture}. "
                    f"Atmosphere: {atmosphere}. Production: {production}. {output}"
                )
                self.safety.require_safe(prompt)
                recent_work.add(signature)
                plans.append(PromptPlan(prompt, signature, scene, mood, instrumentation, scene_label, mood_label))
                break
            else:
                raise RuntimeError(
                    f"這張專輯的變化組合已用盡（已組出 {len(plans)}/{count} 首）。"
                    "請在 config/prompts.yaml 增加 moods／tempos／instrumentations／atmospheres 的選項。"
                )
        return plans
