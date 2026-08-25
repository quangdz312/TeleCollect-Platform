from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from src.api.demos import _extract_review_package
from src.export.review_package import build_review_package
from src.services import storage


def _package(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, payload in entries.items():
            bundle.writestr(name, payload)
    return path


def test_extract_review_package_accepts_flat_episode_artifacts(tmp_path: Path) -> None:
    archive = _package(tmp_path / "episode.telecollect.zip", {
        "manifest.json": json.dumps({"task_name": "lift_cube"}).encode(),
        storage.FRONT_FILENAME: b"fake-mp4",
        storage.ACTIONS_FILENAME: b"parquet",
        storage.META_FILENAME: json.dumps({"task_name": "lift_cube"}).encode(),
    })
    destination = tmp_path / "out"
    destination.mkdir()

    assert _extract_review_package(archive, destination, 1024) == "lift_cube"
    assert (destination / storage.FRONT_FILENAME).read_bytes() == b"fake-mp4"
    assert (destination / storage.ACTIONS_FILENAME).exists()


def test_build_review_package_is_accepted_by_web_importer(tmp_path: Path) -> None:
    episode = tmp_path / "episode-1"
    episode.mkdir()
    (episode / storage.FRONT_FILENAME).write_bytes(b"fake-mp4")
    (episode / storage.ACTIONS_FILENAME).write_bytes(b"parquet")
    (episode / storage.META_FILENAME).write_text(
        json.dumps({"episode_id": "episode-1", "task_name": "lift_cube"}), encoding="utf-8"
    )
    package = build_review_package(episode, tmp_path / "episode-1.telecollect.zip")
    destination = tmp_path / "out-from-local"
    destination.mkdir()

    assert _extract_review_package(package, destination, 1024) == "lift_cube"
    assert (destination / storage.META_FILENAME).exists()


@pytest.mark.parametrize("name", ["../front.mp4", "nested/front.mp4", "..\\front.mp4"])
def test_extract_review_package_rejects_paths(tmp_path: Path, name: str) -> None:
    archive = _package(tmp_path / "bad.zip", {
        "manifest.json": json.dumps({"task_name": "lift_cube"}).encode(),
        name: b"not-a-video",
    })
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(ValueError):
        _extract_review_package(archive, destination, 1024)
