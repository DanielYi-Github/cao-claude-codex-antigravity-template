from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from lyria_auto.config import load_config
from lyria_auto.errors import GenerationError

REPO_ROOT = Path(__file__).resolve().parents[1]


def install_scene_presets(root: Path) -> Path:
    """把真正的 config/scene_presets.yaml 複製到測試用的專案根目錄。

    刻意複製正本而不是寫一份精簡的假檔：這份 YAML 的措辭長度本身就是被
    測試的對象（全部組合必須落在 FLUX schnell 的 256 token 訓練長度內），
    用假資料測等於什麼都沒測。
    """
    dest = root / "config" / "scene_presets.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "config" / "scene_presets.yaml", dest)
    return dest


def write_sine_audio(path: Path, duration: float = 25.0) -> None:
    """產生真實可播放的測試音訊（440Hz 正弦波，44.1kHz 立體聲）。
    刻意用 sine 而非 anullsrc：純靜音會讓 loudnorm 行為異常。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-ac", "2", "-c:a", "libmp3lame", str(path),
        ],
        check=True, capture_output=True,
    )


class FakeLyria:
    """假的 Lyria client 生成器。用輸出檔名（例如 track_02_raw.mp3）而非 prompt
    內容來控制哪一軌失敗，因為 prompt 是 PromptEngine 隨機組出來的，用檔名穩定得多。"""

    def __init__(self):
        self.calls: list[str] = []
        self.prompts: list[str] = []
        self.fail_targets: dict[str, int] = {}

    def client_factory(self):
        recorder = self

        class _FakeLyriaClient:
            def __init__(self, *, model, max_attempts, request_delay_seconds):
                pass

            def generate(self, prompt, output_path):
                name = Path(output_path).name
                recorder.calls.append(name)
                recorder.prompts.append(prompt)
                remaining = recorder.fail_targets.get(name, 0)
                if remaining > 0:
                    recorder.fail_targets[name] = remaining - 1
                    raise GenerationError(f"模擬失敗：{name}")
                write_sine_audio(Path(output_path))
                return Path(output_path)

        return _FakeLyriaClient


@pytest.fixture
def fake_lyria():
    return FakeLyria()


@pytest.fixture
def project_config(tmp_path):
    """複製一份完整的 config/ 到暫存目錄並縮小規模，避免每次測試都編碼長音訊或真的 sleep。

    max_tracks_per_video=3 讓規劃出的 Prompt 剛好 3 則，測試才有確定的曲目編號可斷言；
    target_duration_minutes=2（素材目標 120 秒）刻意高於 3 首假音訊的總長（3×25=75 秒），
    使 3 首全部都會被生成 —— 若設得太低，累加邏輯會提早收手而讓測試不穩定。"""
    root = tmp_path / "project"
    shutil.copytree("config", root / "config")
    (root / "config" / "channels.yaml").write_text(
        (root / "config" / "channels.example.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    settings_path = root / "config" / "settings.yaml"
    settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    settings["generation"]["max_tracks_per_video"] = 3
    settings["generation"]["request_delay_seconds"] = 0
    settings["video"]["target_duration_minutes"] = 2
    settings_path.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    return load_config(root)
