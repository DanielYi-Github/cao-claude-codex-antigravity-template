from __future__ import annotations

import argparse
import json
import os
import sys

from .config import load_config
from .doctor import run_doctor
from .errors import LyriaAutoError
from .logging_utils import configure_logging
from .pipeline import Pipeline
from .prompt_engine import PromptEngine
from .providers.youtube import YouTubeClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lyria-auto", description="Lyria 3 音樂生成與 YouTube 自動發布")
    parser.add_argument("--root", default=".", help="專案根目錄")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="檢查環境與憑證")
    doctor.add_argument("--channel", default="main")

    auth = sub.add_parser("authorize-youtube", help="首次 YouTube OAuth 授權")
    auth.add_argument("--channel", default="main")

    preview = sub.add_parser("prompt-preview", help="預覽 Prompt，不呼叫 API")
    preview.add_argument("--count", type=int, default=20)

    run = sub.add_parser("run", help="執行生成與發布流程")
    run.add_argument("--videos", type=int, default=1)
    run.add_argument("--channel", default="main")
    run.add_argument("--upload", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument(
        "--from-job", type=int, default=None, metavar="ID",
        help="用既有 dry-run 工作的計畫實際生成，文案與縮圖與當時預覽的完全一致",
    )

    sub.add_parser("scheduler", help="啟動常駐排程器")

    studio = sub.add_parser("studio", help="啟動視覺工作室審核台（六道人工關卡，見 studio-architecture-plan.md）")
    studio.add_argument("--host", default="127.0.0.1")
    studio.add_argument("--port", type=int, default=8799)

    status =     config_migrate = sub.add_parser("config-migrate", help="設定遷移（視覺模組）")
    config_migrate.add_argument("--preview", action="store_true", help="僅預覽，不寫入")
    config_migrate.add_argument("--apply", action="store_true", help="建立備份並套用遷移")
    config_migrate.add_argument("--enable-visual", action="store_true", help="啟用視覺模組")

    status = sub.add_parser("status", help="查看近期工作")
    status.add_argument("--limit", type=int, default=20)

    resume = sub.add_parser("resume", help="續跑未完成的工作")
    resume.add_argument("--job", type=int, default=None, help="指定 job id，省略則自動找最新一個")
    resume.add_argument("--upload", action="store_true")

    # Visual pipeline commands
    preflight = sub.add_parser("visual-preflight", help="執行視覺模型之付費前置驗證（Watermark Smoke Test）")
    preflight.add_argument("--watermark-smoke-test", action="store_true", help="執行強制生成測試用內容")
    preflight.add_argument("--allow-image-outputs", type=int, default=0, help="授權圖片生成次數上限")
    preflight.add_argument("--allow-video-outputs", type=int, default=0, help="授權影片生成次數上限")
    preflight.add_argument("--status", type=int, default=None, metavar="RUN_ID", help="檢查某次 Preflight 的狀態")
    preflight.add_argument("--resume", type=int, default=None, metavar="RUN_ID", help="從失敗/暫停的狀態接續 Preflight")
    preflight.add_argument("--approve", action="store_true", help="批准素材")
    preflight.add_argument("--reject", action="store_true", help="拒絕素材")
    preflight.add_argument("--run-id", type=int, default=None, help="批准/拒絕的 Run ID")

    review = sub.add_parser("review", help="檢視並批准/拒絕視覺素材")
    review.add_argument("--job", type=int, required=True, help="Job ID")
    review.add_argument("--approve", action="store_true", help="批准素材")
    review.add_argument("--reject", action="store_true", help="拒絕素材")

    regenerate = sub.add_parser("regenerate", help="重新生成視覺素材")
    regenerate.add_argument("--job", type=int, required=True, help="Job ID")
    regenerate.add_argument("--scene", type=str, default=None, help="指定場景標籤（A/B/C/D），省略則全部重新生成")

    report = sub.add_parser("report", help="顯示營運指標報告")
    report.add_argument("--job", type=int, default=None, help="指定 job id，省略則顯示所有工作")

    return parser


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    config = load_config(args.root)
    log_dir = config.root / config.section("project").get("logs", "logs")
    configure_logging(log_dir)

    if args.command == "doctor":
        rows = run_doctor(config, args.channel)
        for name, ok, detail in rows:
            print(("[OK] " if ok else "[FAIL] ") + f"{name}: {detail}")
        if not all(ok for _, ok, _ in rows):
            sys.exit(1)
        return

    if args.command == "authorize-youtube":
        channel = config.channel(args.channel)
        token = YouTubeClient.authorize(channel, config.root)
        print(f"YouTube 授權完成：{token}")
        return

    if args.command == "prompt-preview":
        generation = config.section("generation")
        project = config.section("project")
        engine = PromptEngine(config.prompts, generation.get("prompt_profile", "coffeehouse_lofi_jazz"), int(project.get("random_seed", 1)))
        plans = engine.compose_album(args.count)
        for i, plan in enumerate(plans, 1):
            print(f"\n### {i:03d}\n{plan.prompt}")
        return

    if args.command == "config-migrate":
        from .config_migration import (
            apply_settings_migration,
            enable_visual,
            preview_settings_migration,
        )

        settings_path = config.root / "config" / "settings.yaml"
        if args.preview:
            result = preview_settings_migration(settings_path)
            print(f"Missing sections: {result.missing_sections}")
            print(f"Would create backup: {result.would_create_backup}")
            return
        if args.apply:
            apply_settings_migration(settings_path)
            print("設定遷移已完成（visual.enabled = false）")
            return
        if args.enable_visual:
            enable_visual(settings_path)
            print("視覺模組已啟用（visual.enabled = true）")
            return
        print("請指定 --preview、--apply 或 --enable-visual")
        sys.exit(1)

    if args.command == "scheduler":
        from .scheduler import start_scheduler
        start_scheduler(config)
        return

    if args.command == "studio":
        import uvicorn

        from .providers.comfyui import ComfyUIClient
        from .studio import veo as veo_support
        from .studio.app import create_app
        from .studio.stages import build_handlers
        from .studio.worker import StudioWorker

        pipeline = Pipeline(config)
        studio_cfg = config.section("studio")
        local_comfyui = ComfyUIClient(
            base_url=studio_cfg.get("comfyui_base_url", "http://127.0.0.1:8188")
        )
        # Empty (the default) means every stage runs on local_comfyui.
        # generate_clip and upscale_clip are the two GPU-heavy enough to be
        # worth a rented instance -- see the 2026-08-18 cloud-economics
        # writeup for why keyframe/motion-test stay local either way.
        remote_url = studio_cfg.get("comfyui_remote_base_url", "")
        remote_comfyui = ComfyUIClient(base_url=remote_url) if remote_url else None
        workflows_dir = (config.root / studio_cfg["comfyui_workflows_dir"]).resolve()

        # Google Veo path (artifacts/spec.md). AppConfig already called
        # load_dotenv, so a key in .env is visible here; when there isn't
        # one the credential simply starts empty and tab 2 shows its
        # input box instead. The key lives only in this in-memory holder,
        # shared by the HTTP app and the worker's handlers -- it is never
        # written to the database, a task payload, or a log line.
        veo_credential = veo_support.VeoCredential(os.environ.get("GEMINI_API_KEY"))

        def veo_client_factory():
            """Build a Gemini client from whatever key is loaded right now.

            Rebuilt per call rather than cached because the key can
            arrive from tab 2 long after startup, and because nothing
            holding a key should outlive the call that needed it.
            """
            key = veo_credential.get()
            if not key:
                return None
            from .providers.gemini_visual import GeminiVisualClient

            visual_cfg = config.section("visual")
            return GeminiVisualClient(
                api_key=key,
                image_model=visual_cfg.get("image_model", "gemini-3.1-flash-image"),
                video_model=studio_cfg.get("veo", {}).get(
                    "default_model", "veo-3.1-fast-generate-preview"
                ),
            )

        # Which models this key can actually reach, so the dropdown can
        # grey out the rest. Best-effort and non-fatal: an empty result
        # means "unknown", never "nothing available" (see veo.py).
        reachable = veo_support.probe_available_models(
            getattr(veo_client_factory(), "client", None)
        )
        # build_loop / render_final have no handler yet (Stage 4/5, see
        # studio-architecture-plan.md 六) -- a task reaching those types
        # fails loudly with "no handler registered" instead of hanging.
        handlers = build_handlers(
            config, local_comfyui, workflows_dir,
            remote_comfyui=remote_comfyui,
            veo_client_factory=veo_client_factory,
        )
        worker = StudioWorker(pipeline.db, handlers=handlers)
        worker.start()
        try:
            app = create_app(
                pipeline.db, config, local_comfyui,
                veo_credential=veo_credential,
                veo_reachable_models=reachable,
            )
            print(f"Lyria Studio: http://{args.host}:{args.port}")
            uvicorn.run(app, host=args.host, port=args.port)
        finally:
            worker.stop()
            pipeline.close()
        return

    pipeline = Pipeline(config)
    try:
        try:
            if args.command == "run":
                if args.from_job is not None:
                    if args.dry_run:
                        raise LyriaAutoError("--from-job 是要實際生成，不能與 --dry-run 併用。")
                    result = pipeline.run_from_job(args.from_job, args.upload)
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    results = pipeline.run_batch(args.videos, args.channel, args.upload, args.dry_run)
                    print(json.dumps(results, ensure_ascii=False, indent=2))
            elif args.command == "status":
                print(json.dumps(pipeline.db.recent_jobs(args.limit), ensure_ascii=False, indent=2))
            elif args.command == "resume":
                result = pipeline.resume_one(args.job, args.upload)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.command == "review":
                if args.approve and args.reject:
                    raise LyriaAutoError("不能同時指定 --approve 和 --reject")
                if not args.approve and not args.reject:
                    raise LyriaAutoError("請指定 --approve 或 --reject")
                result = pipeline.review_assets(args.job, approve=args.approve)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.command == "regenerate":
                result = pipeline.regenerate_visual(args.job, args.scene)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.command == "report":
                result = pipeline.report_metrics(args.job)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            elif args.command == "visual-preflight":
                from .visual_preflight import VisualPreflightService
                op = VisualPreflightService(config, pipeline.db)
                if args.status is not None:
                    print(f"Status of run {args.status}")
                elif args.resume is not None:
                    run_id = op.resume(args.resume)
                    print(f"Resumed run {run_id}")
                elif args.approve or args.reject:
                    if args.run_id is None:
                        raise LyriaAutoError("Please provide --run-id")
                    op.approve(args.run_id, image_ok=args.approve, video_ok=args.approve)
                    print("Approved" if args.approve else "Rejected")
                elif args.watermark_smoke_test:
                    run_id = op.start(allow_image_outputs=args.allow_image_outputs, allow_video_outputs=args.allow_video_outputs)
                    print(f"Started Preflight Run ID: {run_id}")
                else:
                    raise LyriaAutoError("Invalid visual-preflight arguments")
        except LyriaAutoError as exc:
            print(str(exc))
            sys.exit(1)
    finally:
        pipeline.close()
