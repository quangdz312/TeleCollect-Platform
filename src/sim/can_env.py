"""Robosuite environment factory for the Can data-collection task."""

from __future__ import annotations

import json
import random
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


REFERENCE_DATASET = Path("data/datasets/can/ph/low_dim_v15.hdf5")


@dataclass(frozen=True)
class CanEnvironmentConfig:
    env_name: str
    robot: str
    control_freq: int
    controller_name: str = "BASIC"


def _default_can_env_args() -> dict[str, Any]:
    return {
        "env_name": "PickPlaceCan",
        "env_kwargs": {
            "robots": ["Panda"],
            "controller_configs": {"body_parts": {"right": {"type": "OSC_POSE"}}},
            "control_freq": 20,
        },
    }


def read_reference_env_args(reference_path: str | Path = REFERENCE_DATASET) -> dict[str, Any]:
    """Read the environment metadata that is the source of truth for collection."""
    import h5py

    path = Path(reference_path)
    if not path.exists():
        return _default_can_env_args()
    with h5py.File(path, "r") as handle:
        return json.loads(handle["data"].attrs["env_args"])


def reference_config(reference_path: str | Path = REFERENCE_DATASET) -> CanEnvironmentConfig:
    args = read_reference_env_args(reference_path)
    kwargs = args["env_kwargs"]
    controller = kwargs["controller_configs"]["body_parts"]["right"]["type"]
    if controller != "OSC_POSE":
        raise ValueError(f"Can reference dataset uses unsupported controller {controller!r}")
    robots = kwargs["robots"]
    if robots != ["Panda"]:
        raise ValueError(f"Can reference dataset uses unexpected robots {robots!r}")
    return CanEnvironmentConfig(args["env_name"], robots[0], int(kwargs["control_freq"]))


def make_can_environment(
    *,
    render: bool = False,
    seed: int | None = None,
    reference_path: str | Path = REFERENCE_DATASET,
) -> Any:
    """Create the exact low-dimensional Can environment described by the reference."""
    import robosuite
    from robosuite.controllers import load_composite_controller_config

    config = reference_config(reference_path)
    controller = load_composite_controller_config(controller=config.controller_name)
    env = robosuite.make(
        config.env_name,
        robots=config.robot,
        controller_configs=controller,
        has_renderer=render,
        has_offscreen_renderer=False,
        render_camera="agentview",
        use_object_obs=True,
        use_camera_obs=False,
        control_freq=config.control_freq,
        ignore_done=True,
        reward_shaping=False,
        hard_reset=False,
    )
    return env


def reset_can_environment(env: Any, seed: int | None = None) -> dict[str, Any]:
    """Reset reproducibly despite Robosuite 1.5.1's mixed RNG APIs."""
    if seed is not None:
        import numpy as np

        env.seed = seed
        env.rng = np.random.default_rng(seed)
        np.random.seed(seed)
        random.seed(seed)
    return env.reset()


def close_environment(env: Any) -> None:
    """Close Robosuite and its viewer safely, including partially initialized envs."""
    if env is None:
        return
    try:
        env.close()
    except Exception:
        viewer = getattr(env, "viewer", None)
        if viewer is not None and hasattr(viewer, "close"):
            viewer.close()


@contextmanager
def can_environment(**kwargs: Any) -> Iterator[Any]:
    env = make_can_environment(**kwargs)
    try:
        yield env
    finally:
        close_environment(env)
