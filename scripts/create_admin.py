"""Tạo (hoặc nâng quyền) tài khoản admin đầu tiên.

BẮT BUỘC phải có script này: `POST /auth/register` chỉ bao giờ tạo role
`operator`, nên không có đường nào khác để có admin phục vụ test/vận hành.

Dùng:
    python -m scripts.create_admin --username admin --password "Str0ngPass!"

Hoặc không truyền tham số, script đọc từ biến môi trường / `.env`:
    BOOTSTRAP_ADMIN_USERNAME=admin
    BOOTSTRAP_ADMIN_PASSWORD=Str0ngPass!

Idempotent: nếu username đã tồn tại, script chỉ đảm bảo user đó có
role=admin và is_active=True (không tạo trùng).
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Cho phép chạy cả `python scripts/create_admin.py` lẫn `python -m scripts.create_admin`
# — cách đầu không tự thêm thư mục gốc repo vào sys.path nên `import src.*` sẽ vỡ.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import select

from src.models.db import User, get_engine, init_db, session_factory
from src.models.enums import UserRole
from src.services.security import hash_password


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=None, help="Mặc định lấy từ BOOTSTRAP_ADMIN_USERNAME")
    parser.add_argument("--password", default=None, help="Mặc định lấy từ BOOTSTRAP_ADMIN_PASSWORD")
    return parser.parse_args()


async def create_admin(username: str, password: str) -> None:
    engine = get_engine()
    await init_db(engine)
    factory = session_factory(engine)

    async with factory() as session:
        existing = await session.scalar(select(User).where(User.username == username))
        if existing is not None:
            existing.role = UserRole.ADMIN
            existing.is_active = True
            await session.commit()
            print(f"User '{username}' đã tồn tại — đã nâng lên role=admin, is_active=True.")
            return

        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=username,
            role=UserRole.ADMIN,
        )
        session.add(user)
        await session.commit()
        print(f"Đã tạo admin '{username}'.")


def main() -> None:
    load_dotenv()
    args = _parse_args()

    username = (args.username or os.getenv("BOOTSTRAP_ADMIN_USERNAME") or "").strip().lower()
    password = args.password or os.getenv("BOOTSTRAP_ADMIN_PASSWORD") or ""

    if not username or not password:
        print(
            "Thiếu username/password. Truyền --username/--password hoặc đặt "
            "BOOTSTRAP_ADMIN_USERNAME/BOOTSTRAP_ADMIN_PASSWORD trong .env.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        asyncio.run(create_admin(username, password))
    except ValueError as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
