from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from .config import AppConfig
from .db import StateDB
from .errors import (
    PaidStartUncertainError,
    VisualPollTransientError,
    VisualPreflightError,
    sanitize_exception,
    structured_error,
)
from .providers.gemini_visual import GeminiVisualClient
from .utils import ensure_dir, run_command, sha256_file, utc_now_iso
from .visual_models import MediaSnapshot, VisualSource, credential_fingerprint

PREFLIGHT_IMAGE_PROMPT = (
    "Photorealistic documentary photograph of an empty neighborhood coffee shop, "
    "natural materials, physically plausible light, 16:9, no people, no text, "
    "no logo, no border, no product mark."
)
PREFLIGHT_VIDEO_PROMPT = (
    "Single continuous photorealistic shot of this empty coffee shop. Locked-off "
    "camera. Only subtle steam and curtain movement. No people, no text, no cuts, "
    "no zoom, no pan, no logo."
)


def snapshot_verified_media(
    input_path: Path,
    expected_sha256: str,
    kind: str,
    snapshot_root: Path
) -> MediaSnapshot:
    with open(input_path, "rb") as f:
        data = f.read()

    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha256:
        raise ValueError("Tampered file")

    size_bytes = len(data)
    probe = {}

    if kind == "image":
        import io
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
            probe = {"width": img.width, "height": img.height, "format": img.format}
    elif kind == "video":
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            result = run_command(
                [
                    "ffprobe", "-v", "error", "-show_streams", "-show_format",
                    "-of", "json", str(tmp_path),
                ]
            )
            probe = json.loads(result.stdout)
        finally:
            tmp_path.unlink(missing_ok=True)

    snapshot_root.mkdir(parents=True, exist_ok=True)
    ext = ".png" if kind == "image" else ".mp4"
    final_path = snapshot_root / f"{actual_sha}{ext}"
    if not final_path.exists():
        partial = final_path.with_name(final_path.stem + ".partial" + ext)
        partial.write_bytes(data)
        partial.replace(final_path)

    return MediaSnapshot(
        path=final_path,
        sha256=actual_sha,
        size_bytes=size_bytes,
        probe=probe
    )


