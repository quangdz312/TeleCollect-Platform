import asyncio
import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.models.db import Dataset, DatasetEpisode, Episode, Task, User
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.services import storage
from src.services.security import create_access_token, hash_password

API = "/api/v1/datasets"


def test_dataset_source_defaults_to_both_and_rejects_unknown_value():
    from pydantic import ValidationError

    from src.models.schemas import DatasetCreateRequest

    assert DatasetCreateRequest(name="source-default").data_source == "both"
    assert DatasetCreateRequest(name="source-teleop", data_source="teleop").data_source == "teleop"
    with pytest.raises(ValidationError):
        DatasetCreateRequest(name="source-invalid", data_source="camera")
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


@pytest.mark.asyncio
async def test_explicit_episode_ids_export_only_selected_approved_demo(
    client, db_session, storage_dir, operator, reviewer
):
    await _create_task(db_session, "pick_place")
    selected = Episode(
        task_name="pick_place",
        operator_id=operator.id,
        reviewer_id=reviewer.id,
        reviewed_at=datetime.now(UTC),
        status=DemoStatus.APPROVED,
        outcome=DemoOutcome.SUCCESS,
        num_frames=12,
    )
    unselected = Episode(
        task_name="pick_place",
        operator_id=operator.id,
        reviewer_id=reviewer.id,
        reviewed_at=datetime.now(UTC),
        status=DemoStatus.APPROVED,
        outcome=DemoOutcome.SUCCESS,
        num_frames=8,
    )
    db_session.add_all([selected, unselected])
    await db_session.commit()
    await db_session.refresh(selected)
    await db_session.refresh(unselected)

    # Raw ZIP chỉ copy artifact; vài byte giả là đủ và giúp test không phụ thuộc ffmpeg.
    for episode in (selected, unselected):
        directory = storage.episode_dir(episode.id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / storage.FRONT_FILENAME).write_bytes(b"test-video")

    response = await client.post(
        API,
        json={"name": "manual-selection", "episode_ids": [selected.id]},
        headers=_auth_headers(reviewer),
    )

    assert response.status_code == 202, response.text
    dataset = await _wait_ready(client, _auth_headers(reviewer), response.json()["id"])
    assert dataset["status"] == "ready"
    linked_ids = set(
        await db_session.scalars(
            select(DatasetEpisode.episode_id).where(
                DatasetEpisode.dataset_id == response.json()["id"]
            )
        )
    )
    assert linked_ids == {selected.id}
    assert unselected.id not in linked_ids


@pytest.mark.asyncio
async def test_explicit_episode_ids_reject_unknown_or_ineligible_demo(
    client, db_session, storage_dir, reviewer
):
    response = await client.post(
        API,
        json={"name": "invalid-selection", "episode_ids": ["missing-episode"]},
        headers=_auth_headers(reviewer),
    )

    assert response.status_code == 422
    assert "không tồn tại hoặc không đủ điều kiện" in response.json()["detail"]


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
async def test_dataset_list_filters_and_detail_expose_provenance(client, db_session, reviewer):
    first = Dataset(
        name="lift-teleop-v1", task_names=["lift_cube"], include_failures=False,
        status="ready", data_source="teleop", created_by="reviewer1",
        collection_batch_id=None, exporter_version="1.0",
        episode_inventory=[{
            "episode_id": "episode-1", "source": "teleop", "task": "lift_cube",
            "outcome": "success", "frames": 42, "review_status": "approved",
        }],
        schema_manifest={"fields": [{"path": "actions", "shape": [42, 7], "dtype": "float32"}]},
    )
    second = Dataset(
        name="square-scripted-v1", task_names=["square"], include_failures=True,
        status="failed", data_source="scripted", created_by="reviewer1",
    )
    db_session.add_all([first, second])
    await db_session.commit()

    listed = await client.get(
        f"{API}?search=lift&status=ready&source=teleop",
        headers=_auth_headers(reviewer),
    )
    detail = await client.get(f"{API}/{first.id}", headers=_auth_headers(reviewer))

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["name"] == "lift-teleop-v1"
    assert detail.status_code == 200
    assert detail.json()["created_by"] == "reviewer1"
    assert detail.json()["episodes"][0]["episode_id"] == "episode-1"
    assert detail.json()["schema_manifest"]["fields"][0]["path"] == "actions"


