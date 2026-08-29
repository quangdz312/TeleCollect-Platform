import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from scripts.evaluate_robomimic import (
    _environment_fingerprint,
    _install_egl_probe_fallback,
    _prepare_state_bank,
    _rollout_with_success_tail,
    _should_keep_video,
    _write_result,
)
from src.models.enums import JobStatus
from src.models.schemas import EvaluationJobRequest
from src.training.evaluation_jobs import EvaluationJobManager

# Deterministic seeding calls `torch.manual_seed`, and torch lives in
# requirements-train.txt: CI installs the runtime deps only, so these four
# tests have nothing to seed there.
requires_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None,
    reason="needs torch from requirements-train.txt",
)


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


# Script thay cho `evaluate_robomimic.py`: tôn trọng --seed/--n-rollouts và
# ghi state bank, để một lần chạy chia cho nhiều process cho ra đúng số
# episode như chạy một process.
_FAKE_EVALUATE = """
import argparse, json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--result')
p.add_argument('--video-dir')
p.add_argument('--seed', type=int, default=5000)
p.add_argument('--n-rollouts', type=int, default=1)
p.add_argument('--record-videos', type=int, default=0)
p.add_argument('--state-bank')
a, _ = p.parse_known_args()

if a.state_bank:
    bank = Path(a.state_bank)
    bank.parent.mkdir(parents=True, exist_ok=True)
    bank.write_bytes(b'bank')

v = Path(a.video_dir)
v.mkdir(parents=True, exist_ok=True)

episodes = []
for index in range(a.n_rollouts):
    seed = a.seed + index
    success = seed % 3 != 1
    video = None
    if index < a.record_videos:
        name = f"seed_{seed}_{'success' if success else 'fail'}.mp4"
        (v / name).write_bytes(b'video')
        video = name
    episodes.append({
        'seed': seed, 'success': success,
        'steps': 30 + (seed - 5000), 'video': video,
    })

done = len(episodes)
Path(a.result).write_text(json.dumps({
    'task_name': 'Lift',
    'num_episodes': a.n_rollouts,
    'completed_episodes': done,
    'success_rate': sum(e['success'] for e in episodes) / done,
    'mean_episode_length': sum(e['steps'] for e in episodes) / done,
    'episodes': episodes,
}))
print('evaluation complete')
"""


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
    script.write_text(_FAKE_EVALUATE, encoding="utf-8")
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
    video = manager.video(created.id, "seed_5000_success.mp4")
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


def test_failed_evaluation_can_be_retried_and_deleted(tmp_path: Path) -> None:
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
    failed = _wait(manager, manager.submit(_request()).id)

    retried = manager.retry(failed.id)

    assert retried is not None and retried.id != failed.id
    assert _wait(manager, retried.id).status == JobStatus.FAILED
    assert manager.delete(failed.id) is True
    assert manager.get(failed.id) is None


def test_result_writer_retries_windows_file_lock(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "result.json"
    target.write_text("{}", encoding="utf-8")
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(path: Path, destination: Path):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporarily locked")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    _write_result(target, {"success_rate": 1.0})

    assert attempts == 3
    assert json.loads(target.read_text()) == {"success_rate": 1.0}


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

    with pytest.raises(ValueError, match="đang chạy"):
        manager.delete(created.id)

    manager.cancel(created.id)

    assert _wait(manager, created.id).status == JobStatus.CANCELLED


def test_evaluation_request_rejects_more_videos_than_rollouts() -> None:
    try:
        _request(num_rollouts=2, record_videos=3)
    except ValueError:
        return
    raise AssertionError("invalid evaluation request was accepted")


def test_video_retention_keeps_requested_prefix_and_failures() -> None:
    assert _should_keep_video(index=0, requested_videos=3, success=True)
    assert _should_keep_video(index=8, requested_videos=3, success=False)
    assert not _should_keep_video(index=8, requested_videos=3, success=True)


def test_environment_fingerprint_ignores_render_settings() -> None:
    class _Env:
        def __init__(self, *, offscreen: bool, control_freq: int = 20):
            self.offscreen = offscreen
            self.control_freq = control_freq

        def serialize(self):
            return {
                "env_name": "Lift",
                "env_kwargs": {
                    "has_offscreen_renderer": self.offscreen,
                    "camera_names": ["agentview"],
                    "control_freq": self.control_freq,
                },
            }

    assert _environment_fingerprint(_Env(offscreen=False)) == _environment_fingerprint(
        _Env(offscreen=True)
    )
    assert _environment_fingerprint(_Env(offscreen=False)) != _environment_fingerprint(
        _Env(offscreen=False, control_freq=10)
    )


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
        return [float(ob["step"])]


class _RolloutEnvStub:
    rollout_exceptions = ()

    def __init__(self, success_at: int | None) -> None:
        self.success_at = success_at
        self.steps = 0
        self.seed = None
        self.rng = None

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


def test_rollout_resynchronizes_controller_after_restoring_state() -> None:
    calls: list[str] = []

    class _Array:
        def __init__(self, values):
            self.values = list(values)

        def __setitem__(self, _key, value):
            self.values = [value for _ in self.values]

        def __array__(self, dtype=None):
            return np.asarray(self.values, dtype=dtype)

    class _Sim:
        def __init__(self):
            self.model = type(
                "Model", (), {"opt": type("Opt", (), {"disableflags": 0})()}
            )()
            self.data = type(
                "Data",
                (),
                {
                    "qpos": np.asarray([1.0, 2.0, 3.0]),
                    "ctrl": _Array([4.0]),
                    "qacc_warmstart": _Array([5.0]),
                    "qfrc_applied": _Array([6.0]),
                    "xfrc_applied": _Array([7.0]),
                },
            )()

        def forward(self):
            calls.append("forward")

    sim = _Sim()

    class _PartController:
        qpos_index = [0, 2]
        pass

        def update_initial_joints(self, joints):
            calls.append(f"initial_joints:{np.asarray(joints).tolist()}")

    class _CompositeController:
        part_controllers = {"right": _PartController()}

        def update_state(self):
            calls.append("update_state")

        def reset(self):
            calls.append("reset")

    env = _RolloutEnvStub(success_at=1)
    env.sim = sim
    _PartController.sim = sim
    env.robots = [type("Robot", (), {"composite_controller": _CompositeController()})()]

    _rollout_with_success_tail(
        policy=_PolicyStub(),
        env=env,
        horizon=1,
        success_tail_steps=0,
        video_writer=None,
        video_skip=1,
        camera_names=[],
        initial_state_vector=[0],
    )

    assert calls == ["forward", "update_state", "initial_joints:[1.0, 3.0]", "reset"]
    assert sim.data.ctrl.values == [0]
    assert sim.data.qacc_warmstart.values == [0]
    assert sim.data.qfrc_applied.values == [0]
    assert sim.data.xfrc_applied.values == [0]


@requires_torch
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
        episode_seed=5000,
    )

    assert stats["Success_Rate"] == 1.0
    assert stats["Horizon"] == 5
    assert env.steps == 5
    assert env.seed == 5000
    assert len(stats["initial_state_hash"]) == 64
    assert len(stats["action_hash"]) == 64
    assert stats["first_action"] == [0.0]
    assert stats["action_prefix"] == [[0.0], [1.0], [2.0], [3.0], [4.0]]
    assert len(stats["state_prefix_hashes"]) == 5


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


@requires_torch
def test_same_seed_reproduces_initial_and_action_fingerprints() -> None:
    first = _rollout_with_success_tail(
        policy=_PolicyStub(), env=_RolloutEnvStub(success_at=2), horizon=10,
        success_tail_steps=3, video_writer=None, video_skip=1,
        camera_names=["agentview"], episode_seed=5000,
    )
    second = _rollout_with_success_tail(
        policy=_PolicyStub(), env=_RolloutEnvStub(success_at=2), horizon=10,
        success_tail_steps=3, video_writer=None, video_skip=1,
        camera_names=["agentview"], episode_seed=5000,
    )

    assert first == second


@requires_torch
def test_seeding_mutates_shared_environment_rng_in_place() -> None:
    class _Sampler:
        pass

    env = _RolloutEnvStub(success_at=None)
    env.rng = np.random.default_rng(99)
    sampler = _Sampler()
    sampler.rng = env.rng
    original_rng = env.rng

    from scripts.evaluate_robomimic import _seed_episode

    _seed_episode(5000, env)
    first = sampler.rng.uniform()
    _seed_episode(5000, env)
    second = sampler.rng.uniform()

    assert env.rng is original_rng
    assert sampler.rng is original_rng
    assert first == second


@requires_torch
def test_state_bank_reloads_the_same_saved_states(tmp_path: Path) -> None:
    class _BankEnv(_RolloutEnvStub):
        def serialize(self):
            return {"env_name": "Lift", "version": 1}

        def reset(self):
            self.steps = int(self.rng.integers(1, 1_000_000))
            return {"step": self.steps}

        def get_state(self):
            return {"states": [self.steps]}

    path = tmp_path / "lift_states.npz"
    env = _BankEnv(success_at=None)
    env.rng = np.random.default_rng(0)
    created = _prepare_state_bank(path, env, [5000, 5001], "Lift")

    env.rng = np.random.default_rng(999)
    loaded = _prepare_state_bank(path, env, [5000, 5001], "Lift")

    assert np.array_equal(created[5000], loaded[5000])
    assert np.array_equal(created[5001], loaded[5001])
    assert not np.array_equal(loaded[5000], loaded[5001])


# --- chia rollout cho nhiều process -------------------------------------------


def test_split_gives_every_rollout_exactly_one_owner():
    from src.training.evaluation_jobs import split_rollouts

    chunks = split_rollouts(5000, 20, 3)

    assert sum(size for _, size in chunks) == 20
    seeds = [seed + offset for seed, size in chunks for offset in range(size)]
    assert seeds == list(range(5000, 5020))  # không trùng, không sót


def test_split_never_makes_more_processes_than_rollouts():
    from src.training.evaluation_jobs import split_rollouts

    assert len(split_rollouts(5000, 2, 8)) == 2


def test_merge_recomputes_the_summary_from_the_merged_list():
    from src.training.evaluation_jobs import merge_results

    # Hai phần không bằng nhau: lấy trung bình của các trung bình sẽ ra 0.75,
    # con số đúng là 2/3.
    parts = [
        {"task_name": "Lift", "success_rate": 1.0, "mean_episode_length": 10.0,
         "episodes": [{"seed": 5001, "success": True, "steps": 10}]},
        {"task_name": "Lift", "success_rate": 0.5, "mean_episode_length": 20.0,
         "episodes": [
             {"seed": 5002, "success": True, "steps": 20},
             {"seed": 5000, "success": False, "steps": 20},
         ]},
    ]

    merged = merge_results(parts, requested=3)

    assert merged["success_rate"] == 2 / 3
    assert merged["completed_episodes"] == 3
    assert [item["seed"] for item in merged["episodes"]] == [5000, 5001, 5002]


def test_merge_keeps_what_finished_when_a_process_died():
    from src.training.evaluation_jobs import merge_results

    merged = merge_results(
        [{"task_name": "Lift", "episodes": [{"seed": 5000, "success": True, "steps": 10}]}],
        requested=4,
    )

    assert merged["num_episodes"] == 4  # người dùng đã yêu cầu 4
    assert merged["completed_episodes"] == 1  # nhưng chỉ 1 chạy xong


def test_a_split_run_produces_the_same_episodes_as_a_single_process(tmp_path, monkeypatch):
    """Chia việc là chi tiết vận hành: kết quả phải không đổi."""
    from src.config import get_settings

    script = tmp_path / "fake_evaluate.py"
    script.write_text(_FAKE_EVALUATE, encoding="utf-8")
    checkpoint = tmp_path / "model.pth"
    checkpoint.touch()

    def run(workers: int, root: str):
        monkeypatch.setattr(get_settings(), "evaluation_workers", workers)
        manager = EvaluationJobManager(
            tmp_path / root,
            TrainingJobsStub(checkpoint),  # type: ignore[arg-type]
            repo_root=tmp_path,
            python_executable=sys.executable,
            evaluation_script=script,
        )
        return _wait(manager, manager.submit(_request(num_rollouts=7)).id)

    single = run(1, "one")
    split = run(3, "three")

    assert single.status == split.status == JobStatus.SUCCEEDED
    assert [(e.seed, e.success, e.steps) for e in single.episodes] == [
        (e.seed, e.success, e.steps) for e in split.episodes
    ]
    assert single.success_rate == split.success_rate
