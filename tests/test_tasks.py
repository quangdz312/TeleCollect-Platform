import pytest

from src.models.db import Episode, Task, User
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.services.security import create_access_token, hash_password

API = "/api/v1/tasks"


async def _create_user(db_session, username: str, role: UserRole) -> User:
    user = User(username=username, password_hash=hash_password("correctpass"), display_name=username, role=role)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _create_task(db_session, name: str = "pick_place") -> Task:
    task = Task(
        name=name,
        description="desc",
        instruction="do it",
        hints=["hint1"],
        action_dim=7,
        max_steps=200,
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task


# --- read endpoints ------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tasks_without_token_returns_401(client):
    resp = await client.get(API)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_tasks_with_token_returns_200(client, db_session):
    await _create_task(db_session)
    operator = await _create_user(db_session, "op1", UserRole.OPERATOR)

    resp = await client.get(API, headers=_auth_headers(operator))
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()]
    assert "pick_place" in names


@pytest.mark.asyncio
async def test_get_task_not_found_returns_404(client, db_session):
    operator = await _create_user(db_session, "op2", UserRole.OPERATOR)

    resp = await client.get(f"{API}/does_not_exist", headers=_auth_headers(operator))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_task_found_returns_full_body(client, db_session):
    await _create_task(db_session, "stack")
    operator = await _create_user(db_session, "op3", UserRole.OPERATOR)

    resp = await client.get(f"{API}/stack", headers=_auth_headers(operator))
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "stack"
    assert body["hints"] == ["hint1"]
    assert body["instruction"] == "do it"


# --- phân quyền tạo/sửa task -----------------------------------------------------


@pytest.mark.asyncio
async def test_operator_cannot_create_task(client, db_session):
    operator = await _create_user(db_session, "op4", UserRole.OPERATOR)

    resp = await client.post(
        API,
        json={"name": "new_task", "action_dim": 7, "max_steps": 100},
        headers=_auth_headers(operator),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_reviewer_cannot_create_task(client, db_session):
    reviewer = await _create_user(db_session, "rev1", UserRole.REVIEWER)

    resp = await client.post(
        API,
        json={"name": "new_task", "action_dim": 7, "max_steps": 100},
        headers=_auth_headers(reviewer),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_create_task(client, db_session):
    admin = await _create_user(db_session, "admin1", UserRole.ADMIN)

    resp = await client.post(
        API,
        json={
            "name": "new_task",
            "description": "d",
            "instruction": "i",
            "hints": ["h1", "h2"],
            "action_dim": 7,
            "max_steps": 100,
        },
        headers=_auth_headers(admin),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "new_task"
    assert body["hints"] == ["h1", "h2"]


# --- validate -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_duplicate_name_returns_409(client, db_session):
    await _create_task(db_session, "dup_task")
    admin = await _create_user(db_session, "admin2", UserRole.ADMIN)

    resp = await client.post(
        API,
        json={"name": "dup_task", "action_dim": 7, "max_steps": 100},
        headers=_auth_headers(admin),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_task_invalid_name_with_spaces_and_uppercase_returns_422(client, db_session):
    admin = await _create_user(db_session, "admin3", UserRole.ADMIN)

    resp = await client.post(
        API,
        json={"name": "Pick Place", "action_dim": 7, "max_steps": 100},
        headers=_auth_headers(admin),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_task_action_dim_zero_returns_422(client, db_session):
    admin = await _create_user(db_session, "admin4", UserRole.ADMIN)

    resp = await client.post(
        API,
        json={"name": "zero_dim", "action_dim": 0, "max_steps": 100},
        headers=_auth_headers(admin),
    )
    assert resp.status_code == 422


# --- update -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_task_cannot_change_name(client, db_session):
    await _create_task(db_session, "immutable_name")
    admin = await _create_user(db_session, "admin5", UserRole.ADMIN)

    resp = await client.patch(
        f"{API}/immutable_name",
        json={"name": "renamed"},
        headers=_auth_headers(admin),
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_task_updates_fields(client, db_session):
    await _create_task(db_session, "editable")
    admin = await _create_user(db_session, "admin6", UserRole.ADMIN)

    resp = await client.patch(
        f"{API}/editable",
        json={"description": "new desc", "max_steps": 999},
        headers=_auth_headers(admin),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "new desc"
    assert body["max_steps"] == 999


@pytest.mark.asyncio
async def test_patch_task_not_found_returns_404(client, db_session):
    admin = await _create_user(db_session, "admin7", UserRole.ADMIN)

    resp = await client.patch(
        f"{API}/does_not_exist", json={"description": "x"}, headers=_auth_headers(admin)
    )
    assert resp.status_code == 404


# --- stats ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_not_found_task_returns_404(client, db_session):
    operator = await _create_user(db_session, "op5", UserRole.OPERATOR)

    resp = await client.get(f"{API}/does_not_exist/stats", headers=_auth_headers(operator))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stats_with_no_demos_returns_zero_without_crashing(client, db_session):
    await _create_task(db_session, "empty_task")
    operator = await _create_user(db_session, "op6", UserRole.OPERATOR)

    resp = await client.get(f"{API}/empty_task/stats", headers=_auth_headers(operator))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["success_rate"] == 0.0
    assert body["approval_rate"] == 0.0
    assert body["approved_count"] == 0
    assert body["success_count"] == 0


@pytest.mark.asyncio
async def test_stats_with_demos_counts_correctly(client, db_session):
    task = await _create_task(db_session, "counted_task")
    operator = await _create_user(db_session, "op7", UserRole.OPERATOR)

    episodes = [
        Episode(task_name=task.name, operator_id=operator.id, status=DemoStatus.RECORDED),
        Episode(
            task_name=task.name,
            operator_id=operator.id,
            status=DemoStatus.LABELED,
            outcome=DemoOutcome.FAILURE,
        ),
        Episode(
            task_name=task.name,
            operator_id=operator.id,
            status=DemoStatus.APPROVED,
            outcome=DemoOutcome.SUCCESS,
        ),
        Episode(
            task_name=task.name,
            operator_id=operator.id,
            status=DemoStatus.APPROVED,
            outcome=DemoOutcome.SUCCESS,
        ),
        Episode(
            task_name=task.name,
            operator_id=operator.id,
            status=DemoStatus.REJECTED,
            outcome=DemoOutcome.FAILURE,
        ),
    ]
    db_session.add_all(episodes)
    await db_session.commit()

    resp = await client.get(f"{API}/counted_task/stats", headers=_auth_headers(operator))
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 5
    assert body["by_status"]["recorded"] == 1
    assert body["by_status"]["labeled"] == 1
    assert body["by_status"]["approved"] == 2
    assert body["by_status"]["rejected"] == 1
    assert body["by_status"]["recording"] == 0
    assert body["by_outcome"]["success"] == 2
    assert body["by_outcome"]["failure"] == 2
    assert body["approved_count"] == 2
    assert body["success_count"] == 2
    assert body["success_rate"] == pytest.approx(2 / 5)
    assert body["approval_rate"] == pytest.approx(2 / 5)
