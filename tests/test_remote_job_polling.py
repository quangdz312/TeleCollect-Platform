"""Vòng hỏi trạng thái của job chạy trên GPU thuê.

Trọng tâm: hỏi trạng thái hỏng không đồng nghĩa training hỏng. Máy GPU vẫn
chạy và vẫn đẩy log về, nên một lần rớt mạng không được kết thúc job.
"""

from pathlib import Path

import pytest

from src.config import get_settings
from src.models.enums import JobStatus
from src.models.schemas import TrainingJobRequest
from src.training.jobs import TrainingJobManager
from src.training.runpod_runner import RunPodError


class FakeRunner:
    """Runner giả: `poll` trả lần lượt các kết quả đã dựng sẵn."""

    def __init__(self, results: list):
        self.results = list(results)
        self.polls = 0
        self.cancelled = False

    def start(self, record):
        return {"runpod_id": "rp-1"}

    def poll(self, record):
        self.polls += 1
        outcome = self.results.pop(0) if self.results else self.results
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def cancel(self, record):
        self.cancelled = True


@pytest.fixture
def fast_poll(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "runpod_poll_interval_s", 0.001)
    monkeypatch.setattr(settings, "runpod_max_hours", 1.0)
    return settings


def _job(manager: TrainingJobManager, tmp_path: Path) -> str:
    job_id = "job-1"
    output_dir = manager.root / job_id / "output"
    output_dir.mkdir(parents=True)
    record = {
        "id": job_id,
        "dataset_id": "dataset-1",
        "dataset_path": str(tmp_path / "dataset.hdf5"),
        "name": "run",
        "status": JobStatus.RUNNING,
        "config": TrainingJobRequest(dataset_id="dataset-1", name="run").model_dump(
            mode="json"
        ),
        "output_dir": str(output_dir),
        "created_at": "2026-01-01T00:00:00+00:00",
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": None,
        "epoch": 0,
        "train_loss": None,
        "validation_loss": None,
        "error": None,
        "checkpoints": [],
        "cancel_requested": False,
        "runner_state": {},
    }
    manager._jobs[job_id] = record
    manager._write(record)
    return job_id


def _run(runner: FakeRunner, tmp_path: Path) -> dict:
    manager = TrainingJobManager(tmp_path / "training", runner=runner)
    job_id = _job(manager, tmp_path)
    manager._run_remote(job_id)
    return manager._jobs[job_id]


def test_a_transient_poll_failure_does_not_end_the_job(fast_poll, tmp_path):
    """Đây chính là lỗi đã gặp: `_ssl.c:999 handshake timed out` giữa chừng."""
    runner = FakeRunner(
        [
            RunPodError("_ssl.c:999: The handshake operation timed out"),
            RunPodError("_ssl.c:999: The handshake operation timed out"),
            {"status": JobStatus.SUCCEEDED, "error": None},
        ]
    )
    record = _run(runner, tmp_path)
    assert record["status"] == JobStatus.SUCCEEDED
    assert record["error"] is None
    assert runner.polls == 3


def test_persistent_failure_still_ends_the_job(fast_poll, tmp_path):
    """Mất liên lạc thật thì vẫn phải dừng, không treo mãi."""
    runner = FakeRunner([RunPodError("no route") for _ in range(200)])
    record = _run(runner, tmp_path)
    assert record["status"] == JobStatus.FAILED
    assert "Mất liên lạc với RunPod" in record["error"]


def test_failure_counter_resets_after_a_good_poll(fast_poll, tmp_path):
    """Rớt lẻ tẻ rải rác không được cộng dồn thành mất liên lạc."""
    results: list = []
    for _ in range(5):
        results.extend([RunPodError("blip")] * 20)
        results.append({"status": JobStatus.RUNNING, "error": None})
    results.append({"status": JobStatus.SUCCEEDED, "error": None})
    record = _run(FakeRunner(results), tmp_path)
    assert record["status"] == JobStatus.SUCCEEDED


def test_running_out_of_gpu_hours_cancels_rather_than_fails(fast_poll, tmp_path, monkeypatch):
    """Hết giờ GPU là dừng đúng lúc, không phải hỏng.

    Training lưu checkpoint theo từng epoch, nên khi trần thời gian cắt ngang
    thì phần đã train vẫn dùng được. Đánh dấu FAILED khiến người dùng tưởng
    mất trắng lần chạy và thấy nó nằm chung với lỗi thật.
    """
    # Trần 0 giờ: quá hạn ngay lần kiểm tra đầu.
    monkeypatch.setattr(fast_poll, "runpod_max_hours", 0.0000001)
    runner = FakeRunner([{"status": JobStatus.RUNNING, "error": None} for _ in range(50)])

    record = _run(runner, tmp_path)

    assert record["status"] == JobStatus.CANCELLED
    assert "checkpoint" in record["error"]
    # Vẫn phải hủy bên RunPod, nếu không hóa đơn cứ chạy.
    assert runner.cancelled is True


def test_hours_come_from_runpod_execution_time_not_wall_clock(tmp_path):
    """Giờ trừ theo thời gian RunPod tính tiền, không tính lúc chờ hàng đợi."""
    manager = TrainingJobManager(tmp_path / "training", runner=FakeRunner([]))
    record = {
        "gpu_seconds": 1800.0,  # RunPod báo 30 phút chạy thật
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T02:00:00+00:00",  # 2 giờ tường vì chờ hàng đợi
    }

    assert manager._billed_hours(record) == pytest.approx(0.5)


def test_wall_clock_is_the_fallback_when_runpod_reports_nothing(tmp_path):
    """Thiếu số liệu thì tính hơi rộng còn hơn cho chạy miễn phí."""
    manager = TrainingJobManager(tmp_path / "training", runner=FakeRunner([]))
    record = {
        "gpu_seconds": None,
        "started_at": "2026-01-01T00:00:00+00:00",
        "finished_at": "2026-01-01T00:45:00+00:00",
    }

    assert manager._billed_hours(record) == pytest.approx(0.75)


def test_a_job_that_never_started_is_not_charged(tmp_path):
    manager = TrainingJobManager(tmp_path / "training", runner=FakeRunner([]))

    assert manager._billed_hours({"started_at": None, "finished_at": None}) == 0.0


def test_charging_a_job_with_no_owner_does_nothing(tmp_path):
    """Job cũ và job chạy runner local không có chủ, và không tốn giờ thuê."""
    manager = TrainingJobManager(tmp_path / "training", runner=FakeRunner([]))

    manager._charge_hours(None, 1.0)  # không được ném
