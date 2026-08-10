import asyncio
import hashlib
import json
import zipfile
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from src.models.db import Dataset, Episode, Task, User
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.services import storage
from src.services.security import create_access_token, hash_password

API = "/api/v1/datasets"
DEMOS_API = "/api/v1/demos"


# --- helpers -----------------------------------------------------------------------


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


async def _upload_demo(client, user: User, sample_mp4_bytes, task_name: str, with_wrist: bool = False) -> dict:
    files = {"front": ("front.mp4", sample_mp4_bytes, "video/mp4")}
    if with_wrist:
        files["wrist"] = ("wrist.mp4", sample_mp4_bytes, "video/mp4")
    resp = await client.post(
        f"{DEMOS_API}/upload",
        data={"task_name": task_name},
        files=files,
        headers=_auth_headers(user),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_approved_demo(
    client,
    db_session,
    operator: User,
    reviewer: User,
    sample_mp4_bytes,
    task_name: str,
    outcome: DemoOutcome = DemoOutcome.SUCCESS,
    with_wrist: bool = False,
) -> dict:
    """Upload rồi duyệt thẳng qua DB (bỏ qua flow label/review từng bước —
    đã test riêng ở `test_demo_rules.py`/`test_demos_review.py`)."""
    demo = await _upload_demo(client, operator, sample_mp4_bytes, task_name, with_wrist=with_wrist)
    episode = await db_session.get(Episode, demo["id"])
    episode.status = DemoStatus.APPROVED
    episode.outcome = outcome
    episode.reviewer_id = reviewer.id
    episode.reviewed_at = datetime.now(UTC)
    await db_session.commit()
    await db_session.refresh(episode)
    return demo


@pytest_asyncio.fixture
async def reviewer(db_session) -> User:
    return await _create_user(db_session, "reviewer1", role=UserRole.REVIEWER)


@pytest_asyncio.fixture
async def operator(db_session) -> User:
    return await _create_user(db_session, "operator1", role=UserRole.OPERATOR)


async def _wait_ready(client, headers, dataset_id: str, timeout: float = 5.0) -> dict:
    """BackgroundTasks của Starlette chạy trong CÙNG lời gọi ASGI khi test dùng
    `ASGITransport` nên thường ĐÃ xong khi `POST` trả về — nhưng poll thêm cho
    chắc, không phụ thuộc chi tiết implementation đó."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        resp = await client.get(f"{API}/{dataset_id}", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] != "building":
            return data
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"Dataset {dataset_id} vẫn 'building' sau {timeout}s")
        await asyncio.sleep(0.05)


# --- name validation -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_dataset_invalid_name_with_space_returns_422(client, db_session, storage_dir, reviewer):
    resp = await client.post(API, json={"name": "My Dataset"}, headers=_auth_headers(reviewer))
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_dataset_path_traversal_name_returns_422(client, db_session, storage_dir, reviewer):
    resp = await client.post(API, json={"name": "../evil"}, headers=_auth_headers(reviewer))
    assert resp.status_code == 422


# --- role check ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_dataset_as_operator_returns_403(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session)
    await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    resp = await client.post(API, json={"name": "ds1"}, headers=_auth_headers(operator))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_dataset_as_reviewer_returns_202(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session)
    await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    resp = await client.post(API, json={"name": "ds1"}, headers=_auth_headers(reviewer))
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "building"


# --- no matching episodes ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_dataset_no_approved_demos_returns_422(client, db_session, storage_dir, reviewer):
    await _create_task(db_session)
    resp = await client.post(API, json={"name": "empty-ds"}, headers=_auth_headers(reviewer))
    assert resp.status_code == 422


# --- selection correctness --------------------------------------------------------------


@pytest.mark.asyncio
async def test_selection_only_approved_and_excludes_failure_by_default(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session, "pick_place")

    approved_success = await _make_approved_demo(
        client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place", outcome=DemoOutcome.SUCCESS
    )
    approved_failure = await _make_approved_demo(
        client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place", outcome=DemoOutcome.FAILURE
    )
    # Demo chưa duyệt (chỉ recorded) — không được chọn dù cùng task.
    await _upload_demo(client, operator, sample_mp4_bytes, "pick_place")

    resp = await client.post(API, json={"name": "success-only"}, headers=_auth_headers(reviewer))
    assert resp.status_code == 202
    dataset = await _wait_ready(client, _auth_headers(reviewer), resp.json()["id"])

    assert dataset["status"] == "ready"
    assert dataset["num_episodes"] == 1
    episode_ids = {e["id"] for e in dataset["episodes"]}
    assert episode_ids == {approved_success["id"]}
    assert approved_failure["id"] not in episode_ids


@pytest.mark.asyncio
async def test_selection_include_failures_true_includes_failure_outcome(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session, "pick_place")
    success_demo = await _make_approved_demo(
        client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place", outcome=DemoOutcome.SUCCESS
    )
    failure_demo = await _make_approved_demo(
        client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place", outcome=DemoOutcome.FAILURE
    )

    resp = await client.post(
        API, json={"name": "with-failures", "include_failures": True}, headers=_auth_headers(reviewer)
    )
    assert resp.status_code == 202
    dataset = await _wait_ready(client, _auth_headers(reviewer), resp.json()["id"])

    episode_ids = {e["id"] for e in dataset["episodes"]}
    assert episode_ids == {success_demo["id"], failure_demo["id"]}


@pytest.mark.asyncio
async def test_selection_task_names_filters_correctly(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session, "pick_place")
    await _create_task(db_session, "stack")

    pick_demo = await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")
    await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "stack")

    resp = await client.post(
        API, json={"name": "pick-only", "task_names": ["pick_place"]}, headers=_auth_headers(reviewer)
    )
    assert resp.status_code == 202
    dataset = await _wait_ready(client, _auth_headers(reviewer), resp.json()["id"])

    episode_ids = {e["id"] for e in dataset["episodes"]}
    assert episode_ids == {pick_demo["id"]}


# --- duplicate name / overwrite ----------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_name_without_overwrite_returns_409(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session)
    await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    resp1 = await client.post(API, json={"name": "dup-ds"}, headers=_auth_headers(reviewer))
    assert resp1.status_code == 202
    await _wait_ready(client, _auth_headers(reviewer), resp1.json()["id"])

    resp2 = await client.post(API, json={"name": "dup-ds"}, headers=_auth_headers(reviewer))
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_duplicate_name_with_overwrite_replaces_dataset(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session, "pick_place")
    demo1 = await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    resp1 = await client.post(API, json={"name": "dup-ds"}, headers=_auth_headers(reviewer))
    assert resp1.status_code == 202
    first = await _wait_ready(client, _auth_headers(reviewer), resp1.json()["id"])
    assert first["num_episodes"] == 1

    demo2 = await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    resp2 = await client.post(
        API, json={"name": "dup-ds", "overwrite": True}, headers=_auth_headers(reviewer)
    )
    assert resp2.status_code == 202, resp2.text
    second = await _wait_ready(client, _auth_headers(reviewer), resp2.json()["id"])

    assert second["id"] != first["id"]
    assert second["num_episodes"] == 2
    episode_ids = {e["id"] for e in second["episodes"]}
    assert episode_ids == {demo1["id"], demo2["id"]}

    # Dataset cũ không còn tồn tại (đã bị xoá record).
    old_resp = await client.get(f"{API}/{first['id']}", headers=_auth_headers(reviewer))
    assert old_resp.status_code == 404


# --- zip content: test giá trị nhất --------------------------------------------------------


@pytest.mark.asyncio
async def test_built_zip_structure_and_sha256_match(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session, "pick_place")
    demo = await _make_approved_demo(
        client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place", with_wrist=True
    )

    resp = await client.post(API, json={"name": "zip-check"}, headers=_auth_headers(reviewer))
    assert resp.status_code == 202
    dataset = await _wait_ready(client, _auth_headers(reviewer), resp.json()["id"])
    assert dataset["status"] == "ready"
    assert dataset["num_episodes"] == 1

    zip_path = storage.dataset_zip_path(dataset["id"])
    assert zip_path.exists()

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        assert "zip-check/meta.json" in names
        assert f"zip-check/episodes/{demo['id']}/front.mp4" in names
        assert f"zip-check/episodes/{demo['id']}/wrist.mp4" in names
        assert f"zip-check/episodes/{demo['id']}/meta.json" in names

        dataset_meta = json.loads(zf.read("zip-check/meta.json"))
        assert dataset_meta["name"] == "zip-check"
        assert dataset_meta["num_episodes"] == 1
        assert dataset_meta["episodes"][0]["episode_id"] == demo["id"]

        episode_meta = json.loads(zf.read(f"zip-check/episodes/{demo['id']}/meta.json"))
        assert episode_meta["task_name"] == "pick_place"
        assert episode_meta["outcome"] == "success"

        recorded_hashes = dataset_meta["episodes"][0]["files"]
        for filename in ("front.mp4", "wrist.mp4"):
            actual_bytes = zf.read(f"zip-check/episodes/{demo['id']}/{filename}")
            actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
            assert recorded_hashes[filename] == actual_sha256


# --- download ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_while_building_returns_409(client, db_session, storage_dir, reviewer):
    """Tạo thẳng row Dataset ở trạng thái building (không qua endpoint) để
    kiểm soát chính xác thời điểm — tránh phụ thuộc tốc độ background task."""
    dataset = Dataset(name="still-building", task_names=[], include_failures=False, status="building")
    db_session.add(dataset)
    await db_session.commit()
    await db_session.refresh(dataset)

    resp = await client.get(f"{API}/{dataset.id}/download", headers=_auth_headers(reviewer))
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_download_supports_range_request(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session, "pick_place")
    await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    resp = await client.post(API, json={"name": "range-ds"}, headers=_auth_headers(reviewer))
    dataset = await _wait_ready(client, _auth_headers(reviewer), resp.json()["id"])

    full_resp = await client.get(f"{API}/{dataset['id']}/download", headers=_auth_headers(reviewer))
    assert full_resp.status_code == 200
    assert full_resp.headers["content-type"] == "application/zip"
    assert "range-ds.zip" in full_resp.headers["content-disposition"]

    range_resp = await client.get(
        f"{API}/{dataset['id']}/download",
        headers={**_auth_headers(reviewer), "Range": "bytes=0-1023"},
    )
    assert range_resp.status_code == 206
    assert len(range_resp.content) == 1024
    assert range_resp.content == full_resp.content[:1024]


@pytest.mark.asyncio
async def test_download_via_query_token(client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer):
    await _create_task(db_session, "pick_place")
    await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    resp = await client.post(API, json={"name": "token-ds"}, headers=_auth_headers(reviewer))
    dataset = await _wait_ready(client, _auth_headers(reviewer), resp.json()["id"])

    token = create_access_token(reviewer.id, reviewer.role)
    resp2 = await client.get(f"{API}/{dataset['id']}/download?token={token}")
    assert resp2.status_code == 200


# --- demo deleted after dataset ready — zip đã copy, không đổi ---------------------------


@pytest.mark.asyncio
async def test_deleting_source_demo_after_ready_does_not_change_zip(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session, "pick_place")
    demo = await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    resp = await client.post(API, json={"name": "snapshot-ds"}, headers=_auth_headers(reviewer))
    dataset = await _wait_ready(client, _auth_headers(reviewer), resp.json()["id"])

    before_resp = await client.get(f"{API}/{dataset['id']}/download", headers=_auth_headers(reviewer))
    assert before_resp.status_code == 200
    before_bytes = before_resp.content

    del_resp = await client.delete(f"{DEMOS_API}/{demo['id']}", headers=_auth_headers(reviewer))
    assert del_resp.status_code == 204

    after_resp = await client.get(f"{API}/{dataset['id']}/download", headers=_auth_headers(reviewer))
    assert after_resp.status_code == 200
    assert after_resp.content == before_bytes


# --- missing source file before build -----------------------------------------------------


@pytest.mark.asyncio
async def test_missing_source_file_before_build_skips_episode_with_warning_still_ready(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session, "pick_place")
    good_demo = await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")
    bad_demo = await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    # Xoá tay file front.mp4 của bad_demo TRƯỚC khi build — mô phỏng file nguồn mất.
    bad_front = storage.episode_dir(bad_demo["id"]) / storage.FRONT_FILENAME
    bad_front.unlink()

    resp = await client.post(API, json={"name": "partial-ds"}, headers=_auth_headers(reviewer))
    assert resp.status_code == 202
    dataset = await _wait_ready(client, _auth_headers(reviewer), resp.json()["id"])

    assert dataset["status"] == "ready"
    assert dataset["num_episodes"] == 1
    episode_ids = {e["id"] for e in dataset["episodes"]}
    assert episode_ids == {good_demo["id"], bad_demo["id"]}  # dataset_episodes vẫn ghi cả 2 (snapshot lựa chọn)

    zip_path = storage.dataset_zip_path(dataset["id"])
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert any(f"episodes/{good_demo['id']}/front.mp4" in n for n in names)
        assert not any(f"episodes/{bad_demo['id']}/" in n for n in names)

        dataset_meta = json.loads(zf.read("partial-ds/meta.json"))
        assert dataset_meta["num_episodes"] == 1
        assert any(bad_demo["id"] in w for w in dataset_meta["warnings"])


# --- delete dataset --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_dataset_removes_zip_file(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session, "pick_place")
    await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    resp = await client.post(API, json={"name": "to-delete"}, headers=_auth_headers(reviewer))
    dataset = await _wait_ready(client, _auth_headers(reviewer), resp.json()["id"])

    zip_path = storage.dataset_zip_path(dataset["id"])
    assert zip_path.exists()

    del_resp = await client.delete(f"{API}/{dataset['id']}", headers=_auth_headers(reviewer))
    assert del_resp.status_code == 204
    assert not zip_path.exists()

    get_resp = await client.get(f"{API}/{dataset['id']}", headers=_auth_headers(reviewer))
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_dataset_as_operator_returns_403(
    client, db_session, storage_dir, sample_mp4_bytes, operator, reviewer
):
    await _create_task(db_session, "pick_place")
    await _make_approved_demo(client, db_session, operator, reviewer, sample_mp4_bytes, "pick_place")

    resp = await client.post(API, json={"name": "protected"}, headers=_auth_headers(reviewer))
    dataset = await _wait_ready(client, _auth_headers(reviewer), resp.json()["id"])

    del_resp = await client.delete(f"{API}/{dataset['id']}", headers=_auth_headers(operator))
    assert del_resp.status_code == 403
