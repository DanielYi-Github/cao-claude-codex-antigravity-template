from __future__ import annotations

import pytest

from lyria_auto.media.timeline import build_timeline, transition_offsets

SCENES = [
    ("A", "a.mp4"),
    ("B", "b.mp4"),
    ("C", "c.mp4"),
    ("D", "d.mp4"),
]


@pytest.mark.parametrize(
    ("minutes", "labels", "last_window"),
    [
        (45, ["A", "B"], (1799, 2700)),
        (120, ["A", "B", "C", "D"], (5399, 7200)),
        (150, ["A", "B", "C", "D", "A"], (7199, 9000)),
        (240, ["A", "B", "C", "D", "A", "B", "C", "D"], (12599, 14400)),
        (300, ["A", "B", "C", "D", "A", "B", "C", "D", "A", "B"], (16199, 18000)),
    ],
)
def test_scene_sequence_and_global_windows(minutes, labels, last_window):
    timeline = build_timeline(minutes * 60, SCENES, interval_seconds=1800)

    assert [item.label for item in timeline] == labels
    assert (
        timeline[-1].global_start_seconds,
        timeline[-1].global_end_seconds,
    ) == last_window
    assert timeline[0].global_start_seconds == 0
    assert timeline[-1].global_end_seconds == minutes * 60


def test_45_minute_boundary_uses_two_second_overlap():
    timeline = build_timeline(45 * 60, SCENES, interval_seconds=1800)

    assert (timeline[0].global_start_seconds, timeline[0].global_end_seconds) == (0, 1801)
    assert (timeline[1].global_start_seconds, timeline[1].global_end_seconds) == (1799, 2700)
    assert transition_offsets(timeline) == [1799]


def test_timeline_rejects_no_scenes():
    with pytest.raises(ValueError, match="場景"):
        build_timeline(60, [], interval_seconds=30)


def test_timeline_rejects_zero_duration():
    with pytest.raises(ValueError, match="長度"):
        build_timeline(0, [("A", "a.mp4")], interval_seconds=30)


def test_timeline_rejects_negative_interval():
    with pytest.raises(ValueError, match="間隔"):
        build_timeline(60, [("A", "a.mp4")], interval_seconds=-1)


def test_timeline_single_scene_repeats():
    """單一場景應該重複使用。"""
    timeline = build_timeline(5400, [("A", "a.mp4")], interval_seconds=1800)
    assert [item.label for item in timeline] == ["A", "A", "A"]
    assert timeline[-1].global_end_seconds == 5400


def test_timeline_three_scenes_90_minutes():
    """90 分鐘應該產生 A→B→C 三個場景。"""
    timeline = build_timeline(90 * 60, SCENES, interval_seconds=1800)
    assert [item.label for item in timeline] == ["A", "B", "C"]
    assert len(timeline) == 3


def test_timeline_duration_property():
    """TimelineSlice.duration_seconds 應該等於 end - start。"""
    timeline = build_timeline(45 * 60, SCENES, interval_seconds=1800)
    assert timeline[0].duration_seconds == 1801
    assert timeline[1].duration_seconds == 901


def test_transition_offsets_empty_for_single_slice():
    """單一 slice 沒有 transition。"""
    timeline = build_timeline(1800, [("A", "a.mp4")], interval_seconds=1800)
    assert transition_offsets(timeline) == []


def test_transition_offsets_count():
    """10 個 slices 應該有 9 個 transitions。"""
    timeline = build_timeline(300 * 60, SCENES, interval_seconds=1800)
    assert len(timeline) == 10
    assert len(transition_offsets(timeline)) == 9
