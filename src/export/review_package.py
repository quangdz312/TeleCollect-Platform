"""Portable review packages shared by the future local app and the web importer."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from src.services import storage


PACKAGE_VERSION = 1
PACKAGE_MANIFEST = "manifest.json"
PACKAGE_ARTIFACTS = (
    storage.FRONT_FILENAME,
    storage.WRIST_FILENAME,
    storage.ACTIONS_FILENAME,
    storage.META_FILENAME,
)


def build_review_package(episode_dir: str | Path, output: str | Path) -> Path:
    """Create a flat ``.telecollect.zip`` while leaving local source data untouched."""
    source = Path(episode_dir)
    target = Path(output)
    meta_path = source / storage.META_FILENAME
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Episode thiếu meta.json hợp lệ") from exc
    task_name = metadata.get("task_name") if isinstance(metadata, dict) else None
    if not isinstance(task_name, str) or not task_name:
        raise ValueError("meta.json thiếu task_name")
    front = source / storage.FRONT_FILENAME
    if not front.is_file():
        raise ValueError("Episode thiếu front.mp4 để review")

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(PACKAGE_MANIFEST, json.dumps({
            "package_version": PACKAGE_VERSION,
            "task_name": task_name,
            "episode_id": str(metadata.get("episode_id", source.name)),
        }, separators=(",", ":")))
        for filename in PACKAGE_ARTIFACTS:
            artifact = source / filename
            if artifact.is_file():
                bundle.write(artifact, arcname=filename)
    return target
