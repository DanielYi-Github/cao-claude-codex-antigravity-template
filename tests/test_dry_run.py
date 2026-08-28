import shutil
from pathlib import Path

from lyria_auto.config import load_config
from lyria_auto.pipeline import Pipeline


def test_dry_run_creates_plan_and_thumbnail(tmp_path, monkeypatch):
    root = tmp_path / "project"
    shutil.copytree("config", root / "config")
    (root / "config" / "channels.yaml").write_text((root / "config" / "channels.example.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_config(root)
    pipeline = Pipeline(cfg)
    try:
        result = pipeline.run_batch(1, "main", upload=False, dry_run=True)[0]
        assert Path(result["plan"]).exists()
        assert (Path(result["directory"]) / "thumbnail.jpg").exists()
    finally:
        pipeline.close()
