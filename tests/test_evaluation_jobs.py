import json
import sys
import time
from pathlib import Path

from scripts.evaluate_robomimic import (
    _install_egl_probe_fallback,
    _rollout_with_success_tail,
)
from src.models.enums import JobStatus
from src.models.schemas import EvaluationJobRequest
from src.training.evaluation_jobs import EvaluationJobManager


class TrainingJobsStub:
    def __init__(self, checkpoint: Path | None) -> None:
        self.checkpoint = checkpoint

    def checkpoint_path(self, _job_id: str, _checkpoint_id: str) -> Path | None:
        return self.checkpoint


def _request(**overrides) -> EvaluationJobRequest:
    values = {
        "training_run_id": "training-1",
        "checkpoint_id": "checkpoint-1",
        "num_rollouts": 3,
        "seed": 5000,
        "record_videos": 1,
    }
    values.update(overrides)
    return EvaluationJobRequest(**values)


def _wait(manager: EvaluationJobManager, evaluation_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get(evaluation_id)
        if job and job.status not in {JobStatus.PENDING, JobStatus.RUNNING}:
            return job
        time.sleep(0.02)
    raise AssertionError("evaluation job did not finish")


def test_evaluation_job_persists_results_and_video(tmp_path: Path) -> None:
    script = tmp_path / "fake_evaluate.py"
    script.write_text(
        "import argparse, json\n"
        "from pathlib import Path\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('--result'); p.add_argument('--video-dir')\n"
        "a,_=p.parse_known_args()\n"
        "v=Path(a.video_dir); v.mkdir(parents=True, exist_ok=True)\n"
        "(v/'episode_000_success.mp4').write_bytes(b'video')\n"
        "Path(a.result).write_text(json.dumps({"
        "'task_name':'Lift','success_rate':2/3,'mean_episode_length':42.0,"
        "'episodes':[{'seed':5000,'success':True,'steps':30,'video':'episode_000_success.mp4'},"
        "{'seed':5001,'success':False,'steps':50,'video':None},"
        "{'seed':5002,'success':True,'steps':46,'video':None}]}))\n"
        "print('evaluation complete')\n",
        encoding="utf-8",
    )
    checkpoint = tmp_path / "model.pth"
    checkpoint.touch()
    manager = EvaluationJobManager(
        tmp_path / "training",
        TrainingJobsStub(checkpoint),  # type: ignore[arg-type]
        repo_root=tmp_path,
        python_executable=sys.executable,
        evaluation_script=script,
    )

    created = manager.submit(_request())
    finished = _wait(manager, created.id)

    assert finished.status == JobStatus.SUCCEEDED
    assert finished.task_name == "Lift"
    assert finished.success_rate == 2 / 3
    assert len(finished.episodes) == 3
    video = manager.video(created.id, "episode_000_success.mp4")
    assert video is not None and video.read_bytes() == b"video"
    assert manager.video(created.id, "../job.json") is None
    job_file = tmp_path / "training" / "training-1" / "evaluations" / created.id / "job.json"
    assert json.loads(job_file.read_text())["status"] == "succeeded"


def test_evaluation_failure_records_exit_code(tmp_path: Path) -> None:
    script = tmp_path / "fail.py"
    script.write_text("raise SystemExit(9)\n", encoding="utf-8")
    checkpoint = tmp_path / "model.pth"
    checkpoint.touch()
    manager = EvaluationJobManager(
        tmp_path / "training",
        TrainingJobsStub(checkpoint),  # type: ignore[arg-type]
        repo_root=tmp_path,
        python_executable=sys.executable,
        evaluation_script=script,
    )

    finished = _wait(manager, manager.submit(_request()).id)

    assert finished.status == JobStatus.FAILED
    assert finished.error == "Evaluation process exited with code 9"


def test_evaluation_can_be_cancelled(tmp_path: Path) -> None:
    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(2)\n", encoding="utf-8")
    checkpoint = tmp_path / "model.pth"
    checkpoint.touch()
    manager = EvaluationJobManager(
        tmp_path / "training",
        TrainingJobsStub(checkpoint),  # type: ignore[arg-type]
        repo_root=tmp_path,
        python_executable=sys.executable,
        evaluation_script=script,
    )
    created = manager.submit(_request())

    manager.cancel(created.id)

    assert _wait(manager, created.id).status == JobStatus.CANCELLED


def test_evaluation_request_rejects_more_videos_than_rollouts() -> None:
    try:
        _request(num_rollouts=2, record_videos=3)
    except ValueError:
        return
    raise AssertionError("invalid evaluation request was accepted")


def test_manager_rejects_checkpoint_not_owned_by_training_job(tmp_path: Path) -> None:
    manager = EvaluationJobManager(
        tmp_path / "training",
        TrainingJobsStub(None),  # type: ignore[arg-type]
    )

    try:
        manager.submit(_request(checkpoint_id="checkpoint-from-another-job"))
    except ValueError as exc:
        assert "không thuộc training job" in str(exc)
        return
    raise AssertionError("foreign checkpoint was accepted")


def test_missing_egl_probe_uses_empty_device_fallback(monkeypatch) -> None:
    monkeypatch.delitem(sys.modules, "egl_probe", raising=False)

    installed = _install_egl_probe_fallback()

    assert installed is True
    assert sys.modules["egl_probe"].get_available_devices() == []  # type: ignore[attr-defined]


class _PolicyStub:
    def start_episode(self) -> None:
        pass

    def __call__(self, *, ob):
        return ob


class _RolloutEnvStub:
    rollout_exceptions = ()

    def __init__(self, success_at: int | None) -> None:
        self.success_at = success_at
        self.steps = 0

    def reset(self):
        self.steps = 0
        return {"step": self.steps}

    def get_state(self):
        return {"states": [self.steps]}

    def reset_to(self, _state):
        return {"step": self.steps}

    def step(self, _action):
        self.steps += 1
        return {"step": self.steps}, 1.0, False, {}

    def is_success(self):
        return {"task": self.success_at is not None and self.steps >= self.success_at}


def test_rollout_records_tail_after_first_success() -> None:
    env = _RolloutEnvStub(success_at=2)

    stats = _rollout_with_success_tail(
        policy=_PolicyStub(),
        env=env,
        horizon=10,
        success_tail_steps=3,
        video_writer=None,
        video_skip=1,
        camera_names=["agentview"],
    )

    assert stats["Success_Rate"] == 1.0
    assert stats["Horizon"] == 5
    assert env.steps == 5


def test_unsuccessful_rollout_still_stops_at_horizon() -> None:
    env = _RolloutEnvStub(success_at=None)

    stats = _rollout_with_success_tail(
        policy=_PolicyStub(),
        env=env,
        horizon=4,
        success_tail_steps=30,
        video_writer=None,
        video_skip=1,
        camera_names=["agentview"],
    )

    assert stats["Success_Rate"] == 0.0
    assert stats["Horizon"] == 4
    assert env.steps == 4
