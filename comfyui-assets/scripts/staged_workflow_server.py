#!/usr/bin/env python3
"""Local approval console for the three-stage ComfyUI cafe workflow."""

from __future__ import annotations

import argparse
import difflib
import json
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "console" / "local"
STATE_DIR = ROOT / ".staged"
STATE_FILE = STATE_DIR / "state.json"


def initial_state() -> dict:
    return {
        "stage": "image",
        "comfyui_dir": "",
        "approved_image": "",
        "image_prompt": "",
        "motion_prompt": "",
        "preview_approved": False,
        "updated_at": None,
    }


def load_state() -> dict:
    state = initial_state()
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return state


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_comfyui_dir(value: str) -> Path:
    """Accept the ComfyUI root, input directory, or output directory."""
    path = Path(value).expanduser().resolve()
    if path.name.lower() in {"input", "output"}:
        path = path.parent
    if not path.is_dir():
        raise ValueError(f"找不到 ComfyUI 資料夾：{path}")
    if not (path / "input").is_dir() and not (path / "output").is_dir():
        raise ValueError("請填 ComfyUI 根目錄，或它的 input／output 資料夾")
    return path


def find_approval_image(comfy: Path, value: str) -> Path:
    """Resolve an image already in ComfyUI input/output without escaping them."""
    supplied = Path(value).expanduser()
    candidates = [supplied.resolve()] if supplied.is_absolute() else [
        (comfy / "output" / supplied).resolve(),
        (comfy / "input" / supplied).resolve(),
    ]
    allowed = [(comfy / "output").resolve(), (comfy / "input").resolve()]
    for candidate in candidates:
        inside = any(root == candidate.parent or root in candidate.parents for root in allowed)
        if inside and candidate.is_file():
            return candidate
    available = []
    for folder in (comfy / "output", comfy / "input"):
        if folder.is_dir():
            available.extend(str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file())
    matches = difflib.get_close_matches(supplied.name, available, n=3, cutoff=0.6)
    suggestion = f"；你是不是要選：{matches[0]}" if matches else ""
    raise ValueError(f"找不到圖片；請確認檔名位於 ComfyUI 的 input 或 output 資料夾{suggestion}")


def workflow_for(stage: str, state: dict) -> dict:
    if stage == "image":
        path = ROOT / "workflows" / "cafe-keyframe-flux-mps.json"
        workflow = json.loads(path.read_text(encoding="utf-8"))
        for node in workflow["nodes"]:
            if node.get("type") == "CLIPTextEncode" and state["image_prompt"]:
                node["widgets_values"][0] = state["image_prompt"]
        return workflow

    if stage == "cloud":
        # 1080p is no longer a remote-GPU bundle: it's a local, post-process-only
        # upscale of an already-approved clip (see cafe-upscale-1080p-mps.json).
        # Re-diffusing at a higher resolution would change the approved motion,
        # so there is nothing left for this stage to hand off.
        raise ValueError(
            "1080p 升頻已改為本機後製流程，不再透過雲端打包生成。"
            "核准動作預覽後，請在 ComfyUI 開啟 cafe-flf2v-wan22-mps.json 手動跑一次正式生成"
            "（節點 9 改成 1024x576），完成後再用 cafe-upscale-1080p-mps.json 升頻到 1920x1080。"
        )

    # stage == "preview": cheap motion test-render at 768x432 (16:9, same frame
    # math as the 1024x576 official run it predicts — only resolution differs).
    path = ROOT / "workflows" / "cafe-flf2v-wan22-mps.json"
    workflow = json.loads(path.read_text(encoding="utf-8"))
    for node in workflow["nodes"]:
        node_type = node.get("type")
        if node_type == "LoadImage":
            node["widgets_values"][0] = state["approved_image"]
        elif node_type == "CLIPTextEncode" and node.get("id") == 5 and state["motion_prompt"]:
            node["widgets_values"][0] = state["motion_prompt"]
        elif node_type == "WanFirstLastFrameToVideo":
            node["widgets_values"][:3] = [768, 432, 65]
        elif node_type == "SaveVideo":
            node["widgets_values"][0] = "video/cafe-loop-preview"
    return workflow


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def json_response(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/state":
            return self.json_response(load_state())
        if path.startswith("/api/workflow/"):
            stage = path.rsplit("/", 1)[-1]
            state = load_state()
            if stage not in {"image", "preview", "cloud"}:
                return self.json_response({"error": "unknown stage"}, 404)
            if stage != "image" and not state["approved_image"]:
                return self.json_response({"error": "approve an image first"}, 409)
            try:
                workflow = workflow_for(stage, state)
            except ValueError as exc:
                return self.json_response({"error": str(exc)}, 400)
            data = json.dumps(workflow, ensure_ascii=False, indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", f'attachment; filename="cafe-{stage}.json"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        return super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path, payload, state = urlparse(self.path).path, self.body(), load_state()
        try:
            if path == "/api/approve-image":
                comfy = normalize_comfyui_dir(payload["comfyui_dir"])
                source = find_approval_image(comfy, payload["output_file"])
                target = comfy / "input" / f"approved-{source.name}"
                target.parent.mkdir(parents=True, exist_ok=True)
                if source != target:
                    shutil.copy2(source, target)
                state.update({
                    "stage": "preview",
                    "comfyui_dir": str(comfy),
                    "approved_image": target.name,
                    "image_prompt": payload.get("image_prompt", ""),
                    "motion_prompt": payload.get("motion_prompt", ""),
                    "preview_approved": False,
                })
                save_state(state)
                return self.json_response(state)
            if path == "/api/approve-preview":
                if not state["approved_image"]:
                    raise ValueError("請先核准圖片")
                state.update({"stage": "cloud", "preview_approved": True,
                              "motion_prompt": payload.get("motion_prompt", state["motion_prompt"])})
                save_state(state)
                return self.json_response(state)
            if path == "/api/reset":
                state = initial_state()
                save_state(state)
                return self.json_response(state)
            if path == "/api/cloud-bundle":
                if not state["preview_approved"]:
                    raise ValueError("請先核准 480P 預覽")
                comfy = Path(state["comfyui_dir"])
                image = comfy / "input" / state["approved_image"]
                with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                    bundle = Path(tmp.name)
                manifest = {**state, "target": "1920x1080", "duration_seconds": 8,
                            "generated_at": datetime.now(timezone.utc).isoformat()}
                with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("workflow/cafe-cloud-1080p.json",
                                     json.dumps(workflow_for("cloud", state), ensure_ascii=False, indent=2))
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                    archive.write(image, f"input/{image.name}")
                    archive.writestr("README.txt", "Upload input/ to ComfyUI/input, import the workflow JSON, then Queue Prompt.\n")
                data = bundle.read_bytes()
                bundle.unlink(missing_ok=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="cafe-cloud-1080p-bundle.zip"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            return self.json_response({"error": "not found"}, 404)
        except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
            return self.json_response({"error": str(exc)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    print(f"Cafe Loop approval console: http://{args.host}:{args.port}")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
