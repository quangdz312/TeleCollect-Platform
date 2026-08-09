"""Seed ~40 demo mẫu bằng video sinh từ ffmpeg testsrc (thời lượng 5-15s).

Rải đều 3 task, đủ status recorded/labeled/approved/rejected và outcome
success/failure — phục vụ test filter/phân trang/summary mà không phải upload
tay từng cái. Ghi thẳng file + row DB, không gọi qua API (không cần chạy
server, không cần token).

Dùng:
    python -m scripts.seed_demos [--count 40] [--reset]

--reset xoá sạch demo cũ (row DB + thư mục episodes/) trước khi seed.
"""

import argparse
import asyncio
import random
import shutil
import subprocess

from sqlalchemy import select

from src.models.db import Episode, Task, User, get_engine, init_db, session_factory
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.services import media, storage
from src.services.security import hash_password

SEED_TASKS = [
    {"name": "pick_place", "action_dim": 7, "max_steps": 200},
    {"name": "stack", "action_dim": 7, "max_steps": 250},
    {"name": "push", "action_dim": 7, "max_steps": 150},
]

SEED_OPERATORS = ["seed_operator1", "seed_operator2"]

# (status, outcome) khả dĩ — rải đều qua toàn bộ vòng đời demo.
STATUS_OUTCOME_CHOICES: list[tuple[DemoStatus, DemoOutcome | None]] = [
    (DemoStatus.RECORDED, None),
    (DemoStatus.LABELED, DemoOutcome.SUCCESS),
    (DemoStatus.LABELED, DemoOutcome.FAILURE),
    (DemoStatus.APPROVED, DemoOutcome.SUCCESS),
    (DemoStatus.APPROVED, DemoOutcome.FAILURE),
    (DemoStatus.REJECTED, DemoOutcome.FAILURE),
]


def _make_testsrc_mp4(dest_path, duration_s: float) -> None:
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=64x64:rate=10",
        "-t",
        str(duration_s),
        "-pix_fmt",
        "yuv420p",
        str(dest_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)


async def _ensure_tasks(session) -> None:
    for spec in SEED_TASKS:
        existing = await session.scalar(select(Task).where(Task.name == spec["name"]))
        if existing is None:
            session.add(
                Task(
                    name=spec["name"],
                    description=f"Seed task {spec['name']}",
                    instruction=f"Thực hiện {spec['name']}",
                    hints=[],
                    action_dim=spec["action_dim"],
                    max_steps=spec["max_steps"],
                )
            )
    await session.commit()


async def _ensure_operators(session) -> list[User]:
    operators = []
    for username in SEED_OPERATORS:
        existing = await session.scalar(select(User).where(User.username == username))
        if existing is None:
            existing = User(
                username=username,
                password_hash=hash_password("seedpassword1"),
                display_name=username,
                role=UserRole.OPERATOR,
            )
            session.add(existing)
            await session.commit()
            await session.refresh(existing)
        operators.append(existing)
    return operators


async def _reset_existing_demos(session) -> None:
    episodes = list((await session.scalars(select(Episode))).all())
    for ep in episodes:
        storage.delete_episode(ep.id)
        await session.delete(ep)
    await session.commit()
    print(f"Đã xoá {len(episodes)} demo cũ.")


async def seed_demos(count: int, reset: bool) -> None:
    engine = get_engine()
    await init_db(engine)
    factory = session_factory(engine)

    async with factory() as session:
        if reset:
            await _reset_existing_demos(session)

        await _ensure_tasks(session)
        operators = await _ensure_operators(session)

        created = 0
        for i in range(count):
            task_spec = SEED_TASKS[i % len(SEED_TASKS)]
            operator = random.choice(operators)
            demo_status, outcome = random.choice(STATUS_OUTCOME_CHOICES)
            duration_s = round(random.uniform(5.0, 15.0), 1)

            tmp_dir = storage.new_tmp_dir()
            try:
                front_path = tmp_dir / storage.FRONT_FILENAME
                _make_testsrc_mp4(front_path, duration_s)
                probe = await media.probe_video(front_path)
                thumb_path = tmp_dir / storage.THUMBNAIL_FILENAME
                await media.generate_thumbnail(front_path, thumb_path)
                size_bytes = storage.dir_size_bytes(tmp_dir)

                episode = Episode(
                    task_name=task_spec["name"],
                    operator_id=operator.id,
                    status=demo_status,
                    outcome=outcome,
                    fps=probe.fps,
                    num_frames=probe.num_frames,
                    duration_s=probe.duration_s,
                    size_bytes=size_bytes,
                    has_wrist=False,
                    has_trajectory=False,
                )
                session.add(episode)
                await session.commit()
                await session.refresh(episode)

                storage.promote_tmp_to_episode(tmp_dir, episode.id)
                created += 1
            except Exception as exc:  # noqa: BLE001 — seed script: log và tiếp tục, không để 1 demo lỗi chặn cả mẻ
                shutil.rmtree(tmp_dir, ignore_errors=True)
                print(f"Bỏ qua demo #{i} do lỗi: {exc}")
                continue

        print(f"Đã tạo {created}/{count} demo.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=40)
    parser.add_argument("--reset", action="store_true", help="Xoá sạch demo cũ trước khi seed")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(seed_demos(args.count, args.reset))


if __name__ == "__main__":
    main()
