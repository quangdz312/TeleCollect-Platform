import pytest

from src.models.db import Episode, Task, User
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.services.security import create_access_token, hash_password

API = "/api/v1/demos"


async def _create_user(db_session, username: str, role: UserRole = UserRole.OPERATOR) -> User:
    user = User(username=username, password_hash=hash_password("correctpass"), display_name=username, role=role)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _create_task(db_session, name: str) -> Task:
    task = Task(name=name, description="d", instruction="i", hints=[], action_dim=7, max_steps=200)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _create_episode(
    db_session, task_name: str, operator_id: str, status: DemoStatus, outcome: DemoOutcome | None = None
) -> Episode:
    episode = Episode(task_name=task_name, operator_id=operator_id, status=status, outcome=outcome)
    db_session.add(episode)
    await db_session.commit()
    await db_session.refresh(episode)
    return episode


# --- get by id -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_demo_not_found_returns_404(client, db_session):
    user = await _create_user(db_session, "op1")

    resp = await client.get(f"{API}/does-not-exist", headers=_auth_headers(user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_demo_found_returns_detail(client, db_session):
    task = await _create_task(db_session, "pick_place")
    user = await _create_user(db_session, "op2")
    episode = await _create_episode(db_session, task.name, user.id, DemoStatus.RECORDED)

    resp = await client.get(f"{API}/{episode.id}", headers=_auth_headers(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == episode.id
    assert body["has_thumbnail"] is False


# --- list: auth --------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_demos_without_token_returns_401(client):
    resp = await client.get(API)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_operator_can_list_all_demos_not_just_own(client, db_session):
    """Ma trận quyền chỉ giới hạn hành động sửa/xoá, không giới hạn đọc — mọi
    role (kể cả operator) xem được toàn bộ danh sách."""
    task = await _create_task(db_session, "pick_place")
    owner = await _create_user(db_session, "owner1")
    other = await _create_user(db_session, "other1")
    await _create_episode(db_session, task.name, owner.id, DemoStatus.RECORDED)

    resp = await client.get(API, headers=_auth_headers(other))
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


# --- list: filters -------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_by_task(client, db_session):
    task_a = await _create_task(db_session, "pick_place")
    task_b = await _create_task(db_session, "stack")
    user = await _create_user(db_session, "op3")
    await _create_episode(db_session, task_a.name, user.id, DemoStatus.RECORDED)
    await _create_episode(db_session, task_a.name, user.id, DemoStatus.RECORDED)
    await _create_episode(db_session, task_b.name, user.id, DemoStatus.RECORDED)

    resp = await client.get(f"{API}?task=pick_place", headers=_auth_headers(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(item["task_name"] == "pick_place" for item in body["items"])


@pytest.mark.asyncio
async def test_list_filters_by_status(client, db_session):
    task = await _create_task(db_session, "pick_place")
    user = await _create_user(db_session, "op4")
    await _create_episode(db_session, task.name, user.id, DemoStatus.RECORDED)
    await _create_episode(db_session, task.name, user.id, DemoStatus.APPROVED, DemoOutcome.SUCCESS)
    await _create_episode(db_session, task.name, user.id, DemoStatus.APPROVED, DemoOutcome.SUCCESS)

    resp = await client.get(f"{API}?status=approved", headers=_auth_headers(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert all(item["status"] == "approved" for item in body["items"])


@pytest.mark.asyncio
async def test_list_filters_by_operator_id(client, db_session):
    task = await _create_task(db_session, "pick_place")
    op_a = await _create_user(db_session, "op_a")
    op_b = await _create_user(db_session, "op_b")
    await _create_episode(db_session, task.name, op_a.id, DemoStatus.RECORDED)
    await _create_episode(db_session, task.name, op_a.id, DemoStatus.RECORDED)
    await _create_episode(db_session, task.name, op_b.id, DemoStatus.RECORDED)

    resp = await client.get(f"{API}?operator_id={op_a.id}", headers=_auth_headers(op_a))
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_list_mine_filters_to_current_user(client, db_session):
    task = await _create_task(db_session, "pick_place")
    op_a = await _create_user(db_session, "op_mine_a")
    op_b = await _create_user(db_session, "op_mine_b")
    await _create_episode(db_session, task.name, op_a.id, DemoStatus.RECORDED)
    await _create_episode(db_session, task.name, op_b.id, DemoStatus.RECORDED)
    await _create_episode(db_session, task.name, op_b.id, DemoStatus.RECORDED)

    resp = await client.get(f"{API}?mine=true", headers=_auth_headers(op_b))
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


# --- pagination ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_page_size_over_100_returns_422(client, db_session):
    user = await _create_user(db_session, "op5")

    resp = await client.get(f"{API}?page_size=101", headers=_auth_headers(user))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_pagination_page_2_does_not_overlap_page_1(client, db_session):
    task = await _create_task(db_session, "pick_place")
    user = await _create_user(db_session, "op6")
    for _ in range(25):
        await _create_episode(db_session, task.name, user.id, DemoStatus.RECORDED)

    page1 = await client.get(f"{API}?page=1&page_size=20", headers=_auth_headers(user))
    page2 = await client.get(f"{API}?page=2&page_size=20", headers=_auth_headers(user))

    assert page1.status_code == 200 and page2.status_code == 200
    body1, body2 = page1.json(), page2.json()

    assert body1["total"] == 25
    assert body1["total_pages"] == 2
    assert len(body1["items"]) == 20
    assert len(body2["items"]) == 5

    ids_page1 = {item["id"] for item in body1["items"]}
    ids_page2 = {item["id"] for item in body2["items"]}
    assert ids_page1.isdisjoint(ids_page2)


@pytest.mark.asyncio
async def test_pagination_empty_result_has_zero_total_pages(client, db_session):
    user = await _create_user(db_session, "op7")

    resp = await client.get(API, headers=_auth_headers(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["total_pages"] == 0
    assert body["items"] == []