class VisualPreflightService:
    def __init__(
        self,
        config: AppConfig,
        db: StateDB,
        *,
        sdk_client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        video_validator: Callable[[Path], None] | None = None,
    ):
        self.config = config
        self.db = db
        self.visual = config.section("visual")
        self.sleep = sleep
        self.workspace = ensure_dir(
            config.root
            / config.section("project").get("workspace", "workspace")
            / "visual_preflight"
        )
        api_key = os.getenv("GEMINI_API_KEY") or (
            "fake-key" if sdk_client is not None else ""
        )
        try:
            sdk_version = importlib.metadata.version("google-genai")
        except importlib.metadata.PackageNotFoundError:
            sdk_version = "unknown"
        self.source = VisualSource(
            provider=str(self.visual["provider"]),
            image_model=str(self.visual["image_model"]),
            video_model=str(self.visual["video_model"]),
            credential_fingerprint=credential_fingerprint(
                api_key, self.workspace / ".fingerprint.key"
            ),
            sdk_version=sdk_version,
            billing_project_id=os.getenv("GOOGLE_CLOUD_PROJECT"),
        )
        self.client = GeminiVisualClient(
            api_key=api_key,
            image_model=self.source.image_model,
            video_model=self.source.video_model,
            sdk_client=sdk_client,
            video_validator=video_validator,
        )

    def _estimated_cost(self) -> float:
        return float(self.visual["image_2k_estimated_usd"]) + (
            int(self.visual["video_duration_seconds"])
            * float(self.visual["video_1080p_second_estimated_usd"])
        )

    def _create_run(self) -> int:
        run_dir = ensure_dir(self.workspace / "pending")
        run_id = self.db.create_preflight_run(
            self.source,
            str(run_dir / "raw_image.png"),
            str(run_dir / "raw_video.mp4"),
            self._estimated_cost(),
            {
                "date": self.visual["pricing_snapshot_date"],
                "image_2k_usd": self.visual["image_2k_estimated_usd"],
                "video_1080p_second_usd": self.visual[
                    "video_1080p_second_estimated_usd"
                ],
            },
        )
        final_dir = ensure_dir(self.workspace / f"run_{run_id:06d}")
        self.db.conn.execute(
            """
            UPDATE visual_preflight_runs
            SET raw_image_path=?,raw_video_path=?,updated_at=?
            WHERE id=?
            """,
            (
                str(final_dir / "raw_image.png"),
                str(final_dir / "raw_video.mp4"),
                utc_now_iso(),
                run_id,
            ),
        )
        self.db.conn.commit()
        return run_id

    def start(self, *, allow_image_outputs: int, allow_video_outputs: int) -> int:
        preflight = self.visual["preflight"]
        expected_image = int(preflight["max_image_outputs"])
        expected_video = int(preflight["max_video_outputs"])
        if (allow_image_outputs, allow_video_outputs) != (
            expected_image,
            expected_video,
        ):
            raise VisualPreflightError(
                "付費 smoke test 必須精確授權一張圖片及一段影片"
            )
        run_id = self._create_run()
        self.db.create_exact_paid_authorizations(
            scope_type="visual_preflight",
            scope_id=run_id,
            stages={
                ("watermark_smoke", "image"): expected_image,
                ("watermark_smoke", "video"): expected_video,
            },
        )
        return self.resume(run_id)

    def resume(self, run_id: int) -> int:
        lease = self.db.acquire_side_effect_lease(
            scope_type="visual_preflight",
            scope_id=run_id,
        )
        if not lease.acquired:
            raise VisualPreflightError(
                structured_error(
                    code="VISUAL_PREFLIGHT_ALREADY_RUNNING",
                    problem="另一個 process 正在處理此 preflight",
                    cause="目前 lease 尚未過期",
                    fix="等待 owner 完成或 lease 過期後再執行 resume",
                    next_command=f"lyria-auto visual-preflight --status {run_id}",
                    job_id=run_id,
                )
            )
        row = self.db.preflight_run(run_id)
        if row is None:
            raise VisualPreflightError(f"找不到 preflight run {run_id}")
        if row["status"] not in ("running", "polling"):
            return run_id
        image_path = Path(row["raw_image_path"])
        video_path = Path(row["raw_video_path"])
        try:
            image_sha = row["image_sha256"]
            if row["image_start_state"] == "starting":
                if self.db.lease_is_active_for_owner(
                    "visual_preflight", run_id, str(row["owner_token"])
                ):
                    return run_id
                if not image_path.exists():
                    self.db.mark_preflight_start_uncertain(
                        run_id,
                        expected_version=int(row["state_version"]),
                        error_code="VISUAL_IMAGE_START_UNCERTAIN",
                    )
                    raise PaidStartUncertainError(
                        f"preflight run {run_id} 圖片 start 結果不確定；不得自動重試"
                    )
                with Image.open(image_path) as image:
                    image.verify()
                image_sha = sha256_file(image_path)
                self.db.record_preflight_image_result(
                    run_id,
                    expected_version=int(row["state_version"]),
                    owner_token=lease.owner_token,
                    image_sha256=image_sha,
                )
            elif not image_sha:
                claimed = self.db.claim_paid_start(
                    scope_type="visual_preflight",
                    scope_id=run_id,
                    stage="watermark_smoke",
                    kind="image",
                    identity_key=f"preflight:{run_id}:image",
                    expected_state="reserved",
                    expected_version=int(row["state_version"]),
                    owner_token=lease.owner_token,
                )
                if not claimed:
                    return run_id
                generated = self.client.generate_image(
                    PREFLIGHT_IMAGE_PROMPT,
                    image_path,
                    reference_paths=[],
                )
                current = self.db.preflight_run(run_id)
                image_sha = generated.sha256
                self.db.record_preflight_image_result(
                    run_id,
                    expected_version=int(current["state_version"]),
                    owner_token=lease.owner_token,
                    image_sha256=image_sha,
                )

            current = self.db.preflight_run(run_id)
            operation_id = current["video_operation_id"]
            if not operation_id:
                if current["video_start_state"] == "starting":
                    if self.db.lease_is_active_for_owner(
                        "visual_preflight",
                        run_id,
                        str(current["owner_token"]),
                    ):
                        return run_id
                    self.db.mark_preflight_start_uncertain(
                        run_id,
                        expected_version=int(current["state_version"]),
                        error_code="VISUAL_VIDEO_START_UNCERTAIN",
                    )
                    raise PaidStartUncertainError(
                        f"preflight run {run_id} Veo start 結果不確定；不得自動重試"
                    )
                claimed = self.db.claim_paid_start(
                    scope_type="visual_preflight",
                    scope_id=run_id,
                    stage="watermark_smoke",
                    kind="video",
                    identity_key=f"preflight:{run_id}:video",
                    expected_state="reserved",
                    expected_version=int(current["state_version"]),
                    owner_token=lease.owner_token,
                )
                if not claimed:
                    return run_id
                operation_id = self.client.start_video(
                    PREFLIGHT_VIDEO_PROMPT, image_path
                )
                claimed_row = self.db.preflight_run(run_id)
                self.db.record_preflight_video_operation(
                    run_id,
                    expected_version=int(claimed_row["state_version"]),
                    owner_token=lease.owner_token,
                    operation_id=operation_id,
                )

            while True:
                poll = self.client.poll_video(operation_id, video_path)
                if not poll.done:
                    self.sleep(float(self.visual["poll_interval_seconds"]))
                    continue
                if poll.error or poll.path is None or poll.sha256 is None:
                    raise VisualPreflightError(
                        poll.error or "Veo 沒有產生可下載影片"
                    )
                latest = self.db.preflight_run(run_id)
                self.db.finish_preflight_generation(
                    run_id,
                    str(image_sha),
                    poll.sha256,
                    expected_version=int(latest["state_version"]),
                    owner_token=lease.owner_token,
                )
                return run_id
        except VisualPollTransientError as exc:
            safe_error = sanitize_exception(
                exc,
                code="VISUAL_POLL_TRANSIENT",
                next_command=f"lyria-auto visual-preflight --resume {run_id}",
            )
            self.db.record_preflight_error(run_id, safe_error)
            raise VisualPreflightError(safe_error) from exc
        except PaidStartUncertainError as exc:
            safe_error = sanitize_exception(exc)
            self.db.mark_preflight_start_uncertain(
                run_id,
                expected_version=int(
                    self.db.preflight_run(run_id)["state_version"]
                ),
                error=safe_error,
            )
            raise VisualPreflightError(safe_error) from exc
        except Exception as exc:
            if isinstance(exc, VisualPreflightError):
                raise
            safe_error = sanitize_exception(exc)
            self.db.record_preflight_error(run_id, safe_error)
            raise VisualPreflightError(safe_error) from exc

    def approve(self, run_id: int, *, image_ok: bool, video_ok: bool) -> None:
        row = self.db.preflight_run(run_id)
        if row is None:
            raise VisualPreflightError(f"找不到 preflight run {run_id}")
        image_path = Path(row["raw_image_path"])
        video_path = Path(row["raw_video_path"])
        try:
            image_snapshot = snapshot_verified_media(
                image_path,
                expected_sha256=row["image_sha256"],
                kind="image",
                snapshot_root=self.workspace / "snapshots",
            )
            video_snapshot = snapshot_verified_media(
                video_path,
                expected_sha256=row["video_sha256"],
                kind="video",
                snapshot_root=self.workspace / "snapshots",
            )
        except Exception:  # noqa: BLE001
            changed = self.db.transition_preflight(
                run_id,
                expected_status="awaiting_review",
                expected_version=int(row["state_version"]),
                status="failed_tampered",
                error_code="VISUAL_PREFLIGHT_TAMPERED",
            )
            if not changed:
                raise VisualPreflightError("preflight 狀態已被其他 reviewer 更新")
            raise VisualPreflightError(
                "raw preflight 檔案已改變或無法解碼；此 run 不可核准，請建立新 run"
            )
        valid_days = int(self.visual["preflight"]["valid_days"])
        self.db.review_preflight(
            run_id,
            image_ok=image_ok,
            video_ok=video_ok,
            valid_days=valid_days,
            expected_status="awaiting_review",
            expected_version=int(row["state_version"]),
            image_snapshot=image_snapshot,
            video_snapshot=video_snapshot,
        )

    def require_valid_preflight(self) -> None:
        if self.db.valid_preflight(self.source) is None:
            raise VisualPreflightError(
                "目前 API 模式／模型／SDK／憑證沒有有效的浮水印 preflight；"
                "先執行 lyria-auto visual-preflight --watermark-smoke-test "
                "--allow-image-outputs 1 --allow-video-outputs 1"
            )
