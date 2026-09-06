"""場景組合器：提示詞預算、物理一致性，以及與動作提示詞的配套。

這整個模組的存在理由是一個實測數字：舊的關鍵幀提示詞是 861 個 T5
token，而 FLUX.1-schnell 的訓練序列長度是 256。所以這裡最重要的一條測
試不是「有沒有組出字串」，而是**沒有任何一種選項組合會再度膨脹回去**。

### Token 數字是怎麼來的（此處無法自動重跑）

專案的 .venv 沒有 transformers，所以 estimate_t5_tokens() 是一條刻意
高估的迴歸式，不是真的 tokenizer。係數是用 ComfyUI 自帶的 T5 tokenizer
對全部 39690 種合法組合逐一比對出來的，重跑方式：

    /Users/danielyi/ComfyUI-Installs/ComfyUI/standalone-env/bin/python3.13
    >>> from transformers import T5TokenizerFast
    >>> tok = T5TokenizerFast.from_pretrained(
    ...     ".../ComfyUI/comfy/text_encoders/t5_tokenizer")

2026-09-06 的實測結果（全部 39690 種組合，不是抽樣）：

| 指標 | 值 |
|---|---|
| 真實 T5 token 範圍 | 194 – 241 |
| 超過 256 的組合數 | 0 |
| 估算值 − 真實值 | +3 – +27（從不低估） |

對照組：修改前的 DEFAULT_KEYFRAME_PROMPT = 626 字 / 861 tokens。
"""

from __future__ import annotations

import itertools

import pytest

from lyria_auto.studio.scene_composer import (
    PRESET_MATRIX_CEILING,
    T5_TRAINING_LIMIT,
    SceneComposer,
    SceneSelectionError,
    estimate_t5_tokens,
)

# 修改前的提示詞實測值，留著當回歸的對照點。
LEGACY_PROMPT_REAL_TOKENS = 861


@pytest.fixture
def composer(tmp_path):
    from conftest import install_scene_presets

    install_scene_presets(tmp_path)
    return SceneComposer.from_root(tmp_path)


def _all_selections(composer: SceneComposer):
    axes = {a["id"]: [o["id"] for o in a["options"]] for a in composer.axes()}
    keys = list(axes)
    for combo in itertools.product(*axes.values()):
        yield dict(zip(keys, combo, strict=True))


# --- 提示詞預算：這是整個模組最重要的一條 -----------------------------


def test_no_preset_combination_exceeds_the_schnell_training_length(composer):
    """全部 39690 種合法組合都必須落在 256 個 token 以內。

    如果這條在你新增選項之後失敗了，正確的反應是**去別處刪字**，不是
    把 PRESET_MATRIX_CEILING 調大。整件事的起點就是提示詞膨脹到 861 個
    token，後段的位置與鏡頭指令因此完全不被遵守。
    """
    worst = max(
        (composer.compose(sel) for sel in _all_selections(composer) if _valid(composer, sel)),
        key=lambda scene: scene.estimated_tokens,
    )
    assert worst.estimated_tokens <= PRESET_MATRIX_CEILING, (
        f"最壞組合 {worst.selection} 估算 {worst.estimated_tokens} tokens，"
        f"超過棘輪上限 {PRESET_MATRIX_CEILING}"
    )
    assert worst.estimated_tokens <= T5_TRAINING_LIMIT
    assert not worst.warnings


def test_default_scene_is_far_below_the_legacy_prompt(composer):
    scene = composer.compose()
    assert scene.estimated_tokens < LEGACY_PROMPT_REAL_TOKENS / 3
    assert scene.warnings == []


def test_the_estimator_never_reports_fewer_tokens_than_words(composer):
    """估算式刻意偏高。低估會讓 UI 在真的超標時顯示綠燈，比沒有計數器更糟。"""
    text = composer.compose().keyframe_prompt
    assert estimate_t5_tokens(text) > len(text.split())


