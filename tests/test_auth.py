import jwt
import pytest

from src.config import get_settings
from src.models.db import User
from src.models.enums import UserRole
from src.services.security import create_access_token, create_refresh_token, hash_password

API = "/api/v1/auth"


async def _create_user(db_session, username: str, password: str, role: UserRole, is_active: bool = True) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name=username,
        role=role,
        is_active=is_active,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# --- register ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_creates_operator(client):
    resp = await client.post(
        f"{API}/register",
        json={"username": "Alice", "password": "goodpass1", "display_name": "Alice A"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "alice"  # normalize lowercase
    assert body["role"] == "operator"
    assert body["is_active"] is False  # chờ admin duyệt mới đăng nhập được
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_register_duplicate_username_returns_409(client):
    payload = {"username": "bob", "password": "goodpass1", "display_name": "Bob"}
    first = await client.post(f"{API}/register", json=payload)
    assert first.status_code == 201

    second = await client.post(f"{API}/register", json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_register_password_too_short_returns_422(client):
    resp = await client.post(
        f"{API}/register",
        json={"username": "carol", "password": "short7x", "display_name": "Carol"},
    )
    assert len("short7x") == 7
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_register_disabled_returns_403(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "allow_self_register", False)

    resp = await client.post(
        f"{API}/register",
        json={"username": "denied", "password": "goodpass1", "display_name": "Denied"},
    )
    assert resp.status_code == 403


# --- login --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success_returns_tokens(client, db_session):
    await _create_user(db_session, "dave", "correctpass", UserRole.OPERATOR)

    resp = await client.post(f"{API}/login", data={"username": "dave", "password": "correctpass"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client, db_session):
    await _create_user(db_session, "eve", "correctpass", UserRole.OPERATOR)

    resp = await client.post(f"{API}/login", data={"username": "eve", "password": "wrongpass"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_unknown_and_wrong_password_share_same_message(client, db_session):
    await _create_user(db_session, "frank", "correctpass", UserRole.OPERATOR)

    unknown = await client.post(f"{API}/login", data={"username": "ghost", "password": "whatever1"})
    wrong = await client.post(f"{API}/login", data={"username": "frank", "password": "wrongpass"})

    assert unknown.status_code == 401
    assert wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


@pytest.mark.asyncio
async def test_login_inactive_user_is_told_it_is_pending(client, db_session):
    """Mật khẩu đúng nhưng chưa được duyệt: nói thẳng, đừng để người vừa đăng ký
    tưởng mình gõ sai. Không lộ gì thêm — họ đã chứng minh biết mật khẩu."""
    await _create_user(db_session, "gina", "correctpass", UserRole.OPERATOR, is_active=False)

    resp = await client.post(f"{API}/login", data={"username": "gina", "password": "correctpass"})
    assert resp.status_code == 403
    assert "duyệt" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_login_inactive_user_with_wrong_password_returns_401(client, db_session):
    """Sai mật khẩu thì vẫn là 401 chung, không tiết lộ tài khoản có tồn tại."""
    await _create_user(db_session, "hana", "correctpass", UserRole.OPERATOR, is_active=False)

    resp = await client.post(f"{API}/login", data={"username": "hana", "password": "wrongpass"})
    assert resp.status_code == 401


# --- me / token validation ------------------------------------------------------


@pytest.mark.asyncio
async def test_me_without_token_returns_401(client):
    resp = await client.get(f"{API}/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_garbage_token_returns_401(client):
    resp = await client.get(f"{API}/me", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_refresh_token_as_access_returns_401(client, db_session):
    user = await _create_user(db_session, "harry", "correctpass", UserRole.OPERATOR)
    refresh_token = create_refresh_token(user.id, user.role)

    resp = await client.get(f"{API}/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_valid_access_token_returns_user(client, db_session):
    user = await _create_user(db_session, "iris", "correctpass", UserRole.REVIEWER)
    access_token = create_access_token(user.id, user.role)

    resp = await client.get(f"{API}/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "iris"
    assert body["role"] == "reviewer"
    assert "password_hash" not in body


@pytest.mark.asyncio
async def test_me_token_of_soft_deleted_user_returns_401(client, db_session):
    user = await _create_user(db_session, "jack", "correctpass", UserRole.OPERATOR)
    access_token = create_access_token(user.id, user.role)

    # Vô hiệu hoá user SAU khi đã phát hành token — token còn hạn nhưng phải
    # bị từ chối ngay vì current_user bắt buộc query DB, không tin payload.
    user.is_active = False
    await db_session.commit()

    resp = await client.get(f"{API}/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 401


# --- refresh ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_returns_new_token_pair(client, db_session):
    user = await _create_user(db_session, "kelly", "correctpass", UserRole.OPERATOR)
    refresh_token = create_refresh_token(user.id, user.role)

    resp = await client.post(f"{API}/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["refresh_token"]


@pytest.mark.asyncio
async def test_refresh_with_access_token_returns_401(client, db_session):
    user = await _create_user(db_session, "liam", "correctpass", UserRole.OPERATOR)
    access_token = create_access_token(user.id, user.role)

    resp = await client.post(f"{API}/refresh", json={"refresh_token": access_token})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_with_expired_token_returns_401(client, db_session):
    user = await _create_user(db_session, "mona", "correctpass", UserRole.OPERATOR)
    settings = get_settings()
    expired_payload = {
        "sub": user.id,
        "role": user.role,
        "type": "refresh",
        "iat": 1,
        "exp": 2,  # đã hết hạn từ epoch 1970
    }
    expired_token = jwt.encode(expired_payload, settings.jwt_secret, algorithm="HS256")

    resp = await client.post(f"{API}/refresh", json={"refresh_token": expired_token})
    assert resp.status_code == 401


# --- change password ------------------------------------------------------------


@pytest.mark.asyncio
async def test_change_password_wrong_old_password_returns_401(client, db_session):
    user = await _create_user(db_session, "nancy", "correctpass", UserRole.OPERATOR)
    access_token = create_access_token(user.id, user.role)

    resp = await client.patch(
        f"{API}/me/password",
        json={"old_password": "wrongold1", "new_password": "brandnewpass1"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_change_password_success_allows_new_login(client, db_session):
    user = await _create_user(db_session, "oscar", "correctpass", UserRole.OPERATOR)
    access_token = create_access_token(user.id, user.role)

    resp = await client.patch(
        f"{API}/me/password",
        json={"old_password": "correctpass", "new_password": "brandnewpass1"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200

    old_login = await client.post(f"{API}/login", data={"username": "oscar", "password": "correctpass"})
    assert old_login.status_code == 401

    new_login = await client.post(f"{API}/login", data={"username": "oscar", "password": "brandnewpass1"})
    assert new_login.status_code == 200
