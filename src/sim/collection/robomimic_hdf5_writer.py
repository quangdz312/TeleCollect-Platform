"""Atomic writer for raw Robomimic-compatible HDF5 demonstrations."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Any

import h5py
import numpy as np

ROBOSUITE_ENV_TYPE = 1


def normalize_robomimic_env_args(env_args: Mapping[str, Any]) -> dict[str, Any]:
    """Return RoboMimic environment metadata with the required simulator type.

    Every simulator used by TeleCollect is a RoboSuite environment. RoboMimic
    dispatches its observation handling from this top-level integer and raises
    ``KeyError('type')`` before training when it is absent.
    """

    normalized = deepcopy(dict(env_args))
    existing = normalized.setdefault("type", ROBOSUITE_ENV_TYPE)
    if existing != ROBOSUITE_ENV_TYPE:
        raise ValueError(
            f"TeleCollect datasets require RoboSuite env type {ROBOSUITE_ENV_TYPE}, got {existing!r}"
        )
    try:
        robosuite_distribution = distribution("robosuite")
        robosuite_version = version("robosuite")
    except ModuleNotFoundError as exc:
        raise RuntimeError("RoboSuite is required to build rollout-compatible metadata") from exc

    normalized.setdefault("env_version", robosuite_version)
    env_kwargs = deepcopy(dict(normalized.get("env_kwargs", {})))
    # `stage` is TeleCollect provenance, not a valid RoboSuite constructor kwarg.
    env_kwargs.pop("stage", None)

    supplied_controller = env_kwargs.get("controller_configs")
    # Importing ``robosuite.controllers`` triggers Numba JIT initialisation and
    # can hold a simple dataset export for minutes on a fresh Windows machine.
    # The loader itself only reads this packaged JSON and flattens ``arms``;
    # reproduce that data-only operation here so export never boots a simulator.
    controller_path = robosuite_distribution.locate_file(
        "robosuite/controllers/config/default/composite/basic.json"
    )
    try:
        full_controller = json.loads(Path(controller_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Cannot read RoboSuite BASIC controller metadata") from exc
    raw_parts = dict(full_controller.get("body_parts", {}))
    arms = dict(raw_parts.pop("arms", {}))
    full_controller["body_parts"] = {**arms, **raw_parts}
    if isinstance(supplied_controller, Mapping):
        supplied_right = supplied_controller.get("body_parts", {}).get("right", {})
        if isinstance(supplied_right, Mapping):
            full_controller["body_parts"]["right"].update(deepcopy(dict(supplied_right)))
    env_kwargs["controller_configs"] = full_controller
    env_kwargs.setdefault("use_object_obs", True)
    env_kwargs.setdefault("use_camera_obs", False)
    env_kwargs.setdefault("reward_shaping", False)
    env_kwargs.setdefault("ignore_done", True)
    env_kwargs.setdefault("hard_reset", False)
    normalized["env_kwargs"] = env_kwargs
    return normalized


@dataclass
class EpisodeData:
    actions: list[np.ndarray] = field(default_factory=list)
    states: list[np.ndarray] = field(default_factory=list)
    rewards: list[float] = field(default_factory=list)
    dones: list[bool] = field(default_factory=list)
    observations: list[dict[str, np.ndarray]] = field(default_factory=list)
    next_observations: list[dict[str, np.ndarray]] = field(default_factory=list)
    model_xml: str = ""
    success: bool = False
    termination_reason: str = "unknown"
    seed: int = 0

    tool_name: str = "unknown"
    operator_version: str = "unknown"
    def append(

        self, *, state: Any, observation: dict[str, Any], action: Any,
        reward: float, done: bool, next_observation: dict[str, Any],
    ) -> None:
        self.states.append(np.asarray(state).copy())
        self.observations.append({k: np.asarray(v).copy() for k, v in observation.items()})
        self.actions.append(np.asarray(action).copy())
        self.rewards.append(float(reward))
        self.dones.append(bool(done))
        self.next_observations.append({k: np.asarray(v).copy() for k, v in next_observation.items()})


class RobomimicHDF5Writer:
    """Collect episodes into a temporary HDF5 and publish only on clean close."""

    REQUIRED_OBS_KEYS = (
        "robot0_eef_pos", "robot0_eef_quat", "robot0_eef_quat_site",
        "robot0_gripper_qpos", "robot0_gripper_qvel", "robot0_joint_pos",
        "robot0_joint_pos_cos", "robot0_joint_pos_sin", "robot0_joint_vel", "object",
    )

    def __init__(
        self,
        output: str | Path,
        env_args: dict[str, Any],
        *,
        overwrite: bool = False,
        collection_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.output = Path(output)
        if self.output.exists() and not overwrite:
            raise FileExistsError(f"Output already exists: {self.output}")
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.temp_path = self.output.with_name(f".{self.output.name}.{uuid.uuid4().hex}.tmp")
        self._handle = h5py.File(self.temp_path, "w")
        self._data = self._handle.create_group("data")
        self._data.attrs["env_args"] = json.dumps(
            normalize_robomimic_env_args(env_args), indent=4,
        )
        # Additive batch provenance; the core Robomimic schema is unchanged.
        if collection_metadata:
            self._data.attrs.update(dict(collection_metadata))
        self._total = 0
        self._closed = False

    def write_episode(
        self, episode: EpisodeData, *, provenance: Mapping[str, Any] | None = None,
    ) -> str:
        lengths = {
            len(episode.actions), len(episode.states), len(episode.rewards), len(episode.dones),
            len(episode.observations), len(episode.next_observations),
        }
        if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
            raise ValueError(f"Episode timestep lengths must be equal and non-zero: {sorted(lengths)}")
        count = len(episode.actions)
        actions = np.asarray(episode.actions)
        if actions.shape != (count, 7):
            raise ValueError(f"Expected actions shape ({count}, 7), got {actions.shape}")
        obs_keys = set(episode.observations[0])
        next_keys = set(episode.next_observations[0])
        missing = set(self.REQUIRED_OBS_KEYS) - obs_keys
        if missing or obs_keys != next_keys:
            raise ValueError(f"Observation schema mismatch; missing={sorted(missing)}")

        name = f"demo_{len(self._data)}"
        demo = self._data.create_group(name)
        demo.create_dataset("actions", data=actions)
        demo.create_dataset("states", data=np.asarray(episode.states))
        demo.create_dataset("rewards", data=np.asarray(episode.rewards, dtype=np.float64))
        demo.create_dataset("dones", data=np.asarray(episode.dones, dtype=np.int64))
        obs_group = demo.create_group("obs")
        next_group = demo.create_group("next_obs")
        for key in sorted(obs_keys):
            try:
                obs_values = np.stack([sample[key] for sample in episode.observations])
                next_values = np.stack([sample[key] for sample in episode.next_observations])
            except (KeyError, ValueError) as exc:
                raise ValueError(f"Inconsistent observation values for key {key!r}") from exc
            obs_group.create_dataset(key, data=obs_values)
            next_group.create_dataset(key, data=next_values)

        demo.attrs.update({
            "num_samples": count,
            "model_file": episode.model_xml,
            "camera_info": "{}",
            "source": "scripted_operator",
            "collection_status": "raw",
            "success": bool(episode.success),
            "termination_reason": episode.termination_reason,
            "seed": int(episode.seed),
            "tool_name": episode.tool_name,
            "operator_version": episode.operator_version,
        })
        if provenance:
            # Episode provenance lives beside the core attrs so a reader can
            # reproduce the sampled variation without a sidecar file.
            demo.attrs.update(dict(provenance))
        self._total += count
        self._data.attrs["total"] = self._total
        self._handle.flush()
        return name

    def close(self, *, publish: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        self._data.attrs["total"] = self._total
        self._handle.flush()
        self._handle.close()
        if publish:
            os.replace(self.temp_path, self.output)
        elif self.temp_path.exists():
            self.temp_path.unlink()

    def __enter__(self) -> RobomimicHDF5Writer:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close(publish=exc_type is None)
