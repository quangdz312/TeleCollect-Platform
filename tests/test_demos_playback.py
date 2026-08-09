import shutil

import pytest
import pytest_asyncio

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


async def _create_task(db_session, name: str = "pick_place") -> Task:
    task = Task(name=name, description="d", instruction="i", hints=[], action_dim=7, max_steps=200)
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _upload_demo(client, user: User, sample_mp4_bytes, task_name: str = "pick_place", with_wrist: bool = False):
    files = {"front": ("front.mp4", sample_mp4_bytes, "video/mp4")}
    if with_wrist:
        files["wrist"] = ("wrist.mp4", sample_mp4_bytes, "video/mp4")
    resp = await client.post(
        f"{API}/upload",
        data={"task_name": task_name},
        files=files,
        headers=_auth_headers(user),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def uploaded_demo(client, db_session, storage_dir, sample_mp4_bytes):
    await _create_task(db_session)
    user = await _create_user(db_session, "player1")
    demo = await _upload_demo(client, user, sample_mp4_bytes)
    return demo, user


# --- auth ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_playback_without_token_returns_401(client, uploaded_demo):
    demo, _user = uploaded_demo
    resp = await client.get(f"{API}/{demo['id']}/playback")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_playback_with_query_token_returns_200(client, uploaded_demo):
    demo, user = uploaded_demo
    token = create_access_token(user.id, user.role)

    resp = await client.get(f"{API}/{demo['id']}/playback?token={token}")
    assert resp.status_code == 200


# --- full file / headers ----------------------------------------------------------


@pytest.mark.asyncio
async def test_playback_no_range_returns_200_with_accept_ranges(client, uploaded_demo):
    demo, user = uploaded_demo
    resp = await client.get(f"{API}/{demo['id']}/playback", headers=_auth_headers(user))
    assert resp.status_code == 200
    assert resp.headers["accept-ranges"] == "bytes"
    assert resp.headers["content-type"] == "video/mp4"
    assert int(resp.headers["content-length"]) == len(resp.content)
    assert len(resp.content) > 0


@pytest.mark.asyncio
async def test_playback_range_0_1023_returns_206(client, uploaded_demo):
    demo, user = uploaded_demo
    resp = await client.get(
        f"{API}/{demo['id']}/playback",
        headers={**_auth_headers(user), "Range": "bytes=0-1023"},
    )
    assert resp.status_code == 206
    assert len(resp.content) == 1024
    total = int(resp.headers["content-range"].split("/")[-1])
    assert resp.headers["content-range"] == f"bytes 0-1023/{total}"
    assert resp.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_playback_range_beyond_filesize_returns_416(client, uploaded_demo):
    demo, user = uploaded_demo
    resp = await client.get(
        f"{API}/{demo['id']}/playback",
        headers={**_auth_headers(user), "Range": "bytes=99999999-"},
    )
    assert resp.status_code == 416
    assert resp.headers["content-range"].startswith("bytes */")
    assert resp.content == b""


# --- reassembly: bằng chứng tua video thật hoạt động --------------------------------


@pytest.mark.asyncio
async def test_reassembling_ranges_matches_original_file_byte_for_byte(client, uploaded_demo):
    """Test quan trọng nhất: tải file bằng nhiều range liên tiếp (khối 1024
    byte), ghép lại phải BẰNG ĐÚNG BYTE với file gốc trên đĩa."""
    demo, user = uploaded_demo

    original_path = storage.episode_dir(demo["id"]) / storage.FRONT_FILENAME
    original_bytes = original_path.read_bytes()
    total = len(original_bytes)

    chunk_size = 1024
    reassembled = bytearray()
    start = 0
    while start < total:
        end = min(start + chunk_size - 1, total - 1)
        resp = await client.get(
            f"{API}/{demo['id']}/playback",
            headers={**_auth_headers(user), "Range": f"bytes={start}-{end}"},
        )
        assert resp.status_code == 206
        reassembled.extend(resp.content)
        start = end + 1

    assert bytes(reassembled) == original_bytes


# --- HEAD ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_head_playback_returns_200_with_content_length_empty_body(client, uploaded_demo):
    demo, user = uploaded_demo
    resp = await client.request("HEAD", f"{API}/{demo['id']}/playback", headers=_auth_headers(user))
    assert resp.status_code == 200
    assert int(resp.headers["content-length"]) > 0
    assert resp.content == b""


# --- camera=wrist ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrist_camera_on_demo_without_wrist_returns_404(client, uploaded_demo):
    demo, user = uploaded_demo
    resp = await client.get(f"{API}/{demo['id']}/playback?camera=wrist", headers=_auth_headers(user))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_wrist_camera_on_demo_with_wrist_returns_200(client, db_session, storage_dir, sample_mp4_bytes):
    await _create_task(db_session, "stack")
    user = await _create_user(db_session, "player2")
    demo = await _upload_demo(client, user, sample_mp4_bytes, task_name="stack", with_wrist=True)

    resp = await client.get(f"{API}/{demo['id']}/playback?camera=wrist", headers=_auth_headers(user))
    assert resp.status_code == 200


# --- thumbnail -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_thumbnail_without_token_returns_401(client, uploaded_demo):
    demo, _user = uploaded_demo
    resp = await client.get(f"{API}/{demo['id']}/thumbnail")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_thumbnail_success_returns_jpeg(client, uploaded_demo):
    demo, user = uploaded_demo
    assert demo["has_thumbnail"] is True

    resp = await client.get(f"{API}/{demo['id']}/thumbnail", headers=_auth_headers(user))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert len(resp.content) > 0


@pytest.mark.asyncio
async def test_thumbnail_via_query_token(client, uploaded_demo):
    demo, user = uploaded_demo
    token = create_access_token(user.id, user.role)

    resp = await client.get(f"{API}/{demo['id']}/thumbnail?token={token}")
    assert resp.status_code == 200


# --- no file handle leak on Windows ----------------------------------------------


@pytest.mark.asyncio
async def test_episode_dir_deletable_after_streaming_no_handle_leak(client, uploaded_demo):
    """Sau khi stream xong, phải xoá được thư mục episode bằng shutil.rmtree
    mà không lỗi — chứng minh file handle không rò rỉ (Windows chặn xoá file
    đang mở, Linux thì không, nên lỗi này dễ lọt qua CI Linux)."""
    demo, user = uploaded_demo

    resp = await client.get(f"{API}/{demo['id']}/playback", headers=_auth_headers(user))
    assert resp.status_code == 200
    assert len(resp.content) > 0

    range_resp = await client.get(
        f"{API}/{demo['id']}/playback",
        headers={**_auth_headers(user), "Range": "bytes=0-1023"},
    )
    assert range_resp.status_code == 206

    episode_dir = storage.episode_dir(demo["id"])
    shutil.rmtree(episode_dir)  # ném exception nếu còn handle mở trên Windows
    assert not episode_dir.exists()
