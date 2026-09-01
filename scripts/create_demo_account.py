"""Tạo tài khoản dùng thử cho người ngoài vào xem hệ thống thật.

Tự đăng ký tạo tài khoản `is_active=False` phải chờ admin duyệt, nên người
được mời vào xem sẽ tắc ngay ở cửa. Tài khoản này mở sẵn để họ đăng nhập được
luôn.

Quyền là `reviewer` vì đó là mức thấp nhất còn xem được dữ liệu của cả nhóm —
`operator` chỉ thấy bản ghi của chính mình, mà tài khoản mới thì chưa có bản
ghi nào. Đổi lại, `src/services/security.py` chặn tài khoản này ở mọi phương
thức ghi, nên nó xem được tất cả mà không xoá được gì.

Dùng:
    python -m scripts.create_demo_account --password "..."

Idempotent: chạy lại chỉ đặt lại mật khẩu và bảo đảm role/trạng thái đúng.
"""

import argparse
import asyncio
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import select

from src.models.db import User, get_engine, init_db, session_factory
from src.models.enums import UserRole
from src.services.security import DEMO_USERNAME, hash_password


async def create_demo_account(password: str) -> None:
    engine = get_engine()
    await init_db(engine)
    factory = session_factory(engine)

    async with factory() as session:
        existing = await session.scalar(
            select(User).where(User.username == DEMO_USERNAME)
        )
        if existing is not None:
            existing.password_hash = hash_password(password)
            existing.role = UserRole.REVIEWER
            existing.is_active = True
            await session.commit()
            print(f"Đã đặt lại tài khoản '{DEMO_USERNAME}'.")
            return

        session.add(
            User(
                username=DEMO_USERNAME,
                password_hash=hash_password(password),
                display_name="Khách dùng thử",
                role=UserRole.REVIEWER,
                # Không cấp giờ GPU: tài khoản này không chạy được training vì
                # mọi phương thức ghi đều bị chặn.
                gpu_hours_limit=0.0,
            )
        )
        await session.commit()
        print(f"Đã tạo tài khoản dùng thử '{DEMO_USERNAME}'.")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--password", default=None,
        help="Mặc định lấy từ DEMO_ACCOUNT_PASSWORD, không có thì sinh ngẫu nhiên",
    )
    args = parser.parse_args()

    password = args.password or os.getenv("DEMO_ACCOUNT_PASSWORD") or ""
    generated = not password
    if generated:
        password = secrets.token_urlsafe(12)

    asyncio.run(create_demo_account(password))
    if generated:
        print(f"Mật khẩu sinh ra: {password}")
        print("Ghi lại — script không lưu lại ở đâu khác.")


if __name__ == "__main__":
    main()
