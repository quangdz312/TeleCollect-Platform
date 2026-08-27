"""Local-only API and UI host; it has no dependency on web app routes."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from local_app import state

ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
STATIC_DIR = ROOT / "local_app" / "ui" if getattr(sys, "frozen", False) else Path(__file__).with_name("ui")


class ExportRequest(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    mode: Literal["all", "exclude_rejected", "selected"] = "exclude_rejected"
    episode_ids: list[str] = Field(default_factory=list)


class EpisodeUpdate(BaseModel):
    status: Literal["active", "rejected"]


def _episodes_root(data_dir: Path) -> Path:
    root = data_dir / "episodes"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_episodes(data_dir: Path) -> list[dict[str, object]]:
    statuses = state.load(data_dir)
    episodes: list[dict[str, object]] = []
    for directory in sorted(_episodes_root(data_dir).iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        meta_path = directory / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        episode_id = str(meta.get("episode_id") or directory.name)
        episodes.append({
            "id": episode_id,
            "task_name": str(meta.get("task_name", "unknown")),
            "num_steps": int(meta.get("num_steps", 0)),
            "duration_s": float(meta.get("duration_s", 0)),
            "has_video": (directory / "review_front.mp4").is_file() or (directory / "front.mp4").is_file(),
            "has_trajectory": (directory / "actions.parquet").is_file(),
            "status": statuses.get(episode_id, "active"),
        })
    return episodes


def create_app(data_dir: str | Path) -> FastAPI:
    data_root = Path(data_dir).expanduser().resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="TeleCollect Local", docs_url=None, redoc_url=None)

    @app.get("/api/status")
    def status() -> dict[str, object]:
        usage = shutil.disk_usage(data_root)
        episodes = _read_episodes(data_root)
        return {
            "data_dir": str(data_root),
            "free_bytes": usage.free,
            "episodes": len(episodes),
            "exports": len(list((data_root / "exports").glob("*.hdf5"))),
        }

    @app.get("/api/tasks")
    def tasks() -> list[dict[str, str]]:
        from src.sim.tasks import list_tasks
        return [{"name": task.name, "description": task.description} for task in list_tasks()]

    @app.post("/api/collect/{task_name}")
    def collect(task_name: str) -> dict[str, object]:
        from src.sim.tasks import get_task
        try:
            get_task(task_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown task") from exc
        env = {**os.environ, "STORAGE_DIR": str(data_root)}
        command = (
            [str(ROOT / "TeleCollectCollector.exe"), "--task", task_name, "--operator", "local"]
            if getattr(sys, "frozen", False)
            else [sys.executable, str(ROOT / "scripts" / "teleop_ui.py"), "--task", task_name, "--operator", "local"]
        )
        log_dir = data_root / "logs"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"collector-{uuid.uuid4().hex[:8]}.log"
        with open(log_path, "w", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        # GLFW/MuJoCo failures normally happen before a window is visible. Do
        # not report success in that case; surface the captured log instead.
        time.sleep(0.5)
        if process.poll() is not None:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-1500:]
            raise HTTPException(status_code=500, detail=f"Collector did not start:\n{detail}")
        return {"pid": process.pid, "task": task_name, "log": str(log_path)}

    @app.get("/api/episodes")
    def episodes() -> list[dict[str, object]]:
        return _read_episodes(data_root)

    @app.patch("/api/episodes/{episode_id}")
    def update_episode(episode_id: str, body: EpisodeUpdate) -> dict[str, str]:
        if not (data_root / "episodes" / episode_id).is_dir():
            raise HTTPException(status_code=404, detail="Episode not found")
        statuses = state.load(data_root)
        statuses[episode_id] = body.status
        state.save(data_root, statuses)
        return {"id": episode_id, "status": body.status}

    @app.post("/api/export")
    def export_dataset(body: ExportRequest) -> dict[str, object]:
        episodes = _read_episodes(data_root)
        if body.mode == "exclude_rejected":
            episodes = [item for item in episodes if item["status"] != "rejected"]
        elif body.mode == "selected":
            selected = set(body.episode_ids)
            episodes = [item for item in episodes if item["id"] in selected]
        usable = [item for item in episodes if item["has_trajectory"]]
        if not usable:
            raise HTTPException(status_code=422, detail="No selected episode has actions.parquet")
        tasks = {str(item["task_name"]) for item in usable}
        if len(tasks) != 1:
            raise HTTPException(status_code=422, detail="Export one task at a time")
        from src.services.robomimic_dataset_builder import build_robomimic_hdf5
        output = data_root / "exports" / f"{body.name}.hdf5"
        items = [{
            "artifact_format": "teleop_dir", "episode_dir": str(data_root / "episodes" / item["id"]),
            "episode_id": item["id"], "decision": item["status"], "successful": True,
        } for item in usable]
        count, frames = build_robomimic_hdf5(output, items)
        return {"path": str(output), "episodes": count, "frames": frames}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    return app