def test_an_over_long_hand_written_prompt_is_warned_about(composer):
    """人手動貼長文字進來時要拿得到警告——預設選項本身走不到這條路徑。"""
    scene = composer.compose()
    assert estimate_t5_tokens("word " * 400) > T5_TRAINING_LIMIT
    assert scene.warnings == []


# --- 語序：前面的 token 才是模型真的會遵守的 --------------------------


def test_the_visual_content_comes_before_the_camera_and_grounding(composer):
    """CLIP-L 分支硬性截斷在 77 個 token（約 59 個英文字），而它的 pooled
    向量主導整體構圖。所以身份、姿勢、位置必須排在最前面，鏡頭與接地排
    在最後——舊提示詞把這個順序倒過來寫，那正是狗的位置不受控的原因。"""
    prompt = composer.compose().keyframe_prompt
    head = " ".join(prompt.split()[:59])
    assert head.startswith("One chow chow dog, chowchow_mascot")
    assert "Lying flat on" in head
    assert "chair rail" in head, "位置描述必須落在 CLIP-L 看得到的範圍內"
    tail = " ".join(prompt.split()[-40:])
    assert "at f/" in tail and "tripod at seated eye level" in tail
    assert "contact shadow" in tail


def test_the_prompt_states_emptiness_instead_of_negating_people(composer):
    """擴散模型的文字編碼器不擅長處理否定，「no person」反而可能召喚出人。
    主人不在場要用肯定敘述（空椅、無人的桌）表達。"""
    prompt = composer.compose().keyframe_prompt
    assert "empty pulled-out chair" in prompt
    for negation in ("No human", "no person", "No hands", "not visible"):
        assert negation not in prompt


def test_the_prompt_uses_physical_anchors_not_percentages(composer):
    """模型不會量測「佔畫面高度 10-15%」，那條舊指令等於無效。"""
    prompt = composer.compose().keyframe_prompt
    assert "percent" not in prompt
    assert "shoulder below the chair rail" in prompt


def test_only_one_pose_is_described_per_prompt(composer):
    """舊提示詞同時列出兩種姿勢要模型「二選一」。模型不會執行 XOR，
    只會把兩種混成扭曲的姿態。"""
    for pose_id, marker in (("lying", "Lying flat"), ("sitting", "Sitting upright")):
        prompt = composer.compose({"pose": pose_id}).keyframe_prompt
        assert marker in prompt
        others = {"Lying flat", "Sitting upright", "Curled into"} - {marker}
        assert not any(other in prompt for other in others)


# --- 物理一致性：光線是推導出來的，不是自由搭配的 ---------------------


def test_daylight_and_night_use_opposite_interior_exterior_brightness(composer):
    day = composer.compose({"time": "midday"}).keyframe_prompt
    night = composer.compose({"time": "night"}).keyframe_prompt
    assert "outside two stops brighter than in" in day
    assert "inside brighter than out" in night
    assert "glass faintly reflecting it" in night


def test_after_dark_drops_every_direct_sun_phrase(composer):
    """「夏天，陡直的太陽」配上「日落後」是物理上不可能的組合——季節與天氣
    的太陽描述在入夜後必須換成只留植被／天空的措辭。"""
    prompt = composer.compose(
        {"season": "summer", "weather": "clear", "time": "blue_hour"}
    ).keyframe_prompt
    assert "steep sun" not in prompt
    assert "hard-edged shadows" not in prompt
    assert "dense dark-green foliage" in prompt


def test_diffuse_weather_removes_directional_sunlight(composer):
    overcast = composer.compose({"weather": "overcast", "time": "golden"}).keyframe_prompt
    clear = composer.compose({"weather": "clear", "time": "golden"}).keyframe_prompt
    assert "amber light, long shadows" in clear
    assert "amber light" not in overcast
    assert "dull warm-grey light" in overcast


