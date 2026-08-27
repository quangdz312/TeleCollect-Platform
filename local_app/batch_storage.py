"""Physical, file-explorer-friendly storage for local project batches."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

import h5py


_INVALID_WINDOWS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_folder_name(value: str, fallback: str = "batch") -> str:
    """Keep human-readable names while making them valid Windows folders."""

    cleaned = _INVALID_WINDOWS.sub("_", value).strip().rstrip(". ")
    return cleaned or fallback


def batch_folder(workspace: Path, batch: dict[str, Any]) -> Path:
    folder = safe_folder_name(str(batch.get("folder") or batch.get("name") or "batch"))
    return workspace / "batches" / folder


def ensure_batch_folder(workspace: Path, batch: dict[str, Any]) -> Path:
    root = batch_folder(workspace, batch)
    root.mkdir(parents=True, exist_ok=True)
    (root / "episodes").mkdir(exist_ok=True)
    manifest = {
        "format_version": 1,
        "id": str(batch["id"]),
        "name": str(batch["name"]),
        "task": str(batch["task"]),
        "description": str(batch.get("description") or ""),
        "created_at": str(batch.get("created_at") or ""),
        "updated_at": str(batch.get("updated_at") or ""),
    }
    target = root / "batch.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return root


def episode_folder_name(episode_id: str) -> str:
    readable = safe_folder_name(episode_id, "episode")
    if readable == episode_id:
        return readable
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, f"telecollect-episode:{episode_id}").hex[:8]
    return f"{readable[:100]}--{suffix}"


def episode_folder(workspace: Path, batch: dict[str, Any], episode_id: str) -> Path:
    return ensure_batch_folder(workspace, batch) / "episodes" / episode_folder_name(episode_id)


def iter_batch_episode_dirs(workspace: Path):
    root = workspace / "batches"
    if not root.is_dir():
        return
    for batch_dir in sorted(root.iterdir()):
        episodes = batch_dir / "episodes"
        if not episodes.is_dir():
            continue
        for episode_dir in sorted(episodes.iterdir()):
            if episode_dir.is_dir():
                yield episode_dir


def find_episode_folder(workspace: Path, episode_id: str) -> Path | None:
    for directory in iter_batch_episode_dirs(workspace) or ():
        try:
            meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(meta.get("episode_id") or directory.name) == episode_id:
            return directory
    legacy = workspace / "episodes" / episode_id
    if legacy.is_dir():
        return legacy
    legacy_root = workspace / "episodes"
    for directory in legacy_root.glob("*") if legacy_root.is_dir() else ():
        try:
            meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(meta.get("episode_id") or directory.name) == episode_id:
            return directory
    return None


def _copy_directory_atomic(source: Path, target: Path) -> None:
    if target.is_dir() and (target / "meta.json").is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copytree(source, temporary)
        if not (temporary / "meta.json").is_file():
            raise ValueError(f"Episode has no meta.json: {source}")
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def materialize_manual(workspace: Path, batch: dict[str, Any], episode_id: str, source: Path) -> Path:
    target = episode_folder(workspace, batch, episode_id)
    if source.resolve() != target.resolve():
        _copy_directory_atomic(source, target)
    return target


def _link_or_copy(source: Path, target: Path) -> None:
    if target.exists() or not source.is_file():
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _video_metadata(path: Path) -> dict[str, object]:
    try:
        import cv2

        capture = cv2.VideoCapture(str(path))
        result = {
            "fps": float(capture.get(cv2.CAP_PROP_FPS) or 0),
            "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
            "frames": int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
        }
        capture.release()
        return result
    except Exception:
        return {}


def _attach_scripted_video(target: Path, video: Path | None) -> None:
    if video is None or not video.is_file():
        return
    _link_or_copy(video, target / "review.mp4")
    meta_path = target / "meta.json"
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    details = _video_metadata(video)
    if details and metadata.get("video") != details:
        metadata["video"] = details
        temporary = meta_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(meta_path)


def materialize_scripted(
    workspace: Path,
    batch: dict[str, Any],
    record: dict[str, Any],
    source: Path,
    video: Path | None,
) -> Path:
    """Extract one HDF5 demo into one physical episode directory."""

    episode_id = str(record["episode_id"])
    target = episode_folder(workspace, batch, episode_id)
    trajectory = target / "trajectory.hdf5"
    if target.is_dir() and trajectory.is_file() and (target / "meta.json").is_file():
        _attach_scripted_video(target, video)
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.mkdir()
    try:
        demo_name = str(record["demo"])
        with h5py.File(source, "r") as source_file, h5py.File(temporary / "trajectory.hdf5", "w") as output:
            source_data = source_file["data"]
            if demo_name not in source_data:
                raise KeyError(f"{demo_name} not found in {source.name}")
            output_data = output.create_group("data")
            for key, value in source_data.attrs.items():
                output_data.attrs[key] = value
            source_file.copy(source_data[demo_name], output_data, name="demo_0")
            output_data.attrs["total"] = int(output_data["demo_0"].attrs.get("num_samples", 0))
        metadata = {
            "format_version": 1,
            "episode_id": episode_id,
            "task_name": str(record.get("task") or "unknown"),
            "source": "scripted",
            "num_steps": int(record.get("length") or 0),
            "requested_quality": str(record.get("requested_quality") or ""),
            "recorded_success": record.get("recorded_success"),
            "original_source": source.name,
            "original_demo": demo_name,
        }
        (temporary / "meta.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        if video is not None:
            _link_or_copy(video, temporary / "review.mp4")
            metadata["video"] = _video_metadata(video)
            (temporary / "meta.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def configure_runtime_storage(workspace: Path) -> None:
    """Route local manual recording reads/writes through physical batch folders.

    This patch is process-local to the desktop backend; shared web code and its
    on-server storage layout are untouched.
    """

    from local_app import catalog
    from src.services import storage

    def active_root() -> Path:
        data = catalog.load(workspace)
        batch = data["batches"].get(data["active_batch_id"])
        if isinstance(batch, dict):
            return ensure_batch_folder(workspace, batch) / "episodes"
        fallback = workspace / "episodes"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def episodes_root() -> Path:
        return active_root()

    def episode_dir(episode_id: str) -> Path:
        existing = find_episode_folder(workspace, episode_id)
        if existing is not None:
            return existing
        return storage._resolve_within(active_root(), episode_id)

    storage.episodes_root = episodes_root
    storage.episode_dir = episode_dir
