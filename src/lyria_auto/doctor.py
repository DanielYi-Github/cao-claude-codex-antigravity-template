from __future__ import annotations

import importlib.util
import os
import shutil

from .config import AppConfig


def _check_gemini_sdk_capabilities() -> tuple[bool, str]:
    try:
        from google import genai
    except ImportError:
        return False, "google-genai not installed"

    version = getattr(genai, "__version__", "unknown")
    client = genai.Client(api_key="doctor-placeholder")
    has_interactions = hasattr(client, "interactions") and callable(getattr(client.interactions, "create", None))
    has_generate_videos = hasattr(client, "models") and callable(getattr(client.models, "generate_videos", None))
    has_operations = hasattr(client, "operations") and callable(getattr(client.operations, "get", None))

    caps = []
    if has_interactions:
        caps.append("interactions.create")
    if has_generate_videos:
        caps.append("models.generate_videos")
    if has_operations:
        caps.append("operations.get")

    all_present = has_interactions and has_generate_videos and has_operations
    detail = f"gemini-developer-api, v{version}, {', '.join(caps) or 'no capabilities'}"
    return all_present, detail


def run_doctor(config: AppConfig, channel_name: str = "main") -> list[tuple[str, bool, str]]:
    rows = []
    rows.append(("ffmpeg", shutil.which("ffmpeg") is not None, shutil.which("ffmpeg") or "not found"))
    rows.append(("ffprobe", shutil.which("ffprobe") is not None, shutil.which("ffprobe") or "not found"))
    rows.append(("GEMINI_API_KEY", bool(os.getenv("GEMINI_API_KEY")), "set" if os.getenv("GEMINI_API_KEY") else "missing"))
    for module in ("google.genai", "googleapiclient", "google_auth_oauthlib", "yaml", "PIL"):
        ok = importlib.util.find_spec(module) is not None
        rows.append((module, ok, "installed" if ok else "missing"))
    ok, detail = _check_gemini_sdk_capabilities()
    rows.append(("gemini_sdk_capabilities", ok, detail))
    project = config.section("project")
    workspace = config.root / project.get("workspace", "workspace")
    try:
        workspace.mkdir(parents=True, exist_ok=True)
        probe = workspace / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        rows.append(("workspace", True, str(workspace)))
    except OSError as exc:
        rows.append(("workspace", False, str(exc)))
    if config.channels:
        try:
            channel = config.channel(channel_name)
            secret = config.root / channel["client_secret_file"]
            token = config.root / channel["token_file"]
            rows.append(("youtube client_secret", secret.exists(), str(secret)))
            rows.append(("youtube token", token.exists(), str(token)))
        except (KeyError, OSError) as exc:
            rows.append(("youtube channel config", False, str(exc)))
    return rows
