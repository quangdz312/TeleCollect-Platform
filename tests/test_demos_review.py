import pytest

from src.models.db import Dataset, DatasetEpisode, Episode, Task, User
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.services import storage
from src.services.security import create_access_token, hash_password

API = "/api/v1/demos"


async def _create_user(db_session, username: str, role: UserRole = UserRole.OPERATOR) -> User:
    user = User(username=username, password_hash=hash_password("correctpass"), display_name=username, role=role)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _create_task(db_session, name: str = "pick_place") -> Task:
    task = Task(name=name, description="d", instruction="i", hints=[], action_dim=7, max_steps=200)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _create_episode(
    db_session,
    task_name: str,
    operator_id: str,
    status: DemoStatus = DemoStatus.RECORDED,
    outcome: DemoOutcome | None = None,
    duration_s: float = 10.0,
) -> Episode:
    episode = Episode(
        task_name=task_name, operator_id=operator_id, status=status, outcome=outcome, duration_s=duration_s
    )
    db_session.add(episode)
    await db_session.commit()
    await db_session.refresh(episode)
    return episode


# --- bẫy thứ tự route: /summary trước /{id} ----------------------------------


@pytest.mark.asyncio
async def test_summary_route_is_not_shadowed_by_id_route(client, db_session):
    """GET /demos/summary phải trả summary thật, không phải 404 (chứng minh
    route không bị /{demo_id} nuốt mất)."""
    user = await _create_user(db_session, "u1")

    resp = await client.get(f"{API}/summary", headers=_auth_headers(user))
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "success_rate" in body


# --- quyền sở hữu (trim/label/delete) -------------------------------------------


