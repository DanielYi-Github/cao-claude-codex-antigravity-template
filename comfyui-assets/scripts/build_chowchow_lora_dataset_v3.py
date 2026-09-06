#!/usr/bin/env python3
"""Build the private Chow Chow LoRA v3 dataset from the two local source sets."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHOWCHOW_ROOT = PROJECT_ROOT / "comfyui-assets/character-reference/chowchow"
DEFAULT_MANIFEST = CHOWCHOW_ROOT / "lora-training-set-v3-manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        type=Path,
        default=CHOWCHOW_ROOT / "source",
        help="Directory containing lora-training-set and lora-training-set-v2",
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, help="Override manifest output directory")
    parser.add_argument("--force", action="store_true", help="Replace an existing output directory")
    return parser.parse_args()


def build(source_root: Path, manifest_path: Path, output: Path | None, force: bool) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    images = manifest["images"]
    identity_prefix = manifest["identity_prefix"].strip().rstrip(",")
    output_dir = output or source_root / manifest["output_directory"]

    keys = [(item["source_set"], item["file"]) for item in images]
    if len(keys) != len(set(keys)):
        raise ValueError("manifest contains duplicate source images")
    if not 20 <= len(images) <= 30:
        raise ValueError(f"expected 20-30 curated images, got {len(images)}")

    missing = [str(source_root / folder / name) for folder, name in keys if not (source_root / folder / name).is_file()]
    if missing:
        raise FileNotFoundError("missing source images:\n" + "\n".join(missing))

    if output_dir.exists():
        if not force:
            raise FileExistsError(f"{output_dir} already exists; pass --force to rebuild it")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    for index, item in enumerate(images, start=1):
        source = source_root / item["source_set"] / item["file"]
        stem = f"chowchow-v3-{index:02d}"
        destination = output_dir / f"{stem}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        caption = f"{identity_prefix}, {item['description'].strip().rstrip('.')}"
        (output_dir / f"{stem}.txt").write_text(caption + "\n", encoding="utf-8")

    metadata = {
        "title": "chowchow mascot lora dataset v3",
        "id": manifest["dataset_id"],
        "licenses": [{"name": "unknown"}],
        "image_count": len(images),
        "selection_manifest": manifest_path.name,
    }
    (output_dir / "dataset-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return output_dir


def main() -> None:
    args = parse_args()
    output = build(args.source_root.resolve(), args.manifest.resolve(), args.output, args.force)
    print(f"Built {output} with {len(list(output.glob('*.txt')))} image-caption pairs")


if __name__ == "__main__":
    main()
