import shutil

import pytest

from lyria_auto.config import load_config
from lyria_auto.errors import ConfigurationError
from lyria_auto.pipeline import Pipeline


def test_invalid_channel_raises_and_leaves_no_job_row(tmp_path):
    root = tmp_path / "project"
    shutil.copytree("config", root / "config")
    (root / "config" / "channels.yaml").write_text((root / "config" / "channels.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_config(root)
    pipeline = Pipeline(cfg)
    try:
        assert pipeline.db.recent_jobs(10) == []
        with pytest.raises(ConfigurationError):
            pipeline.run_one("does-not-exist", upload=False, dry_run=True)
        # 頻道驗證必須在 create_job 之前失敗：DB 裡不應該留下這次失敗的 job 紀錄。
        assert pipeline.db.recent_jobs(10) == []
    finally:
        pipeline.close()