@pytest.mark.asyncio
async def test_retry_rejects_non_failed_dataset(client, db_session, reviewer):
    dataset = Dataset(name="ready-dataset", task_names=[], status="ready")
    db_session.add(dataset)
    await db_session.commit()

    response = await client.post(
        f"{API}/{dataset.id}/retry", json={}, headers=_auth_headers(reviewer),
    )

    assert response.status_code == 409


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


# --- uploading a dataset built elsewhere -------------------------------------------


def _robomimic_bytes(tmp_path, *, demos: int = 2, frames: int = 30, action_dim: int = 7) -> bytes:
    import h5py
    import numpy as np

    path = tmp_path / "uploaded.hdf5"
    with h5py.File(path, "w") as handle:
        data = handle.create_group("data")
        data.attrs["env_args"] = json.dumps({"env_name": "Lift"})
        for index in range(demos):
            demo = data.create_group(f"demo_{index}")
            demo.attrs["telecollect_task"] = "lift"
            demo.create_dataset("actions", data=np.zeros((frames, action_dim)))
            observations = demo.create_group("obs")
            observations.create_dataset("robot0_eef_pos", data=np.zeros((frames, 3)))
    return path.read_bytes()


UPLOAD_API = f"{API}/uploads"


@pytest.mark.asyncio
async def test_dataset_upload_requires_reviewer(client, db_session, storage_dir, operator, tmp_path):
    response = await client.post(
        UPLOAD_API,
        headers=_auth_headers(operator),
        data={"name": "uploaded-v1"},
        files={"file": ("d.hdf5", _robomimic_bytes(tmp_path), "application/x-hdf5")},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_dataset_upload_is_ready_and_countable(client, db_session, storage_dir, reviewer, tmp_path):
    """No background build: the file already is the target format."""

    response = await client.post(
        UPLOAD_API,
        headers=_auth_headers(reviewer),
        data={"name": "uploaded-v1"},
        files={"file": ("d.hdf5", _robomimic_bytes(tmp_path), "application/x-hdf5")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ready"
    assert body["format"] == "robomimic"
    assert body["num_episodes"] == 2
    assert body["num_frames"] == 60
    assert body["task_names"] == ["lift"]
    assert body["exporter_version"] == "upload"

    stored = await db_session.get(Dataset, body["id"])
    await db_session.refresh(stored)
    assert Path(stored.zip_path).is_file()
    assert stored.schema_manifest["action_dim"] == 7


@pytest.mark.asyncio
async def test_uploaded_dataset_can_be_trained_on(client, db_session, storage_dir, reviewer, tmp_path):
    """The point of the upload: `_validated_dataset_path` must accept it."""

    from src.api.training import _validated_dataset_path

    response = await client.post(
        UPLOAD_API,
        headers=_auth_headers(reviewer),
        data={"name": "uploaded-trainable"},
        files={"file": ("d.hdf5", _robomimic_bytes(tmp_path), "application/x-hdf5")},
    )
    assert response.status_code == 201

    stored = await db_session.get(Dataset, response.json()["id"])
    await db_session.refresh(stored)
    assert _validated_dataset_path(stored).is_file()


@pytest.mark.asyncio
async def test_dataset_upload_rejects_a_file_that_is_not_hdf5(
    client, db_session, storage_dir, reviewer,
):
    response = await client.post(
        UPLOAD_API,
        headers=_auth_headers(reviewer),
        data={"name": "uploaded-garbage"},
        files={"file": ("d.hdf5", b"definitely not hdf5", "application/x-hdf5")},
    )

    assert response.status_code == 422
    assert "HDF5" in response.json()["detail"]


@pytest.mark.asyncio
async def test_dataset_upload_rejects_hdf5_without_demos(
    client, db_session, storage_dir, reviewer, tmp_path,
):
    import h5py

    path = tmp_path / "empty.hdf5"
    with h5py.File(path, "w") as handle:
        handle.create_group("data")

    response = await client.post(
        UPLOAD_API,
        headers=_auth_headers(reviewer),
        data={"name": "uploaded-empty"},
        files={"file": ("d.hdf5", path.read_bytes(), "application/x-hdf5")},
    )

    assert response.status_code == 422
    assert "demo_*" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_rejected_upload_leaves_no_file_behind(
    client, db_session, storage_dir, reviewer,
):
    before = set(storage.datasets_root().glob("*")) if storage.datasets_root().is_dir() else set()

    await client.post(
        UPLOAD_API,
        headers=_auth_headers(reviewer),
        data={"name": "uploaded-garbage"},
        files={"file": ("d.hdf5", b"definitely not hdf5", "application/x-hdf5")},
    )

    after = set(storage.datasets_root().glob("*")) if storage.datasets_root().is_dir() else set()
    assert after == before


@pytest.mark.asyncio
async def test_dataset_upload_name_clash_is_409_without_overwrite(
    client, db_session, storage_dir, reviewer, tmp_path,
):
    payload = _robomimic_bytes(tmp_path)
    first = await client.post(
        UPLOAD_API,
        headers=_auth_headers(reviewer),
        data={"name": "uploaded-twice"},
        files={"file": ("d.hdf5", payload, "application/x-hdf5")},
    )
    assert first.status_code == 201

    second = await client.post(
        UPLOAD_API,
        headers=_auth_headers(reviewer),
        data={"name": "uploaded-twice"},
        files={"file": ("d.hdf5", payload, "application/x-hdf5")},
    )

    assert second.status_code == 409
    # The first dataset's file must survive the refused second attempt.
    kept = await db_session.get(Dataset, first.json()["id"])
    await db_session.refresh(kept)
    assert Path(kept.zip_path).is_file()


# --- LeRobot -----------------------------------------------------------------


def _lerobot_teleop(directory: Path, *, control_hz: int = 10, frames: int = 5) -> None:
    """A schema-v2 teleop recording with the video LeRobot needs."""

    import subprocess

    import imageio_ffmpeg
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    directory.mkdir(parents=True, exist_ok=True)
    qpos = np.arange(frames * 9, dtype=np.float64).reshape(frames, 9) / 100.0
    ee_pose = np.zeros((frames, 7), dtype=np.float64)
    ee_pose[:, :3] = np.arange(frames * 3).reshape(frames, 3) / 10.0
    ee_pose[:, 3] = 1.0
    pq.write_table(
        pa.table({
            "t": [index / control_hz for index in range(frames)],
            "qpos": qpos.tolist(),
            "qvel": (qpos + 0.5).tolist(),
            "ee_pose": ee_pose.tolist(),
            "privileged_state": np.arange(frames * 12, dtype=np.float64).reshape(frames, 12).tolist(),
            "object": (np.arange(frames * 10, dtype=np.float64).reshape(frames, 10) / 100.0).tolist(),
            "action": (np.arange(frames * 7, dtype=np.float64).reshape(frames, 7) / 10.0).tolist(),
        }),
        directory / storage.ACTIONS_FILENAME,
    )
    (directory / storage.META_FILENAME).write_text(
        json.dumps({
            "episode_id": directory.name,
            "task_name": "lift_cube",
            "operator_id": "operator-1",
            "num_steps": frames,
            "control_hz": control_hz,
            "teleop_schema_version": 2,
            "task_success": True,
        }),
        encoding="utf-8",
    )
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "lavfi",
            "-i", "testsrc=duration=1:size=64x64:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(directory / storage.FRONT_FILENAME),
        ],
        capture_output=True, check=True,
    )


@pytest.mark.asyncio
async def test_lerobot_export_builds_a_directory_dataset(
    client, db_session, storage_dir, operator, reviewer
):
    """The whole point of the format: a LeRobot v3 tree, not a zip of videos."""

    await _create_task(db_session, "lift_cube")
    episode = Episode(
        task_name="lift_cube",
        operator_id=operator.id,
        reviewer_id=reviewer.id,
        reviewed_at=datetime.now(UTC),
        status=DemoStatus.APPROVED,
        outcome=DemoOutcome.SUCCESS,
        num_frames=5,
    )
    db_session.add(episode)
    await db_session.commit()
    await db_session.refresh(episode)
    _lerobot_teleop(storage.episode_dir(episode.id))

    response = await client.post(
        API,
        json={
            "name": "lerobot-v1",
            "format": "lerobot",
            "task_names": ["lift_cube"],
            "data_source": "teleop",
        },
        headers=_auth_headers(reviewer),
    )

    assert response.status_code == 202, response.text
    dataset = await _wait_ready(client, _auth_headers(reviewer), response.json()["id"])
    assert dataset["status"] == "ready", dataset.get("error_message")
    assert dataset["format"] == "lerobot"

    stored = await db_session.get(Dataset, response.json()["id"])
    await db_session.refresh(stored)
    root = Path(stored.zip_path)
    assert root.is_dir()
    info = json.loads((root / "meta" / "info.json").read_text(encoding="utf-8"))
    assert info["codebase_version"] == "v3.0"
    assert info["total_episodes"] == 1


@pytest.mark.asyncio
async def test_a_lerobot_dataset_downloads_as_one_zip(
    client, db_session, storage_dir, operator, reviewer
):
    """A directory cannot be streamed as a file, so download packs it."""

    await _create_task(db_session, "lift_cube")
    episode = Episode(
        task_name="lift_cube",
        operator_id=operator.id,
        reviewer_id=reviewer.id,
        reviewed_at=datetime.now(UTC),
        status=DemoStatus.APPROVED,
        outcome=DemoOutcome.SUCCESS,
        num_frames=5,
    )
    db_session.add(episode)
    await db_session.commit()
    await db_session.refresh(episode)
    _lerobot_teleop(storage.episode_dir(episode.id))

    created = await client.post(
        API,
        json={
            "name": "lerobot-download",
            "format": "lerobot",
            "task_names": ["lift_cube"],
            "data_source": "teleop",
        },
        headers=_auth_headers(reviewer),
    )
    dataset_id = created.json()["id"]
    await _wait_ready(client, _auth_headers(reviewer), dataset_id)

    response = await client.get(f"{API}/{dataset_id}/download", headers=_auth_headers(reviewer))

    assert response.status_code == 200, response.text
    assert "lerobot.zip" in response.headers["content-disposition"]


@pytest.mark.asyncio
async def test_deleting_a_lerobot_dataset_removes_the_whole_tree(
    client, db_session, storage_dir, operator, reviewer
):
    """`unlink` raises on a directory, which would silently orphan gigabytes."""

    await _create_task(db_session, "lift_cube")
    episode = Episode(
        task_name="lift_cube",
        operator_id=operator.id,
        reviewer_id=reviewer.id,
        reviewed_at=datetime.now(UTC),
        status=DemoStatus.APPROVED,
        outcome=DemoOutcome.SUCCESS,
        num_frames=5,
    )
    db_session.add(episode)
    await db_session.commit()
    await db_session.refresh(episode)
    _lerobot_teleop(storage.episode_dir(episode.id))

    created = await client.post(
        API,
        json={
            "name": "lerobot-delete",
            "format": "lerobot",
            "task_names": ["lift_cube"],
            "data_source": "teleop",
        },
        headers=_auth_headers(reviewer),
    )
    dataset_id = created.json()["id"]
    await _wait_ready(client, _auth_headers(reviewer), dataset_id)
    root = Path((await db_session.get(Dataset, dataset_id)).zip_path)
    assert root.is_dir()

    response = await client.delete(f"{API}/{dataset_id}", headers=_auth_headers(reviewer))

    assert response.status_code == 204
    assert not root.exists()
