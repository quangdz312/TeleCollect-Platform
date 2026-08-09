"""Seed 3 task mặc định: pick_place, stack, push.

BẮT BUỘC phải chạy trước khi upload demo — `episodes.task_name` tham chiếu
tới `tasks.name`, bảng `tasks` rỗng thì `POST /demos/upload` sẽ fail vì
`task_name` không tồn tại.

Dùng:
    python -m scripts.seed_tasks

Idempotent: task nào đã có (theo `name`) thì bỏ qua, không ghi đè, không
nhân bản. Chạy lại nhiều lần an toàn.
"""

import asyncio
import sys
from pathlib import Path

# Cho phép chạy cả `python scripts/seed_tasks.py` lẫn `python -m scripts.seed_tasks`
# — cách đầu không tự thêm thư mục gốc repo vào sys.path nên `import src.*` sẽ vỡ.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from src.models.db import Task, get_engine, init_db, session_factory

DEFAULT_TASKS = [
    {
        "name": "pick_place",
        "description": "Nhặt một vật thể và đặt vào vị trí mục tiêu.",
        "instruction": "Gắp vật thể màu đỏ trên bàn và đặt vào khay đích ở góc phải.",
        "hints": ["Giữ gripper song song với mặt bàn", "Hạ chậm trước khi gắp"],
        "action_dim": 7,
        "max_steps": 200,
    },
    {
        "name": "stack",
        "description": "Xếp chồng khối này lên khối khác.",
        "instruction": "Xếp khối xanh lên trên khối vàng sao cho không đổ.",
        "hints": ["Căn giữa trước khi hạ", "Thả tay gripper từ từ"],
        "action_dim": 7,
        "max_steps": 250,
    },
    {
        "name": "push",
        "description": "Đẩy vật thể tới vị trí đích trên mặt bàn.",
        "instruction": "Đẩy khối gỗ tới vạch đích màu trắng mà không làm rơi khỏi bàn.",
        "hints": ["Đẩy theo đường thẳng", "Giảm tốc khi gần đích"],
        "action_dim": 7,
        "max_steps": 150,
    },
]


async def seed_tasks() -> None:
    engine = get_engine()
    await init_db(engine)
    factory = session_factory(engine)

    async with factory() as session:
        for spec in DEFAULT_TASKS:
            existing = await session.scalar(select(Task).where(Task.name == spec["name"]))
            if existing is not None:
                print(f"Task '{spec['name']}' đã tồn tại — bỏ qua.")
                continue
            session.add(Task(**spec))
            print(f"Đã tạo task '{spec['name']}'.")
        await session.commit()


def main() -> None:
    asyncio.run(seed_tasks())


if __name__ == "__main__":
    main()
