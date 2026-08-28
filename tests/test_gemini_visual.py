from __future__ import annotations

import pytest
from fakes_visual import FakeVisualSDK, png_bytes

from lyria_auto.errors import PaidStartUncertainError, VisualGenerationError
from lyria_auto.providers.gemini_visual import GeminiVisualClient


def client(fake):
    return GeminiVisualClient(
        api_key="unused",
        image_model="gemini-3.1-flash-image",
        video_model="veo-3.1-fast-generate-preview",
        sdk_client=fake,
        video_validator=lambda path: None,
    )


def test_generate_image_writes_decodable_raw_bytes(tmp_path):
    fake = FakeVisualSDK()

    result = client(fake).generate_image(
        "photorealistic empty cafe",
        tmp_path / "raw.png",
        reference_paths=[],
    )

    assert result.path.exists()
    assert result.mime_type == "image/png"
    assert len(result.sha256) == 64
    assert fake.image_calls == 1


def test_video_operation_can_resume_from_persisted_name(tmp_path):
    fake = FakeVisualSDK()
    adapter = client(fake)
    frame = tmp_path / "frame.png"
    frame.write_bytes(png_bytes())

    operation_id = adapter.start_video("locked camera", frame)
    poll = adapter.poll_video(operation_id, tmp_path / "raw.mp4")

    assert operation_id == "operations/video-1"
    assert poll.done is True
    assert poll.path == tmp_path / "raw.mp4"
    assert fake.operations_seen == ["operations/video-1"]
    assert fake.video_starts == 1


@pytest.mark.parametrize("status_code", [429, 503])
def test_paid_start_is_at_most_once_when_result_is_uncertain(tmp_path, status_code):
    fake = FakeVisualSDK()
    attempts = 0

    class TransientError(RuntimeError):
        pass

    def uncertain(**kwargs):
        nonlocal attempts
        attempts += 1
        error = TransientError("response lost")
        error.status_code = status_code
        raise error

    fake.interactions.create = uncertain
    adapter = GeminiVisualClient(
        api_key="unused",
        image_model="gemini-3.1-flash-image",
        video_model="veo-3.1-fast-generate-preview",
        sdk_client=fake,
        video_validator=lambda path: None,
    )

    with pytest.raises(PaidStartUncertainError, match="不得自動重試"):
        adapter.generate_image(
            "photorealistic empty cafe",
            tmp_path / "raw.png",
            reference_paths=[],
        )

    assert attempts == 1


def test_non_transient_failure_is_not_retried(tmp_path):
    fake = FakeVisualSDK()
    attempts = 0

    def invalid(**kwargs):
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid request")

    fake.interactions.create = invalid
    adapter = client(fake)

    with pytest.raises(VisualGenerationError, match="invalid request"):
        adapter.generate_image(
            "photorealistic empty cafe",
            tmp_path / "raw.png",
            reference_paths=[],
        )

    assert attempts == 1


def test_paid_start_uncertain_on_video_start(tmp_path):
    fake = FakeVisualSDK()
    attempts = 0

    class ServerError(RuntimeError):
        status_code = 500

    def failing(**kwargs):
        nonlocal attempts
        attempts += 1
        raise ServerError("server unavailable")

    fake.models.generate_videos = failing
    adapter = client(fake)
    frame = tmp_path / "frame.png"
    frame.write_bytes(png_bytes())

    with pytest.raises(PaidStartUncertainError, match="不得自動重試"):
        adapter.start_video("locked camera", frame)

    assert attempts == 1


def test_adapter_requires_api_key_or_sdk():
    with pytest.raises(VisualGenerationError, match="GEMINI_API_KEY"):
        GeminiVisualClient(
            api_key=None,
            image_model="test",
            video_model="test",
        )
