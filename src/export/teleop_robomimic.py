"""Convert a recorded manual-teleop directory into RoboMimic arrays.

The recorder stores observation ``i`` beside the action applied at ``i``.
Observation ``i + 1`` is therefore the only truthful next observation; the
last recorded action is intentionally omitted because its successor was not
recorded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from src.sim.tasks import get_task, parse_model_path


@dataclass(frozen=True)
class TeleopRoboMimicEpisode:
    episode_id: str
    task_name: str
    actions: np.ndarray
    states: np.ndarray
    rewards: np.ndarray
    dones: np.ndarray
    observations: dict[str, np.ndarray]
    next_observations: dict[str, np.ndarray]
    attrs: dict[str, Any]
    env_args: dict[str, Any]

    @property
    def num_samples(self) -> int:
        return len(self.actions)


def _matrix(table: Any, column: str, *, width: int | None = None) -> np.ndarray:
    if column not in table.column_names:
        raise ValueError(f"actions.parquet thiếu cột bắt buộc {column!r}")
    values = table[column].to_pylist()
    if any(value is None for value in values):
        raise ValueError(f"Cột {column!r} chứa giá trị null")
    try:
        result = np.asarray(values, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Cột {column!r} không có shape nhất quán") from exc
    if result.ndim != 2 or (width is not None and result.shape[1] != width):
        expected = f"(*, {width})" if width is not None else "ma trận 2 chiều"
        raise ValueError(f"Cột {column!r} phải có shape {expected}, nhận {result.shape}")
    if not np.all(np.isfinite(result)):
        raise ValueError(f"Cột {column!r} chứa NaN hoặc Inf")
    return result


def _observations(
    qpos: np.ndarray,
    qvel: np.ndarray,
    ee_pose: np.ndarray,
    object_state: np.ndarray,
) -> dict[str, np.ndarray]:
    joint_pos = qpos[:, :7]
    # Recorder stores MuJoCo quaternions as wxyz. RoboSuite's regular EEF key
    # is xyzw, while the explicit site key used by this project stays wxyz.
    site_quat = ee_pose[:, 3:7]
    return {
        "object": object_state.astype(np.float32),
        "robot0_eef_pos": ee_pose[:, :3].astype(np.float32),
        "robot0_eef_quat": site_quat[:, [1, 2, 3, 0]].astype(np.float32),
        "robot0_eef_quat_site": site_quat.astype(np.float32),
        "robot0_gripper_qpos": qpos[:, 7:9].astype(np.float32),
        "robot0_gripper_qvel": qvel[:, 7:9].astype(np.float32),
        "robot0_joint_pos": joint_pos.astype(np.float32),
        "robot0_joint_pos_cos": np.cos(joint_pos).astype(np.float32),
        "robot0_joint_pos_sin": np.sin(joint_pos).astype(np.float32),
        "robot0_joint_vel": qvel[:, :7].astype(np.float32),
    }


def _object_observations(
    states: np.ndarray,
    ee_pose: np.ndarray,
    task_name: str,
    seed: int | None,
) -> np.ndarray:
    """Decode RoboSuite object-state from the recorded privileged simulator states."""

    if task_name == "lift_cube":
        # Lift's flattened MuJoCo state is [time, robot qpos(9), cube free
        # joint(7), qvel...]. RoboSuite object-state is cube position,
        # quaternion in xyzw order, then gripper-to-cube displacement.
        if states.shape[1] < 17:
            raise ValueError("Privileged state Lift không đủ phần tử để giải mã cube")
        cube_pos = states[:, 10:13]
        cube_quat_xyzw = states[:, [14, 15, 16, 13]]
        return np.concatenate((cube_pos, cube_quat_xyzw, cube_pos - ee_pose[:, :3]), axis=1)

    from src.sim.can_env import make_can_environment
    from src.sim.lift_env import make_lift_environment
    from src.sim.square_env import make_square_environment

    factories = {
        "lift_cube": make_lift_environment,
        "pick_place_can": make_can_environment,
        "nut_assembly_square": make_square_environment,
    }
    factory = factories.get(task_name)
    if factory is None:
        raise ValueError(f"Task {task_name!r} chưa hỗ trợ giải mã object observation Teleop")
    env = factory(render=False, seed=seed)
    values: list[np.ndarray] = []
    try:
        env.reset()
        for state in states:
            env.sim.set_state_from_flattened(np.asarray(state, dtype=np.float64).copy())
            env.sim.forward()
            raw = env._get_observations()
            value = raw.get("object", raw.get("object-state"))
            if value is None:
                raise ValueError(f"RoboSuite không trả object-state cho task {task_name!r}")
            values.append(np.asarray(value, dtype=np.float32).copy())
    finally:
        env.close()
    try:
        result = np.stack(values)
    except ValueError as exc:
        raise ValueError("Object observation không có shape nhất quán") from exc
    if result.ndim != 2 or not np.all(np.isfinite(result)):
        raise ValueError("Object observation không hợp lệ")
    return result


def load_teleop_episode(
    episode_dir: str | Path,
    *,
    trim_start_s: float | None = None,
    trim_end_s: float | None = None,
    successful: bool | None = None,
) -> TeleopRoboMimicEpisode:
    """Load, validate and align one schema-v2 manual recording."""
    root = Path(episode_dir)
    try:
        meta = json.loads((root / "meta.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Không đọc được metadata của episode {root.name}") from exc
    table_path = root / "actions.parquet"
    if not table_path.is_file():
        raise ValueError(f"Episode {root.name} thiếu actions.parquet")
    table = pq.read_table(table_path)
    if table.num_rows < 2:
        raise ValueError("Episode cần ít nhất 2 observation để tạo một transition")

    task_name = str(meta.get("task_name", ""))
    try:
        spec = get_task(task_name)
    except KeyError as exc:
        raise ValueError(f"Task {task_name!r} không có trong registry") from exc
    env_name, robot = parse_model_path(spec.model_path)

    if "t" not in table.column_names:
        raise ValueError("actions.parquet thiếu cột bắt buộc 't'")
    timestamps = np.asarray(table["t"].to_pylist(), dtype=np.float64)
    if timestamps.shape != (table.num_rows,) or not np.all(np.isfinite(timestamps)):
        raise ValueError("Cột 't' không hợp lệ")
    if np.any(np.diff(timestamps) <= 0):
        raise ValueError("Timestamp phải tăng nghiêm ngặt")

    start = -np.inf if trim_start_s is None else float(trim_start_s)
    end = np.inf if trim_end_s is None else float(trim_end_s)
    if end <= start:
        raise ValueError("Khoảng trim không hợp lệ")
    indexes = np.flatnonzero((timestamps >= start) & (timestamps <= end))
    if len(indexes) < 2:
        raise ValueError("Khoảng trim cần ít nhất 2 observation")
    if np.any(np.diff(indexes) != 1):
        raise ValueError("Khoảng trim phải tạo thành một đoạn liên tục")
    rows = slice(int(indexes[0]), int(indexes[-1]) + 1)

    qpos = _matrix(table, "qpos")[rows]
    qvel = _matrix(table, "qvel")[rows]
    ee_pose = _matrix(table, "ee_pose", width=7)[rows]
    actions = _matrix(table, "action", width=7)[rows]
    states = _matrix(table, "privileged_state")[rows]
    if "object" in table.column_names:
        object_state = _matrix(table, "object")[rows]
    else:
        object_state = _object_observations(states, ee_pose, task_name, meta.get("seed"))
    if qpos.shape[1] != 9 or qvel.shape[1] != 9:
        raise ValueError(f"Panda qpos/qvel phải có 9 phần tử, nhận {qpos.shape[1]}/{qvel.shape[1]}")
    quat_norm = np.linalg.norm(ee_pose[:, 3:7], axis=1)
    if np.any(np.abs(quat_norm - 1.0) > 1e-3):
        raise ValueError("Quaternion end-effector không được chuẩn hóa")

    current = _observations(qpos[:-1], qvel[:-1], ee_pose[:-1], object_state[:-1])
    following = _observations(qpos[1:], qvel[1:], ee_pose[1:], object_state[1:])
    count = len(actions) - 1
    is_success = bool(meta.get("task_success", False) if successful is None else successful)
    rewards = np.zeros(count, dtype=np.float32)
    rewards[-1] = 1.0 if is_success else 0.0
    dones = np.zeros(count, dtype=np.int64)
    dones[-1] = 1
    control_hz = int(meta.get("control_hz", 0))
    if control_hz <= 0:
        raise ValueError("control_hz phải là số nguyên dương")

    return TeleopRoboMimicEpisode(
        episode_id=str(meta.get("episode_id") or root.name),
        task_name=task_name,
        actions=actions[:-1].astype(np.float32),
        states=states[:-1],
        rewards=rewards,
        dones=dones,
        observations=current,
        next_observations=following,
        attrs={
            "source": "manual_teleop",
            "operator_id": str(meta.get("operator_id", "")),
            "control_hz": control_hz,
            "teleop_schema_version": int(meta.get("teleop_schema_version", 1)),
            "task_success": is_success,
            "partial": bool(meta.get("partial", False)),
            "interrupted": bool(meta.get("interrupted", False)),
            "started_at": str(meta.get("started_at", "")),
            "source_num_observations": len(indexes),
            "alignment": "obs[i],action[i],next_obs[i+1];last_action_dropped",
        },
        env_args={
            "env_name": env_name,
            "type": 1,
            "env_kwargs": {
                "robots": robot,
                "control_freq": control_hz,
            },
        },
    )
