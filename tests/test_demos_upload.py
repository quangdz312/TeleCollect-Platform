import json

import pytest

from src.config import get_settings
from src.models.db import Task, User
from src.models.enums import UserRole
from src.services import storage
from src.services.security import create_access_token, hash_password

API = "/api/v1/demos"


async def _create_user(db_session, username: str, role: UserRole = UserRole.OPERATOR) -> User:
    user = User(username=username, password_hash=hash_password("correctpass"), display_name=username, role=role)
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _create_task(db_session, name: str = "pick_place", action_dim: int = 7) -> Task:
    task = Task(name=name, description="d", instruction="i", hints=[], action_dim=action_dim, max_steps=200)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


def _no_orphans(storage_dir) -> bool:
    """episodes/ và tmp/ không còn thư mục con nào — dọn rác sạch."""
    episodes_left = list(storage.episodes_root().glob("*")) if storage.episodes_root().exists() else []
    tmp_left = list(storage.tmp_root().glob("*")) if storage.tmp_root().exists() else []
    return not episodes_left and not tmp_left


# --- auth / not-found ------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_without_token_returns_401(client, storage_dir, sample_mp4_bytes):
    resp = await client.post(
        f"{API}/upload",
        data={"task_name": "pick_place"},
        files={"front": ("front.mp4", sample_mp4_bytes, "video/mp4")},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_upload_unknown_task_returns_404(client, db_session, storage_dir, sample_mp4_bytes):
    user = await _create_user(db_session, "op1")

    resp = await client.post(
        f"{API}/upload",
        data={"task_name": "does_not_exist"},
        files={"front": ("front.mp4", sample_mp4_bytes, "video/mp4")},
        headers=_auth_headers(user),
    )
    assert resp.status_code == 404
    assert _no_orphans(storage_dir)


# --- magic bytes -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_fake_mp4_without_ftyp_returns_422_no_orphan(client, db_session, storage_dir):
    await _create_task(db_session)
    user = await _create_user(db_session, "op2")

    fake_content = b"this is just a plain text file renamed to .mp4, no magic bytes here"
    resp = await client.post(
        f"{API}/upload",
        data={"task_name": "pick_place"},
        files={"front": ("front.mp4", fake_content, "video/mp4")},
        headers=_auth_headers(user),
    )
    assert resp.status_code == 422
    assert _no_orphans(storage_dir)


# --- size limit --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_exceeds_max_size_returns_413_and_cleans_up(
    client, db_session, storage_dir, sample_mp4_bytes, monkeypatch
):
    await _create_task(db_session)
    user = await _create_user(db_session, "op3")

    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_mb", 0)  # max_bytes=0 -> byte đầu tiên đã vượt

    resp = await client.post(
        f"{API}/upload",
        data={"task_name": "pick_place"},
        files={"front": ("front.mp4", sample_mp4_bytes, "video/mp4")},
        headers=_auth_headers(user),
    )
    assert resp.status_code == 413
    assert _no_orphans(storage_dir)


# --- trajectory --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_trajectory_invalid_json_returns_422(
    client, db_session, storage_dir, sample_mp4_bytes
):
    await _create_task(db_session)
    user = await _create_user(db_session, "op4")

    resp = await client.post(
        f"{API}/upload",
        data={"task_name": "pick_place"},
        files={
            "front": ("front.mp4", sample_mp4_bytes, "video/mp4"),
            "trajectory": ("trajectory.json", b"{not valid json", "application/json"),
        },
        headers=_auth_headers(user),
    )
    assert resp.status_code == 422
    assert _no_orphans(storage_dir)


@pytest.mark.asyncio
async def test_upload_trajectory_wrong_action_dim_returns_422(
    client, db_session, storage_dir, sample_mp4_bytes
):
    await _create_task(db_session, action_dim=7)
    user = await _create_user(db_session, "op5")

    bad_trajectory = json.dumps({"action": [[0, 1, 2], [0, 1, 2]]}).encode()  # len 3 != action_dim 7
    resp = await client.post(
        f"{API}/upload",
        data={"task_name": "pick_place"},
        files={
            "front": ("front.mp4", sample_mp4_bytes, "video/mp4"),
            "trajectory": ("trajectory.json", bad_trajectory, "application/json"),
        },
        headers=_auth_headers(user),
    )
    assert resp.status_code == 422
    assert _no_orphans(storage_dir)


# --- success -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_front_only_succeeds(client, db_session, storage_dir, sample_mp4_bytes):
    await _create_task(db_session)
    user = await _create_user(db_session, "op6")

    resp = await client.post(
        f"{API}/upload",
        data={"task_name": "pick_place"},
        files={"front": ("front.mp4", sample_mp4_bytes, "video/mp4")},
        headers=_auth_headers(user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_wrist"] is False
    assert body["has_trajectory"] is False
    assert body["status"] == "recorded"
    assert body["operator_id"] == user.id


@pytest.mark.asyncio
async def test_upload_success_probe_values_are_nonzero_and_match_real_file(
    client, db_session, storage_dir, sample_mp4_bytes
):
    await _create_task(db_session)
    user = await _create_user(db_session, "op7")

    resp = await client.post(
        f"{API}/upload",
        data={"task_name": "pick_place"},
        files={"front": ("front.mp4", sample_mp4_bytes, "video/mp4")},
        headers=_auth_headers(user),
    )
    assert resp.status_code == 201
    body = resp.json()

    assert body["duration_s"] is not None and body["duration_s"] > 0
    assert body["fps"] is not None and body["fps"] > 0
    assert body["num_frames"] is not None and body["num_frames"] > 0
    assert body["size_bytes"] is not None and body["size_bytes"] > 0

    # duration khớp file thật (sample_mp4_bytes sinh với -t 2 giây), sai số nhỏ do encode
    assert 1.5 <= body["duration_s"] <= 2.5

    # file thật phải nằm đúng chỗ: storage_dir/episodes/<id>/front.mp4
    episode_dir = storage.episode_dir(body["id"])
    assert (episode_dir / storage.FRONT_FILENAME).exists()
    assert not (storage.tmp_root()).exists() or not list(storage.tmp_root().glob("*"))


@pytest.mark.asyncio
async def test_upload_with_wrist_and_trajectory_succeeds(
    client, db_session, storage_dir, sample_mp4_bytes
):
    await _create_task(db_session, action_dim=2)
    user = await _create_user(db_session, "op8")

    good_trajectory = json.dumps({"action": [[0.1, 0.2], [0.3, 0.4]]}).encode()
    resp = await client.post(
        f"{API}/upload",
        data={"task_name": "pick_place"},
        files={
            "front": ("front.mp4", sample_mp4_bytes, "video/mp4"),
            "wrist": ("wrist.mp4", sample_mp4_bytes, "video/mp4"),
            "trajectory": ("trajectory.json", good_trajectory, "application/json"),
        },
        headers=_auth_headers(user),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_wrist"] is True
    assert body["has_trajectory"] is True

    episode_dir = storage.episode_dir(body["id"])
    assert (episode_dir / storage.WRIST_FILENAME).exists()
    assert (episode_dir / storage.TRAJECTORY_FILENAME).exists()
