import pytest

from src.models.db import User
from src.models.enums import UserRole
from src.services.security import create_access_token, hash_password

API = "/api/v1/users"


async def _create_user(db_session, username: str, role: UserRole, password: str = "correctpass") -> User:
    user = User(username=username, password_hash=hash_password(password), display_name=username, role=role)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


# --- phân quyền theo ma trận (mục 2.1 plan) ------------------------------------


@pytest.mark.asyncio
async def test_operator_cannot_list_users(client, db_session):
    operator = await _create_user(db_session, "op1", UserRole.OPERATOR)

    resp = await client.get(API, headers=_auth_headers(operator))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reviewer_cannot_list_users(client, db_session):
    reviewer = await _create_user(db_session, "rev1", UserRole.REVIEWER)

    resp = await client.get(API, headers=_auth_headers(reviewer))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_list_users(client, db_session):
    admin = await _create_user(db_session, "admin1", UserRole.ADMIN)

    resp = await client.get(API, headers=_auth_headers(admin))
    assert resp.status_code == 200
    usernames = [u["username"] for u in resp.json()]
    assert "admin1" in usernames


@pytest.mark.asyncio
async def test_require_min_role_admin_passes_reviewer_gate(client, db_session):
    """Admin phải đi qua được `require_min_role(reviewer)` nhờ kế thừa role —
    router /users đòi admin, nhưng đây test rank-based, không so khớp tên."""
    admin = await _create_user(db_session, "admin2", UserRole.ADMIN)

    resp = await client.get(API, headers=_auth_headers(admin))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_no_token_returns_401_not_403(client):
    resp = await client.get(API)
    assert resp.status_code == 401


# --- CRUD ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_creates_user_with_chosen_role(client, db_session):
    admin = await _create_user(db_session, "admin3", UserRole.ADMIN)

    resp = await client.post(
        API,
        json={"username": "NewReviewer", "password": "goodpass1", "display_name": "New", "role": "reviewer"},
        headers=_auth_headers(admin),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "newreviewer"
    assert body["role"] == "reviewer"
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_admin_create_user_duplicate_username_returns_409(client, db_session):
    admin = await _create_user(db_session, "admin4", UserRole.ADMIN)
    await _create_user(db_session, "dup", UserRole.OPERATOR)

    resp = await client.post(
        API,
        json={"username": "dup", "password": "goodpass1", "display_name": "Dup", "role": "operator"},
        headers=_auth_headers(admin),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_admin_updates_other_user_role(client, db_session):
    admin = await _create_user(db_session, "admin5", UserRole.ADMIN)
    target = await _create_user(db_session, "target1", UserRole.OPERATOR)

    resp = await client.patch(
        f"{API}/{target.id}", json={"role": "reviewer"}, headers=_auth_headers(admin)
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "reviewer"


@pytest.mark.asyncio
async def test_admin_soft_deletes_other_user(client, db_session):
    admin = await _create_user(db_session, "admin6", UserRole.ADMIN)
    target = await _create_user(db_session, "target2", UserRole.OPERATOR)

    resp = await client.delete(f"{API}/{target.id}", headers=_auth_headers(admin))
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


# --- chốt an toàn: admin không tự khoá chính mình -------------------------------


@pytest.mark.asyncio
async def test_admin_cannot_downgrade_own_role(client, db_session):
    admin = await _create_user(db_session, "admin7", UserRole.ADMIN)

    resp = await client.patch(
        f"{API}/{admin.id}", json={"role": "operator"}, headers=_auth_headers(admin)
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_self_via_patch(client, db_session):
    admin = await _create_user(db_session, "admin8", UserRole.ADMIN)

    resp = await client.patch(
        f"{API}/{admin.id}", json={"is_active": False}, headers=_auth_headers(admin)
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_cannot_self_delete(client, db_session):
    admin = await _create_user(db_session, "admin9", UserRole.ADMIN)

    resp = await client.delete(f"{API}/{admin.id}", headers=_auth_headers(admin))
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_admin_can_update_own_display_name(client, db_session):
    """Chỉ chặn tự hạ role / tự vô hiệu hoá — đổi display_name của chính mình vẫn ok."""
    admin = await _create_user(db_session, "admin10", UserRole.ADMIN)

    resp = await client.patch(
        f"{API}/{admin.id}", json={"display_name": "New Name"}, headers=_auth_headers(admin)
    )
    assert resp.status_code == 200
    assert resp.json()["display_name"] == "New Name"


# --- hạn mức giờ GPU ----------------------------------------------------------


@pytest.mark.asyncio
async def test_new_user_starts_with_one_gpu_hour(client, db_session):
    admin = await _create_user(db_session, "quota_admin", UserRole.ADMIN)

    response = await client.get(API, headers=_auth_headers(admin))

    assert response.status_code == 200
    row = next(item for item in response.json() if item["username"] == "quota_admin")
    assert row["gpu_hours_limit"] == 1.0
    assert row["gpu_hours_used"] == 0.0


@pytest.mark.asyncio
async def test_admin_can_change_the_gpu_hour_limit(client, db_session):
    admin = await _create_user(db_session, "quota_boss", UserRole.ADMIN)
    target = await _create_user(db_session, "quota_target", UserRole.REVIEWER)

    response = await client.patch(
        f"{API}/{target.id}", headers=_auth_headers(admin), json={"gpu_hours_limit": 5.5}
    )

    assert response.status_code == 200
    assert response.json()["gpu_hours_limit"] == 5.5


@pytest.mark.asyncio
async def test_hours_used_is_not_writable_through_the_admin_form(client, db_session):
    """Số giờ đã dùng do hệ thống cộng dồn; admin cấp thêm bằng cách nâng hạn mức."""
    admin = await _create_user(db_session, "quota_admin2", UserRole.ADMIN)
    target = await _create_user(db_session, "quota_target2", UserRole.REVIEWER)
    target.gpu_hours_used = 0.75
    await db_session.commit()

    response = await client.patch(
        f"{API}/{target.id}", headers=_auth_headers(admin), json={"gpu_hours_used": 0.0}
    )

    assert response.status_code == 200
    assert response.json()["gpu_hours_used"] == 0.75


@pytest.mark.asyncio
async def test_a_negative_limit_is_refused(client, db_session):
    admin = await _create_user(db_session, "quota_admin3", UserRole.ADMIN)
    target = await _create_user(db_session, "quota_target3", UserRole.REVIEWER)

    response = await client.patch(
        f"{API}/{target.id}", headers=_auth_headers(admin), json={"gpu_hours_limit": -1}
    )

    assert response.status_code == 422
