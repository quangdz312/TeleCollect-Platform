"""File-based workspace model for the native TeleCollect Local application.

This module deliberately has no HTTP, database, FastAPI, or frontend
dependency. A workspace is simply a user-selected folder. Review annotations
are kept in the private application profile so opening an existing data folder
does not add application files to it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import h5py

from local_app.config import app_data_dir

ReviewDecision = Literal["unreviewed", "accepted", "rejected"]
ExportMode = Literal["all", "not_rejected", "selected"]


@dataclass(frozen=True)
class LocalEpisode:
    """One reviewable item discovered directly from the selected folder."""

    id: str
    task: str
    source: Literal["manual", "scripted"]
    success: bool | None
    frames: int | None
    duration_s: float | None
    video_path: Path | None
    episode_dir: Path | None = None
    hdf5_path: Path | None = None
    hdf5_demo: str | None = None


class LocalWorkspace:
    """Read and manage a folder without creating an app database inside it."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._annotations = self._load_annotations()

    @property
    def annotation_path(self) -> Path:
        key = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:24]
        directory = app_data_dir() / "workspaces"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{key}.json"

    def _load_annotations(self) -> dict[str, dict[str, str]]:
        try:
            value = json.loads(self.annotation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            str(key): item for key, item in value.items()
            if isinstance(item, dict)
        }

    def _save_annotations(self) -> None:
        temporary = self.annotation_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._annotations, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        temporary.replace(self.annotation_path)

    def decision_for(self, episode_id: str) -> ReviewDecision:
        value = self._annotations.get(episode_id, {}).get("decision", "unreviewed")
        return value if value in {"unreviewed", "accepted", "rejected"} else "unreviewed"

    def note_for(self, episode_id: str) -> str:
        return self._annotations.get(episode_id, {}).get("note", "")

    def review(self, episode_ids: list[str], decision: ReviewDecision, note: str = "") -> None:
        if decision == "unreviewed":
            raise ValueError("Use accepted or rejected when reviewing an episode")
        updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        for episode_id in episode_ids:
            self._annotations[episode_id] = {
                "decision": decision, "note": note.strip(), "updated_at": updated_at,
            }
        self._save_annotations()

    @staticmethod
    def _manual_video(directory: Path) -> Path | None:
        for filename in ("front.mp4", "review_front.mp4", "agentview.mp4", "wrist.mp4"):
            candidate = directory / filename
            if candidate.is_file():
                return candidate
        videos = sorted(directory.glob("*.mp4"))
        return videos[0] if videos else None

    def _manual_episodes(self) -> list[LocalEpisode]:
        roots = [self.root / "episodes"]
        roots.extend(self.root.glob("batches/*/episodes"))
        found: list[LocalEpisode] = []
        seen: set[str] = set()
        directories = [directory for root in roots if root.is_dir() for directory in root.iterdir()]
        for directory in sorted(directories):
            metadata = directory / "meta.json"
            if not directory.is_dir() or not metadata.is_file():
                continue
            try:
                content = json.loads(metadata.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            episode_id = str(content.get("episode_id") or directory.name)
            if str(content.get("source") or "") == "scripted" or episode_id in seen:
                continue
            seen.add(episode_id)
            found.append(LocalEpisode(
                id=episode_id,
                task=str(content.get("task_name") or "unknown"), source="manual",
                success=bool(content["task_success"]) if "task_success" in content else None,
                frames=_as_int(content.get("num_steps")),
                duration_s=_as_float(content.get("duration_s")),
                video_path=self._manual_video(directory), episode_dir=directory,
            ))
        return found

    def _scripted_roots(self) -> list[Path]:
        candidates = [self.root / "review" / "datasets", self.root / "datasets"]
        return [candidate for candidate in candidates if candidate.is_dir()]

    def _scripted_episodes(self) -> list[LocalEpisode]:
        found: list[LocalEpisode] = []
        seen: set[str] = set()
        for episode_dir in sorted(self.root.glob("batches/*/episodes/*")):
            try:
                meta = json.loads((episode_dir / "meta.json").read_text(encoding="utf-8"))
                if str(meta.get("source") or "") != "scripted":
                    continue
                path = episode_dir / "trajectory.hdf5"
                with h5py.File(path, "r") as source:
                    demo = source["data"]["demo_0"]
                    episode_id = str(meta.get("episode_id") or episode_dir.name)
                    found.append(LocalEpisode(
                        id=episode_id,
                        task=str(meta.get("task_name") or "unknown"),
                        source="scripted",
                        success=meta.get("recorded_success"),
                        frames=_as_int(demo.attrs.get("num_samples")),
                        duration_s=None,
                        video_path=(episode_dir / "review.mp4") if (episode_dir / "review.mp4").is_file() else None,
                        episode_dir=episode_dir,
                        hdf5_path=path,
                        hdf5_demo="demo_0",
                    ))
                    seen.add(episode_id)
            except (OSError, KeyError, json.JSONDecodeError):
                continue
        for directory in self._scripted_roots():
            for hdf5_path in sorted([*directory.glob("*.hdf5"), *directory.glob("*.h5")]):
                try:
                    with h5py.File(hdf5_path, "r") as source:
                        data = source.get("data")
                        if data is None:
                            continue
                        for demo_name, demo in data.items():
                            frames = _as_int(demo.attrs.get("num_samples"))
                            task = str(demo.attrs.get("task", hdf5_path.stem.split("_")[0]))
                            episode_id = f"{hdf5_path.name}::{demo_name}"
                            if episode_id in seen:
                                continue
                            found.append(LocalEpisode(
                                id=episode_id, task=task, source="scripted",
                                success=None, frames=frames, duration_s=None, video_path=None,
                                hdf5_path=hdf5_path, hdf5_demo=demo_name,
                            ))
                except OSError:
                    continue
        return found

    def episodes(self) -> list[LocalEpisode]:
        return [*self._manual_episodes(), *self._scripted_episodes()]

    def export_hdf5(self, target: Path, mode: ExportMode, selected_ids: set[str]) -> tuple[int, int]:
        """Create one RoboMimic HDF5 from the requested local review slice."""
        from src.services.robomimic_dataset_builder import build_robomimic_hdf5

        all_items = self.episodes()
        if mode == "selected":
            included = [item for item in all_items if item.id in selected_ids]
        elif mode == "not_rejected":
            included = [item for item in all_items if self.decision_for(item.id) != "rejected"]
        else:
            included = all_items
        if not included:
            raise ValueError("Không có episode nào phù hợp để export")

        payload: list[dict[str, object]] = []
        for item in included:
            annotation = self._annotations.get(item.id, {})
            base: dict[str, object] = {
                "episode_id": item.id,
                "decision": annotation.get("decision", "unreviewed"),
                "reviewer": "local-operator",
                "reviewed_at": annotation.get("updated_at", ""),
                "note": annotation.get("note", ""),
                "reasons": [],
            }
            if item.source == "manual" and item.episode_dir is not None:
                payload.append({**base, "artifact_format": "teleop_dir", "episode_dir": item.episode_dir,
                                "successful": item.success})
            elif item.hdf5_path is not None and item.hdf5_demo is not None:
                payload.append({**base, "artifact_format": "scripted", "source_path": item.hdf5_path,
                                "demo": item.hdf5_demo})
        return build_robomimic_hdf5(target, payload)


def _as_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
