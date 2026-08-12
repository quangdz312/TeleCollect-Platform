"""Robomimic-compatible scripted collection for ToolHang Stage 1."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import mujoco

from src.sim.collection.robomimic_hdf5_writer import EpisodeData, RobomimicHDF5Writer
from src.sim.perturbations.collection import DatasetProvenance, EpisodeProvenance
from src.sim.tool_hang import make_tool_hang_environment, project_root


TOOL_NAME = "tool_hang_stage1"
TOOLHANG_TASK_CODE = 4
TOOLHANG_PROFILE = "tool-hang-stage1-baseline"


@contextmanager
def environment(*, render: bool = False, seed: int = 0) -> Iterator[Any]:
    env = make_tool_hang_environment(
        render=render,
        control_freq=20,
        horizon=6000,
        seed=seed,
        offscreen=False,
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
    eef_id = env.sim.model.site_name2id("gripper0_right_grip_site")
    eef_quat = np.zeros(4, dtype=np.float64)
    mujoco.mju_mat2Quat(eef_quat, data.site_xmat[eef_id])
    frame_id = env.sim.model.body_name2id("frame_root")
    frame_pos = np.asarray(data.body_xpos[frame_id]).copy()
    frame_quat = np.asarray(data.xquat[frame_id]).copy()
    joint_pos = np.asarray(data.qpos[joint_ids]).copy()
    # Stage 1's frame is the object being manipulated. Keep the standard
    # robomimic observation keys so the existing review and video pipeline can
    # consume ToolHang without a second dataset format.
    obj = np.concatenate((frame_pos, frame_quat))
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


def collect(
    output: str | Path,
    *,
    episodes: int,
    seed: int,
    overwrite: bool = False,
    logger: Any = print,
) -> dict[str, Any]:
    """Collect only clean Stage-1 episodes and publish the shared HDF5 schema."""

    provenance = DatasetProvenance(
        task="tool_hang",
        tool_name=TOOL_NAME,
        requested_quality="clean",
        profile_version=TOOLHANG_PROFILE,
        candidate_profile_version=TOOLHANG_PROFILE,
        acceptance_amendment="stage1-geometric-gate",
        noise_scale=0.0,
        base_seed=seed,
        task_code=TOOLHANG_TASK_CODE,
        stream_code=1,
        coverage="stage1-only",
        position_landmarks=("frame_pos",),
        orientation_landmarks=("frame_quat",),
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    kept = 0
    failed = 0
    with environment(seed=seed) as env:
        with _skillgen_stage1() as Stage1:
            with RobomimicHDF5Writer(
                output,
                {
                    "env_name": "ToolHang",
                    "type": 1,
                    "env_kwargs": {
                        "robots": "Panda",
                        "control_freq": 20,
                        "horizon": 6000,
                        "stage": 1,
                    },
                },
                overwrite=overwrite,
                collection_metadata=provenance.as_attrs(),
            ) as writer:
                for index in range(episodes):
                    episode_seed = seed + index
                    skill = Stage1(env, max_attempts=2, max_steps=6000, collect=True)
                    result = skill.run(episode_seed)
                    if not result.success or not skill.record:
                        failed += 1
                        logger(f"episode={index} seed={episode_seed} success=False")
                        continue
                    states = [np.asarray(item[0], dtype=np.float64) for item in skill.record]
                    actions = [np.asarray(item[1], dtype=np.float64) for item in skill.record]
                    next_states = states[1:] + [np.asarray(env.sim.get_state().flatten())]
                    episode = EpisodeData(
                        model_xml=env.sim.model.get_xml(),
                        success=True,
                        termination_reason="success",
                        seed=episode_seed,
                        tool_name=TOOL_NAME,
                        operator_version="toolhang-stage1-robosuite-1.5.2",
                    )
                    for state, next_state, action in zip(states, next_states, actions):
                        episode.append(
                            state=state,
                            observation=_observation(env, state),
                            action=action,
                            reward=1.0 if next_state is next_states[-1] else 0.0,
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
                        outcome="success", success=True, episode_length=len(actions),
                        terminal_reason="success", terminal_phase="stage1_done",
                    )
                    writer.write_episode(episode, provenance=item_provenance.as_attrs())
                    kept += 1
                    logger(f"episode={index} seed={episode_seed} success=True")
    return {"episodes": kept, "successes": kept, "failed": failed, "output": str(output)}


@contextmanager
def _skillgen_stage1() -> Iterator[Any]:
    import sys

    root = str(project_root())
    sys.path.insert(0, root)
    try:
        from skillgen.stage1 import Stage1

        yield Stage1
    finally:
        try:
            sys.path.remove(root)
        except ValueError:
            pass
