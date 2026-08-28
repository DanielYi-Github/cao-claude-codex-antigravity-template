from __future__ import annotations

from lyria_auto.visual_models import VisualSource, credential_fingerprint


def test_default_visual_models_and_hard_limits(project_config):
    visual = project_config.section("visual")

    assert visual["enabled"] is False
    assert visual["image_model"] == "gemini-3.1-flash-image"
    assert visual["video_model"] == "veo-3.1-fast-generate-preview"
    assert visual["max_image_outputs"] == 14
    assert visual["max_video_outputs"] == 8
    assert visual["quality"]["seam_max_normalized_mae"] == 0.06
    assert visual["quality"]["motion_min_normalized_mae"] == 0.002
    assert visual["quality"]["motion_max_normalized_mae"] == 0.10
    assert visual["quality"]["duration_tolerance_seconds"] == 0.25
    assert visual["preflight"]["max_image_outputs"] == 1
    assert visual["preflight"]["max_video_outputs"] == 1
    assert visual["preflight"]["valid_days"] == 30


def test_credential_fingerprint_is_stable_and_does_not_contain_key(tmp_path):
    secret_path = tmp_path / "fingerprint.key"

    first = credential_fingerprint("private-api-key", secret_path)
    second = credential_fingerprint("private-api-key", secret_path)

    assert first == second
    assert len(first) == 64
    assert "private-api-key" not in first
    assert secret_path.read_bytes() != b"private-api-key"


def test_visual_source_key_changes_with_model():
    base = VisualSource(
        provider="gemini-developer-api",
        image_model="gemini-3.1-flash-image",
        video_model="veo-3.1-fast-generate-preview",
        credential_fingerprint="abc",
        sdk_version="2.13.0",
    )
    changed = VisualSource(
        provider=base.provider,
        image_model="gemini-3-pro-image",
        video_model=base.video_model,
        credential_fingerprint=base.credential_fingerprint,
        sdk_version=base.sdk_version,
    )

    assert base.identity_key() != changed.identity_key()
