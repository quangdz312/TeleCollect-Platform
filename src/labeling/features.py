"""Read a collected HDF5 into the plain arrays the scoring layer works on.

This is the only module in :mod:`src.labeling` that touches the filesystem.
Everything above it takes arrays and returns numbers, which is what makes the
scorer testable without fixtures and re-runnable in batch.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import h5py
import numpy as np

#: Where the object pose sits inside the flattened ``object`` observation, per
#: task. Verified against live Robosuite observables rather than assumed.
OBJECT_LAYOUT: Mapping[str, Mapping[str, tuple[int, int]]] = {
    "lift": {"position": (0, 3), "orientation": (3, 7)},
    "can": {"position": (7, 10), "orientation": (10, 14)},
    "square": {"position": (7, 10), "orientation": (10, 14)},
    "tool_hang": {"position": (0, 3), "orientation": (3, 7)},
}

# RoboSuite 1.5 ToolHang concatenates its enabled object observables in this
# order: base-to-eef, base pose, frame-to-eef, frame pose, tool-to-eef, tool
# pose, and two success predicates. Consequently the frame pose in the native
# 44-D object-state starts at column 21. Keep the legacy 14-D layout above so
# already collected recordings remain reviewable.
TOOLHANG_ROBOSUITE_LAYOUT: Mapping[str, tuple[int, int]] = {
    "position": (21, 24),
    "orientation": (24, 28),
}


def _object_layout(task: str, width: int) -> Mapping[str, tuple[int, int]] | None:
    if task == "tool_hang" and width == 44:
        return TOOLHANG_ROBOSUITE_LAYOUT
    return OBJECT_LAYOUT.get(task)

DEFAULT_CONTROL_HZ = 20.0


class EpisodeLoadError(ValueError):
    """Raised when a demo cannot be read into the scoring arrays."""


@dataclass(frozen=True)
class EpisodeArrays:
    """One demonstration, reduced to what the checks and penalties need.

    Two views of the same rollout are kept on purpose.

    The ``*_position`` arrays have one row per action, which is what the
    penalties want: they describe the commanded motion.

    The ``*_trajectory`` properties have one row per *state* — ``T + 1`` of them,
    because the state after the final action is only recorded in ``next_obs``.
    The checks need that view. The executor evaluates the success condition
    after stepping, so on a successful episode the frame where the task actually
    succeeded is exactly the one that exists only in ``next_obs``; reading
    ``obs`` alone makes every successful episode look like it never finished.
    """

    source: Path
    demo: str
    task: str
    quality: str
    actions: np.ndarray
    eef_position: np.ndarray
    gripper_qpos: np.ndarray
    object_position: np.ndarray
    object_orientation: np.ndarray
    final_eef_position: np.ndarray
    final_gripper_qpos: np.ndarray
    final_object_position: np.ndarray
    final_object_orientation: np.ndarray
    rewards: np.ndarray
    dones: np.ndarray
    recorded_success: bool
    termination_reason: str
    control_hz: float = DEFAULT_CONTROL_HZ
    action_low: np.ndarray = field(default_factory=lambda: np.full(7, -1.0))
    action_high: np.ndarray = field(default_factory=lambda: np.full(7, 1.0))
    num_samples_attr: int = -1
    provenance: Mapping[str, Any] = field(default_factory=dict)

    @property
    def episode_id(self) -> str:
        return f"{self.source.name}::{self.demo}"

    @property
    def length(self) -> int:
        return int(self.actions.shape[0])

    @property
    def dt(self) -> float:
        return 1.0 / float(self.control_hz)

    @property
    def gripper_command(self) -> np.ndarray:
        """Commanded gripper signal; positive closes on this action contract."""

        return self.actions[:, 6]

    @property
    def object_trajectory(self) -> np.ndarray:
        return np.vstack((self.object_position, self.final_object_position[None, :]))

    @property
    def eef_trajectory(self) -> np.ndarray:
        return np.vstack((self.eef_position, self.final_eef_position[None, :]))

    @property
    def gripper_qpos_trajectory(self) -> np.ndarray:
        return np.vstack((self.gripper_qpos, self.final_gripper_qpos[None, :]))

    @property
    def gripper_command_trajectory(self) -> np.ndarray:
        """Command per state; the final state inherits the last command issued."""

        command = self.gripper_command
        if command.size == 0:
            return command
        return np.concatenate((command, command[-1:]))

    @property
    def gripper_to_object(self) -> np.ndarray:
        """Hand-to-object offset over the full state trajectory."""

        return self.object_trajectory - self.eef_trajectory


def _attr(group: Any, name: str, default: Any = None) -> Any:
    value = group.attrs.get(name, default)
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.generic):
        return value.item()
    return value


def _control_hz(data: Any) -> float:
    try:
        env_args = json.loads(_attr(data, "env_args", "{}"))
        return float(env_args["env_kwargs"]["control_freq"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return DEFAULT_CONTROL_HZ


def _provenance(data: Any, demo: Any) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for container in (data, demo):
        for key in container.attrs:
            if not key.startswith("telecollect_"):
                continue
            value = _attr(container, key)
            if key == "telecollect_sampled_variation" and isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            record[key[len("telecollect_") :]] = value
    return record


def _task_of(data: Any, demo: Any, fallback: str | None) -> str:
    task = _attr(demo, "telecollect_task") or _attr(data, "telecollect_task")
    if task:
        return str(task)
    tool = _attr(demo, "tool_name", "")
    by_tool = {
        "lift_cube": "lift",
        "pick_place_can": "can",
        "assemble_square": "square",
        "tool_hang_stage1": "tool_hang",
    }
    if tool in by_tool:
        return by_tool[tool]
    if fallback:
        return fallback
    raise EpisodeLoadError(
        "Cannot determine the task; pass task= or collect with provenance attributes",
    )


def load_episodes(
    path: str | Path,
    *,
    task: str | None = None,
) -> list[EpisodeArrays]:
    """Load every demo in one collected HDF5 file.

    ``task`` is only needed for legacy files written before provenance existed.
    """

    source = Path(path)
    episodes: list[EpisodeArrays] = []
    with h5py.File(source, "r") as handle:
        if "data" not in handle:
            raise EpisodeLoadError(f"{source}: missing group 'data'")
        data = handle["data"]
        control_hz = _control_hz(data)
        names = sorted(
            (key for key in data if key.startswith("demo_")),
            key=lambda name: int(name.split("_")[1]),
        )
        for name in names:
            demo = data[name]
            resolved_task = _task_of(data, demo, task)
            observations = demo["obs"]
            next_observations = demo["next_obs"]
            object_state = np.asarray(observations["object"], dtype=np.float64)
            next_object_state = np.asarray(next_observations["object"], dtype=np.float64)
            layout = _object_layout(resolved_task, object_state.shape[1])
            if layout is None:
                raise EpisodeLoadError(f"{source}::{name}: unsupported task {resolved_task!r}")
            position_slice = layout["position"]
            orientation_slice = layout["orientation"]
            if object_state.shape[1] < orientation_slice[1]:
                raise EpisodeLoadError(
                    f"{source}::{name}: object observation has {object_state.shape[1]} "
                    f"columns, expected at least {orientation_slice[1]} for {resolved_task}",
                )
            episodes.append(
                EpisodeArrays(
                    source=source,
                    demo=name,
                    task=resolved_task,
                    quality=str(_attr(demo, "telecollect_requested_quality", "unknown")),
                    actions=np.asarray(demo["actions"], dtype=np.float64),
                    eef_position=np.asarray(observations["robot0_eef_pos"], dtype=np.float64),
                    gripper_qpos=np.asarray(observations["robot0_gripper_qpos"], dtype=np.float64),
                    object_position=object_state[:, position_slice[0] : position_slice[1]],
                    object_orientation=object_state[
                        :, orientation_slice[0] : orientation_slice[1]
                    ],
                    final_eef_position=np.asarray(
                        next_observations["robot0_eef_pos"][-1], dtype=np.float64,
                    ),
                    final_gripper_qpos=np.asarray(
                        next_observations["robot0_gripper_qpos"][-1], dtype=np.float64,
                    ),
                    final_object_position=next_object_state[
                        -1, position_slice[0] : position_slice[1]
                    ],
                    final_object_orientation=next_object_state[
                        -1, orientation_slice[0] : orientation_slice[1]
                    ],
                    rewards=np.asarray(demo["rewards"], dtype=np.float64),
                    dones=np.asarray(demo["dones"]),
                    recorded_success=bool(_attr(demo, "success", False)),
                    termination_reason=str(_attr(demo, "termination_reason", "unknown")),
                    control_hz=control_hz,
                    num_samples_attr=int(_attr(demo, "num_samples", -1)),
                    provenance=_provenance(data, demo),
                ),
            )
    return episodes


def load_many(paths: list[str | Path], *, task: str | None = None) -> list[EpisodeArrays]:
    episodes: list[EpisodeArrays] = []
    for path in paths:
        episodes.extend(load_episodes(path, task=task))
    return episodes
