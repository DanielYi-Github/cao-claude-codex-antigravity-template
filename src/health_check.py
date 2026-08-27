"""Small dependency-free validator for the synthetic project-health fixture."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "src" / "data" / "project_health.json"


def load_health_data() -> dict:
    with DATA_FILE.open(encoding="utf-8") as handle:
        data = json.load(handle)

    project = data.get("project")
    checks = data.get("checks")
    if not isinstance(project, dict) or not isinstance(checks, list):
        raise ValueError("expected project object and checks array")

    score = project.get("health_score")
    if not isinstance(score, int) or not 0 <= score <= 100:
        raise ValueError("project.health_score must be an integer from 0 to 100")

    allowed_statuses = {"passing", "warning", "failing"}
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("each check must be an object")
        if check.get("status") not in allowed_statuses:
            raise ValueError(f"unknown check status: {check.get('status')!r}")
        duration = check.get("duration_ms")
        if not isinstance(duration, int) or duration < 0:
            raise ValueError("checks[].duration_ms must be a non-negative integer")

    return data


def main() -> int:
    data = load_health_data()
    print(
        f"OK: {data['project']['name']} | "
        f"health={data['project']['health_score']} | "
        f"checks={len(data['checks'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
