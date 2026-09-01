from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import get_settings
from src.models.db import Episode, Task, User
from src.models.enums import DemoOutcome, DemoStatus, UserRole
from src.services import storage
from src.services.security import create_access_token, hash_password

API = "/api/v1/raw/episodes"


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


async def _create_user(db_session, username: str, role: UserRole) -> User:
    user = User(
        username=username,
        password_hash=hash_password("correctpass"),
        display_name=username,
        role=role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def _create_teleop(db_session, operator: User, *, task_name: str = "lift") -> Episode:
    if await db_session.get(Task, task_name) is None:
        db_session.add(
            Task(
                name=task_name,
                description="d",
                instruction="i",
                hints=[],
                action_dim=7,
                max_steps=200,
            )
        )
        await db_session.flush()
    episode = Episode(
        task_name=task_name,
        operator_id=operator.id,
        status=DemoStatus.APPROVED,
        outcome=DemoOutcome.SUCCESS,
        num_frames=60,
        duration_s=2.0,
        size_bytes=1234,
    )
    db_session.add(episode)
    await db_session.commit()
    await db_session.refresh(episode)
    return episode


def _write_scripted_workspace(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    scores = [
        {
            "episode_id": "lift_clean_seed0.hdf5::demo_0",
            "source": "lift_clean_seed0.hdf5",
            "display_name": "lift_001",
            "demo": "demo_0",
            "task": "lift",
            "length": 90,
            "requested_quality": "clean",
            "recorded_success": True,
            "provenance": {"collection_batch_id": "batch-1", "control_hz": 30},
        },
        {
            "episode_id": "can_poor_seed0.hdf5::demo_0",
            "source": "can_poor_seed0.hdf5",
            "display_name": "can_001",
            "demo": "demo_0",
            "task": "can",
            "length": 120,
            "requested_quality": "poor",
            "recorded_success": False,
            "provenance": {},
        },
    ]
    labels = [
        {
            "episode_id": "lift_clean_seed0.hdf5::demo_0",
            "human_decision": "approved",
        }
    ]
    (root / "scores.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in scores), encoding="utf-8"
    )
    (root / "labels.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in labels), encoding="utf-8"
    )
    datasets = root / "datasets"
    datasets.mkdir()
    import h5py
    import numpy as np

    # A complete demo, not a stub: importing a batch rescores the whole corpus,
    # so every file here has to be loadable by `src.labeling.features`.
    with h5py.File(datasets / "lift_clean_seed0.hdf5", "w") as handle:
        demo = handle.create_group("data").create_group("demo_0")
        demo.attrs["telecollect_task"] = "lift"
        demo.attrs["telecollect_requested_quality"] = "clean"
        demo.attrs["num_samples"] = 90
        demo.attrs["success"] = True
        demo.create_dataset("actions", data=np.arange(630, dtype=float).reshape(90, 7) / 100)
        demo.create_dataset("rewards", data=np.linspace(0, 1, 90))
        demo.create_dataset("dones", data=np.zeros(90, dtype=np.int64))
        demo.create_dataset("states", data=np.zeros((90, 12)))
        for group_name in ("obs", "next_obs"):
            obs = demo.create_group(group_name)
            obs.create_dataset("robot0_eef_pos", data=np.zeros((90, 3)))
            obs.create_dataset("robot0_eef_quat", data=np.zeros((90, 4)))
            obs.create_dataset("robot0_joint_pos", data=np.zeros((90, 7)))
            obs.create_dataset("robot0_joint_vel", data=np.zeros((90, 7)))
            obs.create_dataset("robot0_gripper_qpos", data=np.zeros((90, 2)))
            obs.create_dataset("robot0_gripper_qvel", data=np.zeros((90, 2)))
            obs.create_dataset("object", data=np.zeros((90, 14)))
    videos = root / "videos"
    videos.mkdir()
    (videos / "lift_clean_seed0__demo_0.mp4").write_bytes(b"scripted-video")


@pytest.fixture
def raw_workspace(tmp_path: Path, monkeypatch):
    settings = get_settings()
    review_dir = tmp_path / "review"
    storage_dir = tmp_path / "storage"
    monkeypatch.setattr(settings, "review_dir", str(review_dir))
    monkeypatch.setattr(settings, "storage_dir", str(storage_dir))
    _write_scripted_workspace(review_dir)
    return review_dir


@pytest.mark.asyncio
async def test_raw_list_requires_reviewer(client, db_session, raw_workspace):
    operator = await _create_user(db_session, "raw_operator", UserRole.OPERATOR)

    response = await client.get(API, headers=_auth_headers(operator))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_raw_list_combines_teleop_and_scripted(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "raw_reviewer", UserRole.REVIEWER)
    teleop = await _create_teleop(db_session, reviewer)
    directory = storage.episode_dir(teleop.id)
    directory.mkdir(parents=True)
    (directory / storage.FRONT_FILENAME).write_bytes(b"front")
    (directory / "birdview.mp4").write_bytes(b"bird")
    (directory / "robot0_eye_in_hand.mp4").write_bytes(b"wrist")
    storage.meta_path(teleop.id).write_text(
        json.dumps({"control_hz": 30, "cameras": ["review_front", "birdview", "robot0_eye_in_hand"]}),
        encoding="utf-8",
    )

    response = await client.get(API, headers=_auth_headers(reviewer))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    by_id = {item["episode_id"]: item for item in body["items"]}
    assert by_id[teleop.id]["source"] == "teleop"
    assert by_id[teleop.id]["cameras"] == {"front": True, "birdview": True, "wrist": True}
    assert by_id["lift_clean_seed0.hdf5::demo_0"]["source"] == "scripted"
    assert by_id["lift_clean_seed0.hdf5::demo_0"]["quality"] == "clean"
    summary = body["summary"]
    by_day = summary.pop("by_day")
    assert summary == {
        "total": 3,
        "teleop": 1,
        "scripted": 2,
        "successes": 2,
        "failures": 1,
        "pending": 1,
        "approved": 2,
        "rejected": 0,
        "archived": 0,
        "by_task": {"can": 1, "lift_cube": 2},
        "by_quality": {"clean": 1, "good": 0, "medium": 0, "poor": 1},
        "by_batch": {"batch-1": 1},
        "undated": 1,
    }
    assert sum(day["teleop"] for day in by_day.values()) == 1
    assert sum(day["scripted"] for day in by_day.values()) == 1
    assert body["available_tasks"] == ["can", "lift_cube"]
    assert body["available_batches"] == ["batch-1"]
    assert by_id["lift_clean_seed0.hdf5::demo_0"]["task"] == "lift_cube"


@pytest.mark.asyncio
async def test_raw_task_aliases_filter_the_same_canonical_task(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "raw_task_alias_reviewer", UserRole.REVIEWER)
    teleop = await _create_teleop(db_session, reviewer, task_name="lift_cube")

    legacy = await client.get(f"{API}?task=lift", headers=_auth_headers(reviewer))
    canonical = await client.get(f"{API}?task=lift_cube", headers=_auth_headers(reviewer))

    assert legacy.status_code == 200
    assert canonical.status_code == 200
    assert {item["episode_id"] for item in legacy.json()["items"]} == {
        teleop.id,
        "lift_clean_seed0.hdf5::demo_0",
    }
    assert {item["episode_id"] for item in canonical.json()["items"]} == {
        teleop.id,
        "lift_clean_seed0.hdf5::demo_0",
    }


@pytest.mark.asyncio
async def test_raw_list_filters_and_paginates(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "raw_filter_reviewer", UserRole.REVIEWER)
    await _create_teleop(db_session, reviewer)

    filtered = await client.get(
        f"{API}?source=scripted&outcome=failure&quality=poor&review_status=pending",
        headers=_auth_headers(reviewer),
    )
    page = await client.get(
        f"{API}?source=scripted&page=2&page_size=1",
        headers=_auth_headers(reviewer),
    )
    batch = await client.get(
        f"{API}?collection_batch_id=batch-1",
        headers=_auth_headers(reviewer),
    )

    assert filtered.status_code == 200
    assert [item["episode_id"] for item in filtered.json()["items"]] == [
        "can_poor_seed0.hdf5::demo_0"
    ]
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert page.json()["total_pages"] == 2
    assert len(page.json()["items"]) == 1
    assert batch.status_code == 200
    assert [item["episode_id"] for item in batch.json()["items"]] == [
        "lift_clean_seed0.hdf5::demo_0"
    ]


@pytest.mark.asyncio
async def test_raw_detail_supports_scripted_episode_ids(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "raw_detail_reviewer", UserRole.REVIEWER)

    response = await client.get(
        f"{API}/lift_clean_seed0.hdf5::demo_0", headers=_auth_headers(reviewer)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["episode_id"] == "lift_clean_seed0.hdf5::demo_0"
    assert body["review_status"] == "approved"
    assert body["duration_s"] == 3.0
    assert body["control_hz"] == 30.0
    assert body["artifacts"][0]["name"] == "lift_clean_seed0.hdf5"
    assert body["artifacts"][0]["kind"] == "dataset"
    assert body["artifacts"][0]["exists"] is True
    assert body["artifacts"][0]["size_bytes"] > 0


@pytest.mark.asyncio
async def test_raw_detail_reports_teleop_artifacts(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "raw_artifact_reviewer", UserRole.REVIEWER)
    teleop = await _create_teleop(db_session, reviewer)
    directory = storage.episode_dir(teleop.id)
    directory.mkdir(parents=True)
    storage.meta_path(teleop.id).write_text("{}", encoding="utf-8")
    storage.actions_path(teleop.id).write_bytes(b"parquet")
    (directory / "review_front.mp4").write_bytes(b"front")
    (directory / "birdview.mp4").write_bytes(b"bird")
    (directory / "robot0_eye_in_hand.mp4").write_bytes(b"wrist")

    response = await client.get(f"{API}/{teleop.id}", headers=_auth_headers(reviewer))

    assert response.status_code == 200
    artifacts = {item["name"]: item for item in response.json()["artifacts"]}
    assert artifacts["meta.json"]["exists"] is True
    assert artifacts["actions.parquet"]["kind"] == "table"
    assert artifacts["review_front.mp4"]["camera"] == "front"
    assert artifacts["birdview.mp4"]["camera"] == "birdview"
    assert artifacts["robot0_eye_in_hand.mp4"]["camera"] == "wrist"


@pytest.mark.asyncio
async def test_raw_teleop_video_supports_query_token_and_range(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "raw_video_reviewer", UserRole.REVIEWER)
    teleop = await _create_teleop(db_session, reviewer)
    directory = storage.episode_dir(teleop.id)
    directory.mkdir(parents=True)
    payload = b"0123456789abcdef"
    (directory / "review_front.mp4").write_bytes(payload)
    token = create_access_token(reviewer.id, reviewer.role)

    response = await client.get(
        f"{API}/{teleop.id}/video/front?token={token}",
        headers={"Range": "bytes=2-7"},
    )

    assert response.status_code == 206
    assert response.content == payload[2:8]
    assert response.headers["accept-ranges"] == "bytes"


@pytest.mark.asyncio
async def test_raw_scripted_video_uses_composite_stream(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "raw_scripted_video", UserRole.REVIEWER)

    response = await client.get(
        f"{API}/lift_clean_seed0.hdf5::demo_0/video/composite",
        headers=_auth_headers(reviewer),
    )
    wrong_camera = await client.get(
        f"{API}/lift_clean_seed0.hdf5::demo_0/video/front",
        headers=_auth_headers(reviewer),
    )

    assert response.status_code == 200
    assert response.content == b"scripted-video"
    assert wrong_camera.status_code == 404


@pytest.mark.asyncio
async def test_prepare_scripted_video_returns_ready_when_cached(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "raw_prepare_video", UserRole.REVIEWER)

    response = await client.post(
        f"{API}/lift_clean_seed0.hdf5::demo_0/video/prepare",
        headers=_auth_headers(reviewer),
    )

    assert response.status_code == 202
    assert response.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_raw_teleop_signals_downsample_and_preserve_endpoints(client, db_session, raw_workspace):
    import pyarrow as pa
    import pyarrow.parquet as pq

    reviewer = await _create_user(db_session, "raw_teleop_signals", UserRole.REVIEWER)
    teleop = await _create_teleop(db_session, reviewer)
    directory = storage.episode_dir(teleop.id)
    directory.mkdir(parents=True)
    pq.write_table(
        pa.table(
            {
                "t": [index / 30 for index in range(10)],
                "action": [[float(index)] * 7 for index in range(10)],
                "ee_pose": [[float(index)] * 7 for index in range(10)],
                "qpos": [[float(index)] * 9 for index in range(10)],
                "qvel": [[0.0] * 9 for _ in range(10)],
                "privileged_state": [[0.0] * 3 for _ in range(10)],
            }
        ),
        storage.actions_path(teleop.id),
    )

    response = await client.get(
        f"{API}/{teleop.id}/signals?fields=action,ee_pose&max_points=4",
        headers=_auth_headers(reviewer),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sampled_frames"] == [0, 3, 6, 9]
    assert body["signals"]["action"]["labels"][-1] == "gripper"
    assert body["signals"]["action"]["values"][-1] == [9.0] * 7
    assert body["t"][-1] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_raw_scripted_signals_map_hdf5_observations(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "raw_scripted_signals", UserRole.REVIEWER)

    response = await client.get(
        f"{API}/lift_clean_seed0.hdf5::demo_0/signals?fields=action,ee_pose,qpos,reward&max_points=5",
        headers=_auth_headers(reviewer),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "scripted"
    assert body["control_hz"] == 20.0
    assert body["sampled_frames"] == [0, 22, 44, 67, 89]
    assert len(body["signals"]["ee_pose"]["labels"]) == 7
    assert len(body["signals"]["qpos"]["labels"]) == 9
    assert body["signals"]["reward"]["values"][-1] == [1.0]


@pytest.mark.asyncio
async def test_raw_archive_requires_admin_and_preserves_source_files(
    client, db_session, raw_workspace,
):
    reviewer = await _create_user(db_session, "raw_archive_reviewer", UserRole.REVIEWER)
    admin = await _create_user(db_session, "raw_archive_admin", UserRole.ADMIN)
    teleop = await _create_teleop(db_session, reviewer)
    directory = storage.episode_dir(teleop.id)
    directory.mkdir(parents=True)
    raw_path = directory / "actions.parquet"
    raw_path.write_bytes(b"immutable-raw")

    forbidden = await client.post(
        f"{API}/{teleop.id}/archive",
        headers=_auth_headers(reviewer),
        json={"archived": True, "expected_version": 0},
    )
    archived = await client.post(
        f"{API}/{teleop.id}/archive",
        headers=_auth_headers(admin),
        json={"archived": True, "expected_version": 0},
    )
    restored = await client.post(
        f"{API}/{teleop.id}/archive",
        headers=_auth_headers(admin),
        json={"archived": False, "expected_version": 1},
    )

    assert forbidden.status_code == 403
    assert archived.status_code == 200
    assert archived.json()["review_status"] == "archived"
    assert restored.status_code == 200
    assert restored.json()["review_status"] == "approved"
    assert raw_path.read_bytes() == b"immutable-raw"


@pytest.mark.asyncio
async def test_raw_mutations_reject_operator_role(client, db_session, raw_workspace):
    operator = await _create_user(db_session, "raw_mutation_operator", UserRole.OPERATOR)
    episode = await _create_teleop(db_session, operator)
    headers = _auth_headers(operator)

    archive = await client.post(
        f"{API}/{episode.id}/archive",
        headers=headers,
        json={"archived": True, "expected_version": 0},
    )

    assert archive.status_code == 403


@pytest.mark.asyncio
async def test_raw_detail_returns_404(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "raw_missing_reviewer", UserRole.REVIEWER)

    response = await client.get(f"{API}/missing", headers=_auth_headers(reviewer))

    assert response.status_code == 404


# --- Đợt thu ----------------------------------------------------------------

BATCHES = "/api/v1/raw/batches"


@pytest.mark.asyncio
async def test_batches_require_reviewer(client, db_session, raw_workspace):
    operator = await _create_user(db_session, "batch_operator", UserRole.OPERATOR)

    response = await client.get(BATCHES, headers=_auth_headers(operator))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_batch_seen_only_in_provenance_still_shows_up(
    client, db_session, raw_workspace
):
    """Đợt thu cũ chưa ai đặt tên vẫn phải hiện, nếu không là mất dữ liệu."""
    reviewer = await _create_user(db_session, "batch_reader", UserRole.REVIEWER)

    response = await client.get(BATCHES, headers=_auth_headers(reviewer))

    assert response.status_code == 200
    batches = response.json()
    assert [item["id"] for item in batches] == ["batch-1"]
    batch = batches[0]
    assert batch["named"] is False
    assert batch["name"] == "batch-1"
    assert batch["episodes"] == 1
    assert batch["scripted"] == 1
    assert batch["teleop"] == 0
    assert batch["approved"] == 1


@pytest.mark.asyncio
async def test_naming_a_batch_keeps_its_existing_episodes(
    client, db_session, raw_workspace
):
    """Đặt tên cho đợt đã có dữ liệu không được làm số liệu về 0."""
    reviewer = await _create_user(db_session, "batch_namer", UserRole.REVIEWER)
    headers = _auth_headers(reviewer)

    created = await client.post(
        BATCHES,
        headers=headers,
        json={"id": "batch-1", "name": "Lift đợt 1", "task_name": "lift"},
    )

    assert created.status_code == 201
    body = created.json()
    assert body["named"] is True
    assert body["name"] == "Lift đợt 1"
    assert body["episodes"] == 1
    assert body["approved"] == 1


@pytest.mark.asyncio
async def test_creating_the_same_batch_twice_conflicts(
    client, db_session, raw_workspace
):
    reviewer = await _create_user(db_session, "batch_dup", UserRole.REVIEWER)
    headers = _auth_headers(reviewer)
    payload = {"id": "batch-1", "name": "Lần đầu"}
    assert (await client.post(BATCHES, headers=headers, json=payload)).status_code == 201

    again = await client.post(BATCHES, headers=headers, json=payload)

    assert again.status_code == 409


@pytest.mark.asyncio
async def test_update_renames_a_batch(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "batch_editor", UserRole.REVIEWER)
    headers = _auth_headers(reviewer)
    await client.post(BATCHES, headers=headers, json={"id": "batch-1", "name": "Cũ"})

    response = await client.patch(
        f"{BATCHES}/batch-1", headers=headers, json={"name": "Mới", "description": "ghi chú"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Mới"
    assert response.json()["description"] == "ghi chú"


@pytest.mark.asyncio
async def test_archived_batch_hidden_unless_asked_for(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "batch_archiver", UserRole.REVIEWER)
    headers = _auth_headers(reviewer)
    await client.post(BATCHES, headers=headers, json={"id": "batch-1", "name": "Xong"})
    await client.patch(f"{BATCHES}/batch-1", headers=headers, json={"archived": True})

    hidden = await client.get(BATCHES, headers=headers)
    shown = await client.get(f"{BATCHES}?include_archived=true", headers=headers)

    assert hidden.json() == []
    assert [item["id"] for item in shown.json()] == ["batch-1"]


@pytest.mark.asyncio
async def test_update_unknown_batch_is_404(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "batch_missing", UserRole.REVIEWER)

    response = await client.patch(
        f"{BATCHES}/nope", headers=_auth_headers(reviewer), json={"name": "x"}
    )

    assert response.status_code == 404


# --- importing a batch folder from the desktop app ---------------------------


def _app_batch_zip(tmp_path: Path, *, source: str = "lift_clean_seed77.hdf5") -> bytes:
    """The zip a person makes from `<app workspace>/batches/<name>/`."""

    from tests.test_batch_import import _archive, _episode_dir
    import zipfile

    staging = tmp_path / "app-batch"
    root = staging / "Lift v9"
    root.mkdir(parents=True)
    (root / "batch.json").write_text(
        json.dumps({"id": "abc", "name": "Lift v9", "task": "lift"}), encoding="utf-8",
    )
    _episode_dir(root, "lift_101", source=source, demo="demo_0")
    _episode_dir(root, "lift_102", source=source, demo="demo_1")

    archive_path = tmp_path / "app-batch.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())
    return archive_path.read_bytes()


IMPORT_API = "/api/v1/raw/batches/{}/import"


@pytest.mark.asyncio
async def test_an_operator_can_import_the_batches_they_collected(
    client, db_session, raw_workspace, tmp_path,
):
    """Collecting is an operator's job, so delivering a collection must be too.

    Gating this at reviewer would leave the people producing the data unable to
    get it onto the server. Judging what arrives is still reviewer-only.
    """

    operator = await _create_user(db_session, "import_operator", UserRole.OPERATOR)

    response = await client.post(
        IMPORT_API.format("lift-v9"),
        headers=_auth_headers(operator),
        files={"archive": ("batch.zip", _app_batch_zip(tmp_path), "application/zip")},
    )

    assert response.status_code == 201, response.text
    assert response.json()["episodes"] == 2


AUTO_IMPORT_API = "/api/v1/raw/batches/import"


@pytest.mark.asyncio
async def test_an_import_without_an_id_takes_its_name_from_the_archive(
    client, db_session, raw_workspace, tmp_path,
):
    """The name is already inside the zip, so asking for it again is asking twice."""

    operator = await _create_user(db_session, "auto_id_operator", UserRole.OPERATOR)

    response = await client.post(
        AUTO_IMPORT_API,
        headers=_auth_headers(operator),
        files={"archive": ("batch.zip", _app_batch_zip(tmp_path), "application/zip")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    # batch.json carries id "abc" and name "Lift v9".
    assert body["batch"]["id"] == "abc"
    assert body["batch"]["name"] == "Lift v9"


@pytest.mark.asyncio
async def test_a_second_import_of_the_same_name_gets_a_number(
    client, db_session, raw_workspace, tmp_path,
):
    """What a file manager does with a duplicate: keep the name, add a counter."""

    operator = await _create_user(db_session, "dup_name_operator", UserRole.OPERATOR)
    headers = _auth_headers(operator)

    first = await client.post(
        AUTO_IMPORT_API,
        headers=headers,
        files={"archive": ("batch.zip", _app_batch_zip(tmp_path), "application/zip")},
    )
    assert first.status_code == 201, first.text
    assert first.json()["batch"]["id"] == "abc"

    second = await client.post(
        AUTO_IMPORT_API,
        headers=headers,
        files={
            "archive": (
                "batch.zip",
                _app_batch_zip(tmp_path / "again", source="lift_clean_seed78.hdf5"),
                "application/zip",
            ),
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["batch"]["id"] == "abc-1"


@pytest.mark.asyncio
async def test_importing_into_a_batch_of_another_task_is_refused(
    client, db_session, raw_workspace, tmp_path,
):
    """One batch is one task: Data diversity filters on it, and a dataset that
    mixes two tasks is a broken dataset."""

    operator = await _create_user(db_session, "task_clash_operator", UserRole.OPERATOR)
    headers = _auth_headers(operator)

    first = await client.post(
        AUTO_IMPORT_API,
        headers=headers,
        files={"archive": ("batch.zip", _app_batch_zip(tmp_path), "application/zip")},
    )
    assert first.status_code == 201, first.text
    batch_id = first.json()["batch"]["id"]

    staging = tmp_path / "other-task"
    root = staging / "Can v1"
    root.mkdir(parents=True)
    (root / "batch.json").write_text(
        json.dumps({"id": "can-v1", "name": "Can v1", "task": "pick_place_can"}),
        encoding="utf-8",
    )
    from tests.test_batch_import import _episode_dir
    import zipfile

    _episode_dir(root, "can_201", source="can_clean_seed5.hdf5", demo="demo_0")
    other = tmp_path / "other-task.zip"
    with zipfile.ZipFile(other, "w") as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())

    response = await client.post(
        IMPORT_API.format(batch_id),
        headers=headers,
        files={"archive": ("batch.zip", other.read_bytes(), "application/zip")},
    )

    assert response.status_code == 409, response.text
    assert "pick_place_can" in response.json()["detail"]


def _extra_episodes_zip(tmp_path: Path, *, source: str, ids: tuple[str, ...]) -> bytes:
    """Another zip for batch `abc`: same batch, same task, different episodes."""

    from tests.test_batch_import import _episode_dir
    import zipfile

    staging = tmp_path / f"extra-{'-'.join(ids)}"
    root = staging / "Lift v9"
    root.mkdir(parents=True)
    (root / "batch.json").write_text(
        json.dumps({"id": "abc", "name": "Lift v9", "task": "lift"}), encoding="utf-8",
    )
    for index, episode_id in enumerate(ids):
        _episode_dir(root, episode_id, source=source, demo=f"demo_{index}")

    archive_path = staging.with_suffix(".zip")
    with zipfile.ZipFile(archive_path, "w") as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())
    return archive_path.read_bytes()


@pytest.mark.asyncio
async def test_episodes_can_be_added_to_a_batch_that_already_exists(
    client, db_session, raw_workspace, tmp_path,
):
    """Chọn một đợt thu rồi nạp thêm tập vào đúng nó — không tạo đợt thu thứ hai.

    Thu dữ liệu là việc nhiều buổi. Nếu mỗi lần nạp lại đẻ ra một đợt thu mới
    thì một task bị xé thành `abc`, `abc-1`, `abc-2`, và Data diversity đếm ba
    đợt thu rời rạc thay vì một.
    """

    operator = await _create_user(db_session, "merge_operator", UserRole.OPERATOR)
    headers = _auth_headers(operator)

    first = await client.post(
        AUTO_IMPORT_API,
        headers=headers,
        files={"archive": ("batch.zip", _app_batch_zip(tmp_path), "application/zip")},
    )
    assert first.status_code == 201, first.text
    batch_id = first.json()["batch"]["id"]
    assert first.json()["episodes"] == 2

    second = await client.post(
        IMPORT_API.format(batch_id),
        headers=headers,
        files={
            "archive": (
                "more.zip",
                _extra_episodes_zip(
                    tmp_path, source="lift_clean_seed90.hdf5", ids=("lift_201", "lift_202"),
                ),
                "application/zip",
            ),
        },
    )

    assert second.status_code == 201, second.text
    # Cùng một mã đợt thu, không phải `abc-1`.
    assert second.json()["batch"]["id"] == batch_id

    assert second.json()["episodes"] == 2

    # Cả bốn tập cùng mang một mã đợt thu, không bị xé làm hai. Mã nằm trong
    # thuộc tính của file collection — đó mới là thứ quyết định một tập thuộc
    # đợt thu nào, chứ không phải chỗ file nằm.
    import h5py

    tagged = 0
    for name in set(first.json()["sources"]) | set(second.json()["sources"]):
        with h5py.File(raw_workspace / "datasets" / name, "r") as handle:
            data = handle["data"]
            assert data.attrs["telecollect_collection_batch_id"] == batch_id
            tagged += len(data.keys())
    assert tagged == 4


@pytest.mark.asyncio
async def test_adding_a_run_whose_name_is_taken_gets_a_number(
    client, db_session, raw_workspace, tmp_path,
):
    """Trùng tên file thu nhưng khác nội dung thì đánh số, không từ chối cả gói.

    Hai buổi thu khác nhau vẫn có thể sinh ra file cùng tên — tên đến từ task và
    seed, không phải từ thời điểm. Từ chối thì buổi thứ hai mất trắng.
    """

    operator = await _create_user(db_session, "collide_operator", UserRole.OPERATOR)
    headers = _auth_headers(operator)

    first = await client.post(
        AUTO_IMPORT_API,
        headers=headers,
        files={"archive": ("batch.zip", _app_batch_zip(tmp_path), "application/zip")},
    )
    assert first.status_code == 201, first.text
    batch_id = first.json()["batch"]["id"]
    original = first.json()["sources"]

    second = await client.post(
        IMPORT_API.format(batch_id),
        headers=headers,
        files={
            "archive": (
                "more.zip",
                # Cùng tên file nguồn với gói đầu, nhưng là tập khác.
                _extra_episodes_zip(
                    tmp_path, source="lift_clean_seed77.hdf5", ids=("lift_301",),
                ),
                "application/zip",
            ),
        },
    )

    assert second.status_code == 201, second.text
    assert second.json()["episodes"] == 1
    rebuilt = second.json()["sources"]
    # Tên cũ giữ nguyên, bản mới mang số đếm — cả hai cùng tồn tại.
    assert rebuilt != original
    assert any("-1" in name for name in rebuilt), rebuilt


@pytest.mark.asyncio
async def test_batch_import_still_requires_signing_in(client, db_session, raw_workspace, tmp_path):
    response = await client.post(
        IMPORT_API.format("lift-v9"),
        files={"archive": ("batch.zip", _app_batch_zip(tmp_path), "application/zip")},
    )

    assert response.status_code in {401, 403}


@pytest.mark.asyncio
async def test_batch_import_creates_the_batch_and_lists_its_episodes(
    client, db_session, raw_workspace, tmp_path,
):
    reviewer = await _create_user(db_session, "import_reviewer", UserRole.REVIEWER)

    response = await client.post(
        IMPORT_API.format("lift-v9"),
        headers=_auth_headers(reviewer),
        files={"archive": ("batch.zip", _app_batch_zip(tmp_path), "application/zip")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["episodes"] == 2
    assert body["videos"] == 2
    assert body["sources"] == ["lift_clean_seed77.hdf5"]
    assert body["skipped"] == []
    # The batch did not exist beforehand; batch.json supplied name and task.
    assert body["batch"]["name"] == "Lift v9"
    assert body["batch"]["task_name"] == "lift"
    assert body["batch"]["named"] is True

    listed = await client.get(
        f"{API}?collection_batch_id=lift-v9", headers=_auth_headers(reviewer),
    )
    assert listed.status_code == 200
    assert {item["episode_id"] for item in listed.json()["items"]} == {
        "lift_clean_seed77.hdf5::demo_0",
        "lift_clean_seed77.hdf5::demo_1",
    }


@pytest.mark.asyncio
async def test_batch_import_refuses_an_unsafe_batch_id(
    client, db_session, raw_workspace, tmp_path,
):
    """The id becomes part of a dataset filename."""

    reviewer = await _create_user(db_session, "import_unsafe", UserRole.REVIEWER)

    response = await client.post(
        IMPORT_API.format("../escape"),
        headers=_auth_headers(reviewer),
        files={"archive": ("batch.zip", _app_batch_zip(tmp_path), "application/zip")},
    )

    assert response.status_code in {404, 422}


@pytest.mark.asyncio
async def test_a_collection_run_whose_name_is_taken_gets_a_number(
    client, db_session, raw_workspace, tmp_path,
):
    """`lift_clean_seed0.hdf5` is already in the fixture workspace, holding
    different recordings — so this upload is a second take, not a re-send, and
    it lands beside the existing run rather than being refused."""

    reviewer = await _create_user(db_session, "import_clash", UserRole.REVIEWER)

    response = await client.post(
        IMPORT_API.format("lift-v9"),
        headers=_auth_headers(reviewer),
        files={
            "archive": (
                "batch.zip",
                _app_batch_zip(tmp_path, source="lift_clean_seed0.hdf5"),
                "application/zip",
            ),
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["sources"] == ["lift_clean_seed0-1.hdf5"]


@pytest.mark.asyncio
async def test_batch_import_rejects_a_file_that_is_not_a_zip(
    client, db_session, raw_workspace,
):
    reviewer = await _create_user(db_session, "import_garbage", UserRole.REVIEWER)

    response = await client.post(
        IMPORT_API.format("lift-v9"),
        headers=_auth_headers(reviewer),
        files={"archive": ("batch.zip", b"not a zip at all", "application/zip")},
    )

    assert response.status_code == 422
    assert "zip" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_batch_keeps_its_episodes_by_default(
    client, db_session, raw_workspace
):
    """Xoá đợt thu chỉ bỏ phần mô tả: episode phải còn và quay về chưa đặt tên."""
    reviewer = await _create_user(db_session, "batch_deleter", UserRole.REVIEWER)
    headers = _auth_headers(reviewer)
    await client.post(BATCHES, headers=headers, json={"id": "batch-1", "name": "Đợt 1"})

    response = await client.delete(f"{BATCHES}/batch-1", headers=headers)

    assert response.status_code == 204
    remaining = (await client.get(BATCHES, headers=headers)).json()
    assert [item["id"] for item in remaining] == ["batch-1"]
    assert remaining[0]["named"] is False
    assert remaining[0]["episodes"] == 1


@pytest.mark.asyncio
async def test_delete_batch_with_purge_removes_teleop_episodes(
    client, db_session, raw_workspace, storage_dir
):
    """purge_episodes xoá episode teleop; episode scripted vẫn phải còn."""
    from src.services import storage

    reviewer = await _create_user(db_session, "batch_purger", UserRole.REVIEWER)
    operator = await _create_user(db_session, "purge_op", UserRole.OPERATOR)
    headers = _auth_headers(reviewer)

    episode = await _create_teleop(db_session, operator)
    directory = storage.episode_dir(episode.id)
    directory.mkdir(parents=True, exist_ok=True)
    # Teleop gắn với đợt thu qua meta.json, giống dữ liệu app ghi ra.
    (directory / "meta.json").write_text(
        json.dumps({"collection_batch_id": "batch-1"}), encoding="utf-8"
    )

    response = await client.delete(
        f"{BATCHES}/batch-1?purge_episodes=true", headers=headers
    )

    assert response.status_code == 204
    db_session.expunge_all()
    assert await db_session.get(Episode, episode.id) is None
    assert not directory.exists()
    # Episode scripted của batch-1 nằm trong workspace, không bị đụng tới.
    still_there = (await client.get(BATCHES, headers=headers)).json()
    assert [item["id"] for item in still_there] == ["batch-1"]
    assert still_there[0]["scripted"] == 1


@pytest.mark.asyncio
async def test_delete_unknown_batch_is_404(client, db_session, raw_workspace):
    reviewer = await _create_user(db_session, "batch_gone", UserRole.REVIEWER)

    response = await client.delete(f"{BATCHES}/nope", headers=_auth_headers(reviewer))

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_batch_requires_reviewer(client, db_session, raw_workspace):
    operator = await _create_user(db_session, "batch_del_op", UserRole.OPERATOR)

    response = await client.delete(f"{BATCHES}/batch-1", headers=_auth_headers(operator))

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_unnamed_batch_with_purge_works(
    client, db_session, raw_workspace, storage_dir
):
    """Đợt chưa ai đặt tên không có row DB, nhưng vẫn phải xoá được episode."""
    reviewer = await _create_user(db_session, "batch_unnamed_del", UserRole.REVIEWER)
    operator = await _create_user(db_session, "unnamed_op", UserRole.OPERATOR)
    headers = _auth_headers(reviewer)

    from src.services import storage

    episode = await _create_teleop(db_session, operator)
    directory = storage.episode_dir(episode.id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meta.json").write_text(
        json.dumps({"collection_batch_id": "never-named"}), encoding="utf-8"
    )

    response = await client.delete(
        f"{BATCHES}/never-named?purge_episodes=true", headers=headers
    )

    assert response.status_code == 204
    db_session.expunge_all()
    assert await db_session.get(Episode, episode.id) is None
