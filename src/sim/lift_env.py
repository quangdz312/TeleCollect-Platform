"""Reference-metadata-backed environment factory for Robosuite Lift."""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

LIFT_REFERENCE_DATASET = Path("data/datasets/lift/ph/low_dim_v15.hdf5")


def _default_lift_env_args() -> dict[str, Any]:
    return {
        "env_name": "Lift",
        "env_kwargs": {
            "robots": ["Panda"],
            "controller_configs": {"body_parts": {"right": {"type": "OSC_POSE"}}},
            "control_freq": 20,
        },
    }


def read_lift_env_args(reference_path: str | Path = LIFT_REFERENCE_DATASET) -> dict[str, Any]:
    import h5py

    path = Path(reference_path)
    if not path.exists():
        return _default_lift_env_args()
    with h5py.File(path, "r") as handle:
        return json.loads(handle["data"].attrs["env_args"])


def make_lift_environment(
    *,
    render: bool = False,
    seed: int | None = None,
    reference_path: str | Path = LIFT_REFERENCE_DATASET,
) -> Any:
    import robosuite
    from robosuite.controllers import load_composite_controller_config

    args = read_lift_env_args(reference_path)
    kwargs = args["env_kwargs"]
    robots = kwargs["robots"]
    controller_type = kwargs["controller_configs"]["body_parts"]["right"]["type"]
    if robots != ["Panda"] or controller_type != "OSC_POSE":
        raise ValueError(f"Unsupported Lift reference: robots={robots}, controller={controller_type}")
    controller = load_composite_controller_config(controller="BASIC")
    return robosuite.make(
        args["env_name"],
        robots=robots[0],
        controller_configs=controller,
        has_renderer=render,
        has_offscreen_renderer=False,
        render_camera="agentview",
        use_object_obs=True,
        use_camera_obs=False,
        control_freq=int(kwargs["control_freq"]),
        ignore_done=True,
        reward_shaping=False,
        hard_reset=False,
    )


def reset_lift_environment(env: Any, seed: int | None = None) -> dict[str, Any]:
    if seed is not None:
        import numpy as np

        env.seed = seed
        env.rng = np.random.default_rng(seed)
        np.random.seed(seed)
        random.seed(seed)
    return env.reset()


@contextmanager
def lift_environment(**kwargs: Any) -> Iterator[Any]:
    env = make_lift_environment(**kwargs)
    try:
        yield env
    finally:
        env.close()
