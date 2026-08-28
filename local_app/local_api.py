"""Routes installed only inside the Electron local runtime."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

import asyncio

from local_app import catalog, sync
from local_app.batch_storage import configure_runtime_storage, find_episode_folder
from local_app.lerobot_export import export as export_lerobot


class EpisodePatch(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=30)
    trashed: bool | None = None


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)


class CollectionPatch(CollectionCreate):
    episode_ids: list[str] | None = None


class ExportRequest(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    format: Literal["lerobot", "hdf5"]
    mode: Literal["all", "exclude_rejected", "selected"] = "exclude_rejected"
    episode_ids: list[str] = Field(default_factory=list)
    collection_id: str | None = None
    batch_ids: list[str] = Field(default_factory=list, max_length=1000)


class BulkReviewRequest(BaseModel):
    episode_ids: list[str] = Field(min_length=1, max_length=5000)
    decision: Literal["approved", "rejected", "unreviewed"]


class SyncSignIn(BaseModel):
    server: str = Field(min_length=1, max_length=300)
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=200)


class BatchCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    task: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)


class BatchPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class ActiveBatchRequest(BaseModel):
    batch_id: str = Field(min_length=1, max_length=80)


class EpisodeBatchRequest(BaseModel):
    batch_id: str = Field(min_length=1, max_length=80)


def install(app: FastAPI, workspace: Path) -> None:
    configure_runtime_storage(workspace)

    def scripted_workspace():
        from src.labeling.workspace import Workspace

        return Workspace(workspace / "review")

    async def current_episodes() -> list[dict]:
        """Overlay review decisions from the local app DB onto raw artefacts.

        The catalogue owns organisational metadata only; a quality decision is
        always made in Review, so it must not be independently editable here.
        """
        from src.models.db import Episode, get_engine, session_factory
        from src.models.enums import DemoStatus

        decisions: dict[str, str] = {}
        async with session_factory(get_engine())() as session:
            rows = (await session.execute(select(Episode.id, Episode.status))).all()
        for episode_id, status in rows:
            if status == DemoStatus.APPROVED:
                decisions[str(episode_id)] = "accepted"
            elif status == DemoStatus.REJECTED:
                decisions[str(episode_id)] = "rejected"
            else:
                decisions[str(episode_id)] = "unreviewed"
        space = scripted_workspace()
        scores = space.scores()
        items = catalog.raw_episodes(workspace)
        catalog.sync_batches(workspace, items, scores)
        items = catalog.raw_episodes(workspace)
        catalogue = catalog.load(workspace)
        batches = catalogue["batches"]
        for item in items:
            item["status"] = decisions.get(item["id"], "unreviewed")
            item["source"] = "teleop"
            item["batch_name"] = str(batches.get(item.get("batch_id"), {}).get("name") or "Unassigned")
            # The web API can calculate a richer recommendation when a demo is
            # opened.  The local inbox still needs a stable value before that,
            # so uninspected teleop data is deliberately routed to review.
            item["auto_label"] = "review"
            item["auto_label_reason"] = "Teleop recording requires local review"

        from src.labeling.auto_gate import evaluate

        labels = space.labels_by_id()
        extras = catalogue["episodes"]
        for record in scores:
            episode_id = str(record["episode_id"])
            label = labels.get(episode_id)
            extra = extras.get(episode_id, {})
            display_name = str(record.get("display_name") or record.get("demo") or episode_id)
            decision = str(label.get("human_decision")) if label else "unreviewed"
            gate = evaluate(record)
            auto_label = {"approve": "accept", "reject": "reject", "review": "review", "audit": "review"}[gate.action]
            items.append({
                "id": episode_id,
                "title": str(extra.get("title") or display_name),
                "task": str(record.get("task") or "unknown"),
                "frames": int(record.get("length") or 0),
                "duration_s": float(record.get("length") or 0) / 30.0,
                "recorded_at": "",
                "status": "accepted" if decision == "approved" else decision,
                "tags": extra.get("tags", []) if isinstance(extra.get("tags"), list) else [],
                "note": str(extra.get("note") or ""),
                "trashed": bool(extra.get("trashed", False)),
                "collections": [],
                "complete_for_lerobot": bool(
                    extra.get("episode_path")
                    and (workspace / str(extra["episode_path"]) / "trajectory.hdf5").is_file()
                    and (workspace / str(extra["episode_path"]) / "review.mp4").is_file()
                ),
                "has_trajectory": True,
                "source": "scripted",
                "batch_id": str(extra.get("batch_id") or ""),
                "batch_name": str(batches.get(extra.get("batch_id"), {}).get("name") or "Unassigned"),
                "auto_label": auto_label,
                "auto_label_reason": gate.reason,
                "requested_quality": str(record.get("requested_quality") or ""),
                "source_path": str(
                    (workspace / str(extra.get("episode_path") or "") / "trajectory.hdf5")
                    if extra.get("episode_path") and (workspace / str(extra["episode_path"]) / "trajectory.hdf5").is_file()
                    else space.resolve_source(str(record["source"]))
                ),
                "demo": "demo_0"
                if extra.get("episode_path") and (workspace / str(extra["episode_path"]) / "trajectory.hdf5").is_file()
                else str(record["demo"]),
                "recorded_success": record.get("recorded_success"),
                "reviewer": str(label.get("reviewer") or "") if label else "",
                "reviewed_at": str(label.get("reviewed_at") or "") if label else "",
                "reasons": label.get("reasons", []) if label else [],
            })
        return items

    def episode_directory(episode_id: str) -> Path:
        found = find_episode_folder(workspace, episode_id)
        if found is not None:
            return found
        raise HTTPException(status_code=404, detail="Episode directory not found")

    @app.get("/api/v1/local/project")
    def project():
        data = catalog.load(workspace)
        return {
            "path": str(workspace),
            "folders": ["batches", "exports"],
            "active_batch_id": data["active_batch_id"] or None,
        }

    @app.get("/api/v1/local/library/episodes")
    async def episodes(include_trashed: bool = False):
        return [item for item in await current_episodes() if include_trashed or not item["trashed"]]

    @app.get("/api/v1/local/library/episodes/{episode_id}/files")
    def files(episode_id: str):
        record = scripted_workspace().scores_by_id().get(episode_id)
        if record is not None:
            directory = find_episode_folder(workspace, episode_id)
            if directory is not None:
                return [
                    {"path": str(item.relative_to(workspace)).replace("\\", "/"), "bytes": item.stat().st_size}
                    for item in sorted(directory.rglob("*")) if item.is_file()
                ]
            source = scripted_workspace().resolve_source(str(record["source"]))
            video = scripted_workspace().video_path(episode_id)
            result = [{"path": f"review/datasets/{source.name}::{record['demo']}", "bytes": source.stat().st_size}]
            if video.is_file():
                result.append({"path": f"review/videos/{video.name}", "bytes": video.stat().st_size})
            return result
        directory = episode_directory(episode_id)
        return [
            {"path": str(item.relative_to(directory)).replace("\\", "/"), "bytes": item.stat().st_size}
            for item in sorted(directory.rglob("*"))
            if item.is_file()
        ]

    @app.patch("/api/v1/local/library/episodes/{episode_id}")
    async def patch_episode(episode_id: str, body: EpisodePatch):
        if not any(item["id"] == episode_id for item in await current_episodes()):
            raise HTTPException(status_code=404, detail="Episode not found")
        return catalog.update_episode(workspace, episode_id, body.model_dump(exclude_none=True))

    @app.post("/api/v1/local/review/bulk")
    async def bulk_review(body: BulkReviewRequest):
        """Apply one inbox decision to teleop and scripted episodes.

        This is intentionally local-only.  The source artefacts stay immutable;
        only the project DB / append-only scripted label log is updated.
        """
        from src.models.db import Episode, get_engine, session_factory
        from src.models.enums import DemoStatus

        requested = set(body.episode_ids)
        known = {item["id"]: item for item in await current_episodes() if item["id"] in requested}
        missing = sorted(requested - set(known))
        if missing:
            raise HTTPException(status_code=404, detail=f"{len(missing)} episode(s) no longer exist in this project")

        scripted = scripted_workspace()
        scripted_ids = {episode_id for episode_id, item in known.items() if item["source"] == "scripted"}
        if body.decision == "unreviewed" and scripted_ids:
            raise HTTPException(status_code=422, detail="Reopening scripted labels in bulk is not supported yet")
        for episode_id in sorted(scripted_ids):
            scripted.append_label(
                episode_id,
                decision=body.decision,
                reasons=[],
                note="Bulk rejected in TeleCollect Local" if body.decision == "rejected" else "",
                reviewer="local-operator",
                blind=False,
            )

        teleop_ids = {episode_id for episode_id, item in known.items() if item["source"] == "teleop"}
        if teleop_ids:
            mapping = {
                "approved": DemoStatus.APPROVED,
                "rejected": DemoStatus.REJECTED,
                "unreviewed": DemoStatus.LABELED,
            }
            async with session_factory(get_engine())() as session:
                rows = (await session.execute(select(Episode).where(Episode.id.in_(teleop_ids)))).scalars().all()
                for row in rows:
                    row.status = mapping[body.decision]
                await session.commit()
        return {"updated": len(known), "decision": body.decision}

    @app.get("/api/v1/local/batches")
    async def batches():
        items = await current_episodes()
        data = catalog.load(workspace)
        result = []
        for batch in data["batches"].values():
            members = [item for item in items if item.get("batch_id") == batch["id"] and not item["trashed"]]
            result.append({
                **batch,
                "active": data["active_batch_id"] == batch["id"],
                "episodes": len(members),
                "teleop": sum(item["source"] == "teleop" for item in members),
                "scripted": sum(item["source"] == "scripted" for item in members),
                "approved": sum(item["status"] == "accepted" for item in members),
                "rejected": sum(item["status"] == "rejected" for item in members),
                "pending": sum(item["status"] == "unreviewed" for item in members),
            })
        return sorted(result, key=lambda item: (not item["active"], item["name"].casefold()))

    # --- sync with the shared server ----------------------------------------
    #
    # A second session, separate from the app's own `local-desktop` account:
    # different database and different signing secret, so neither token means
    # anything to the other side.

    @app.get("/api/v1/local/sync")
    def sync_status():
        session = sync.current_session()
        return session.as_dict() if session else None

    @app.post("/api/v1/local/sync/login")
    async def sync_login(body: SyncSignIn):
        try:
            session = await asyncio.to_thread(
                sync.sign_in, body.server, body.username, body.password,
            )
        except sync.SyncError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return session.as_dict()

    @app.post("/api/v1/local/sync/logout")
    def sync_logout():
        sync.sign_out()
        return {"ok": True}

    @app.post("/api/v1/local/batches/{batch_id}/sync")
    async def sync_batch(batch_id: str):
        data = catalog.load(workspace)
        batch = data["batches"].get(batch_id)
        if not isinstance(batch, dict):
            raise HTTPException(status_code=404, detail="Batch not found")
        try:
            # Zipping and uploading are blocking and can run for minutes on a
            # large batch; keep them off the event loop.
            return await asyncio.to_thread(sync.upload_batch, workspace, batch)
        except sync.SyncError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/local/batches/active")
    async def active_batch():
        # Sync first so a freshly opened legacy project exposes its batches.
        await current_episodes()
        data = catalog.load(workspace)
        batch = data["batches"].get(data["active_batch_id"])
        return batch if isinstance(batch, dict) else None

    @app.post("/api/v1/local/batches")
    def create_batch(body: BatchCreate):
        try:
            return catalog.create_batch(workspace, body.name, body.task, body.description)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.patch("/api/v1/local/batches/{batch_id}")
    def patch_batch(batch_id: str, body: BatchPatch):
        try:
            return catalog.update_batch(workspace, batch_id, body.model_dump(exclude_none=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Batch not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put("/api/v1/local/batches/active")
    def activate_batch(body: ActiveBatchRequest):
        try:
            return catalog.set_active_batch(workspace, body.batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Batch not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put("/api/v1/local/episodes/{episode_id}/batch")
    def assign_episode_batch(episode_id: str, body: EpisodeBatchRequest):
        try:
            return catalog.assign_episode_batch(workspace, episode_id, body.batch_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Batch not found") from exc

    @app.get("/api/v1/local/library/collections")
    def collections():
        return list(catalog.load(workspace)["collections"].values())

    @app.post("/api/v1/local/library/collections")
    def create_collection(body: CollectionCreate):
        return catalog.create_collection(workspace, body.name, body.description)

    @app.patch("/api/v1/local/library/collections/{collection_id}")
    def patch_collection(collection_id: str, body: CollectionPatch):
        try:
            return catalog.update_collection(workspace, collection_id, body.model_dump(exclude_none=True))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Collection not found") from exc

    @app.delete("/api/v1/local/library/collections/{collection_id}")
    def remove_collection(collection_id: str):
        try:
            catalog.delete_collection(workspace, collection_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Collection not found") from exc
        return {"ok": True}

    @app.get("/api/v1/local/exports")
    def exports():
        return catalog.load(workspace)["exports"]

    @app.post("/api/v1/local/exports")
    async def build_export(body: ExportRequest):
        items = await current_episodes()
        if body.batch_ids:
            allowed_batches = set(body.batch_ids)
            items = [item for item in items if item.get("batch_id") in allowed_batches]
        if body.collection_id:
            collection = catalog.load(workspace)["collections"].get(body.collection_id)
            if not collection:
                raise HTTPException(status_code=404, detail="Collection not found")
            allowed = set(collection.get("episode_ids", []))
            items = [item for item in items if item["id"] in allowed]
        if body.mode == "exclude_rejected":
            items = [item for item in items if item["status"] != "rejected" and not item["trashed"]]
        elif body.mode == "selected":
            selected = set(body.episode_ids)
            items = [item for item in items if item["id"] in selected and not item["trashed"]]
        else:
            items = [item for item in items if not item["trashed"]]
        if not items:
            raise HTTPException(status_code=422, detail="No episode matches this export selection")
        exported_items = items
        try:
            if body.format == "lerobot":
                path, count, frames = export_lerobot(workspace, body.name, {item["id"] for item in items})
            else:
                from local_app.hdf5_export import export as build_local_hdf5
                by_task: dict[str, list[dict]] = {}
                for item in items:
                    if not item["has_trajectory"]:
                        continue
                    payload = {
                        "episode_id": item["id"],
                        "decision": item["status"],
                        "reviewer": item.get("reviewer", "local-operator"),
                        "reviewed_at": item.get("reviewed_at", ""),
                        "note": item.get("note", ""),
                        "reasons": item.get("reasons", []),
                        "successful": item.get("recorded_success"),
                    }
                    if item["source"] == "scripted":
                        payload |= {
                            "artifact_format": "scripted",
                            "source_path": item["source_path"],
                            "demo": item["demo"],
                        }
                    else:
                        payload |= {
                            "artifact_format": "teleop_dir",
                            "episode_dir": str(episode_directory(item["id"])),
                        }
                    by_task.setdefault(catalog.canonical_task(str(item["task"])), []).append(payload)
                if not by_task:
                    raise ValueError("No selected episode has a usable trajectory")
                count = frames = 0
                if len(by_task) == 1:
                    path = workspace / "exports" / f"{body.name}.hdf5"
                    task_count, task_frames = build_local_hdf5(path, next(iter(by_task.values())))
                    count += task_count
                    frames += task_frames
                else:
                    path = workspace / "exports" / body.name
                    path.mkdir(parents=True, exist_ok=True)
                    for task, task_items in sorted(by_task.items()):
                        safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "_", task).strip("_") or "task"
                        task_count, task_frames = build_local_hdf5(path / f"{safe_task}.hdf5", task_items)
                        count += task_count
                        frames += task_frames
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return catalog.add_export(workspace, {"name": body.name, "format": body.format, "path": str(path), "episodes": count, "frames": frames, "mode": body.mode, "collection_id": body.collection_id, "episode_ids": [item["id"] for item in exported_items]})
