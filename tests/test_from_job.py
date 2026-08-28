from __future__ import annotations

import json

import pytest

from lyria_auto.errors import LyriaAutoError
from lyria_auto.pipeline import Pipeline


def test_run_from_job_generates_exactly_the_dry_run_plan(project_config, fake_lyria, monkeypatch):
    """本專案最初的缺陷：`run --dry-run` 與後續的 `run` 會產出完全不同的影片，
    因為 Prompt 的隨機種子取自 job_id，每次執行都建新 job 就換了種子。
    轉正必須讓成品與 dry-run 預覽的一字不差 —— 這裡直接比對餵給 Lyria 的
    prompt 字串是否就是 plan.json 裡那幾則，若實作偷偷重新規劃就會不相等。"""
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    pipeline = Pipeline(project_config)
    try:
        dry = pipeline.run_batch(1, "main", upload=False, dry_run=True)[0]
        job_id = dry["job_id"]
        job_dir = pipeline.workspace / f"job_{job_id:06d}"
        plan_before = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
        planned_prompts = [p["prompt"] for p in plan_before["prompts"]]

        result = pipeline.run_from_job(job_id, upload=False)

        # 同一個 job，不是新開的
        assert result["job_id"] == job_id
        # 文案未被重算
        plan_after = json.loads((job_dir / "plan.json").read_text(encoding="utf-8"))
        assert plan_after["metadata"] == plan_before["metadata"]
        assert plan_after["prompts"] == plan_before["prompts"]
        # 真正餵給 Lyria 的就是原計畫那幾則 prompt
        assert fake_lyria.prompts == planned_prompts
    finally:
        pipeline.close()


def test_run_from_job_flips_dry_run_flag_and_completes(project_config, fake_lyria, monkeypatch):
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    pipeline = Pipeline(project_config)
    try:
        dry = pipeline.run_batch(1, "main", upload=False, dry_run=True)[0]
        job_id = dry["job_id"]

        pipeline.run_from_job(job_id, upload=False)

        row = pipeline.db.resumable_job(job_id)
        assert row["dry_run"] == 0
        assert row["status"] == "complete"
    finally:
        pipeline.close()


def test_run_from_job_rejects_a_real_job(project_config, fake_lyria, monkeypatch):
    """已經是正式工作的，該走 resume 而不是轉正 —— 否則會把已完成的成品重跑一遍。"""
    monkeypatch.setattr("lyria_auto.pipeline.LyriaClient", fake_lyria.client_factory())
    pipeline = Pipeline(project_config)
    try:
        real = pipeline.run_batch(1, "main", upload=False, dry_run=False)[0]

        with pytest.raises(LyriaAutoError, match="不是 dry-run"):
            pipeline.run_from_job(real["job_id"], upload=False)
    finally:
        pipeline.close()


def test_run_from_job_rejects_unknown_job(project_config):
    pipeline = Pipeline(project_config)
    try:
        with pytest.raises(LyriaAutoError, match="找不到 job"):
            pipeline.run_from_job(999999, upload=False)
    finally:
        pipeline.close()
