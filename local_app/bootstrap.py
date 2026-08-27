"""First-run setup that belongs to the desktop app, not to the web API."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select


async def ensure_local_admin(username: str, password: str) -> str:
    """Create the local-only administrator once, without changing web routes."""
    from src.models.db import User, get_engine, init_db, session_factory
    from src.models.enums import UserRole
    from src.services.security import hash_password

    engine = get_engine()
    await init_db(engine)
    async with session_factory(engine)() as session:
        existing = await session.scalar(select(User).where(User.username == username))
        if existing is None:
            existing = User(
                username=username,
                password_hash=hash_password(password),
                display_name="Local operator",
                role=UserRole.ADMIN,
            )
            session.add(existing)
            await session.commit()
            await session.refresh(existing)
        return str(existing.id)


async def index_workspace(workspace: Path, operator_id: str) -> int:
    """Index recordings found in a folder without adding files or an app.db there."""
    from src.models.db import Episode, Task, get_engine, session_factory
    from src.models.enums import DemoOutcome, DemoStatus
    from src.sim.tasks import get_task

    roots = [workspace / "episodes"]
    batches_root = workspace / "batches"
    if batches_root.is_dir():
        roots.extend(path / "episodes" for path in batches_root.iterdir() if (path / "episodes").is_dir())
    engine = get_engine()
    indexed = 0
    async with session_factory(engine)() as session:
        episode_dirs = [episode for root in roots if root.is_dir() for episode in root.iterdir()]
        seen: set[str] = set()
        for episode_dir in sorted(episode_dirs):
            meta_path = episode_dir / "meta.json"
            if not episode_dir.is_dir() or not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            episode_id = str(meta.get("episode_id") or episode_dir.name)
            if str(meta.get("source") or "") == "scripted" or episode_id in seen:
                continue
            seen.add(episode_id)
            task_name = str(meta.get("task_name") or "unknown")
            task = await session.get(Task, task_name)
            if task is None:
                try:
                    spec = get_task(task_name)
                    task = Task(
                        name=spec.name, description=spec.description, instruction=spec.description,
                        hints=[], action_dim=7, max_steps=spec.max_steps,
                    )
                except KeyError:
                    task = Task(
                        name=task_name, description=task_name, instruction="", hints=[], action_dim=7,
                        max_steps=max(int(meta.get("num_steps") or 1), 1),
                    )
                session.add(task)
                await session.flush()
            if await session.get(Episode, episode_id) is not None:
                continue
            has_wrist = (episode_dir / "wrist.mp4").is_file() or (episode_dir / "robot0_eye_in_hand.mp4").is_file()
            size_bytes = sum(item.stat().st_size for item in episode_dir.rglob("*") if item.is_file())
            success = bool(meta.get("task_success"))
            duration = float(meta.get("duration_s") or 0.0)
            session.add(Episode(
                id=episode_id, task_name=task_name, operator_id=operator_id,
                status=DemoStatus.LABELED,
                outcome=DemoOutcome.SUCCESS if success else DemoOutcome.FAILURE,
                note="Indexed from local workspace", fps=float(meta.get("control_hz") or 30),
                num_frames=int(meta.get("num_steps") or 0), duration_s=duration, size_bytes=size_bytes,
                trim_start_s=0.0, trim_end_s=duration if duration else None, has_wrist=has_wrist,
                has_trajectory=(episode_dir / "actions.parquet").is_file(),
            ))
            indexed += 1
        await session.commit()
    return indexed
