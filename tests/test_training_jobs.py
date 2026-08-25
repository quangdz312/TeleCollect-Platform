import json
import os
import sys
import time
from pathlib import Path

import pytest

from src.api import training as training_api
from src.config import get_settings
from src.models.db import Dataset, User
from src.models.enums import DatasetStatus, JobStatus, UserRole
from src.models.schemas import TrainingJobRequest
from src.services.security import create_access_token, hash_password
from src.training.jobs import TrainingJobManager, discover_checkpoints, parse_training_progress


def _request(**overrides) -> TrainingJobRequest:
    values = {"dataset_id": "dataset-1", "name": "lift_bc", "epochs": 3}
    values.update(overrides)
    return TrainingJobRequest(**values)


def _wait(manager: TrainingJobManager, job_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(job_id)
        if job and job.status not in {JobStatus.PENDING, JobStatus.RUNNING}:
            return job
        time.sleep(0.02)
    raise AssertionError("training job did not finish")


def test_parse_training_progress_uses_latest_complete_blocks() -> None:
    text = """
Train Epoch 4
{"Loss": 0.25, "Time_Epoch": 1.0}
Validation Epoch 4
{"Loss": 0.20}
Train Epoch 5
{"Loss": 0.15}
Validation Epoch 5
{"Loss": 0.12}
Train Epoch 6
{
"""

    progress = parse_training_progress(text)

    assert progress == {"epoch": 5, "train_loss": 0.15, "validation_loss": 0.12}


def test_discover_checkpoints_marks_best_and_latest(tmp_path: Path) -> None:
    models = tmp_path / "run" / "models"
    models.mkdir(parents=True)
    first = models / "model_epoch_5_best_validation_0.08.pth"
    best = models / "model_epoch_9_best_validation_0.03.pth"
    rollout = models / "model_epoch_7_dataset-id_success_0.8.pth"
    combined = models / "model_epoch_6_best_validation_0.04_dataset-id_success_1.0.pth"
    final = models / "model_epoch_10.pth"
    latest = tmp_path / "run" / "last.pth"
    for index, path in enumerate((first, best, rollout, combined, final, latest), start=1):
        path.write_bytes(b"x" * index)
        timestamp = 1_700_000_000 + index
        path.touch()
        os.utime(path, (timestamp, timestamp))
    (tmp_path / "run" / "last_bak.pth").touch()

    checkpoints = discover_checkpoints(tmp_path, current_epoch=10)

    assert len(checkpoints) == 6
    assert next(item for item in checkpoints if item["is_best_validation"])["epoch"] == 9
    assert next(item for item in checkpoints if item["is_latest"])["epoch"] == 10
    assert next(item for item in checkpoints if "dataset-id_success_0.8" in item["filename"])["epoch"] == 7
    combined_item = next(
        item for item in checkpoints if "best_validation_0.04_dataset-id" in item["filename"]
    )
    assert combined_item["epoch"] == 6
    assert combined_item["validation_loss"] == 0.04
    assert len({item["id"] for item in checkpoints}) == 6


@pytest.mark.parametrize(
    ("normalize", "rollout", "expected_normalize", "expected_rollout"),
    [
        (True, True, "--normalize-observations", "--rollout-enabled"),
        (False, False, "--no-normalize-observations", "--no-rollout-enabled"),
    ],
)
def test_training_command_passes_explicit_boolean_flags(
    tmp_path: Path,
    normalize: bool,
    rollout: bool,
    expected_normalize: str,
    expected_rollout: str,
) -> None:
    manager = TrainingJobManager(tmp_path / "jobs")
    request = _request(
        normalize_observations=normalize,
        rollout_enabled=rollout,
    )
    record = {
        "dataset_path": str(tmp_path / "dataset.hdf5"),
        "output_dir": str(tmp_path / "output"),
        "config": request.model_dump(mode="json"),
    }

    command = manager._command(record)

    normalize_flags = [value for value in command if "normalize-observations" in value]
    rollout_flags = [value for value in command if "rollout-enabled" in value]
    assert normalize_flags == [expected_normalize]
    assert rollout_flags == [expected_rollout]


def test_job_runs_subprocess_and_persists_state(tmp_path: Path) -> None:
    script = tmp_path / "fake_train.py"
    script.write_text(
        "import argparse\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--output-dir'); p.add_argument('--name')\n"
        "a, _ = p.parse_known_args()\n"
        "from pathlib import Path\n"
        "Path(a.output_dir, 'called.txt').write_text(a.name)\n"
        "m=Path(a.output_dir, 'run', 'models'); m.mkdir(parents=True)\n"
        "Path(m, 'model_epoch_3_best_validation_0.12.pth').write_bytes(b'x')\n"
        "print('Train Epoch 3\\n{\"Loss\": 0.15}', flush=True)\n"
        "print('Validation Epoch 3\\n{\"Loss\": 0.12}', flush=True)\n",
        encoding="utf-8",
    )
    dataset = tmp_path / "dataset.hdf5"
    dataset.touch()
    manager = TrainingJobManager(
        tmp_path / "jobs",
        repo_root=tmp_path,
        python_executable=sys.executable,
        training_script=script,
    )

    created = manager.submit(_request(), dataset)
    finished = _wait(manager, created.id)

    assert finished.status == JobStatus.SUCCEEDED
    job_dir = tmp_path / "jobs" / created.id
    assert (job_dir / "output" / "called.txt").read_text() == "lift_bc"
    assert "Train Epoch 3" in (job_dir / "stdout.log").read_text()
    assert finished.epoch == 3
    assert finished.train_loss == 0.15
    assert finished.validation_loss == 0.12
    assert finished.checkpoints[0].is_best_validation is True
    assert json.loads((job_dir / "job.json").read_text())["status"] == "succeeded"

    restored = TrainingJobManager(tmp_path / "jobs", training_script=script)
    assert restored.get(created.id).status == JobStatus.SUCCEEDED


def test_job_failure_records_exit_code(tmp_path: Path) -> None:
    script = tmp_path / "fail.py"
    script.write_text("raise SystemExit(7)\n", encoding="utf-8")
    dataset = tmp_path / "dataset.hdf5"
    dataset.touch()
    manager = TrainingJobManager(
        tmp_path / "jobs",
        repo_root=tmp_path,
        python_executable=sys.executable,
        training_script=script,
    )

    finished = _wait(manager, manager.submit(_request(), dataset).id)

    assert finished.status == JobStatus.FAILED
    assert finished.error == "Training process exited with code 7"


def test_pending_job_can_be_cancelled(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(1)\n", encoding="utf-8")
    dataset = tmp_path / "dataset.hdf5"
    dataset.touch()
    manager = TrainingJobManager(
        tmp_path / "jobs",
        repo_root=tmp_path,
        python_executable=sys.executable,
        training_script=script,
    )
    first = manager.submit(_request(name="first"), dataset)
    second = manager.submit(_request(name="second"), dataset)

    cancelled = manager.cancel(second.id)

    assert cancelled is not None
    assert _wait(manager, second.id).status == JobStatus.CANCELLED
    manager.cancel(first.id)
    assert _wait(manager, first.id).status == JobStatus.CANCELLED


def test_restart_marks_interrupted_job_failed(tmp_path: Path) -> None:
    job_dir = tmp_path / "jobs" / "interrupted"
    job_dir.mkdir(parents=True)
    record = {
        "id": "interrupted",
        "dataset_id": "dataset-1",
        "dataset_path": str(tmp_path / "dataset.hdf5"),
        "name": "lift_bc",
        "status": "running",
        "config": _request().model_dump(mode="json"),
        "output_dir": str(job_dir / "output"),
        "created_at": "2026-08-17T00:00:00+00:00",
        "started_at": "2026-08-17T00:00:01+00:00",
        "finished_at": None,
        "epoch": 0,
        "train_loss": None,
        "validation_loss": None,
        "error": None,
        "checkpoints": [],
        "cancel_requested": False,
    }
    (job_dir / "job.json").write_text(json.dumps(record), encoding="utf-8")

    restored = TrainingJobManager(tmp_path / "jobs")
    job = restored.get("interrupted")

    assert job is not None
    assert job.status == JobStatus.FAILED
    assert "khởi động lại" in job.error


@pytest.mark.asyncio
async def test_create_job_api_accepts_managed_ready_hdf5(
    client, db_session, storage_dir: Path, monkeypatch
) -> None:
    user = User(
        username="training-reviewer",
        password_hash=hash_password("password123"),
        display_name="Training Reviewer",
        role=UserRole.ADMIN,
    )
    dataset = Dataset(
        id="dataset-ready",
        name="lift_export",
        task_names=["lift_cube"],
        status=DatasetStatus.READY,
        zip_path=str(storage_dir / "datasets" / "dataset-ready.hdf5"),
    )
    db_session.add_all([user, dataset])
    await db_session.commit()
    path = storage_dir / "datasets" / "dataset-ready.hdf5"
    path.parent.mkdir(parents=True)
    path.touch()
    script = storage_dir / "fake_train.py"
    script.write_text("print('ok')\n", encoding="utf-8")
    manager = TrainingJobManager(
        storage_dir / "training",
        repo_root=storage_dir,
        python_executable=sys.executable,
        training_script=script,
    )
    monkeypatch.setattr(training_api, "job_manager", lambda: manager)
    headers = {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}

    response = await client.post(
        "/api/v1/training/jobs",
        json={"dataset_id": dataset.id, "name": "lift_bc"},
        headers=headers,
    )

    assert response.status_code == 202
    assert response.json()["dataset_id"] == dataset.id
    finished = _wait(manager, response.json()["id"])
    assert finished.status == JobStatus.SUCCEEDED

    checkpoint_dir = Path(finished.output_dir) / "run" / "models"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / "model_epoch_1.pth").write_bytes(b"checkpoint-bytes")
    refreshed = manager.get(finished.id)
    assert refreshed is not None and refreshed.checkpoints

    download = await client.get(
        f"/api/v1/training/jobs/{finished.id}/checkpoints/{refreshed.checkpoints[0].id}/download",
        headers=headers,
    )

    assert download.status_code == 200
    assert download.content == b"checkpoint-bytes"
    assert "model_epoch_1.pth" in download.headers["content-disposition"]

    deleted = await client.delete(
        f"/api/v1/training/jobs/{finished.id}",
        headers=headers,
    )

    assert deleted.status_code == 204
    assert manager.get(finished.id) is None
    assert not (storage_dir / "training" / finished.id).exists()


@pytest.mark.asyncio
async def test_create_job_api_rejects_when_training_disabled(
    client, db_session, storage_dir: Path, monkeypatch
) -> None:
    """CPU staging sets TRAINING_ENABLED=false; the endpoint must refuse
    before touching TrainingJobManager, never spawn a subprocess and never
    return a fake success."""
    user = User(
        username="training-reviewer-2",
        password_hash=hash_password("password123"),
        display_name="Training Reviewer",
        role=UserRole.REVIEWER,
    )
    dataset = Dataset(
        id="dataset-ready-2",
        name="lift_export",
        task_names=["lift_cube"],
        status=DatasetStatus.READY,
        zip_path=str(storage_dir / "datasets" / "dataset-ready-2.hdf5"),
    )
    db_session.add_all([user, dataset])
    await db_session.commit()
    path = storage_dir / "datasets" / "dataset-ready-2.hdf5"
    path.parent.mkdir(parents=True)
    path.touch()

    manager = TrainingJobManager(storage_dir / "training")
    monkeypatch.setattr(training_api, "job_manager", lambda: manager)
    monkeypatch.setattr(get_settings(), "training_enabled", False)
    headers = {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}

    response = await client.post(
        "/api/v1/training/jobs",
        json={"dataset_id": dataset.id, "name": "lift_bc"},
        headers=headers,
    )

    assert response.status_code == 403
    assert manager.list() == []
