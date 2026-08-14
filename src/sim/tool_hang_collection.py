"""Robomimic-compatible scripted collection for the full ToolHang task.

One episode runs stage 1 (stand the hook frame up in the stand) and then, if
stage 1 succeeded, stage 2 (thread the wrench onto the hook).  Both stages share
one recorder, so the exported trajectory is a single continuous demonstration.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import mujoco
import numpy as np

from src.sim.collection.robomimic_hdf5_writer import EpisodeData, RobomimicHDF5Writer
from src.sim.perturbations.collection import DatasetProvenance, EpisodeProvenance
from src.sim.skillgen.compat import grip_site_id
from src.sim.tool_hang import (
    TOOLHANG_PROFILE,
    TOOLHANG_TASK_CODE,
    TOOLHANG_TOOL_NAME,
    make_tool_hang_environment,
    tool_hang_stage1_success,
    tool_hang_stage2_success,
)

TOOL_NAME = TOOLHANG_TOOL_NAME


@contextmanager
def environment(
    *,
    render: bool = False,
    seed: int = 0,
    frame_extra: float = 0.0,
    tool_extra: float = 0.0,
    yaw_extra: float = 0.0,
    offscreen: bool = False,
) -> Iterator[Any]:
    """Build the ToolHang environment.

    `frame_extra` / `tool_extra` widen the frame and wrench placement boxes by
    that many metres per side, `yaw_extra` widens both rotations by that many
    radians per side.  The defaults leave robosuite's own ranges untouched, but
    those ranges are narrow enough that every seed converges to the same
    solution; 0.04 / 0.04 / 0.35 is the wide range the skill was measured at and
    the one to use when trajectory diversity matters.
    """

    env = make_tool_hang_environment(
        render=render,
        control_freq=20,
        horizon=6000,
        seed=seed,
        offscreen=offscreen,
        frame_extra=frame_extra,
        tool_extra=tool_extra,
        yaw_extra=yaw_extra,
    )
    try:
        yield env
    finally:
        env.close()


def _observation(env: Any, state: np.ndarray) -> dict[str, np.ndarray]:
    env.sim.set_state_from_flattened(state)
    env.sim.forward()
    data = env.sim.data
    robot = env.robots[0]
    joint_ids = np.asarray(robot._ref_joint_pos_indexes, dtype=int)
    gripper_ids = np.asarray(robot._ref_gripper_joint_pos_indexes["right"], dtype=int)
    eef_id = grip_site_id(env.sim.model)
    eef_quat = np.zeros(4, dtype=np.float64)
    mujoco.mju_mat2Quat(eef_quat, data.site_xmat[eef_id])
    frame_id = env.sim.model.body_name2id("frame_root")
    tool_id = env.sim.model.body_name2id("tool_root")
    joint_pos = np.asarray(data.qpos[joint_ids]).copy()
    # Both stages' objects, frame first: stage 1 manipulates the hook frame and
    # stage 2 the wrench, so a frame-only `object` leaves half the task
    # unobservable. Frame first keeps src/labeling/features.py's tool_hang
    # position/orientation slices (0:3, 3:7) valid.
    obj = np.concatenate((
        np.asarray(data.body_xpos[frame_id]).copy(),
        np.asarray(data.xquat[frame_id]).copy(),
        np.asarray(data.body_xpos[tool_id]).copy(),
        np.asarray(data.xquat[tool_id]).copy(),
    ))
    return {
        "robot0_eef_pos": np.asarray(data.site_xpos[eef_id]).copy(),
        "robot0_eef_quat": eef_quat,
        "robot0_eef_quat_site": eef_quat.copy(),
        "robot0_gripper_qpos": np.asarray(data.qpos[gripper_ids]).copy(),
        "robot0_gripper_qvel": np.asarray(data.qvel[gripper_ids]).copy(),
        "robot0_joint_pos": joint_pos,
        "robot0_joint_pos_cos": np.cos(joint_pos),
        "robot0_joint_pos_sin": np.sin(joint_pos),
        "robot0_joint_vel": np.asarray(data.qvel[joint_ids]).copy(),
        "object": obj,
    }


class _CaptureViewer:
    """Adapter that turns Stage1's viewer hook into a video capture.

    `Stage1` calls `viewer.draw(telemetry)` on every control step of both
    stages and only aborts when that returns False, so this records the whole
    two-stage episode without touching the vendored skill.
    """

    def __init__(self, recorder: Any) -> None:
        self._recorder = recorder

    def draw(self, _telemetry: Any) -> None:
        self._recorder.capture()


def _run_episode(
    env: Any,
    seed: int,
    *,
    max_attempts: int,
    recorder: Any = None,
) -> dict[str, Any]:
    """Run stage 1 and, if it succeeded, stage 2. Returns the episode's outcome."""

    from src.sim.skillgen.stage1 import Stage1
    from src.sim.skillgen.stage2 import Stage2

    viewer = None if recorder is None else _CaptureViewer(recorder)
    skill = Stage1(
        env, viewer=viewer, max_attempts=max_attempts, max_steps=6000, collect=True,
    )
    result = skill.run(seed)
    # Gate on the simulator's own predicate, never on the skill's geometric
    # `result.success`: that one accepts a rod jammed at 40 mm where correct
    # assembly needs ~130 mm. It is kept below purely as a diagnostic.
    stage1_ok = tool_hang_stage1_success(env)
    stage2_failure: str | None = None
    stage2_ok = False
    if stage1_ok:
        # Stage 2 appends to stage 1's recorder, so the trajectory below is the
        # concatenation of both stages with no splicing on our side.
        stage2_failure = Stage2(skill, rng_seed=seed).run()
        stage2_ok = tool_hang_stage2_success(env)
    return {
        "skill": skill,
        "result": result,
        "stage1_ok": stage1_ok,
        "stage2_ok": stage2_ok,
        "stage2_failure": stage2_failure,
        "success": stage1_ok and stage2_ok,
    }


