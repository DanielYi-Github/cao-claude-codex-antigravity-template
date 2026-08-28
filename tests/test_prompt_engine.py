from pathlib import Path

import yaml

from lyria_auto.prompt_engine import PromptEngine


def _engine(seed: int = 42) -> PromptEngine:
    cfg = yaml.safe_load(Path("config/prompts.yaml").read_text(encoding="utf-8"))
    return PromptEngine(cfg, "coffeehouse_lofi_jazz", seed)


def test_generates_unique_safe_prompts():
    plans = _engine().compose_album(20)
    assert len(plans) == 20
    assert len({p.signature for p in plans}) == 20
    assert all("Instrumental only" in p.prompt for p in plans)
    assert all("nujabes" not in p.prompt.lower() for p in plans)


def test_album_shares_one_genre_scene_texture_and_production():
    """整支影片必須聽起來像一張專輯。先前每首獨立重抽全部屬性，25 首橫跨 11 個場景、
    5 種曲風，串起來像隨機播放清單；而且標題與縮圖取自第一首的場景，與其餘曲目無關。"""
    plans = _engine().compose_album(20)

    def field(prompt: str, label: str) -> str:
        return prompt.split(f"{label}: ")[1].split(". ")[0]

    assert len({field(p.prompt, "Genre") for p in plans}) == 1
    assert len({field(p.prompt, "Texture") for p in plans}) == 1
    assert len({field(p.prompt, "Production") for p in plans}) == 1
    # 場景同時決定標題與縮圖文字，必須全片一致才不會名不符實
    assert len({p.scene for p in plans}) == 1
    assert len({p.scene_label for p in plans}) == 1


def test_album_still_varies_instrumentation_mood_and_tempo():
    """固定過頭會讓 20 首聽起來一模一樣。樂器編制、情緒、速度仍要逐首變化。"""
    plans = _engine().compose_album(20)

    def field(prompt: str, label: str) -> str:
        return prompt.split(f"{label}: ")[1].split(". ")[0]

    assert len({p.instrumentation for p in plans}) > 1
    assert len({p.mood for p in plans}) > 1
    assert len({field(p.prompt, "Tempo") for p in plans}) > 1


def test_prompt_does_not_request_a_track_length():
    """實測：不指定長度會拿到約 173 秒（接近上限），要求「150 秒」只拿到 144 秒。
    計費按「首」算與長度無關，所以指定秒數等於同樣的錢買到更少音樂。"""
    plans = _engine().compose_album(5)
    for p in plans:
        assert "seconds" not in p.prompt.lower()
        assert "minute" not in p.prompt.lower()
