"""Square-specific reset, success, and recording adapters."""

from __future__ import annotations

from typing import Any, Callable

from src.sim.collection.robomimic_hdf5_writer import RobomimicHDF5Writer
from src.sim.square_env import reset_square_environment
from src.sim.tools.base import ToolContext


def square_observation(observation: dict[str, Any]) -> dict[str, Any]:
    result = dict(observation)
    if "object" not in result and "object-state" in result:
        result["object"] = result["object-state"]
    return {key: result[key] for key in RobomimicHDF5Writer.REQUIRED_OBS_KEYS}


def build_square_tool_context(
    env: Any, *, horizon: int, seed: int, verbose: bool = False,
    logger: Callable[[str], None] = print,
) -> ToolContext:
    return ToolContext(
        env=env, horizon=horizon, seed=seed, reset=reset_square_environment,
        is_success=lambda active_env: bool(active_env._check_success()),
        adapt_observation=square_observation, verbose=verbose, logger=logger,
    )
