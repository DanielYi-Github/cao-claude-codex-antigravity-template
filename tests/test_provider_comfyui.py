from __future__ import annotations

import io
import json
from urllib.error import HTTPError, URLError

import pytest

from lyria_auto.errors import GenerationError
from lyria_auto.providers.comfyui import ComfyUIClient


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def client():
    return ComfyUIClient(base_url="http://127.0.0.1:8188")


def test_submit_returns_prompt_id_and_sends_workflow_as_json(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(json.dumps({"prompt_id": "abc123"}).encode("utf-8"))

    monkeypatch.setattr("lyria_auto.providers.comfyui.urlopen", fake_urlopen)

    prompt_id = client().submit({"1": {"class_type": "X", "inputs": {}}})

    assert prompt_id == "abc123"
    assert captured["url"] == "http://127.0.0.1:8188/prompt"
    assert captured["body"]["prompt"] == {"1": {"class_type": "X", "inputs": {}}}
    assert captured["body"]["client_id"]


def test_submit_raises_generation_error_with_node_errors_on_400(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise HTTPError(
            request.full_url,
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(json.dumps({"node_errors": {"9": "missing input"}}).encode("utf-8")),
        )

    monkeypatch.setattr("lyria_auto.providers.comfyui.urlopen", fake_urlopen)

    with pytest.raises(GenerationError, match="missing input"):
        client().submit({})


def test_submit_raises_generation_error_when_comfyui_unreachable(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise URLError("connection refused")

    monkeypatch.setattr("lyria_auto.providers.comfyui.urlopen", fake_urlopen)

    with pytest.raises(GenerationError, match="連不上 ComfyUI"):
        client().submit({})


def test_wait_for_result_polls_until_outputs_appear(monkeypatch):
    responses = [
        {},
        {"p1": {"status": {}, "outputs": {}}},
        {
            "p1": {
                "status": {"status_str": "success"},
                "outputs": {"7": {"images": [{"filename": "a.png"}]}},
            }
        },
    ]
    calls: list[str] = []

    def fake_urlopen(url, timeout=None):
        calls.append(url)
        return _FakeResponse(json.dumps(responses[len(calls) - 1]).encode("utf-8"))

    monkeypatch.setattr("lyria_auto.providers.comfyui.urlopen", fake_urlopen)
    monkeypatch.setattr("lyria_auto.providers.comfyui.time.sleep", lambda _: None)

    entry = client().wait_for_result("p1", poll_interval=0)

    assert entry["outputs"]["7"]["images"][0]["filename"] == "a.png"
    assert len(calls) == 3


def test_wait_for_result_raises_on_error_status(monkeypatch):
    def fake_urlopen(url, timeout=None):
        return _FakeResponse(
            json.dumps(
                {"p1": {"status": {"status_str": "error", "messages": ["boom"]}, "outputs": {}}}
            ).encode("utf-8")
        )

    monkeypatch.setattr("lyria_auto.providers.comfyui.urlopen", fake_urlopen)

    with pytest.raises(GenerationError, match="boom"):
        client().wait_for_result("p1", poll_interval=0)


def test_wait_for_result_times_out(monkeypatch):
    times = iter([0.0, 0.0, 100.0])

    def fake_urlopen(url, timeout=None):
        return _FakeResponse(b"{}")

    monkeypatch.setattr("lyria_auto.providers.comfyui.time.monotonic", lambda: next(times))
    monkeypatch.setattr("lyria_auto.providers.comfyui.time.sleep", lambda _: None)
    monkeypatch.setattr("lyria_auto.providers.comfyui.urlopen", fake_urlopen)

    with pytest.raises(GenerationError, match="逾時"):
        client().wait_for_result("p1", poll_interval=0, timeout=1)


@pytest.mark.parametrize("output_key", ["images", "videos", "gifs"])
def test_fetch_output_finds_file_under_different_output_keys(monkeypatch, tmp_path, output_key):
    history_entry = {
        "outputs": {"7": {output_key: [{"filename": "out.bin", "subfolder": "", "type": "output"}]}}
    }

    def fake_urlopen(url, timeout=None):
        return _FakeResponse(b"payload-bytes")

    monkeypatch.setattr("lyria_auto.providers.comfyui.urlopen", fake_urlopen)

    dest = tmp_path / "downloaded.bin"
    result = client().fetch_output(history_entry, dest)

    assert result == dest
    assert dest.read_bytes() == b"payload-bytes"


def test_fetch_output_raises_when_no_file_found(tmp_path):
    history_entry = {"outputs": {"7": {"text": ["hello"]}}}

    with pytest.raises(GenerationError, match="找不到檔案"):
        client().fetch_output(history_entry, tmp_path / "x.bin")


def test_stage_input_file_uploads_via_http_and_returns_saved_name(monkeypatch, tmp_path):
    """No local ComfyUI directory involved -- this must work the same whether
    base_url points at 127.0.0.1 or a rented GPU box on the other side of
    the internet, since it's the only thing standing between the two."""
    src = tmp_path / "source.png"
    src.write_bytes(b"fake-png-bytes")
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["content_type"] = request.get_header("Content-type")
        return _FakeResponse(
            json.dumps({"name": "studio-keyframe-42.png", "subfolder": "", "type": "input"}).encode(
                "utf-8"
            )
        )

    monkeypatch.setattr("lyria_auto.providers.comfyui.urlopen", fake_urlopen)

    filename = client().stage_input_file(src, name_hint="keyframe-42")

    assert filename == "studio-keyframe-42.png"
    assert captured["url"] == "http://127.0.0.1:8188/upload/image"
    assert b"fake-png-bytes" in captured["body"]
    assert captured["content_type"].startswith("multipart/form-data")


def test_stage_input_file_uses_the_name_comfyui_actually_saved_it_as(monkeypatch, tmp_path):
    """ComfyUI appends a counter on a filename collision -- the caller needs
    that answer, not the name we proposed, or LoadImage will 404."""
    src = tmp_path / "source.png"
    src.write_bytes(b"x")

    def fake_urlopen(request, timeout=None):
        return _FakeResponse(json.dumps({"name": "studio-keyframe-42 (1).png"}).encode("utf-8"))

    monkeypatch.setattr("lyria_auto.providers.comfyui.urlopen", fake_urlopen)

    filename = client().stage_input_file(src, name_hint="keyframe-42")

    assert filename == "studio-keyframe-42 (1).png"


def test_stage_input_file_raises_generation_error_on_upload_failure(monkeypatch, tmp_path):
    src = tmp_path / "source.png"
    src.write_bytes(b"x")

    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 500, "Internal Server Error", hdrs=None, fp=io.BytesIO(b""))

    monkeypatch.setattr("lyria_auto.providers.comfyui.urlopen", fake_urlopen)

    with pytest.raises(GenerationError, match="上傳檔案到 ComfyUI 失敗"):
        client().stage_input_file(src, name_hint="keyframe-42")
