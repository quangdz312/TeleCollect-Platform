"""Tài khoản dùng thử: xem được mọi thứ, không đổi được gì.

Giám khảo cần vào xem hệ thống thật mà không phải chờ ai duyệt tài khoản. Cho
xem thì phải cho ở mức reviewer — operator chỉ thấy bản ghi của chính mình, mà
tài khoản mới thì chưa có bản ghi nào. Nhưng reviewer xoá được dataset và đợt
thu, nên tài khoản này bị chặn ở mọi phương thức ghi.
"""

import pytest

from src.models.db import User
from src.models.enums import UserRole
from src.services.security import DEMO_USERNAME, create_access_token, hash_password


async def _demo_user(db_session) -> User:
    user = User(
        username=DEMO_USERNAME,
        password_hash=hash_password("demopassword"),
        display_name="Khách dùng thử",
        role=UserRole.REVIEWER,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


@pytest.mark.asyncio
async def test_demo_account_can_read(client, db_session):
    """Xem được — đó là lý do tài khoản này tồn tại."""
    demo = await _demo_user(db_session)

    response = await client.get("/api/v1/auth/me", headers=_headers(demo))

    assert response.status_code == 200
    assert response.json()["username"] == DEMO_USERNAME


@pytest.mark.asyncio
async def test_demo_account_cannot_delete(client, db_session):
    """Reviewer xoá được dataset và đợt thu. Tài khoản dùng thử thì không."""
    demo = await _demo_user(db_session)

    response = await client.delete(
        "/api/v1/raw/batches/batch-1", headers=_headers(demo)
    )

    assert response.status_code == 403
    assert "dùng thử" in response.json()["detail"].lower()


#: Một endpoint cho mỗi phương thức ghi. Chọn endpoint có thật để 405 không
#: che mất kết quả cần đo.
WRITE_ROUTES = [
    ("post", "/api/v1/raw/batches", {"id": "x", "name": "x"}),
    ("patch", "/api/v1/raw/batches/batch-1", {"name": "x"}),
    ("put", "/api/v1/integrations/wandb", {"api_key": "x"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", WRITE_ROUTES)
async def test_demo_account_cannot_write(client, db_session, method, path, body):
    """Chặn theo phương thức, không theo từng endpoint: endpoint ghi thêm sau
    này được bảo vệ sẵn, không phải nhớ bổ sung vào danh sách."""
    demo = await _demo_user(db_session)

    response = await getattr(client, method)(path, headers=_headers(demo), json=body)

    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_a_normal_reviewer_is_not_blocked(client, db_session):
    """Chặn phải bám vào đúng một tài khoản, không phải cả vai trò reviewer."""
    reviewer = User(
        username="real_reviewer",
        password_hash=hash_password("correctpass"),
        display_name="real",
        role=UserRole.REVIEWER,
    )
    db_session.add(reviewer)
    await db_session.commit()
    await db_session.refresh(reviewer)

    response = await client.post(
        "/api/v1/raw/batches",
        headers=_headers(reviewer),
        json={"id": "batch-real", "name": "Thật"},
    )

    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_demo_account_can_still_log_in(client, db_session):
    """Đăng nhập là POST, mà tài khoản này bị chặn POST.

    Không xung đột: `POST /auth/login` chưa có token nên không đi qua
    `current_user` — nhưng đây đúng là chỗ một lần chặn quá tay sẽ khoá luôn
    cửa vào, nên phải có test giữ.
    """
    await _demo_user(db_session)

    response = await client.post(
        "/api/v1/auth/login",
        data={"username": DEMO_USERNAME, "password": "demopassword"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["access_token"]
