from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lyria_auto.config_migration import (
    apply_settings_migration,
    enable_visual,
    preview_settings_migration,
)


@pytest.fixture
def custom_settings(tmp_path) -> Path:
    settings = {
        "project": {"name": "Test", "timezone": "UTC"},
        "generation": {"provider": "lyria", "model": "lyria-3-pro-preview"},
        "video": {"target_duration_minutes": 300, "width": 1920},
        "metadata": {"language": "en"},
        "scheduler": {"enabled": False},
    }
    p = tmp_path / "config" / "settings.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8")
    return p


def test_migration_preview_does_not_write(custom_settings):
    before = custom_settings.read_bytes()
    result = preview_settings_migration(custom_settings)
    assert "visual" in result.missing_sections
    assert custom_settings.read_bytes() == before


def test_migration_preview_detects_existing_visual(custom_settings):
    settings = yaml.safe_load(custom_settings.read_text(encoding="utf-8"))
    settings["visual"] = {"enabled": True}
    custom_settings.write_text(
        yaml.safe_dump(settings, allow_unicode=True), encoding="utf-8"
    )
    result = preview_settings_migration(custom_settings)
    assert "visual" not in result.missing_sections


def test_apply_creates_backup_preserves_values_and_keeps_visual_disabled(custom_settings):
    apply_settings_migration(custom_settings)
    migrated = yaml.safe_load(custom_settings.read_text(encoding="utf-8"))
    assert migrated["video"]["target_duration_minutes"] == 300
    assert migrated["visual"]["enabled"] is False
    backups = list(custom_settings.parent.glob("settings.yaml.*.bak"))
    assert len(backups) == 1


def test_apply_is_idempotent(custom_settings):
    apply_settings_migration(custom_settings)
    before = custom_settings.read_bytes()
    apply_settings_migration(custom_settings)
    assert custom_settings.read_bytes() == before


def test_enable_visual_flips_flag(custom_settings):
    apply_settings_migration(custom_settings)
    enable_visual(custom_settings)
    migrated = yaml.safe_load(custom_settings.read_text(encoding="utf-8"))
    assert migrated["visual"]["enabled"] is True
