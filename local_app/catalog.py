"""Small, workspace-owned catalog for the local data library."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from local_app.config import ensure_project_layout
from local_app.batch_storage import (
    ensure_batch_folder,
    find_episode_folder,
    iter_batch_episode_dirs,
    materialize_manual,
    materialize_scripted,
    safe_folder_name,
)


def _path(workspace: Path) -> Path:
    control = ensure_project_layout(workspace)
    target = control / "library.json"
    # This file belongs solely to the local app, so moving it into the project
    # control directory is safe and keeps an upgraded project self-contained.
    legacy = workspace / "local-library.json"
    if not target.exists() and legacy.is_file():
        legacy.replace(target)
    return target


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load(workspace: Path) -> dict[str, Any]:
    try:
        data = json.loads(_path(workspace).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    return {
        "version": 2,
        "episodes": data.get("episodes", {}) if isinstance(data.get("episodes"), dict) else {},
        "collections": data.get("collections", {}) if isinstance(data.get("collections"), dict) else {},
        "exports": data.get("exports", []) if isinstance(data.get("exports"), list) else [],
        "batches": data.get("batches", {}) if isinstance(data.get("batches"), dict) else {},
        "active_batch_id": str(data.get("active_batch_id") or ""),
        "batch_migration_completed": bool(data.get("batch_migration_completed", False)),
    }


def save(workspace: Path, data: dict[str, Any]) -> None:
    target = _path(workspace)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def raw_episodes(workspace: Path) -> list[dict[str, Any]]:
    catalog = load(workspace)
    result: list[dict[str, Any]] = []
    directories = list(iter_batch_episode_dirs(workspace) or ())
    legacy = workspace / "episodes"
    if legacy.is_dir():
        directories.extend(sorted(legacy.glob("*")))
    seen: set[str] = set()
    for directory in directories:
        if not directory.is_dir():
            continue
        try:
            meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        episode_id = str(meta.get("episode_id") or directory.name)
        if str(meta.get("source") or "") == "scripted" or episode_id in seen:
            continue
        seen.add(episode_id)
        extra = catalog["episodes"].get(episode_id, {})
        memberships = [collection_id for collection_id, collection in catalog["collections"].items() if episode_id in collection.get("episode_ids", [])]
        result.append({"id": episode_id, "title": str(extra.get("title") or episode_id), "task": str(meta.get("task_name") or "unknown"), "frames": int(meta.get("num_steps") or 0), "duration_s": float(meta.get("duration_s") or 0), "recorded_at": str(meta.get("started_at") or ""), "status": str(extra.get("status") or "unreviewed"), "tags": extra.get("tags", []) if isinstance(extra.get("tags"), list) else [], "note": str(extra.get("note") or ""), "trashed": bool(extra.get("trashed", False)), "collections": memberships, "batch_id": str(extra.get("batch_id") or ""), "episode_dir": str(directory), "complete_for_lerobot": (directory / "actions.parquet").is_file() and (directory / "birdview.mp4").is_file(), "has_trajectory": (directory / "actions.parquet").is_file()})
    return result


def update_episode(workspace: Path, episode_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    data = load(workspace)
    entry = data["episodes"].setdefault(episode_id, {})
    for key in ("title", "note", "status", "trashed"):
        if key in patch:
            entry[key] = patch[key]
    if "tags" in patch:
        entry["tags"] = sorted({str(tag).strip() for tag in patch["tags"] if str(tag).strip()})
    entry["updated_at"] = _now()
    save(workspace, data)
    return entry


def canonical_task(task: str) -> str:
    value = task.strip().lower()
    return {
        "lift": "lift_cube",
        "can": "pick_place_can",
        "square": "nut_assembly_square",
        "assemble_square": "nut_assembly_square",
    }.get(value, value or "unknown")


def _batch_id(name: str, task: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "batch"
    suffix = uuid.uuid5(uuid.NAMESPACE_URL, f"telecollect:{name.casefold()}:{task}").hex[:8]
    return f"{stem}-{suffix}"


def _ensure_batch(data: dict[str, Any], name: str, task: str, *, status: str = "closed", description: str = "") -> dict[str, Any]:
    canonical = canonical_task(task)
    for batch in data["batches"].values():
        if str(batch.get("name", "")).casefold() == name.casefold() and batch.get("task") == canonical:
            return batch
    batch_id = _batch_id(name, canonical)
    batch = {
        "id": batch_id,
        "name": name.strip(),
        "folder": safe_folder_name(name.strip()),
        "task": canonical,
        "description": description.strip(),
        "status": status,
        "created_at": _now(),
        "updated_at": _now(),
    }
    data["batches"][batch_id] = batch
    return batch


def sync_batches(workspace: Path, teleop: list[dict[str, Any]], scripted: list[dict[str, Any]]) -> None:
    """Migrate old episodes and attach newly discovered captures to a batch."""

    data = load(workspace)
    changed = False
    initial_migration = not data["batch_migration_completed"]
    if initial_migration:
        for item in teleop:
            entry = data["episodes"].setdefault(str(item["id"]), {})
            if not entry.get("batch_id"):
                batch = _ensure_batch(data, f"Legacy teleop - {canonical_task(str(item['task']))}", str(item["task"]))
                entry["batch_id"] = batch["id"]
        changed = True

    active = data["batches"].get(data["active_batch_id"])
    for item in teleop:
        entry = data["episodes"].setdefault(str(item["id"]), {})
        if entry.get("batch_id"):
            continue
        task = canonical_task(str(item["task"]))
        if active and active.get("task") == task:
            entry["batch_id"] = active["id"]
        elif initial_migration:
            batch = _ensure_batch(data, f"Unassigned teleop - {task}", task)
            entry["batch_id"] = batch["id"]
        if entry.get("batch_id"):
            changed = True

    generated_defaults: set[str] = set()
    for record in scripted:
        episode_id = str(record["episode_id"])
        entry = data["episodes"].setdefault(episode_id, {})
        task = canonical_task(str(record.get("task") or "unknown"))
        provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
        source_name = str(provenance.get("collection_batch_id") or "legacy")

        assigned = data["batches"].get(str(entry.get("batch_id") or ""))
        is_old_default = bool(
            assigned
            and assigned.get("name") == "lift-scripted-v1.2"
            and assigned.get("status") == "closed"
        )
        if is_old_default:
            generated_defaults.add(str(assigned["id"]))
        alternatives = [
            batch for batch in data["batches"].values()
            if batch.get("task") == task and batch.get("id") != (assigned or {}).get("id")
            and batch.get("id") not in generated_defaults
        ]
        target = None
        if assigned and assigned.get("task") == task and not is_old_default:
            target = assigned
        elif active and active.get("task") == task:
            target = active
        elif initial_migration and source_name != "legacy":
            # During the one-time import, provenance is the historical source
            # of truth.  Preserve that grouping instead of folding scripted
            # captures into the only teleop batch that happens to share a task.
            target = _ensure_batch(data, source_name, task)
        elif len(alternatives) == 1:
            target = alternatives[0]
        if target is None and initial_migration:
            name = f"Legacy scripted - {task}" if source_name == "legacy" else source_name
            target = _ensure_batch(data, name, task)
        if target and entry.get("batch_id") != target["id"]:
            entry["batch_id"] = target["id"]
            changed = True

    referenced = {str(entry.get("batch_id") or "") for entry in data["episodes"].values()}
    for batch_id in generated_defaults:
        if batch_id not in referenced and data["active_batch_id"] != batch_id:
            data["batches"].pop(batch_id, None)
            changed = True
    # A batch is a real folder, not just a catalogue relation. Materialise old
    # recordings without deleting their legacy source so migration is safely
    # repeatable and recoverable.
    for batch in data["batches"].values():
        if not batch.get("folder"):
            batch["folder"] = safe_folder_name(str(batch.get("name") or "batch"))
            changed = True
        ensure_batch_folder(workspace, batch)
    for item in teleop:
        entry = data["episodes"].get(str(item["id"]), {})
        batch = data["batches"].get(str(entry.get("batch_id") or ""))
        source_value = item.get("episode_dir")
        source = Path(str(source_value)) if source_value else None
        if isinstance(batch, dict) and source is not None and source.is_dir():
            target = materialize_manual(workspace, batch, str(item["id"]), source)
            relative = str(target.relative_to(workspace)).replace("\\", "/")
            if entry.get("episode_path") != relative:
                entry["episode_path"] = relative
                changed = True
    review_root = workspace / "review"
    for record in scripted:
        episode_id = str(record["episode_id"])
        entry = data["episodes"].get(episode_id, {})
        batch = data["batches"].get(str(entry.get("batch_id") or ""))
        source = review_root / "datasets" / Path(str(record.get("source") or "")).name
        if not isinstance(batch, dict) or not source.is_file():
            continue
        from src.labeling.workspace import Workspace
        review = Workspace(review_root)
        video = review.video_path(episode_id)
        target = materialize_scripted(
            workspace, batch, record, source, video if video.is_file() else None,
        )
        relative = str(target.relative_to(workspace)).replace("\\", "/")
        if entry.get("episode_path") != relative:
            entry["episode_path"] = relative
            changed = True
    if initial_migration:
        data["batch_migration_completed"] = True
        changed = True
    if changed:
        save(workspace, data)


def create_batch(workspace: Path, name: str, task: str, description: str = "") -> dict[str, Any]:
    data = load(workspace)
    if any(str(item.get("name", "")).casefold() == name.strip().casefold() for item in data["batches"].values()):
        raise ValueError("Batch name already exists")
    batch = _ensure_batch(data, name, task, status="open", description=description)
    ensure_batch_folder(workspace, batch)
    save(workspace, data)
    return batch


def update_batch(workspace: Path, batch_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    data = load(workspace)
    batch = data["batches"].get(batch_id)
    if not isinstance(batch, dict):
        raise KeyError(batch_id)
    if "name" in patch:
        name = str(patch["name"]).strip()
        if any(key != batch_id and str(item.get("name", "")).casefold() == name.casefold() for key, item in data["batches"].items()):
            raise ValueError("Batch name already exists")
        batch["name"] = name
    for key in ("description",):
        if key in patch:
            batch[key] = str(patch[key]).strip()
    batch["updated_at"] = _now()
    ensure_batch_folder(workspace, batch)
    save(workspace, data)
    return batch


def set_active_batch(workspace: Path, batch_id: str) -> dict[str, Any]:
    data = load(workspace)
    batch = data["batches"].get(batch_id)
    if not isinstance(batch, dict):
        raise KeyError(batch_id)
    data["active_batch_id"] = batch_id
    batch["activated_at"] = _now()
    batch["updated_at"] = _now()
    save(workspace, data)
    return batch


def assign_episode_batch(workspace: Path, episode_id: str, batch_id: str) -> dict[str, Any]:
    """Persist the batch chosen when a manual recording started."""

    data = load(workspace)
    if batch_id not in data["batches"]:
        raise KeyError(batch_id)
    entry = data["episodes"].setdefault(episode_id, {})
    entry["batch_id"] = batch_id
    source = find_episode_folder(workspace, episode_id)
    if source is not None:
        target = materialize_manual(workspace, data["batches"][batch_id], episode_id, source)
        entry["episode_path"] = str(target.relative_to(workspace)).replace("\\", "/")
    save(workspace, data)
    return entry


def create_collection(workspace: Path, name: str, description: str) -> dict[str, Any]:
    data = load(workspace)
    collection = {"id": uuid.uuid4().hex[:12], "name": name.strip(), "description": description.strip(), "episode_ids": [], "created_at": _now(), "updated_at": _now()}
    data["collections"][collection["id"]] = collection
    save(workspace, data)
    return collection


def update_collection(workspace: Path, collection_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    data = load(workspace)
    collection = data["collections"].get(collection_id)
    if not isinstance(collection, dict):
        raise KeyError(collection_id)
    for key in ("name", "description"):
        if key in patch:
            collection[key] = str(patch[key]).strip()
    if "episode_ids" in patch:
        collection["episode_ids"] = list(dict.fromkeys(str(item) for item in patch["episode_ids"]))
    collection["updated_at"] = _now()
    save(workspace, data)
    return collection


def delete_collection(workspace: Path, collection_id: str) -> None:
    data = load(workspace)
    if collection_id not in data["collections"]:
        raise KeyError(collection_id)
    del data["collections"][collection_id]
    save(workspace, data)


def add_export(workspace: Path, record: dict[str, Any]) -> dict[str, Any]:
    data = load(workspace)
    item = {"id": uuid.uuid4().hex[:12], "created_at": _now(), **record}
    data["exports"].insert(0, item)
    save(workspace, data)
    return item
