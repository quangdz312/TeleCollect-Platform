"""Seed the tasks table from the simulator's own task registry.

REQUIRED before uploading a demo: `episodes.task_name` references `tasks.name`,
so an empty table makes `POST /demos/upload` fail on a missing task.

Usage:
    python -m scripts.seed_tasks

The rows are read from `src.sim.tasks`, never written out by hand. An earlier
version hard-coded `pick_place`, `stack` and `push` -- robosuite sample tasks
from before the four real ones were settled on. The simulator moved on and this
table did not, so the review filter offered `push` and `stack`, which no
episode can ever carry, and spelled the can task `pick_place` where the
simulator calls it `pick_place_can`. Reading the registry keeps the two from
drifting apart again.

Idempotent: an existing task (matched on `name`) has its metadata refreshed
rather than duplicated. Stale rows that the simulator no longer registers are
removed, unless an episode still references them -- those are reported and
left alone, since deleting one would orphan recorded work.
"""

import asyncio
import sys
from pathlib import Path

# Allows both `python scripts/seed_tasks.py` and `python -m scripts.seed_tasks`
# -- the former does not put the repo root on sys.path, breaking `import src.*`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from src.models.db import Episode, Task, get_engine, init_db, session_factory
from src.sim.tasks import list_tasks


async def seed_tasks() -> None:
    engine = get_engine()
    await init_db(engine)
    factory = session_factory(engine)

    specs = list_tasks()
    wanted = {spec.name for spec in specs}

    async with factory() as session:
        for spec in specs:
            existing = await session.scalar(select(Task).where(Task.name == spec.name))
            if existing is None:
                session.add(
                    Task(
                        name=spec.name,
                        description=spec.description,
                        instruction=spec.description,
                        hints=[],
                        action_dim=7,
                        max_steps=spec.max_steps,
                    )
                )
                print(f"Created task '{spec.name}'.")
                continue
            existing.description = spec.description
            existing.max_steps = spec.max_steps
            print(f"Task '{spec.name}' already present - metadata refreshed.")

        for task in (await session.scalars(select(Task))).all():
            if task.name in wanted:
                continue
            referenced = await session.scalar(
                select(Episode).where(Episode.task_name == task.name).limit(1)
            )
            if referenced is not None:
                print(f"Task '{task.name}' is not a simulator task but has episodes - kept.")
                continue
            await session.delete(task)
            print(f"Removed stale task '{task.name}'.")

        await session.commit()


def main() -> None:
    asyncio.run(seed_tasks())


if __name__ == "__main__":
    main()