def _failure_kind(outcome: dict[str, Any]) -> str | None:
    """The failure to record, or None when the full task succeeded."""

    if outcome["success"]:
        return None
    if outcome["stage1_ok"]:
        # Stage 2 ran; its own failure kind is the honest answer when it has one.
        return outcome["stage2_failure"] or "stage2_predicate_failed"
    # A stage can report no failure while robosuite's predicate still says the
    # task is not done -- the same divergence that makes the skill's geometric
    # `success` unusable as a label. Name which stage rather than record an
    # unexplained failure.
    return outcome["result"].failure or "stage1_predicate_failed"


def _episode_summary(outcome: dict[str, Any], seed: int, knobs: dict[str, float]) -> dict[str, Any]:
    """Per-episode diagnostics small enough to live in the HDF5 attrs."""

    result = outcome["result"]
    return {
        "seed": seed,
        "stage1_env_predicate": outcome["stage1_ok"],
        "stage1_geometric_success": bool(result.success),
        "stage1_failure": result.failure,
        "stage1_failed_phase": result.failed_phase,
        "stage1_steps": int(result.steps),
        "stage1_attempts": int(result.attempts),
        "stage2_reached": outcome["stage1_ok"],
        "stage2_tool_on_frame": outcome["stage2_ok"],
        "stage2_failure": outcome["stage2_failure"],
        "final_lateral_mm": float(result.final_lateral_mm),
        "final_depth_mm": float(result.final_depth_mm),
        "wall_time_s": float(result.wall_time_s),
        **knobs,
    }


