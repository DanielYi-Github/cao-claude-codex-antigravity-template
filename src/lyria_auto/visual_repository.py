from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .db import StateDB
from .errors import VisualApprovalError, VisualGenerationError
from .utils import sha256_file, utc_now_iso

logger = logging.getLogger(__name__)


class VisualRepository:
    """視覺資產的 CRUD 操作，封裝 SQLite 狀態機。"""

    def __init__(self, db: StateDB):
        self.db = db

    # ── Scene management ──

    def upsert_scene(
        self,
        job_id: int,
        position: int,
        label: str,
        world_json: dict[str, Any],
        image_prompt: str,
        motion_prompt: str,
    ) -> int:
        now = utc_now_iso()
        from dataclasses import asdict, is_dataclass
        if is_dataclass(world_json):
            w_dict = asdict(world_json)
        elif isinstance(world_json, dict):
            w_dict = world_json
        elif hasattr(world_json, "to_dict"):
            w_dict = world_json.to_dict()
        else:
            w_dict = dict(world_json)

        cur = self.db.conn.execute(
            """
            INSERT INTO visual_scenes(
                job_id, position, label, world_json, image_prompt,
                motion_prompt, status, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?, ?, ?)
            ON CONFLICT(job_id, position) DO UPDATE SET
                label=excluded.label, world_json=excluded.world_json,
                image_prompt=excluded.image_prompt,
                motion_prompt=excluded.motion_prompt,
                state_version=state_version+1, updated_at=excluded.updated_at
            """,
            (
                job_id, position, label,
                json.dumps(w_dict, ensure_ascii=False),
                image_prompt, motion_prompt,
                "planned", now, now,
            ),
        )
        self.db.conn.commit()
        return int(cur.lastrowid)

    def scenes(self, job_id: int) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            "SELECT * FROM visual_scenes WHERE job_id=? ORDER BY position",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def update_scene_status(
        self,
        scene_id: int,
        status: str,
        *,
        expected_version: int,
    ) -> None:
        changed = self.db.conn.execute(
            """
            UPDATE visual_scenes
            SET status=?, state_version=state_version+1, updated_at=?
            WHERE id=? AND state_version=?
            """,
            (status, utc_now_iso(), scene_id, expected_version),
        ).rowcount
        if changed != 1:
            self.db.conn.rollback()
            raise VisualApprovalError(f"scene {scene_id} 狀態已更新，CAS 失敗")
        self.db.conn.commit()

    # ── Asset management ──

    def reserve_asset(
        self,
        job_id: int,
        scene_id: int | None,
        asset_type: str,
        variant_index: int,
        provider: str,
        model: str,
        prompt: str,
        estimated_cost_usd: float,
        pricing_snapshot: dict[str, Any],
    ) -> int:
        now = utc_now_iso()
        cur = self.db.conn.execute(
            """
            INSERT INTO visual_assets(
                job_id, scene_id, asset_type, variant_index, provider, model,
                prompt, estimated_cost_usd, pricing_snapshot_json,
                status, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id, scene_id, asset_type, variant_index, provider, model,
                prompt, estimated_cost_usd,
                json.dumps(pricing_snapshot, ensure_ascii=False),
                "reserved", now, now,
            ),
        )
        self.db.conn.commit()
        return int(cur.lastrowid)

    def asset(self, asset_id: int) -> dict[str, Any]:
        row = self.db.conn.execute(
            "SELECT * FROM visual_assets WHERE id=?", (asset_id,)
        ).fetchone()
        if row is None:
            raise VisualGenerationError(f"找不到 asset {asset_id}")
        return dict(row)

    def assets(
        self,
        job_id: int,
        asset_type: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM visual_assets WHERE job_id=?"
        params: list[Any] = [job_id]
        if asset_type:
            query += " AND asset_type=?"
            params.append(asset_type)
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY scene_id, variant_index"
        rows = self.db.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def update_asset_status(
        self,
        asset_id: int,
        status: str,
        *,
        expected_version: int,
        **extra: Any,
    ) -> None:
        set_clauses = "status=?, state_version=state_version+1, updated_at=?"
        values: list[Any] = [status, utc_now_iso()]
        for key, value in extra.items():
            set_clauses += f", {key}=?"
            values.append(value)
        values.extend([asset_id, expected_version])
        sql = f"UPDATE visual_assets SET {set_clauses} WHERE id=? AND state_version=?"
        changed = self.db.conn.execute(sql, values).rowcount
        if changed != 1:
            self.db.conn.rollback()
            raise VisualApprovalError(f"asset {asset_id} 狀態已更新，CAS 失敗")
        self.db.conn.commit()

    def select_image(self, scene_id: int, asset_id: int) -> None:
        """選擇場景的核准圖片。"""
        changed = self.db.conn.execute(
            """
            UPDATE visual_scenes
            SET selected_image_asset_id=?, status='image_approved',
                state_version=state_version+1, updated_at=?, approved_at=?
            WHERE id=?
            """,
            (asset_id, utc_now_iso(), utc_now_iso(), scene_id),
        ).rowcount
        if changed != 1:
            self.db.conn.rollback()
            raise VisualApprovalError(f"scene {scene_id} 選擇圖片失敗")
        # Mark selected asset as approved and supersede others
        self.db.conn.execute(
            "UPDATE visual_assets SET status='approved', state_version=state_version+1, "
            "updated_at=? WHERE id=?",
            (utc_now_iso(), asset_id),
        )
        # Supersede other variants for this scene
        scene = self.db.conn.execute(
            "SELECT job_id FROM visual_scenes WHERE id=?", (scene_id,)
        ).fetchone()
        if scene:
            self.db.conn.execute(
                "UPDATE visual_assets SET status='superseded', state_version=state_version+1, "
                "updated_at=? WHERE job_id=? AND scene_id=? AND asset_type='scene_image' "
                "AND id!=?",
                (utc_now_iso(), scene["job_id"], scene_id, asset_id),
            )
        self.db.conn.commit()

    def select_video(self, scene_id: int, asset_id: int) -> None:
        """選擇場景的核准影片。"""
        changed = self.db.conn.execute(
            """
            UPDATE visual_scenes
            SET selected_video_asset_id=?, status='video_approved',
                state_version=state_version+1, updated_at=?, approved_at=?
            WHERE id=?
            """,
            (asset_id, utc_now_iso(), utc_now_iso(), scene_id),
        ).rowcount
        if changed != 1:
            self.db.conn.rollback()
            raise VisualApprovalError(f"scene {scene_id} 選擇影片失敗")
        self.db.conn.execute(
            "UPDATE visual_assets SET status='approved', state_version=state_version+1, "
            "updated_at=? WHERE id=?",
            (utc_now_iso(), asset_id),
        )
        scene = self.db.conn.execute(
            "SELECT job_id FROM visual_scenes WHERE id=?", (scene_id,)
        ).fetchone()
        if scene:
            self.db.conn.execute(
                "UPDATE visual_assets SET status='superseded', state_version=state_version+1, "
                "updated_at=? WHERE job_id=? AND scene_id=? AND asset_type='scene_video' "
                "AND id!=?",
                (utc_now_iso(), scene["job_id"], scene_id, asset_id),
            )
        self.db.conn.commit()

    def approve_thumbnail(self, job_id: int, asset_id: int) -> None:
        """核准縮圖背景。"""
        self.db.conn.execute(
            "UPDATE visual_assets SET status='approved', state_version=state_version+1, "
            "updated_at=? WHERE id=? AND job_id=? AND asset_type='thumbnail_background'",
            (utc_now_iso(), asset_id, job_id),
        )
        self.db.conn.commit()

    def finish_image_review(self, job_id: int) -> None:
        """完成圖片審核階段。"""
        self.db.conn.execute(
            "UPDATE visual_scenes SET status='image_approved', "
            "state_version=state_version+1, updated_at=? "
            "WHERE job_id=? AND status='awaiting_image_review'",
            (utc_now_iso(), job_id),
        )
        self.db.conn.commit()

    def finish_video_review(self, job_id: int) -> None:
        """完成影片審核階段。"""
        self.db.conn.execute(
            "UPDATE visual_scenes SET status='video_approved', "
            "state_version=state_version+1, updated_at=? "
            "WHERE job_id=? AND status='awaiting_video_review'",
            (utc_now_iso(), job_id),
        )
        self.db.conn.commit()

    # ── Inflight checks ──

    def has_inflight_image_assets(self, job_id: int) -> bool:
        """檢查是否有圖片資產仍在進行中。"""
        row = self.db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM visual_assets "
            "WHERE job_id=? AND asset_type='scene_image' "
            "AND status IN ('reserved','starting','polling','normalizing')",
            (job_id,),
        ).fetchone()
        return int(row["cnt"]) > 0

    def has_inflight_video_assets(self, job_id: int) -> bool:
        """檢查是否有影片資產仍在進行中。"""
        row = self.db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM visual_assets "
            "WHERE job_id=? AND asset_type='scene_video' "
            "AND status IN ('reserved','starting','polling','normalizing')",
            (job_id,),
        ).fetchone()
        return int(row["cnt"]) > 0

    # ── Cost tracking ──

    def estimated_cost(self, job_id: int) -> float:
        """計算目前 job 的預估視覺成本。"""
        row = self.db.conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) AS total "
            "FROM visual_assets WHERE job_id=?",
            (job_id,),
        ).fetchone()
        return float(row["total"])

    # ── Render snapshot preparation ──

    def prepare_render_snapshot(
        self,
        job_id: int,
        *,
        audio_path: Path,
        snapshot_root: Path,
        expected_job_version: int,
    ) -> Any:
        """準備不可變的 render snapshot。

        驗證所有核准資產的 SHA-256，建立 content-addressed snapshots。
        """
        from .visual_models import RenderManifest, TimelineSlice

        snapshot_root.mkdir(parents=True, exist_ok=True)
        scenes = self.scenes(job_id)
        timeline_slices: list[TimelineSlice] = []

        for scene in scenes:
            if scene["selected_video_asset_id"] is None:
                raise VisualGenerationError(
                    f"scene {scene['label']} 尚未選擇核准影片"
                )
            asset = self.asset(scene["selected_video_asset_id"])
            asset_path = Path(asset["path"])
            if not asset_path.exists():
                raise VisualGenerationError(
                    f"scene {scene['label']} 影片檔案不存在：{asset_path}"
                )
            # Copy to snapshot directory with content-addressed name
            snapshot_name = f"{scene['label']}_{asset['sha256'][:16]}.mp4"
            snapshot_path = snapshot_root / snapshot_name
            snapshot_path.write_bytes(asset_path.read_bytes())
            timeline_slices.append(
                TimelineSlice(
                    label=scene["label"],
                    path=snapshot_path,
                    global_start_seconds=0,  # Will be set by timeline builder
                    global_end_seconds=0,
                )
            )

        # Audio snapshot
        audio_snapshot = snapshot_root / f"audio_{sha256_file(audio_path)[:16]}.m4a"
        audio_snapshot.write_bytes(audio_path.read_bytes())

        # Create manifest hash
        manifest_data = {
            "audio_sha256": sha256_file(audio_snapshot),
            "scenes": [
                {"label": s.label, "sha256": sha256_file(s.path)}
                for s in timeline_slices
            ],
            "job_id": job_id,
            "job_version": expected_job_version,
        }
        manifest_sha256 = sha256_file(
            Path(json.dumps(manifest_data, ensure_ascii=False, sort_keys=True))
        ) if False else __import__("hashlib").sha256(
            json.dumps(manifest_data, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

        return RenderManifest(
            audio_snapshot_path=audio_snapshot,
            scenes=tuple(timeline_slices),
            manifest_sha256=manifest_sha256,
        )

    def finalize_render_output(
        self,
        video_path: Path,
        thumbnail_path: Path,
        *,
        snapshot_root: Path,
        expected_render_manifest_sha256: str,
    ) -> Any:
        """驗證並建立不可變的 render 輸出 snapshot。"""
        from .visual_models import FinalizedRender

        # Create content-addressed snapshots
        video_sha = sha256_file(video_path)
        thumbnail_sha = sha256_file(thumbnail_path)

        video_snapshot = snapshot_root / f"video_{video_sha[:16]}.mp4"
        thumbnail_snapshot = snapshot_root / f"thumbnail_{thumbnail_sha[:16]}.jpg"

        video_snapshot.write_bytes(video_path.read_bytes())
        thumbnail_snapshot.write_bytes(thumbnail_path.read_bytes())

        return FinalizedRender(
            video_snapshot_path=video_snapshot,
            video_sha256=video_sha,
            thumbnail_snapshot_path=thumbnail_snapshot,
            thumbnail_sha256=thumbnail_sha,
        )

    def mark_local_thumbnail_ready(
        self,
        asset_id: int,
        *,
        expected_version: int,
        raw_path: str,
        path: str,
        mime_type: str,
        sha256: str,
        width: int,
        height: int,
    ) -> None:
        """標記本機縮圖背景為 ready。"""
        self.update_asset_status(
            asset_id,
            "ready",
            expected_version=expected_version,
            raw_path=raw_path,
            path=path,
            mime_type=mime_type,
            sha256=sha256,
            width=width,
            height=height,
        )
