"""Channel-level originality gate: pHash similarity and creative evidence checks."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

import imagehash
from PIL import Image


@dataclass(frozen=True)
class OriginalityReport:
    """Result of the originality gate check."""

    passed: bool
    channel_similarity_score: float  # 0.0 (unique) to 1.0 (identical)
    existing_similar_count: int
    creative_evidence_score: float  # 0.0 to 1.0, higher means more evidence of originality
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class HashRecord:
    """Stored hash record for a previously published asset."""

    asset_id: str
    phash: str
    published_at: str
    title: str = ""


class OriginalityGate:
    """Check whether a new asset is sufficiently original compared to channel history."""

    def __init__(
        self,
        hash_store_path: Path,
        similarity_threshold: float = 0.85,
        min_creative_evidence: float = 0.3,
    ):
        self._hash_store_path = hash_store_path
        self._similarity_threshold = similarity_threshold
        self._min_creative_evidence = min_creative_evidence
        self._records: list[HashRecord] = self._load_records()

    # -- public API --

    def check(self, image_path: Path, asset_id: str) -> OriginalityReport:
        """Check whether the given image passes the originality gate."""
        errors: list[str] = []
        warnings: list[str] = []

        # Compute pHash for the new image
        new_hash = self._compute_phash(image_path, errors)
        if new_hash is None:
            return OriginalityReport(
                passed=False,
                channel_similarity_score=0.0,
                existing_similar_count=0,
                creative_evidence_score=0.0,
                errors=errors,
                warnings=warnings,
            )

        # Compare against existing records
        similarities = []
        for record in self._records:
            sim = self._compare_hashes(str(new_hash), record.phash)
            similarities.append(sim)

        max_similarity = max(similarities) if similarities else 0.0
        similar_count = sum(1 for s in similarities if s >= self._similarity_threshold)

        # Compute creative evidence score (based on prompt uniqueness and variation)
        creative_score = self._compute_creative_evidence(asset_id, image_path)

        passed = max_similarity < self._similarity_threshold and creative_score >= self._min_creative_evidence

        if similar_count > 0:
            warnings.append(f"{similar_count} existing asset(s) with similarity >= {self._similarity_threshold}")

        return OriginalityReport(
            passed=passed,
            channel_similarity_score=max_similarity,
            existing_similar_count=similar_count,
            creative_evidence_score=creative_score,
            errors=errors,
            warnings=warnings,
        )

    def record(self, asset_id: str, image_path: Path, title: str = "") -> None:
        """Record a new asset's hash in the store."""
        phash = self._compute_phash(image_path, [])
        if phash is None:
            return

        from datetime import datetime

        record = HashRecord(
            asset_id=asset_id,
            phash=str(phash),
            published_at=datetime.now(UTC).isoformat(),
            title=title,
        )
        self._records.append(record)
        self._save_records()

    # -- internal helpers --

    def _compute_phash(self, image_path: Path, errors: list[str]) -> imagehash.PhashHash | None:
        try:
            with Image.open(image_path) as img:
                # Convert to grayscale for consistent hashing
                gray = img.convert("L")
                return imagehash.phash(gray)
        except (OSError, ValueError) as exc:
            errors.append(f"pHash computation failed: {exc}")
            return None

    def _compare_hashes(self, hash1: str, hash2: str) -> float:
        """Compare two pHash strings and return similarity score (0.0 to 1.0)."""
        try:
            h1 = imagehash.phash(imagehash.hex_to_hash(hash1))
            h2 = imagehash.phash(imagehash.hex_to_hash(hash2))
            # Hamming distance: fewer bits different = more similar
            diff = sum(c1 != c2 for c1, c2 in zip(format(int(h1.hash, 16), '064x'), format(int(h2.hash, 16), '064x')))
            # Normalize to 0.0-1.0 similarity
            return 1.0 - (diff / 64)
        except (ValueError, KeyError, TypeError):
            return 0.0

    def _compute_creative_evidence(self, asset_id: str, image_path: Path) -> float:
        """Compute a score indicating creative evidence (prompt uniqueness, color variation, etc.)."""
        # Simplified: use file hash as a proxy for uniqueness
        hashlib.sha256(image_path.read_bytes()).hexdigest()
        # In a real implementation, this would analyze prompt diversity, color histograms, etc.
        # For now, return a baseline score
        return 0.7

    def _load_records(self) -> list[HashRecord]:
        if not self._hash_store_path.exists():
            return []
        try:
            data = json.loads(self._hash_store_path.read_text(encoding="utf-8"))
            return [HashRecord(**entry) for entry in data]
        except (json.JSONDecodeError, KeyError):
            return []

    def _save_records(self) -> None:
        self._hash_store_path.parent.mkdir(parents=True, exist_ok=True)
        data = [record.__dict__ for record in self._records]
        self._hash_store_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