def collect(
    output: str | Path,
    *,
    episodes: int,
    seed: int,
    overwrite: bool = False,
    logger: Any = print,
    max_attempts: int = 1,
    frame_extra: float = 0.0,
    tool_extra: float = 0.0,
    yaw_extra: float = 0.0,
    video_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Collect ToolHang episodes, successes and failures alike.

    `max_attempts` defaults to 1 because retries make the reported failure
    dishonest: `failure` names only the last attempt's error, and each retry
    rotates the wrist another 90 degrees, so a third attempt fails in ways the
    first never met.  See `environment()` for the difficulty knobs.

    Passing `video_dir` writes each episode's three-pane review mp4 as the
    rollout happens, named `<demo>.mp4` so the review workspace finds it as a
    cached render.  Without it the video is rebuilt later from the recorded
    states by `src/labeling/playback.py`, which costs a second pass.
    """

    knobs = {
        "frame_extra": float(frame_extra),
        "tool_extra": float(tool_extra),
        "yaw_extra": float(yaw_extra),
        "max_attempts": int(max_attempts),
    }
    provenance = DatasetProvenance(
        task="tool_hang",
        tool_name=TOOL_NAME,
        requested_quality="clean",
        profile_version=TOOLHANG_PROFILE,
        candidate_profile_version=TOOLHANG_PROFILE,
        acceptance_amendment="full-task-env-predicate-gate",
        noise_scale=0.0,
        base_seed=seed,
        task_code=TOOLHANG_TASK_CODE,
        stream_code=1,
        coverage="stage1+stage2",
        position_landmarks=("frame_pos", "tool_pos"),
        orientation_landmarks=("frame_quat", "tool_quat"),
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Provisional: the per-step trace is genuinely per-step data and belongs in
    # the HDF5 beside actions and states. Putting it there means changing
    # RobomimicHDF5Writer, which lift/can/square share, and the scoring work that
    # will consume the trace is deferred. Take the zero-risk option now and let
    # that later work choose the permanent home.
    trace_path = Path(str(output) + ".trace.jsonl")
    records: list[dict[str, Any]] = []
    kept = 0
    successes = 0
    videos: list[str] = []
    if video_dir is not None:
        video_dir = Path(video_dir)
        video_dir.mkdir(parents=True, exist_ok=True)
    with environment(
        seed=seed, frame_extra=frame_extra, tool_extra=tool_extra, yaw_extra=yaw_extra,
        # Capturing review frames needs the offscreen renderer; without a video
        # to write there is no reason to pay for building it.
        offscreen=video_dir is not None,
    ) as env:
        with RobomimicHDF5Writer(
            output,
            {
                "env_name": "ToolHang",
                "type": 1,
                "env_kwargs": {
                    "robots": "Panda",
                    "control_freq": 20,
                    "horizon": 6000,
                    "stage": "stage1+stage2",
                },
            },
            overwrite=overwrite,
            collection_metadata=provenance.as_attrs(),
        ) as writer:
            with trace_path.open("w", encoding="utf-8") as trace_file:
                for index in range(episodes):
                    episode_seed = seed + index
                    recorder = None
                    if video_dir is not None:
                        from src.labeling.playback import DEFAULT_PLAYBACK, RolloutRecorder

                        recorder = RolloutRecorder(env, DEFAULT_PLAYBACK)
                    outcome = _run_episode(
                        env, episode_seed, max_attempts=max_attempts, recorder=recorder,
                    )
                    skill = outcome["skill"]
                    result = outcome["result"]
                    success = outcome["success"]
                    summary = _episode_summary(outcome, episode_seed, knobs)
                    failure = _failure_kind(outcome)
                    terminal_reason = "success" if success else failure
                    terminal_phase = "done" if success else (skill.phase or "unknown")
                    logger(
                        f"episode={index} seed={episode_seed} success={success} "
                        f"stage1={outcome['stage1_ok']} stage2={outcome['stage2_ok']} "
                        f"failure={failure} steps={len(skill.record or [])}"
                    )
                    if not skill.record:
                        # Nothing was recorded, so there is no trajectory to write.
                        # Only reachable when a stage aborts before its first
                        # control step (e.g. no_holdable_face).
                        continue
                    states = [np.asarray(item[0], dtype=np.float64) for item in skill.record]
                    actions = [np.asarray(item[1], dtype=np.float64) for item in skill.record]
                    next_states = states[1:] + [np.asarray(env.sim.get_state().flatten())]
                    episode = EpisodeData(
                        # The skill captured this at its own reset, which is when
                        # the placement sampler was rolled; it is the XML these
                        # states belong to.
                        model_xml=skill.model_xml,
                        success=success,
                        termination_reason=terminal_reason,
                        seed=episode_seed,
                        tool_name=TOOL_NAME,
                        operator_version="toolhang-stage1-stage2-robosuite-1.5.2",
                    )
                    for position, (state, next_state, action) in enumerate(
                        zip(states, next_states, actions)
                    ):
                        terminal = position == len(actions) - 1
                        episode.append(
                            state=state,
                            observation=_observation(env, state),
                            action=action,
                            reward=1.0 if terminal and success else 0.0,
                            done=False,
                            next_observation=_observation(env, next_state),
                        )
                    episode.dones[-1] = True
                    item_provenance = EpisodeProvenance(
                        task="tool_hang", tool_name=TOOL_NAME, requested_quality="clean",
                        profile_version=TOOLHANG_PROFILE,
                        candidate_profile_version=TOOLHANG_PROFILE, noise_scale=0.0,
                        base_seed=seed, task_code=TOOLHANG_TASK_CODE, stream_code=1,
                        episode_index=index, environment_seed=episode_seed,
                        sampled_variation=summary,
                        outcome="success" if success else "failure",
                        success=success, episode_length=len(actions),
                        terminal_reason=terminal_reason,
                        failure_stage=None if success else failure,
                        terminal_phase=terminal_phase,
                    )
                    writer.write_episode(episode, provenance=item_provenance.as_attrs())
                    trace_file.write(json.dumps({
                        "episode_index": index,
                        "seed": episode_seed,
                        "success": success,
                        "trace": skill.trace,
                    }) + "\n")
                    if recorder is not None:
                        # `write_episode` names demos in write order, so this
                        # episode is `demo_<kept>` — the key `video_filename`
                        # builds the cached review video's name from.
                        target = video_dir / f"{output.stem}__demo_{kept}.mp4"
                        try:
                            if recorder.write(target) is not None:
                                videos.append(str(target))
                        except Exception as error:  # noqa: BLE001
                            # The trajectory is the artefact that matters; a
                            # failed encode falls back to the replay path.
                            logger(f"video failed for demo_{kept}: {error}")
                    kept += 1
                    successes += int(success)
                    records.append({
                        "episode_index": index,
                        "seed": episode_seed,
                        "success": success,
                        "steps": len(actions),
                        "terminal_reason": terminal_reason,
                        "terminal_phase": terminal_phase,
                        "failure_stage": None if success else failure,
                        "summary": summary,
                    })
    return {
        "episodes": kept,
        "successes": successes,
        "failed": kept - successes,
        "output": str(output),
        "trace_output": str(trace_path),
        "records": records,
        "videos": videos,
    }
