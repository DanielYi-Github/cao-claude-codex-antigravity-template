"""Tests for originality gate (pHash similarity check)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from lyria_auto.originality import OriginalityGate, OriginalityReport


def test_originality_gate_passes_for_unique_image(tmp_path: Path) -> None:
    """A new image with no similar existing records should pass."""
    gate = OriginalityGate(
        hash_store_path=tmp_path / "hashes.json",
        similarity_threshold=0.85,
        min_creative_evidence=0.3,
    )

    image_path = tmp_path / "test_image.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000)

    with (
        patch.object(gate, "_compute_phash", return_value=MagicMock(hash=b"deadbeef")),
        patch.object(gate, "_compute_creative_evidence", return_value=0.7),
    ):
        report = gate.check(image_path, "asset_001")

    assert isinstance(report, OriginalityReport)
    assert report.passed is True
    assert report.existing_similar_count == 0


def test_originality_gate_fails_for_similar_image(tmp_path: Path) -> None:
    """An image very similar to an existing one should fail."""
    gate = OriginalityGate(
        hash_store_path=tmp_path / "hashes.json",
        similarity_threshold=0.85,
        min_creative_evidence=0.3,
    )

    gate._records.append(
        MagicMock(asset_id="existing_001", phash="deadbeef", published_at="2026-01-01T00:00:00+00:00")
    )

    image_path = tmp_path / "test_image.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000)

    with (
        patch.object(gate, "_compute_phash", return_value=MagicMock(hash=b"deadbeef")),
        patch.object(gate, "_compare_hashes", return_value=0.95),
        patch.object(gate, "_compute_creative_evidence", return_value=0.7),
    ):
        report = gate.check(image_path, "asset_002")

    assert isinstance(report, OriginalityReport)
    assert report.passed is False
    assert report.existing_similar_count >= 1
    assert report.channel_similarity_score >= 0.85


def test_originality_gate_records_new_asset(tmp_path: Path) -> None:
    """Recording a new asset should add it to the hash store."""
    gate = OriginalityGate(
        hash_store_path=tmp_path / "hashes.json",
        similarity_threshold=0.85,
        min_creative_evidence=0.3,
    )

    image_path = tmp_path / "test_image.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000)

    with patch.object(gate, "_compute_phash", return_value=MagicMock(hash=b"cafebabe", __str__=lambda self: "cafebabe")):
        gate.record("asset_001", image_path, "Test Title")

    assert len(gate._records) == 1
    assert gate._records[0].asset_id == "asset_001"
    assert gate._records[0].phash == "cafebabe"
    assert gate._records[0].title == "Test Title"


def test_originality_gate_loads_existing_records(tmp_path: Path) -> None:
    """Loading a gate with existing records should restore them."""
    import json

    hash_store = tmp_path / "hashes.json"
    hash_store.write_text(
        json.dumps([
            {"asset_id": "rec_001", "phash": "aaaa", "published_at": "2026-01-01T00:00:00+00:00", "title": "Title 1"},
            {"asset_id": "rec_002", "phash": "bbbb", "published_at": "2026-01-02T00:00:00+00:00", "title": "Title 2"},
        ]),
        encoding="utf-8",
    )

    gate = OriginalityGate(hash_store_path=hash_store)
    assert len(gate._records) == 2
    assert gate._records[0].asset_id == "rec_001"
    assert gate._records[1].asset_id == "rec_002"
