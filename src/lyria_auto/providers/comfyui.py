"""Thin HTTP client for a ComfyUI instance's REST API -- local or remote.

Uses urllib from the standard library rather than adding a runtime HTTP
dependency: submit/poll/upload/download here is plain JSON POST/GET plus a
file transfer, well within what urllib handles without ceremony. (httpx is
only a dev/test dependency, pulled in by FastAPI's TestClient.)

Every operation goes through ComfyUI's HTTP API (POST /prompt, GET
/history, POST /upload/image, GET /view) rather than touching ComfyUI's
input/output directories on disk -- so a ComfyUIClient pointed at
127.0.0.1 and one pointed at a rented GPU box work identically; only
base_url changes. Verified against a running local instance (ComfyUI
0.33.1): /upload/image accepts arbitrary file types, not just images --
the "image" in the endpoint name is historical.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..errors import GenerationError


class ComfyUIClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def stage_input_file(self, src: str | Path, *, name_hint: str) -> str:
        """Upload src so a LoadImage/LoadVideo node can reference it by filename.

        Returns the name ComfyUI actually saved it under (it appends a
        counter on a collision), not the name we sent -- that's the value
        to put in the node's widget.
        """
        src = Path(src)
        filename = f"studio-{name_hint}{src.suffix}"
        boundary = uuid.uuid4().hex
        body = _multipart_body(boundary, "image", filename, src.read_bytes())
        request = Request(
            f"{self.base_url}/upload/image",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urlopen(request, timeout=120) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GenerationError(f"上傳檔案到 ComfyUI 失敗（HTTP {exc.code}）：{detail}") from exc
        except URLError as exc:
            raise GenerationError(f"連不上 ComfyUI（{self.base_url}）：{exc.reason}") from exc
        return payload.get("name", filename)

    def submit(self, workflow: dict[str, Any]) -> str:
        body = json.dumps({"prompt": workflow, "client_id": "lyria-studio"}).encode("utf-8")
        request = Request(
            f"{self.base_url}/prompt", data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GenerationError(f"ComfyUI 拒絕了工作流（HTTP {exc.code}）：{detail}") from exc
        except URLError as exc:
            raise GenerationError(f"連不上 ComfyUI（{self.base_url}）：{exc.reason}") from exc
        prompt_id = payload.get("prompt_id")
        if not prompt_id:
            raise GenerationError(f"ComfyUI 回應沒有 prompt_id：{payload}")
        return prompt_id

    def wait_for_result(
        self, prompt_id: str, *, poll_interval: float = 2.0, timeout: float = 5400.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            history = self._get_json(f"/history/{prompt_id}")
            entry = history.get(prompt_id)
            if entry is not None:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise GenerationError(
                        f"ComfyUI 生成失敗（prompt_id={prompt_id}）：{status.get('messages')}"
                    )
                if entry.get("outputs"):
                    return entry
            if time.monotonic() > deadline:
                # Giving up client-side doesn't stop ComfyUI from still
                # working on it -- interrupt so it actually frees the queue
                # before the (single-threaded) worker moves on to the next
                # task, otherwise two generations end up contending for the
                # same GPU/model memory, which is exactly what the
                # single-worker design exists to prevent.
                self.interrupt()
                raise GenerationError(
                    f"等待 ComfyUI 生成逾時（prompt_id={prompt_id}，{timeout:.0f}s），已送出中斷"
                )
            time.sleep(poll_interval)

    def interrupt(self) -> None:
        try:
            urlopen(Request(f"{self.base_url}/interrupt", data=b""), timeout=10).close()
        except (HTTPError, URLError):
            pass

    def fetch_output(self, history_entry: dict[str, Any], dest: str | Path) -> Path:
        dest = Path(dest)
        for node_output in history_entry.get("outputs", {}).values():
            for items in node_output.values():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict) and "filename" in item:
                        return self._download(item, dest)
        raise GenerationError(f"ComfyUI 的輸出裡找不到檔案：{history_entry.get('outputs')}")

    def _download(self, item: dict[str, Any], dest: Path) -> Path:
        params = {
            "filename": item["filename"],
            "subfolder": item.get("subfolder", ""),
            "type": item.get("type", "output"),
        }
        url = f"{self.base_url}/view?{urlencode(params)}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urlopen(url, timeout=60) as response, dest.open("wb") as f:
                shutil.copyfileobj(response, f)
        except URLError as exc:
            raise GenerationError(f"從 ComfyUI 下載輸出檔失敗：{exc.reason}") from exc
        return dest

    def _get_json(self, path: str) -> dict[str, Any]:
        try:
            with urlopen(f"{self.base_url}{path}", timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except URLError as exc:
            raise GenerationError(f"連不上 ComfyUI（{self.base_url}）：{exc.reason}") from exc


def _multipart_body(boundary: str, field_name: str, filename: str, content: bytes) -> bytes:
    parts = [
        f"--{boundary}".encode(),
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"'.encode(),
        b"Content-Type: application/octet-stream",
        b"",
        content,
        f"--{boundary}--".encode(),
        b"",
    ]
    return b"\r\n".join(parts)
