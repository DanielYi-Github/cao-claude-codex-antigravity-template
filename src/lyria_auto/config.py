from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .errors import ConfigurationError


@dataclass(frozen=True)
class AppConfig:
    settings: dict[str, Any]
    prompts: dict[str, Any]
    channels: dict[str, Any]
    root: Path

    def section(self, name: str) -> dict[str, Any]:
        value = self.settings.get(name, {})
        if not isinstance(value, dict):
            raise ConfigurationError(f"設定區段 {name!r} 必須是物件")
        return value

    def channel(self, name: str) -> dict[str, Any]:
        channels = self.channels.get("channels", {})
        if name not in channels:
            raise ConfigurationError(f"找不到頻道設定：{name}")
        return channels[name]


def _load_yaml(path: Path, *, required: bool = True) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ConfigurationError(f"找不到設定檔：{path}")
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigurationError(f"YAML 根節點必須是物件：{path}")
    return data


def load_config(root: str | Path | None = None) -> AppConfig:
    root_path = Path(root or Path.cwd()).resolve()
    load_dotenv(root_path / ".env")
    settings_rel = os.getenv("LYRIA_AUTO_SETTINGS", "config/settings.yaml")
    channels_rel = os.getenv("LYRIA_AUTO_CHANNELS", "config/channels.yaml")
    settings = _load_yaml(root_path / settings_rel)
    prompts = _load_yaml(root_path / "config/prompts.yaml")
    channels = _load_yaml(root_path / channels_rel, required=False)
    return AppConfig(settings=settings, prompts=prompts, channels=channels, root=root_path)