@pytest.mark.asyncio
async def test_operator_label_demo_of_other_returns_403(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner1")
    other = await _create_user(db_session, "other1")
    demo = await _create_episode(db_session, task.name, owner.id)

    resp = await client.patch(
        f"{API}/{demo.id}/label",
        json={"outcome": "success"},
        headers=_auth_headers(other),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_operator_label_own_demo_returns_200(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner2")
    demo = await _create_episode(db_session, task.name, owner.id)

    resp = await client.patch(
        f"{API}/{demo.id}/label",
        json={"outcome": "success"},
        headers=_auth_headers(owner),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "labeled"


@pytest.mark.asyncio
async def test_reviewer_label_demo_of_other_returns_200(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner3")
    reviewer = await _create_user(db_session, "rev1", UserRole.REVIEWER)
    demo = await _create_episode(db_session, task.name, owner.id)

    resp = await client.patch(
        f"{API}/{demo.id}/label",
        json={"outcome": "failure"},
        headers=_auth_headers(reviewer),
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_operator_trim_demo_of_other_returns_403(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner4")
    other = await _create_user(db_session, "other4")
    demo = await _create_episode(db_session, task.name, owner.id)

    resp = await client.patch(
        f"{API}/{demo.id}/trim",
        json={"trim_start_s": 1.0, "trim_end_s": 5.0},
        headers=_auth_headers(other),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_operator_delete_demo_of_other_returns_403(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner5")
    other = await _create_user(db_session, "other5")
    demo = await _create_episode(db_session, task.name, owner.id)

    resp = await client.delete(f"{API}/{demo.id}", headers=_auth_headers(other))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_operator_delete_own_demo_returns_204(client, db_session, storage_dir):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner6")
    demo = await _create_episode(db_session, task.name, owner.id)

    resp = await client.delete(f"{API}/{demo.id}", headers=_auth_headers(owner))
    assert resp.status_code == 204


# --- review chỉ dành cho reviewer trở lên ---------------------------------------


@pytest.mark.asyncio
async def test_operator_cannot_review(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner7")
    demo = await _create_episode(db_session, task.name, owner.id)

    resp = await client.post(
        f"{API}/{demo.id}/review", json={"decision": "approve"}, headers=_auth_headers(owner)
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_operator_cannot_reopen(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner8")
    demo = await _create_episode(db_session, task.name, owner.id, status=DemoStatus.APPROVED, outcome=DemoOutcome.SUCCESS)

    resp = await client.post(f"{API}/{demo.id}/reopen", headers=_auth_headers(owner))
    assert resp.status_code == 403


# --- transitions qua HTTP --------------------------------------------------------


@pytest.mark.asyncio
async def test_label_demo_already_approved_returns_409(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner9")
    demo = await _create_episode(db_session, task.name, owner.id, status=DemoStatus.APPROVED, outcome=DemoOutcome.SUCCESS)

    resp = await client.patch(
        f"{API}/{demo.id}/label", json={"outcome": "failure"}, headers=_auth_headers(owner)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_trim_demo_already_approved_returns_409(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner10")
    demo = await _create_episode(db_session, task.name, owner.id, status=DemoStatus.APPROVED, outcome=DemoOutcome.SUCCESS)

    resp = await client.patch(
        f"{API}/{demo.id}/trim",
        json={"trim_start_s": 1.0, "trim_end_s": 5.0},
        headers=_auth_headers(owner),
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_review_approve_from_recorded_auto_success(client, db_session):
    """Nới: review thẳng từ recorded (chưa label) -> 200, outcome tự thành success."""
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner11")
    reviewer = await _create_user(db_session, "rev2", UserRole.REVIEWER)
    demo = await _create_episode(db_session, task.name, owner.id, status=DemoStatus.RECORDED)

    resp = await client.post(
        f"{API}/{demo.id}/review", json={"decision": "approve"}, headers=_auth_headers(reviewer)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "approved"
    assert body["outcome"] == "success"
    assert body["reviewer_id"] == reviewer.id
    assert body["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_review_approve_already_approved_returns_409(client, db_session):
    """Chống double-click."""
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner12")
    reviewer = await _create_user(db_session, "rev3", UserRole.REVIEWER)
    demo = await _create_episode(db_session, task.name, owner.id, status=DemoStatus.APPROVED, outcome=DemoOutcome.SUCCESS)

    resp = await client.post(
        f"{API}/{demo.id}/review", json={"decision": "approve"}, headers=_auth_headers(reviewer)
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_review_reject_does_not_auto_assign_outcome(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner13")
    reviewer = await _create_user(db_session, "rev4", UserRole.REVIEWER)
    demo = await _create_episode(db_session, task.name, owner.id, status=DemoStatus.RECORDED)

    resp = await client.post(
        f"{API}/{demo.id}/review", json={"decision": "reject"}, headers=_auth_headers(reviewer)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["outcome"] is None


@pytest.mark.asyncio
async def test_reopen_approved_with_outcome_returns_labeled(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner14")
    reviewer = await _create_user(db_session, "rev5", UserRole.REVIEWER)
    demo = await _create_episode(db_session, task.name, owner.id, status=DemoStatus.APPROVED, outcome=DemoOutcome.SUCCESS)
    demo.reviewer_id = reviewer.id
    await db_session.commit()

    resp = await client.post(f"{API}/{demo.id}/reopen", headers=_auth_headers(reviewer))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "labeled"
    assert body["outcome"] == "success"
    assert body["reviewer_id"] is None
    assert body["reviewed_at"] is None


@pytest.mark.asyncio
async def test_reopen_recorded_returns_409(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner15")
    reviewer = await _create_user(db_session, "rev6", UserRole.REVIEWER)
    demo = await _create_episode(db_session, task.name, owner.id, status=DemoStatus.RECORDED)

    resp = await client.post(f"{API}/{demo.id}/reopen", headers=_auth_headers(reviewer))
    assert resp.status_code == 409


# --- trim validate ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_trim_start_greater_equal_end_returns_422(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner16")
    demo = await _create_episode(db_session, task.name, owner.id, duration_s=10.0)

    resp = await client.patch(
        f"{API}/{demo.id}/trim",
        json={"trim_start_s": 5.0, "trim_end_s": 5.0},
        headers=_auth_headers(owner),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_trim_end_greater_than_duration_returns_422(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner17")
    demo = await _create_episode(db_session, task.name, owner.id, duration_s=10.0)

    resp = await client.patch(
        f"{API}/{demo.id}/trim",
        json={"trim_start_s": 0.0, "trim_end_s": 10.5},
        headers=_auth_headers(owner),
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_trim_negative_returns_422(client, db_session):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner18")
    demo = await _create_episode(db_session, task.name, owner.id, duration_s=10.0)

    resp = await client.patch(
        f"{API}/{demo.id}/trim",
        json={"trim_start_s": -1.0, "trim_end_s": 5.0},
        headers=_auth_headers(owner),
    )
    assert resp.status_code == 422


# --- delete: dataset_episodes bị xoá, dataset zip không bị đụng ------------------


@pytest.mark.asyncio
async def test_delete_removes_row_folder_and_dataset_episode_link_but_not_dataset(
    client, db_session, storage_dir
):
    task = await _create_task(db_session)
    owner = await _create_user(db_session, "owner19")
    demo = await _create_episode(db_session, task.name, owner.id)

    # tạo file thật trên đĩa để kiểm tra thư mục bị xoá
    episode_dir = storage.episode_dir(demo.id)
    episode_dir.mkdir(parents=True, exist_ok=True)
    (episode_dir / storage.FRONT_FILENAME).write_bytes(b"fake video content")
    assert episode_dir.exists()

    dataset = Dataset(name="ds1", task_names=[task.name])
    db_session.add(dataset)
    await db_session.commit()
    await db_session.refresh(dataset)

    # zip giả lập độc lập — KHÔNG được đụng tới khi xoá demo
    dataset_zip_dir = storage_dir / "datasets"
    dataset_zip_dir.mkdir(parents=True, exist_ok=True)
    fake_zip = dataset_zip_dir / f"{dataset.id}.zip"
    fake_zip.write_bytes(b"pretend this is a real zip snapshot")

    db_session.add(DatasetEpisode(dataset_id=dataset.id, episode_id=demo.id))
    await db_session.commit()

    demo_id = demo.id
    dataset_id = dataset.id

    resp = await client.delete(f"{API}/{demo_id}", headers=_auth_headers(owner))
    assert resp.status_code == 204

    # DELETE chạy trên session khác (dependency override của client) — session
    # thô của test còn cache identity map cũ, phải expire trước khi đọc lại.
    from sqlalchemy import select

    db_session.expire_all()
    remaining_episode = await db_session.get(Episode, demo_id)
    assert remaining_episode is None

    remaining_links = (
        await db_session.execute(select(DatasetEpisode).where(DatasetEpisode.episode_id == demo_id))
    ).scalars().all()
    assert remaining_links == []

    # thư mục file mất
    assert not episode_dir.exists()

    # dataset + file zip của nó KHÔNG bị đụng
    remaining_dataset = await db_session.get(Dataset, dataset_id)
    assert remaining_dataset is not None
    assert fake_zip.exists()
    assert fake_zip.read_bytes() == b"pretend this is a real zip snapshot"


# --- summary ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_empty_db_returns_zero_rates_without_crashing(client, db_session):
    user = await _create_user(db_session, "u2")

    resp = await client.get(f"{API}/summary", headers=_auth_headers(user))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["success_rate"] == 0.0
    assert body["approval_rate"] == 0.0
    assert body["labeled_count"] == 0
    assert body["reviewed_count"] == 0
    assert body["total_frames"] == 0
    assert body["total_duration_hours"] == 0.0
    assert body["total_size_bytes"] == 0


@pytest.mark.asyncio
async def test_summary_matches_hand_computed_counts(client, db_session):
    task_a = await _create_task(db_session, "pick_place")
    task_b = await _create_task(db_session, "stack")
    owner = await _create_user(db_session, "owner20")

    # 2 recorded (chưa nhãn), 1 labeled(failure), 2 approved(success), 1 rejected
    e1 = Episode(task_name=task_a.name, operator_id=owner.id, status=DemoStatus.RECORDED, num_frames=100, duration_s=10.0, size_bytes=1000)
    e2 = Episode(task_name=task_a.name, operator_id=owner.id, status=DemoStatus.RECORDED, num_frames=100, duration_s=10.0, size_bytes=1000)
    e3 = Episode(task_name=task_a.name, operator_id=owner.id, status=DemoStatus.LABELED, outcome=DemoOutcome.FAILURE, num_frames=100, duration_s=10.0, size_bytes=1000)
    e4 = Episode(task_name=task_b.name, operator_id=owner.id, status=DemoStatus.APPROVED, outcome=DemoOutcome.SUCCESS, num_frames=200, duration_s=20.0, size_bytes=2000)
    e5 = Episode(task_name=task_b.name, operator_id=owner.id, status=DemoStatus.APPROVED, outcome=DemoOutcome.SUCCESS, num_frames=200, duration_s=20.0, size_bytes=2000)
    e6 = Episode(task_name=task_b.name, operator_id=owner.id, status=DemoStatus.REJECTED, num_frames=100, duration_s=10.0, size_bytes=1000)
    db_session.add_all([e1, e2, e3, e4, e5, e6])
    await db_session.commit()

    user = await _create_user(db_session, "u3")
    resp = await client.get(f"{API}/summary", headers=_auth_headers(user))
    assert resp.status_code == 200
    body = resp.json()

    assert body["total"] == 6
    assert body["by_status"]["recorded"] == 2
    assert body["by_status"]["labeled"] == 1
    assert body["by_status"]["approved"] == 2
    assert body["by_status"]["rejected"] == 1
    assert body["by_outcome"]["success"] == 2
    assert body["by_outcome"]["failure"] == 1
    assert body["by_task"]["pick_place"] == 3
    assert body["by_task"]["stack"] == 3

    # labeled_count = success + failure (theo outcome, không theo status) = 2 + 1 = 3
    assert body["labeled_count"] == 3
    # reviewed_count = approved + rejected = 2 + 1 = 3
    assert body["reviewed_count"] == 3
    assert body["approved_count"] == 2

    assert body["success_rate"] == pytest.approx(2 / 3)
    assert body["approval_rate"] == pytest.approx(2 / 3)

    assert body["total_frames"] == 100 + 100 + 100 + 200 + 200 + 100
    assert body["total_size_bytes"] == 1000 + 1000 + 1000 + 2000 + 2000 + 1000
    total_duration_s = 10 + 10 + 10 + 20 + 20 + 10
    assert body["total_duration_hours"] == pytest.approx(total_duration_s / 3600)
