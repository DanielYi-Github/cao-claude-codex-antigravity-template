"""Tests for metrics collection and reporting."""

from __future__ import annotations

from pathlib import Path

from lyria_auto.metrics import MetricEvent, MetricsCollector


def test_metrics_collector_records_event(tmp_path: Path) -> None:
    """Recording an event should persist it to disk."""
    collector = MetricsCollector(tmp_path / "metrics")

    event = MetricEvent(
        timestamp="2026-07-29T10:00:00+00:00",
        event_type="image_generated",
        job_id="job_001",
        duration_seconds=5.5,
        cost_usd=0.04,
        status="success",
    )

    collector.record(event)

    summary = collector.get_summary("job_001")
    assert summary.total_events == 1
    assert summary.image_count == 1
    assert abs(summary.total_cost_usd - 0.04) < 0.001
    assert abs(summary.total_duration_seconds - 5.5) < 0.001


def test_metrics_collector_aggregates_multiple_events(tmp_path: Path) -> None:
    """Multiple events should be aggregated correctly."""
    collector = MetricsCollector(tmp_path / "metrics")

    events = [
        MetricEvent("2026-07-29T10:00:00+00:00", "image_generated", "job_001", cost_usd=0.04, status="success"),
        MetricEvent("2026-07-29T10:01:00+00:00", "image_generated", "job_001", cost_usd=0.04, status="success"),
        MetricEvent("2026-07-29T10:02:00+00:00", "video_generated", "job_001", cost_usd=2.50, status="success"),
        MetricEvent("2026-07-29T10:03:00+00:00", "render_completed", "job_001", status="success"),
        MetricEvent("2026-07-29T10:04:00+00:00", "review_approved", "job_001", status="success"),
    ]

    for event in events:
        collector.record(event)

    summary = collector.get_summary("job_001")
    assert summary.total_events == 5
    assert summary.image_count == 2
    assert summary.video_count == 1
    assert summary.render_count == 1
    assert summary.review_approvals == 1
    assert abs(summary.total_cost_usd - 2.58) < 0.001  # 0.04 + 0.04 + 2.50 + None + None


def test_metrics_collector_tracks_failures(tmp_path: Path) -> None:
    """Failed events should be counted separately."""
    collector = MetricsCollector(tmp_path / "metrics")

    events = [
        MetricEvent("2026-07-29T10:00:00+00:00", "image_generated", "job_001", status="success"),
        MetricEvent("2026-07-29T10:01:00+00:00", "image_generated", "job_001", status="failure"),
        MetricEvent("2026-07-29T10:02:00+00:00", "video_generated", "job_001", status="skipped"),
    ]

    for event in events:
        collector.record(event)

    summary = collector.get_summary("job_001")
    assert summary.success_count == 1
    assert summary.failure_count == 1
    assert summary.skipped_count == 1


def test_metrics_collector_export_json(tmp_path: Path) -> None:
    """Export should return a JSON-serializable dictionary."""
    collector = MetricsCollector(tmp_path / "metrics")

    collector.record(MetricEvent(
        "2026-07-29T10:00:00+00:00",
        "image_generated",
        "job_001",
        cost_usd=0.04,
        status="success",
    ))

    export = collector.export_json("job_001")
    assert isinstance(export, dict)
    assert export["total_events"] == 1
    assert export["image_count"] == 1
    assert len(export["events"]) == 1


def test_metrics_collector_all_jobs_summary(tmp_path: Path) -> None:
    """Getting summary without job_id should aggregate all jobs."""
    collector = MetricsCollector(tmp_path / "metrics")

    # Record events for multiple jobs
    collector.record(MetricEvent("2026-07-29T10:00:00+00:00", "image_generated", "job_001", status="success"))
    collector.record(MetricEvent("2026-07-29T10:01:00+00:00", "image_generated", "job_002", status="success"))
    collector.record(MetricEvent("2026-07-29T10:02:00+00:00", "video_generated", "job_003", status="success"))

    summary = collector.get_summary()  # No job_id
    assert summary.total_events == 3
    assert summary.image_count == 2
    assert summary.video_count == 1
