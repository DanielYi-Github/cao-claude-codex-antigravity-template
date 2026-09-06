"""把可點擊的預設選項組成關鍵幀與動作提示詞。

存在的理由是實測出來的（artifacts/spec.md「關鍵幀場景組合器」一節）：
舊的 DEFAULT_KEYFRAME_PROMPT 是 861 個 T5 token，而 FLUX.1-schnell 的
訓練序列長度是 256——超過 3.4 倍。ComfyUI 的 T5XXLTokenizer 不會截斷
（max_length=99999999），但遠離訓練分布的結果是注意力被稀釋，寫在後段
的指令實際上不被遵守。用 ComfyUI 自帶的 tokenizer 量測，第 256 個 token
正好落在窗景那段中間，也就是說「狗的位置與大小」「姿勢」「鏡頭與構圖」
整整三段都在模型實際會遵守的範圍之外——這正是人類回報「狗的位置不準、
物理不真實」的直接原因。

所以這個模組的主要工作不是「產生更豐富的提示詞」，而是**在嚴格的 token
預算內把最重要的東西排到最前面**。順序是刻意的：

    身份 → 姿勢與地面接觸 → 相對家具的位置 → 場地與窗外景 → 光線
    → 鏡頭 → 物理接地

前三段要塞進 CLIP-L 硬性截斷的 77 個 token 裡（約 59 個英文字），因為
CLIP-L 的 pooled 向量主導整體構圖。

光線刻意**不是**可以自由點選的一軸，而是由（季節 × 天氣 × 時間）推導。
如果讓人自由組合，「冬天 + 下雪 + 黃金時刻 + 溫暖斜射陽光」這種物理上
不可能的光就會被組出來——那正是要修的問題本身。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import ConfigurationError

PRESETS_RELATIVE_PATH = "config/scene_presets.yaml"

# FLUX.1-schnell 的訓練序列長度。超過不會被截斷（ComfyUI 的
# T5XXLTokenizer 是 max_length=99999999），但會離開訓練分布，後段指令
# 實際上不被遵守。
T5_TRAINING_LIMIT = 256

# 目前這份 config/scene_presets.yaml 全部 39690 種合法組合的估算上限。
# 用 ComfyUI 自帶的 T5 tokenizer 逐一實測過，真實 token 落在 194-241，
# 沒有任何一種組合超過 256。
#
# 這個數字是**棘輪**，不是目標：新增選項時如果測試在這裡失敗，正確的
# 反應是去別處刪字，不是把這個常數調大。整件事的起點就是提示詞膨脹到
# 861 個 token。
PRESET_MATRIX_CEILING = 255

# 係數是拿 ComfyUI 自帶的 T5 tokenizer 對全部 39690 種組合逐一回歸出來
# 的，並刻意選在「永遠不低估」的那一側：實測估算值比真實值高 3-27 個
# token，從不低於。低估會讓 UI 在真的超標時顯示綠燈，那比沒有計數器
# 更糟。專案的 .venv 沒有 transformers，為了數 token 加一個執行期依賴
# 不划算；對照方法記在 tests/test_scene_composer.py。
_TOKENS_PER_WORD = 1.25
_TOKENS_PER_PUNCTUATION = 1.6
_PUNCTUATION = re.compile(r"[.,;:()/-]")


def estimate_t5_tokens(text: str) -> int:
    """估算 T5 token 數，刻意略為高估（見上方註解）。"""
    words = len(text.split())
    punctuation = len(_PUNCTUATION.findall(text))
    return math.ceil(words * _TOKENS_PER_WORD + punctuation * _TOKENS_PER_PUNCTUATION)


class SceneSelectionError(ValueError):
    """選項不存在，或組合違反 rules（例如夏天下雪）。"""


@dataclass(frozen=True)
class ComposedScene:
    """一次組合的完整輸出。

    keyframe 與兩支動作片是**一組**產出，不是各自獨立的：動作提示詞會
    沿用同一組姿勢與場景措辭。少了這點，人只要用預設選項換成戶外露台的
    關鍵幀，stages.py 的 DEFAULT_MOTION_PROMPTS 仍會寫死
    "lying flat on the cafe floor"，等於餵給 FLF2V/Veo 一張與文字互相
    矛盾的起始幀。
    """

    keyframe_prompt: str
    motion_prompts: dict[str, str]
    selection: dict[str, str]
    labels_zh: dict[str, str]
    estimated_tokens: int
    warnings: list[str] = field(default_factory=list)


class SceneComposer:
    def __init__(self, presets: dict[str, Any]):
        self._base = presets.get("base") or {}
        self._axes = presets.get("axes") or {}
        self._defaults = presets.get("defaults") or {}
        self._rules = presets.get("rules") or {}
        if not self._axes:
            raise ConfigurationError(f"{PRESETS_RELATIVE_PATH} 沒有任何 axes")
        missing = [name for name in self._axes if name not in self._defaults]
        if missing:
            # 沒有預設值的軸會讓「不帶參數呼叫 compose()」直接爆掉，
            # 而那正是 UI 第一次載入時走的路徑。
            raise ConfigurationError(f"{PRESETS_RELATIVE_PATH} 的 defaults 缺少：{missing}")

    @classmethod
    def from_root(cls, root: Path) -> SceneComposer:
        path = Path(root) / PRESETS_RELATIVE_PATH
        if not path.exists():
            raise ConfigurationError(f"找不到場景預設檔：{path}")
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")) or {})

    # --- 給 API / UI 用的資料 ---------------------------------------

    def axes(self) -> list[dict[str, Any]]:
        """晶片列要顯示的東西。只回傳 id 與中文標籤——英文措辭是實作細節，
        送到瀏覽器只是讓人有機會在前端拼錯順序。"""
        return [
            {
                "id": name,
                "label_zh": axis.get("label_zh", name),
                "options": [
                    {"id": opt["id"], "zh": opt.get("zh", opt["id"])}
                    for opt in axis.get("options", [])
                ],
            }
            for name, axis in self._axes.items()
        ]

    def defaults(self) -> dict[str, str]:
        return dict(self._defaults)

    def constraints(self) -> list[dict[str, Any]]:
        """UI 用來把不合法的選項直接標成不可選，不必等 API 回 400。"""
        return [dict(rule) for rule in self._rules.get("requires", [])]

    # --- 組合 --------------------------------------------------------

    def _option(self, axis: str, option_id: str) -> dict[str, Any]:
        for opt in self._axes[axis].get("options", []):
            if opt["id"] == option_id:
                return opt
        valid = ", ".join(o["id"] for o in self._axes[axis].get("options", []))
        raise SceneSelectionError(f"{axis} 沒有 {option_id!r} 這個選項（可用：{valid}）")

    def resolve(self, selection: dict[str, str] | None = None) -> dict[str, str]:
        """把使用者的部分選擇補齊成完整選擇，並驗證合法性。"""
        chosen = dict(self._defaults)
        for axis, option_id in (selection or {}).items():
            if axis not in self._axes:
                raise SceneSelectionError(f"沒有 {axis!r} 這個軸（可用：{', '.join(self._axes)}）")
            chosen[axis] = option_id
        for axis, option_id in chosen.items():
            self._option(axis, option_id)  # 不存在就在這裡爆
        for rule in self._rules.get("requires", []):
            if (
                chosen.get(rule["axis"]) == rule["option"]
                and chosen.get(rule["needs_axis"]) not in rule["needs_any"]
            ):
                raise SceneSelectionError(rule.get("message") or f"{rule['option']} 組合不合法")
        return chosen

    def _motion_prompts(
        self,
        *,
        pose: dict[str, Any],
        aspect: dict[str, Any],
        venue: dict[str, Any],
        weather: dict[str, Any],
        after_dark: bool,
        env_clause: str | None,
    ) -> dict[str, str]:
        """sleep / lookup 兩支動作提示詞。

        env_clause 為 None 時由場景推導（場地在動的東西 + 這個天氣的環境
        動態）。呼叫端只有在人類明確按下環境預設按鈕、或 Gemini 視覺辨識
        真的看懂了畫面時才覆寫它——姿勢、地面、開口、光線永遠不讓呼叫端
        覆寫，那正是這次要修的東西。
        """
        ambient = weather["ambient"]
        if after_dark:
            ambient = weather.get("ambient_night", ambient)
        clause = env_clause or f"{venue['motion']}; {ambient}."
        shared = {
            "surface": aspect["surface"],
            "aperture": aspect["aperture"],
            "env_clause": _tidy(clause),
            "light_hold": _LIGHT_HOLD_NIGHT if after_dark else _LIGHT_HOLD_DAY,
        }
        return {
            "sleep": _motion_prompt(behaviour=pose["hold"], **shared),
            "lookup": _motion_prompt(behaviour=pose["glance"], **shared),
        }

    def compose_motion(
        self, selection: dict[str, str] | None = None, *, env_clause: str | None = None
    ) -> dict[str, str]:
        """只要動作提示詞時的入口（頁籤 2 的預設按鈕與視覺辨識端點）。

        走的是跟 compose() 完全相同的推導，所以不可能出現「按鈕產出的字
        跟關鍵幀當初存的那組不一致」——不維護兩套模板。
        """
        chosen = self.resolve(selection)
        time_opt = self._option("time", chosen["time"])
        return self._motion_prompts(
            pose=self._option("pose", chosen["pose"]),
            aspect=self._option("aspect", chosen["aspect"]),
            venue=self._option("venue", chosen["venue"]),
            weather=self._option("weather", chosen["weather"]),
            after_dark=bool(time_opt.get("night")),
            env_clause=env_clause,
        )

    def compose(self, selection: dict[str, str] | None = None) -> ComposedScene:
        chosen = self.resolve(selection)
        venue = self._option("venue", chosen["venue"])
        aspect = self._option("aspect", chosen["aspect"])
        season = self._option("season", chosen["season"])
        weather = self._option("weather", chosen["weather"])
        time_opt = self._option("time", chosen["time"])
        pose = self._option("pose", chosen["pose"])
        camera = self._option("camera", chosen["camera"])

        surface = aspect["surface"]

        # 光線推導：天氣是擴散類（陰/雨/霧/雪）時，時間軸改用沒有方向性
        # 硬陽光的措辭；夜晚與藍調時刻本來就沒有直射日光，兩邊同文。
        after_dark = bool(time_opt.get("night"))
        light_key = "diffuse" if (weather.get("diffuse") or after_dark) else "sun"
        light = time_opt[light_key]

        # 季節與天氣裡的太陽高度描述在入夜後會跟時間互相矛盾（「夏天陡直
        # 陽光」配「日落後」），所以兩者都有只留植被／天空的 night 變體。
        season_text = season.get("night", season["en"]) if after_dark else season["en"]
        weather_text = weather.get("night", weather["en"]) if after_dark else weather["en"]

        # 室內外亮度關係在入夜後反轉，接地那段也得跟著換。
        grounding = self._base["grounding_night" if after_dark else "grounding_day"]

        keyframe = " ".join(
            _tidy(part)
            for part in (
                self._base["identity"],
                pose["en"].format(surface=surface),
                self._base["placement"],
                # 不要用 .capitalize()：它會把句子其餘部分全部轉小寫，
                # "An old Japanese house cafe" 會變成 "japanese"。YAML 裡
                # 的 venue.en 本來就已經是大寫開頭。
                f"{venue['en']}, {venue['view']}.",
                aspect["en"],
                season_text,
                weather_text,
                light,
                camera["en"],
                grounding,
            )
        )

        motion_prompts = self._motion_prompts(
            pose=pose, aspect=aspect, venue=venue, weather=weather,
            after_dark=after_dark, env_clause=None,
        )

        estimated = estimate_t5_tokens(keyframe)
        warnings: list[str] = []
        if estimated > T5_TRAINING_LIMIT:
            # 預設選項本身不可能走到這裡（測試把上限釘在
            # PRESET_MATRIX_CEILING），只有人手動貼長文字進來才會。
            warnings.append(
                f"提示詞估算 {estimated} tokens，超過 FLUX schnell 的訓練長度 "
                f"{T5_TRAINING_LIMIT}——寫在後段的位置與鏡頭指令會被稀釋掉。"
            )

        return ComposedScene(
            keyframe_prompt=keyframe,
            motion_prompts=motion_prompts,
            selection=chosen,
            labels_zh={
                axis: self._option(axis, option_id).get("zh", option_id)
                for axis, option_id in chosen.items()
            },
            estimated_tokens=estimated,
            warnings=warnings,
        )


def _tidy(text: str) -> str:
    """YAML 的折疊字串會留下換行與多餘空白。"""
    return " ".join(text.split())


# 白天／入夜各自的「光線幾乎不變」措辭。這不是可以自由選的一軸——動作
# 提示詞裡寫死 "warm daylight" 而畫面是雪夜，就是 824eb3b 那五個按鈕的
# 錯誤（artifacts/spec.md「動作提示詞與關鍵幀場景的接縫」C）。
_LIGHT_HOLD_DAY = "the daylight shifts almost imperceptibly"
_LIGHT_HOLD_NIGHT = "the lamplight holds steady"


def _motion_prompt(
    *, behaviour: str, surface: str, aperture: str, env_clause: str, light_hold: str
) -> str:
    """一支 8 秒循環的動作提示詞。

    措辭沿用人類在 824eb3b 為 Veo 寫的模板——刻意比關鍵幀長。關鍵幀那邊
    的 256 token 上限是 FLUX.1-schnell 的訓練長度，Veo 與 WAN 的 umT5 都
    沒有那個限制，把影像端的預算硬套到影片端只會白白丟掉可用的描述。

    真正的重點是：姿勢、地面、開口、光線**一律由場景填**，不寫死。原本
    的模板無論晶片選什麼都宣稱狗趴平在室內咖啡館地板、外面有溫暖陽光，
    等於餵給 Veo 一張與文字互相矛盾的起始幀。

    結尾必須明講「最後一幀對齊第一幀」，64 秒巨集循環靠這個接縫，
    studio/veo.py 的 seam_score 也是照這個前提在量。
    """
    return _tidy(
        f"A continuous seamless 8-second loop video based on the image. "
        f"The fluffy chow chow dog on the {surface} {behaviour}. "
        f"Natural environmental dynamics: {aperture} {env_clause} "
        f"Steam continues curling gently from the mug on the table; {light_hold}. "
        f"The owner stays completely out of frame throughout, only the chair, "
        f"laptop, and mug are visible. The dog and every environmental element at "
        f"the end of the clip match the very first frame seamlessly. No camera "
        f"movement, no scene change, no new objects, smooth continuous loop "
        f"returning to the same composition, photorealistic, physically plausible motion."
    )