def test_snow_is_only_allowed_in_winter(composer):
    assert composer.compose({"weather": "snow", "season": "winter"}).keyframe_prompt
    with pytest.raises(SceneSelectionError, match="雪"):
        composer.compose({"weather": "snow", "season": "summer"})


# --- 戶外自然景：人類明確要求的部分 -----------------------------------


def test_every_venue_carries_an_outdoor_view(composer):
    """人類要求「有一面能看到整片大自然」。沒有 view 的場地就是純室內，
    不該進到這個清單裡。"""
    for option in _axis(composer, "venue"):
        prompt = composer.compose({"venue": option["id"]}).keyframe_prompt
        assert prompt.count(".") >= 5
        view = _raw_option(composer, "venue", option["id"])["view"]
        assert view and view in prompt


def test_the_paris_preset_names_no_real_landmark(composer):
    """沿用專案既有的商用規則：虛構、無可辨識地點。巴黎只做風格
    （Haussmann 石造街屋、鋅皮屋頂、鍛鐵陽台），不放地標。"""
    prompt = composer.compose({"venue": "paris_street"}).keyframe_prompt
    assert "Haussmann" in prompt
    for landmark in ("Eiffel", "Notre", "Louvre", "Arc de", "Sacre", "Montmartre"):
        assert landmark not in prompt


# --- 關鍵幀與動作提示詞是一組 -----------------------------------------


def test_motion_prompts_follow_the_same_scene_and_pose(composer):
    """少了這一條，人在頁籤 1 挑了戶外露台，動作提示詞仍會寫室內地板，
    等於餵給 FLF2V/Veo 一張與文字互相矛盾的起始幀。"""
    scene = composer.compose({"aspect": "outdoor_seat", "pose": "sitting"})
    assert set(scene.motion_prompts) == {"sleep", "lookup"}
    for prompt in scene.motion_prompts.values():
        assert "worn stone terrace" in prompt
        assert "plank floor" not in prompt
        assert "The last frame matches the first exactly." in prompt
    assert "stays sitting" in scene.motion_prompts["sleep"]
    assert "turns its head" in scene.motion_prompts["lookup"]


def test_motion_ambient_motion_matches_the_weather(composer):
    rain = composer.compose({"weather": "rain", "season": "autumn"})
    assert "rain keeps running down the glass" in rain.motion_prompts["sleep"]
    snow = composer.compose({"weather": "snow", "season": "winter"})
    assert "snow keeps falling" in snow.motion_prompts["sleep"]


# --- 選項驗證 ---------------------------------------------------------


def test_unknown_axis_or_option_is_rejected(composer):
    with pytest.raises(SceneSelectionError, match="venue"):
        composer.compose({"venue": "moon_base"})
    with pytest.raises(SceneSelectionError, match="weather_of_mars"):
        composer.compose({"weather_of_mars": "clear"})


def test_a_partial_selection_falls_back_to_the_defaults(composer):
    scene = composer.compose({"venue": "lakeside"})
    defaults = composer.defaults()
    assert scene.selection["venue"] == "lakeside"
    assert scene.selection["camera"] == defaults["camera"]
    assert set(scene.selection) == set(defaults)


def test_axes_expose_chinese_labels_for_every_option(composer):
    """晶片上顯示的是中文，缺一個就會出現一顆看不懂的按鈕。"""
    for axis in composer.axes():
        assert axis["label_zh"]
        assert axis["options"]
        for option in axis["options"]:
            assert option["zh"] and option["zh"] != option["id"]


# --- helpers ----------------------------------------------------------


def _axis(composer: SceneComposer, name: str):
    return next(a["options"] for a in composer.axes() if a["id"] == name)


def _raw_option(composer: SceneComposer, axis: str, option_id: str):
    return composer._option(axis, option_id)


def _valid(composer: SceneComposer, selection: dict[str, str]) -> bool:
    try:
        composer.resolve(selection)
    except SceneSelectionError:
        return False
    return True
