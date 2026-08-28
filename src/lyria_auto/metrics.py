"""Local-only operational metrics: redacted event schema and report command."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MetricEvent:
    """A single redacted metric event."""

    timestamp: str
    event_type: str  # "image_generated" | "video_generated" | "render_completed" | "upload_completed" | "review_approved" | "review_rejected"
    job_id: str
    duration_seconds: float | None = None
    cost_usd: float | None = None
    status: str = "success"  # "success" | "failure" | "skipped"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "job_id": self.job_id,
            "duration_seconds": self.duration_seconds,
            "cost_usd": self.cost_usd,
            "status": self.status,
            "details": self.details,
        }


@dataclass
class MetricsSummary:
    """Aggregated metrics summary for a job or channel."""

    total_events: int = 0
    total_cost_usd: float = 0.0
    total_duration_seconds: float = 0.0
    success_count: int = 0
    failure_count: int = 0
    skipped_count: int = 0
    image_count: int = 0
    video_count: int = 0
    render_count: int = 0
    upload_count: int = 0
    review_approvals: int = 0
    review_rejections: int = 0
    events: list[MetricEvent] = field(default_factory=list)


class MetricsCollector:
    """Collect and report local-only operational metrics."""

    def __init__(self, metrics_dir: Path):
        self._metrics_dir = metrics_dir
        self._metrics_dir.mkdir(parents=True, exist_ok=True)

    # -- public API --

    def record(self, event: MetricEvent) -> None:
        """Record a single metric event."""
        event_path = self._get_event_path(event.job_id)
        events = self._load_events(event_path)
        events.append(event.to_dict())
        event_path.write_text(json.dumps(events, indent=2), encoding="utf-8")

    def get_summary(self, job_id: str | None = None) -> MetricsSummary:
        """Get aggregated metrics summary."""
        summary = MetricsSummary()

        if job_id:
            # Single job summary
            event_path = self._get_event_path(job_id)
            if event_path.exists():
                events_data = json.loads(event_path.read_text(encoding="utf-8"))
                for entry in events_data:
                    event = MetricEvent(**entry)
                    summary.events.append(event)
                    self._accumulate(summary, event)
        else:
            # All jobs summary
            for event_file in self._metrics_dir.glob("*.json"):
                events_data = json.loads(event_file.read_text(encoding="utf-8"))
                for entry in events_data:
                    event = MetricEvent(**entry)
                    summary.events.append(event)
                    self._accumulate(summary, event)

        return summary

    def export_json(self, job_id: str | None = None) -> dict[str, Any]:
        """Export metrics as a JSON-serializable dictionary."""
        summary = self.get_summary(job_id)
        return {
            "total_events": summary.total_events,
            "total_cost_usd": summary.total_cost_usd,
            "total_duration_seconds": summary.total_duration_seconds,
            "success_count": summary.success_count,
            "failure_count": summary.failure_count,
            "skipped_count": summary.skipped_count,
            "image_count": summary.image_count,
            "video_count": summary.video_count,
            "render_count": summary.render_count,
            "upload_count": summary.upload_count,
            "review_approvals": summary.review_approvals,
            "review_rejections": summary.review_rejections,
            "events": [e.to_dict() for e in summary.events],
        }

    # -- internal helpers --

    def _get_event_path(self, job_id: str) -> Path:
        return self._metrics_dir / f"{job_id}.json"

    def _load_events(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []

    def _accumulate(self, summary: MetricsSummary, event: MetricEvent) -> None:
        summary.total_events += 1
        if event.cost_usd is not None:
            summary.total_cost_usd += event.cost_usd
        if event.duration_seconds is not None:
            summary.total_duration_seconds += event.duration_seconds

        if event.status == "success":
            summary.success_count += 1
        elif event.status == "failure":
            summary.failure_count += 1
        elif event.status == "skipped":
            summary.skipped_count += 1

        if event.event_type == "image_generated":
            summary.image_count += 1
        elif event.event_type == "video_generated":
            summary.video_count += 1
        elif event.event_type == "render_completed":
            summary.render_count += 1
        elif event.event_type == "upload_completed":
            summary.upload_count += 1
        elif event.event_type == "review_approved":
            summary.review_approvals += 1
        elif event.event_type == "review_rejected":
            summary.review_rejections += 1
