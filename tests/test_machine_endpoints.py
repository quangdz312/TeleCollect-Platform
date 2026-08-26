"""Ba endpoint máy GPU thuê gọi ngược về server, và cách chúng chặn lạm dụng."""

from datetime import timedelta
from pathlib import Path

import pytest

from src.api import training as training_api
from src.models.schemas import TrainingJobRequest
from src.services.security import create_access_token, create_machine_token
from src.training.jobs import TrainingJobManager

API = "/api/v1/training"
ONE_HOUR = timedelta(hours=1)


@pytest.fixture
def manager(tmp_path) -> TrainingJobManager:
    return TrainingJobManager(tmp_path / "training")


def _submit(manager: TrainingJobManager, tmp_path: Path) -> str:
    """Tạo job mà không thực sự chạy training — chỉ cần record trên đĩa."""
    dataset = tmp_path / "dataset.hdf5"
    dataset.write_bytes(b"fake hdf5")
    job_dir = manager.root / "job-1"
    (job_dir / "output").mkdir(parents=True)
    record = {
        "id": "job-1",
        "dataset_id": "dataset-1",
        "dataset_path": str(dataset),
        "name": "run",
        "status": "running",
        "config": TrainingJobRequest(dataset_id="dataset-1", name="run").model_dump(
            mode="json"
        ),
        "output_dir": str(job_dir / "output"),
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": None,
        "finished_at": None,
        "epoch": 0,
        "train_loss": None,
        "validation_loss": None,
        "error": None,
        "checkpoints": [],
        "cancel_requested": False,
        "runner_state": {},
    }
    manager._jobs["job-1"] = record
    manager._write(record)
    return "job-1"


# --- artifact_path: chặn ghi file ra ngoài output_dir ------------------------


def test_artifact_path_accepts_a_plain_checkpoint(manager, tmp_path):
    job_id = _submit(manager, tmp_path)
    path = manager.artifact_path(job_id, "last.pth")
    assert path is not None
    assert path.parent == Path(manager._jobs[job_id]["output_dir"])


@pytest.mark.parametrize(
    "filename",
    [
        "../../etc/passwd.pth",
        "../escape.pth",
        "/absolute/evil.pth",
        "sub/../../out.pth",
    ],
)
def test_artifact_path_rejects_traversal(manager, tmp_path, filename):
    job_id = _submit(manager, tmp_path)
    assert manager.artifact_path(job_id, filename) is None


@pytest.mark.parametrize("filename", ["notes.txt", "model.bin", "", "last.pth.exe"])
def test_artifact_path_accepts_only_pth(manager, tmp_path, filename):
    job_id = _submit(manager, tmp_path)
    assert manager.artifact_path(job_id, filename) is None


def test_artifact_path_unknown_job_is_none(manager):
    assert manager.artifact_path("nope", "last.pth") is None


# --- append_log -------------------------------------------------------------


def test_append_log_accumulates_into_the_file_refresh_reads(manager, tmp_path):
    job_id = _submit(manager, tmp_path)
    assert manager.append_log(job_id, "first\n") is True
    assert manager.append_log(job_id, "second\n") is True
    assert manager.log(job_id) == "first\nsecond\n"


def test_append_log_unknown_job_is_false(manager):
    assert manager.append_log("nope", "x") is False


# --- dataset_path -----------------------------------------------------------


def test_dataset_path_returns_the_managed_file(manager, tmp_path):
    job_id = _submit(manager, tmp_path)
    assert manager.dataset_path(job_id).read_bytes() == b"fake hdf5"


def test_dataset_path_unknown_job_is_none(manager):
    assert manager.dataset_path("nope") is None


# --- ranh giới xác thực HTTP ------------------------------------------------


@pytest.mark.asyncio
async def test_machine_endpoints_reject_missing_token(client):
    for method, path in (
        ("get", f"{API}/jobs/job-1/dataset"),
        ("post", f"{API}/jobs/job-1/log"),
        ("post", f"{API}/jobs/job-1/artifacts"),
    ):
        resp = await getattr(client, method)(path)
        assert resp.status_code == 401, path


@pytest.mark.asyncio
async def test_machine_token_of_one_job_cannot_touch_another(client):
    token = create_machine_token("job-a", ONE_HOUR)
    resp = await client.get(
        f"{API}/jobs/job-b/dataset", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_user_token_cannot_use_machine_endpoints(client):
    """Access token của người dùng không có `job_id` nên không qua được."""
    token = create_access_token("user-1", "admin")
    resp = await client.get(
        f"{API}/jobs/job-1/dataset", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_machine_token_cannot_list_jobs(client):
    """Chiều ngược lại: token máy không mượn được quyền reviewer."""
    token = create_machine_token("job-1", ONE_HOUR)
    resp = await client.get(
        f"{API}/jobs", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401


# --- runner_state không lọt ra API ------------------------------------------


def test_runner_state_never_reaches_the_response(manager, tmp_path):
    """`runner_state` chứa token máy — không được xuất hiện trong API."""
    job_id = _submit(manager, tmp_path)
    manager._jobs[job_id]["runner_state"] = {"runpod_id": "rp-1"}
    payload = manager.get(job_id).model_dump()
    assert "runner_state" not in payload
    assert "dataset_path" not in payload
