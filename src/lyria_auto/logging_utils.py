from __future__ import annotations

import logging
import os
from pathlib import Path


def configure_logging(log_dir: str | Path) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(Path(log_dir) / "lyria-auto.log", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )
