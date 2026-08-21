"""ToolHang: success contract, compat helper, and the failure-record path.

Everything here is simulator-free except `test_collect_runs_one_episode`, which
is opt-in because a real episode takes tens of seconds.
"""

import json
import os

import pytest

from src.labeling.features import OBJECT_LAYOUT
from src.sim.skillgen.compat import GRIP_SITE_NAMES, grip_site_id
from src.sim.tool_hang import (
    TOOLHANG_TOOL_NAME,
    tool_hang_metadata,
    tool_hang_stage1_success,
    tool_hang_stage2_success,
    tool_hang_success,
)


class FakeModel:
    def __init__(self, *site_names):
        self.site_names = tuple(site_names)

    def site_name2id(self, name):
        return self.site_names.index(name)


class FakeEnv:
    """Only the two robosuite predicates the success contract reads."""

    def __init__(self, *, frame_assembled, tool_on_frame):
        self._frame = frame_assembled
        self._tool = tool_on_frame

    def _check_frame_assembled(self):
        return self._frame

    def _check_tool_on_frame(self):
        return self._tool


# --- compat helper ----------------------------------------------------------


@pytest.mark.parametrize("name", GRIP_SITE_NAMES)
def test_grip_site_resolves_either_name(name):
    model = FakeModel("some_other_site", name)
    assert grip_site_id(model) == 1


def test_grip_site_raises_when_neither_name_exists():
    model = FakeModel("gripper0_finger_site")
    with pytest.raises(RuntimeError) as error:
        grip_site_id(model)
    for name in GRIP_SITE_NAMES:
        assert name in str(error.value)


# --- success contract -------------------------------------------------------


@pytest.mark.parametrize(
    ("frame", "tool", "stage1", "stage2", "full"),
    [
        (False, False, False, False, False),
        (True, False, True, False, False),
        (False, True, False, True, False),
        (True, True, True, True, True),
    ],
)
def test_success_contract_uses_the_env_predicates(frame, tool, stage1, stage2, full):
    env = FakeEnv(frame_assembled=frame, tool_on_frame=tool)
    assert tool_hang_stage1_success(env) is stage1
    assert tool_hang_stage2_success(env) is stage2
    assert tool_hang_success(env) is full


def test_metadata_describes_the_full_task():
    metadata = tool_hang_metadata()
    assert metadata["task"] == "tool_hang"
    assert metadata["stage"] == "stage1+stage2"
    assert "stage2_success" in metadata


def test_tool_name_still_maps_back_to_the_task():
    # features.py resolves a demo's task from tool_name; renaming this constant
    # would orphan every dataset already collected under the old name.
    from src.labeling.features import _task_of

    class Group:
        def __init__(self, **attrs):
            self.attrs = attrs

    assert TOOLHANG_TOOL_NAME == "tool_hang_stage1"
    resolved = _task_of(Group(), Group(tool_name=TOOLHANG_TOOL_NAME), None)
    assert resolved == "tool_hang"


def test_object_layout_slices_stay_within_the_frame_pose():
    # The `object` observation grew from 7 to 14 floats (frame pose then tool
    # pose). Frame-first is what keeps these slices correct.
    layout = OBJECT_LAYOUT["tool_hang"]
    assert layout["position"] == (0, 3)
    assert layout["orientation"] == (3, 7)


def test_native_object_layout_reads_the_frame_pose_from_44_columns():
    from src.labeling.features import _object_layout

    layout = _object_layout("tool_hang", 44)
    assert layout is not None
    assert layout["position"] == (21, 24)
    assert layout["orientation"] == (24, 28)


# --- episode summary and provenance ----------------------------------------


def _outcome(*, stage1_ok, stage2_ok, stage2_failure=None, failure=None):
    class Result:
        success = True
        steps = 120
        attempts = 1
        final_lateral_mm = 3.0
        final_depth_mm = 131.0
        wall_time_s = 12.5

    Result.failure = failure
    Result.failed_phase = None if failure is None else "insert"
    return {
        "result": Result(),
        "stage1_ok": stage1_ok,
        "stage2_ok": stage2_ok,
        "stage2_failure": stage2_failure,
        "success": stage1_ok and stage2_ok,
    }


def test_episode_summary_keeps_the_geometric_success_as_a_diagnostic():
    from src.sim.tool_hang_collection import _episode_summary

    knobs = {"frame_extra": 0.04, "tool_extra": 0.04, "yaw_extra": 0.35, "max_attempts": 1}
    summary = _episode_summary(_outcome(stage1_ok=False, stage2_ok=False), 7, knobs)
    # The skill's geometric predicate said success while the simulator did not:
    # exactly the divergence the accept gate must not follow.
    assert summary["stage1_geometric_success"] is True
    assert summary["stage1_env_predicate"] is False
    assert summary["seed"] == 7
    assert summary["frame_extra"] == 0.04


def test_episode_provenance_serialises_the_summary_into_attrs():
    from src.sim.perturbations.collection import EpisodeProvenance
    from src.sim.tool_hang_collection import _episode_summary

    knobs = {"frame_extra": 0.0, "tool_extra": 0.0, "yaw_extra": 0.0, "max_attempts": 1}
    summary = _episode_summary(
        _outcome(stage1_ok=True, stage2_ok=False, stage2_failure="thread_failed"), 2, knobs,
    )
    attrs = EpisodeProvenance(
        task="tool_hang", tool_name=TOOLHANG_TOOL_NAME, requested_quality="clean",
        profile_version="p", candidate_profile_version="p", noise_scale=0.0,
        base_seed=0, task_code=4, stream_code=1, episode_index=2, environment_seed=2,
        sampled_variation=summary, outcome="failure", success=False,
        episode_length=120, terminal_reason="thread_failed",
        failure_stage="thread_failed", terminal_phase="thread",
    ).as_attrs()
    assert attrs["telecollect_success"] is False
    assert attrs["telecollect_failure_stage"] == "thread_failed"
    assert attrs["telecollect_terminal_phase"] == "thread"
    variation = json.loads(attrs["telecollect_sampled_variation"])
    assert variation["stage2_failure"] == "thread_failed"
    assert variation["stage2_tool_on_frame"] is False


# --- failures reach the collection result ----------------------------------


def test_run_collection_reports_failed_tool_hang_episodes(monkeypatch, tmp_path):
    """Failed episodes must appear as records, not be counted away."""

    from src.sim import scripted_generation

    collected = {
        "episodes": 2,
        "successes": 1,
        "failed": 1,
        "output": str(tmp_path / "out.hdf5"),
        "trace_output": str(tmp_path / "out.hdf5.trace.jsonl"),
        "records": [
            {
                "episode_index": 0, "seed": 0, "success": True, "steps": 1724,
                "terminal_reason": "success", "terminal_phase": "done",
                "failure_stage": None, "summary": {"seed": 0},
            },
            {
                "episode_index": 1, "seed": 1, "success": False, "steps": 431,
                "terminal_reason": "grasp_missed", "terminal_phase": "close_gripper",
                "failure_stage": "grasp_missed", "summary": {"seed": 1},
            },
        ],
    }
    monkeypatch.setattr(
        "src.sim.tool_hang_collection.collect", lambda *a, **k: collected, raising=False,
    )
    result = scripted_generation.run_collection(
        "tool_hang", episodes=2, seed=0, output=tmp_path / "out.hdf5",
    )
    assert len(result.episodes) == 2
    assert result.success_count == 1
    failed = result.episodes[1]
    assert failed.success is False
    assert failed.outcome == "failure"
    assert failed.steps == 431
    assert failed.termination_reason == "grasp_missed"
    assert failed.provenance.failure_stage == "grasp_missed"
    assert failed.provenance.terminal_phase == "close_gripper"


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (_outcome(stage1_ok=True, stage2_ok=True), None),
        (_outcome(stage1_ok=False, stage2_ok=False, failure="grasp_missed"), "grasp_missed"),
        (
            _outcome(stage1_ok=True, stage2_ok=False, stage2_failure="thread_failed"),
            "thread_failed",
        ),
        # Both observed in real sweeps: the skill reports no failure while
        # robosuite's predicate still says the stage is not done.
        (_outcome(stage1_ok=False, stage2_ok=False), "stage1_predicate_failed"),
        (_outcome(stage1_ok=True, stage2_ok=False), "stage2_predicate_failed"),
    ],
)
def test_failure_kind_never_leaves_a_failure_unnamed(outcome, expected):
    from src.sim.tool_hang_collection import _failure_kind

    assert _failure_kind(outcome) == expected


def test_run_collection_accepts_non_clean_tool_hang_quality():
    from src.sim import scripted_generation

    result = scripted_generation.run_collection(
        "tool_hang", episodes=1, seed=0, quality="good", dry_run=True,
    )
    assert result.quality == "good"
    assert result.provenance.noise_scale == 0.20
    assert result.provenance.profile_version == "toolhang-candidate-v1"


# --- opt-in integration -----------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("TELECOLLECT_RUN_SIM_TESTS"),
    reason="runs a real ToolHang episode (~15 s) - set TELECOLLECT_RUN_SIM_TESTS=1 to run",
)
def test_collect_runs_one_episode(tmp_path):
    import h5py

    from src.sim.tool_hang_collection import collect

    output = tmp_path / "toolhang.hdf5"
    result = collect(output, episodes=1, seed=3, frame_extra=0.04,
                     tool_extra=0.04, yaw_extra=0.35, logger=lambda _message: None)
    assert result["episodes"] == 1
    assert output.exists()
    trace_path = tmp_path / "toolhang.hdf5.trace.jsonl"
    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    with h5py.File(output, "r") as handle:
        demo = handle["data"]["demo_0"]
        # The trace is 1:1 with the control steps, and `object` is RoboSuite's
        # native ToolHang object-state so RoboMimic rollout uses the same input.
        assert demo["obs"]["object"].shape[1] == 44
        assert demo["next_obs"]["object"].shape[1] == 44
        assert len(json.loads(lines[0])["trace"]) == demo.attrs["num_samples"]


@pytest.mark.skipif(
    not os.environ.get("TELECOLLECT_RUN_SIM_TESTS"),
    reason="builds real ToolHang environments (~10 s) - set TELECOLLECT_RUN_SIM_TESTS=1 to run",
)
def test_same_seed_reproduces_the_same_settled_scene():
    """A seed must select one scene, whatever happened to the env beforehand.

    This is pinned at the reset rather than at the whole episode on purpose:
    the reset is where the only randomness in the stage1+stage2 path lives, and
    an 80-step settle costs seconds where an episode costs ~25 s. Everything
    downstream is deterministic given this state, which is why the measured
    episode-level reproducibility (five runs of seed 3 -> one step count, a
    10-seed sweep repeated three times -> bit-identical) follows from it.

    The property held for none of these cases before `compat.seed_env`: on
    robosuite 1.5 the placement samplers and the robot's initialisation noise
    draw from `env.rng`, which `np.random.seed` cannot reach.
    """
    import hashlib

    import numpy as np

    from src.sim.skillgen.env_setup import make_env, reset_and_settle

    wide = {"frame_extra": 0.04, "tool_extra": 0.04, "yaw_extra": 0.35}

    def settled(env, seed):
        reset_and_settle(env, seed=seed)
        data = env.sim.data
        blob = (np.asarray(data.qpos, dtype=np.float64).tobytes()
                + np.asarray(data.qvel, dtype=np.float64).tobytes())
        return hashlib.sha1(blob).hexdigest()

    seed_3 = []
    for _ in range(2):
        env = make_env(horizon=200000, **wide)
        try:
            seed_3.append(settled(env, 3))
        finally:
            env.close()

    env = make_env(horizon=200000, **wide)
    try:
        seed_3.append(settled(env, 3))
        # Neither another episode's placement nor arbitrary motion may survive
        # into the next reset.
        seed_2 = settled(env, 2)
        seed_3.append(settled(env, 3))
        rng = np.random.default_rng(0)
        for _ in range(50):
            env.step(rng.uniform(-1.0, 1.0, size=env.action_dim))
        seed_3.append(settled(env, 3))
    finally:
        env.close()

    assert len(set(seed_3)) == 1, "same seed produced different settled scenes"
    # ...and the seed must still be doing something.
    assert seed_2 != seed_3[0]
